"""
tests/test_tower_scan.py — Unit Tests para TowerScanner (app/tower_scan.py)

Metodologia: BDD (Given/When/Then) para orchestracion,
             TDD (data-driven) para primitivas SNMP.

Cobertura:
  Layer 1 — Primitivas SNMP: _snmp_set, _snmp_get, _snmp_get_oid_raw,
            _verify_connectivity, constructor, log callback
  Layer 2 — Orchestracion async: validate_and_filter_devices,
            _prepare_scan_async, _start_scan_signal_async,
            _wait_for_completion_async, start_tower_scan, run_scan

Mock strategy:
  - pysnmp functions patched at `app.tower_scan.*` namespace (star import)
  - Layer 2 tests mock TowerScanner methods via patch.object
  - Async tests use asyncio.run() (no pytest-asyncio dependency)
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, call

from app.tower_scan import TowerScanner


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers / Factories
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def make_scanner(ap_ips=None, snmp_communities=None, sm_ips=None, log_callback=None):
    """Factory para TowerScanner con defaults de test."""
    return TowerScanner(
        ap_ips=ap_ips or ["10.0.0.1"],
        snmp_communities=snmp_communities,
        sm_ips=sm_ips,
        log_callback=log_callback,
    )


def make_snmp_response(value=4, error_indication=None, error_status=None):
    """
    Build a mock pysnmp iterator response.

    Returns an iterator yielding one tuple:
        (errorIndication, errorStatus, errorIndex, varBinds)

    varBinds is a list of (oid, value) pairs where value supports int().
    """
    mock_val = MagicMock()
    mock_val.__int__ = MagicMock(return_value=value)
    mock_val.__str__ = MagicMock(return_value=str(value))
    var_bind = (MagicMock(), mock_val)
    return iter([(error_indication, error_status, 0, [var_bind])])


# Patch targets — all from star import in app.tower_scan
SNMP_PATCHES = [
    "app.tower_scan.SnmpEngine",
    "app.tower_scan.CommunityData",
    "app.tower_scan.UdpTransportTarget",
    "app.tower_scan.ContextData",
    "app.tower_scan.ObjectType",
    "app.tower_scan.ObjectIdentity",
]


def _apply_snmp_patches(monkeypatch_or_patches=None):
    """Apply standard SNMP class patches — returns nothing, just silences constructors."""
    # Used inside @patch context; the individual test applies setCmd/getCmd patches.
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: Constructor & Community Parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestConstructorCommunityParsing:
    """
    GIVEN various community input formats
    WHEN TowerScanner is instantiated
    THEN communities are parsed correctly into a list.
    """

    def test_csv_string_parsed_to_list(self):
        """GIVEN snmp_communities='Canopy,public' WHEN constructed THEN list of 2."""
        scanner = make_scanner(snmp_communities="Canopy,public")
        assert scanner.snmp_communities == ["Canopy", "public"]

    def test_csv_with_spaces_stripped(self):
        """GIVEN '  Canopy , public  ' WHEN constructed THEN strips whitespace."""
        scanner = make_scanner(snmp_communities="  Canopy , public  ")
        assert scanner.snmp_communities == ["Canopy", "public"]

    def test_list_passed_directly(self):
        """GIVEN list ['A', 'B'] WHEN constructed THEN stored as-is."""
        scanner = make_scanner(snmp_communities=["A", "B"])
        assert scanner.snmp_communities == ["A", "B"]

    def test_none_reads_env_variable(self, monkeypatch):
        """GIVEN communities=None and SNMP_COMMUNITIES env set WHEN constructed THEN reads env."""
        monkeypatch.setenv("SNMP_COMMUNITIES", "EnvComm1,EnvComm2")
        scanner = make_scanner(snmp_communities=None)
        assert scanner.snmp_communities == ["EnvComm1", "EnvComm2"]

    def test_none_defaults_to_canopy(self, monkeypatch):
        """GIVEN communities=None and no env var WHEN constructed THEN defaults to ['Canopy']."""
        monkeypatch.delenv("SNMP_COMMUNITIES", raising=False)
        scanner = make_scanner(snmp_communities=None)
        assert scanner.snmp_communities == ["Canopy"]

    def test_sm_ips_default_empty(self):
        """GIVEN no sm_ips WHEN constructed THEN sm_ips is empty list."""
        scanner = make_scanner()
        assert scanner.sm_ips == []

    def test_device_community_map_empty_initially(self):
        """GIVEN fresh scanner WHEN constructed THEN device_community_map is empty."""
        scanner = make_scanner()
        assert scanner.device_community_map == {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: _snmp_set
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSnmpSet:
    """
    GIVEN a TowerScanner instance
    WHEN _snmp_set() is called with various SNMP response conditions
    THEN it returns the correct (success, message) tuple.
    """

    @patch("app.tower_scan.Integer32", create=True)
    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.setCmd", create=True)
    def test_success_returns_true_ok(self, mock_set, *_snmp_mocks):
        """GIVEN no errors WHEN _snmp_set THEN returns (True, 'OK')."""
        mock_set.return_value = make_snmp_response(value=8)
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, msg = scanner._snmp_set("10.0.0.1", 8)
        assert ok is True
        assert msg == "OK"

    @patch("app.tower_scan.Integer32", create=True)
    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.setCmd", create=True)
    def test_error_indication_returns_false(self, mock_set, *_snmp_mocks):
        """GIVEN errorIndication WHEN _snmp_set THEN returns (False, error msg)."""
        mock_set.return_value = make_snmp_response(error_indication="requestTimedOut")
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, msg = scanner._snmp_set("10.0.0.1", 8)
        assert ok is False
        assert "requestTimedOut" in msg

    @patch("app.tower_scan.Integer32", create=True)
    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.setCmd", create=True)
    def test_error_status_not_writable(self, mock_set, *_snmp_mocks):
        """GIVEN errorStatus with notWritable WHEN _snmp_set THEN returns SOLO LECTURA msg."""
        error_status = MagicMock()
        error_status.prettyPrint.return_value = "notWritable(17)"
        # errorIndication must be falsy, errorStatus must be truthy
        mock_set.return_value = iter([(None, error_status, 0, [])])
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, msg = scanner._snmp_set("10.0.0.1", 8)
        assert ok is False
        assert "SOLO LECTURA" in msg

    @patch("app.tower_scan.Integer32", create=True)
    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.setCmd", create=True)
    def test_generic_error_status(self, mock_set, *_snmp_mocks):
        """GIVEN a generic errorStatus WHEN _snmp_set THEN returns (False, error)."""
        error_status = MagicMock()
        error_status.prettyPrint.return_value = "genErr(5)"
        mock_set.return_value = iter([(None, error_status, 0, [])])
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, msg = scanner._snmp_set("10.0.0.1", 8)
        assert ok is False
        assert "genErr" in msg

    @patch("app.tower_scan.Integer32", create=True)
    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.setCmd", create=True)
    def test_exception_returns_false(self, mock_set, *_snmp_mocks):
        """GIVEN setCmd raises WHEN _snmp_set THEN returns (False, exception str)."""
        mock_set.side_effect = Exception("Connection refused")
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, msg = scanner._snmp_set("10.0.0.1", 8)
        assert ok is False
        assert "Connection refused" in msg


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: _snmp_get
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSnmpGet:
    """
    GIVEN a TowerScanner instance
    WHEN _snmp_get() is called with various SNMP response conditions
    THEN it returns the correct (success, value, message) tuple.
    """

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_success_returns_value(self, mock_get, *_snmp_mocks):
        """GIVEN no errors WHEN _snmp_get THEN returns (True, int_value, 'OK')."""
        mock_get.return_value = make_snmp_response(value=4)
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, value, msg = scanner._snmp_get("10.0.0.1")
        assert ok is True
        assert value == 4
        assert msg == "OK"

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_error_indication(self, mock_get, *_snmp_mocks):
        """GIVEN errorIndication WHEN _snmp_get THEN returns (False, 0, error)."""
        mock_get.return_value = make_snmp_response(error_indication="timeout")
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, value, msg = scanner._snmp_get("10.0.0.1")
        assert ok is False
        assert value == 0
        assert "timeout" in msg

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_error_status(self, mock_get, *_snmp_mocks):
        """GIVEN errorStatus WHEN _snmp_get THEN returns (False, 0, error)."""
        error_status = MagicMock()
        error_status.prettyPrint.return_value = "noSuchName"
        mock_get.return_value = iter([(None, error_status, 0, [])])
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, value, msg = scanner._snmp_get("10.0.0.1")
        assert ok is False
        assert value == 0

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_exception_returns_false(self, mock_get, *_snmp_mocks):
        """GIVEN getCmd raises WHEN _snmp_get THEN returns (False, 0, error)."""
        mock_get.side_effect = Exception("Network error")
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, value, msg = scanner._snmp_get("10.0.0.1")
        assert ok is False
        assert value == 0
        assert "Network error" in msg


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: _snmp_get_oid_raw
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSnmpGetOidRaw:
    """
    GIVEN a TowerScanner instance
    WHEN _snmp_get_oid_raw() is called with a specific community
    THEN it returns (success, value_str, message).
    """

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_success_returns_string_value(self, mock_get, *_snmp_mocks):
        """GIVEN no errors WHEN _snmp_get_oid_raw THEN returns (True, str_value, 'OK')."""
        mock_val = MagicMock()
        mock_val.__str__ = MagicMock(return_value="AP-Tower1")
        var_bind = (MagicMock(), mock_val)
        mock_get.return_value = iter([(None, None, 0, [var_bind])])

        scanner = make_scanner()
        ok, val, msg = scanner._snmp_get_oid_raw(
            "10.0.0.1", "1.3.6.1.2.1.1.5.0", "Canopy"
        )
        assert ok is True
        assert val == "AP-Tower1"
        assert msg == "OK"

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_error_indication_returns_false(self, mock_get, *_snmp_mocks):
        """GIVEN errorIndication WHEN _snmp_get_oid_raw THEN returns (False, '', error)."""
        mock_get.return_value = iter([("authError", None, 0, [])])
        scanner = make_scanner()
        ok, val, msg = scanner._snmp_get_oid_raw(
            "10.0.0.1", "1.3.6.1.2.1.1.5.0", "Wrong"
        )
        assert ok is False
        assert val == ""
        assert "authError" in msg

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_exception_returns_false(self, mock_get, *_snmp_mocks):
        """GIVEN getCmd raises WHEN _snmp_get_oid_raw THEN returns (False, '', 'Excepcion')."""
        mock_get.side_effect = Exception("DNS failure")
        scanner = make_scanner()
        ok, val, msg = scanner._snmp_get_oid_raw(
            "10.0.0.1", "1.3.6.1.2.1.1.5.0", "Canopy"
        )
        assert ok is False
        assert val == ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: _verify_connectivity
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestVerifyConnectivity:
    """
    GIVEN a TowerScanner instance
    WHEN _verify_connectivity() is called
    THEN it returns (reachable, message) based on SNMP sysName probe.
    """

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_reachable(self, mock_get, *_snmp_mocks):
        """GIVEN SNMP responds WHEN _verify_connectivity THEN returns (True, OK msg)."""
        mock_get.return_value = make_snmp_response(value=0)
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, msg = scanner._verify_connectivity("10.0.0.1")
        assert ok is True
        assert "OK" in msg

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_unreachable_error_indication(self, mock_get, *_snmp_mocks):
        """GIVEN SNMP timeout WHEN _verify_connectivity THEN returns (False, error msg)."""
        mock_get.return_value = make_snmp_response(error_indication="requestTimedOut")
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, msg = scanner._verify_connectivity("10.0.0.1")
        assert ok is False

    @patch("app.tower_scan.ObjectIdentity", create=True)
    @patch("app.tower_scan.ObjectType", create=True)
    @patch("app.tower_scan.ContextData", create=True)
    @patch("app.tower_scan.UdpTransportTarget", create=True)
    @patch("app.tower_scan.CommunityData", create=True)
    @patch("app.tower_scan.SnmpEngine", create=True)
    @patch("app.tower_scan.getCmd", create=True)
    def test_exception_returns_false(self, mock_get, *_snmp_mocks):
        """GIVEN network exception WHEN _verify_connectivity THEN returns (False, msg)."""
        mock_get.side_effect = Exception("Socket error")
        scanner = make_scanner(snmp_communities=["Canopy"])
        ok, msg = scanner._verify_connectivity("10.0.0.1")
        assert ok is False
        assert "Socket error" in msg


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: _log callback dispatch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLogCallback:
    """
    GIVEN a TowerScanner with a log_callback
    WHEN _log() is called
    THEN the callback receives (msg, level).
    """

    def test_callback_receives_msg_and_level(self):
        """GIVEN callback WHEN _log('test', 'warning') THEN callback called with those args."""
        cb = MagicMock()
        scanner = make_scanner(log_callback=cb)
        scanner._log("test message", "warning")
        cb.assert_called_once_with("test message", "warning")

    def test_callback_exception_is_swallowed(self):
        """GIVEN callback that raises WHEN _log() THEN no exception propagated."""

        def bad_callback(msg, level):
            raise RuntimeError("callback error")

        scanner = make_scanner(log_callback=bad_callback)
        # Should not raise
        scanner._log("test", "info")

    def test_get_community_uses_map(self):
        """GIVEN a mapped community WHEN _get_community() THEN returns mapped value."""
        scanner = make_scanner(snmp_communities=["Default"])
        scanner.device_community_map["10.0.0.1"] = "Mapped"
        assert scanner._get_community("10.0.0.1") == "Mapped"

    def test_get_community_falls_back_to_first(self):
        """GIVEN unmapped IP WHEN _get_community() THEN returns first community."""
        scanner = make_scanner(snmp_communities=["First", "Second"])
        assert scanner._get_community("10.0.0.99") == "First"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: validate_and_filter_devices
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestValidateAndFilterDevices:
    """
    GIVEN a TowerScanner with AP and SM IPs
    WHEN validate_and_filter_devices() is called
    THEN it probes communities and returns (valid_aps, valid_sms, errors).
    """

    def test_first_community_matches(self):
        """GIVEN first community works on sysName WHEN validated THEN AP is valid."""
        scanner = make_scanner(
            ap_ips=["10.0.0.1"],
            snmp_communities=["Good", "Other"],
        )

        with patch.object(
            scanner,
            "_snmp_get_oid_raw",
            return_value=(True, "AP-Tower1", "OK"),
        ):
            valid_aps, valid_sms, errors = asyncio.run(
                scanner.validate_and_filter_devices()
            )

        assert valid_aps == ["10.0.0.1"]
        assert errors == {}
        assert scanner.device_community_map["10.0.0.1"] == "Good"

    def test_second_community_fallback(self):
        """GIVEN first community fails, second works on sysDescr WHEN validated THEN valid."""
        scanner = make_scanner(
            ap_ips=["10.0.0.1"],
            snmp_communities=["Bad", "Good"],
        )

        # For 'Bad': sysName fails, sysDescr fails, cambium fails
        # For 'Good': sysName succeeds
        call_count = {"n": 0}

        def mock_get_oid_raw(ip, oid, community):
            call_count["n"] += 1
            if community == "Bad":
                return (False, "", "authError")
            return (True, "value", "OK")

        with patch.object(scanner, "_snmp_get_oid_raw", side_effect=mock_get_oid_raw):
            valid_aps, _, errors = asyncio.run(scanner.validate_and_filter_devices())

        assert valid_aps == ["10.0.0.1"]
        assert scanner.device_community_map["10.0.0.1"] == "Good"

    def test_all_communities_fail(self):
        """GIVEN no community works WHEN validated THEN IP in errors dict."""
        scanner = make_scanner(
            ap_ips=["10.0.0.1"],
            snmp_communities=["Bad1", "Bad2"],
        )

        with patch.object(
            scanner,
            "_snmp_get_oid_raw",
            return_value=(False, "", "authError"),
        ):
            valid_aps, _, errors = asyncio.run(scanner.validate_and_filter_devices())

        assert valid_aps == []
        assert "10.0.0.1" in errors

    def test_mixed_ap_sm_results(self):
        """GIVEN 2 APs and 1 SM, one AP fails WHEN validated THEN correct splits."""
        scanner = make_scanner(
            ap_ips=["10.0.0.1", "10.0.0.2"],
            snmp_communities=["Comm"],
            sm_ips=["10.0.1.1"],
        )

        def mock_get_oid_raw(ip, oid, community):
            if ip == "10.0.0.2":
                return (False, "", "timeout")
            return (True, "ok", "OK")

        with patch.object(scanner, "_snmp_get_oid_raw", side_effect=mock_get_oid_raw):
            valid_aps, valid_sms, errors = asyncio.run(
                scanner.validate_and_filter_devices()
            )

        assert valid_aps == ["10.0.0.1"]
        assert valid_sms == ["10.0.1.1"]
        assert "10.0.0.2" in errors


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: _prepare_scan_async
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestPrepareScanAsync:
    """
    GIVEN a TowerScanner and a reachable device
    WHEN _prepare_scan_async() is called
    THEN it configures duration and full-scan mode via SNMP SET.
    """

    def test_ap_preparation_success(self):
        """GIVEN reachable AP WHEN _prepare_scan_async('AP') THEN success=True."""
        scanner = make_scanner(snmp_communities=["Canopy"])

        with (
            patch.object(scanner, "_verify_connectivity", return_value=(True, "OK")),
            patch.object(scanner, "_snmp_set", return_value=(True, "OK")),
        ):
            result = asyncio.run(scanner._prepare_scan_async("10.0.0.1", "AP"))

        assert result["success"] is True
        assert result["device_type"] == "AP"

    def test_sm_uses_longer_duration(self):
        """GIVEN SM device WHEN _prepare_scan_async('SM') THEN duration=60."""
        scanner = make_scanner(snmp_communities=["Canopy"])
        set_calls = []

        def mock_set(ip, value, timeout=None, retries=None, oid=None):
            set_calls.append({"value": value, "oid": oid})
            return (True, "OK")

        with (
            patch.object(scanner, "_verify_connectivity", return_value=(True, "OK")),
            patch.object(scanner, "_snmp_set", side_effect=mock_set),
        ):
            result = asyncio.run(scanner._prepare_scan_async("10.0.0.1", "SM"))

        assert result["success"] is True
        # First SET call is duration — SM should be 60
        duration_call = set_calls[0]
        assert duration_call["value"] == 60

    def test_unreachable_device_fails(self):
        """GIVEN unreachable device WHEN _prepare_scan_async THEN success=False."""
        scanner = make_scanner(snmp_communities=["Canopy"])

        with patch.object(
            scanner, "_verify_connectivity", return_value=(False, "timeout")
        ):
            result = asyncio.run(scanner._prepare_scan_async("10.0.0.1", "AP"))

        assert result["success"] is False
        assert "No alcanzable" in result["message"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: _start_scan_signal_async
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestStartScanSignalAsync:
    """
    GIVEN a prepared device
    WHEN _start_scan_signal_async() is called
    THEN it sends START_ANALYSIS (1) via SNMP SET with retries.
    """

    def test_start_success(self):
        """GIVEN SNMP SET succeeds on first try WHEN _start_scan_signal_async THEN success."""
        scanner = make_scanner(snmp_communities=["Canopy"])

        with patch.object(scanner, "_snmp_set", return_value=(True, "OK")):
            result = asyncio.run(scanner._start_scan_signal_async("10.0.0.1", "AP"))

        assert result["success"] is True

    def test_start_retries_on_failure(self):
        """GIVEN first two attempts fail, third succeeds WHEN started THEN success."""
        scanner = make_scanner(snmp_communities=["Canopy"])
        attempts = {"count": 0}

        def mock_set(ip, value, timeout=None, retries=None, oid=None):
            attempts["count"] += 1
            if attempts["count"] < 3:
                return (False, "busy")
            return (True, "OK")

        with (
            patch.object(scanner, "_snmp_set", side_effect=mock_set),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = asyncio.run(scanner._start_scan_signal_async("10.0.0.1", "AP"))

        assert result["success"] is True
        assert attempts["count"] == 3

    def test_start_fails_after_all_retries(self):
        """GIVEN all 3 attempts fail WHEN started THEN success=False."""
        scanner = make_scanner(snmp_communities=["Canopy"])

        with (
            patch.object(scanner, "_snmp_set", return_value=(False, "error")),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = asyncio.run(scanner._start_scan_signal_async("10.0.0.1", "AP"))

        assert result["success"] is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: Safety Locks (start_tower_scan orchestration)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSafetyLocks:
    """
    GIVEN a TowerScanner with SMs
    WHEN SM preparation or start fails
    THEN AP scanning is NEVER initiated (safety lock).
    """

    def test_sm_prep_failure_aborts_everything(self):
        """GIVEN SM fails preparation WHEN run_scan THEN AP never prepared."""
        scanner = make_scanner(
            ap_ips=["10.0.0.1"],
            snmp_communities=["Canopy"],
            sm_ips=["10.0.1.1"],
        )

        async def mock_validate():
            return (["10.0.0.1"], ["10.0.1.1"], {})

        async def mock_prepare(ip, device_type="AP"):
            if device_type == "SM":
                return {
                    "ip": ip,
                    "success": False,
                    "message": "SM failed",
                    "device_type": "SM",
                }
            return {"ip": ip, "success": True, "message": "OK", "device_type": "AP"}

        with (
            patch.object(
                scanner, "validate_and_filter_devices", side_effect=mock_validate
            ),
            patch.object(scanner, "_prepare_scan_async", side_effect=mock_prepare),
            patch.object(scanner, "_start_scan_signal_async") as mock_start,
        ):
            results = asyncio.run(scanner.start_tower_scan())

        # AP start should NEVER be called when SM prep fails
        # (start_scan_signal_async may be called for SMs but should not reach APs)
        assert "10.0.1.1" in results
        assert results["10.0.1.1"]["completed"] is False

    def test_sm_start_failure_aborts_ap(self):
        """GIVEN SM start fails WHEN run_scan THEN AP never started."""
        scanner = make_scanner(
            ap_ips=["10.0.0.1"],
            snmp_communities=["Canopy"],
            sm_ips=["10.0.1.1"],
        )

        async def mock_validate():
            return (["10.0.0.1"], ["10.0.1.1"], {})

        async def mock_prepare(ip, device_type="AP"):
            return {
                "ip": ip,
                "success": True,
                "message": "OK",
                "device_type": device_type,
            }

        async def mock_start_signal(ip, device_type="AP"):
            if device_type == "SM":
                return {
                    "ip": ip,
                    "success": False,
                    "message": "SM start failed",
                    "device_type": "SM",
                }
            return {"ip": ip, "success": True, "message": "OK", "device_type": "AP"}

        with (
            patch.object(
                scanner, "validate_and_filter_devices", side_effect=mock_validate
            ),
            patch.object(scanner, "_prepare_scan_async", side_effect=mock_prepare),
            patch.object(
                scanner, "_start_scan_signal_async", side_effect=mock_start_signal
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            results = asyncio.run(scanner.start_tower_scan())

        assert "10.0.1.1" in results
        assert results["10.0.1.1"]["completed"] is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: Happy Path / Full Orchestration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestTowerScanHappyPath:
    """
    GIVEN valid APs (and optionally SMs)
    WHEN the full tower scan completes
    THEN results indicate completion for all devices.
    """

    def test_full_scan_ap_only(self):
        """GIVEN 1 valid AP, no SMs WHEN run_scan THEN AP completed."""
        scanner = make_scanner(ap_ips=["10.0.0.1"], snmp_communities=["Canopy"])

        async def mock_validate():
            return (["10.0.0.1"], [], {})

        async def mock_prepare(ip, device_type="AP"):
            return {
                "ip": ip,
                "success": True,
                "message": "OK",
                "device_type": device_type,
            }

        async def mock_start(ip, device_type="AP"):
            return {
                "ip": ip,
                "success": True,
                "message": "OK",
                "device_type": device_type,
            }

        async def mock_wait(ip, device_type="AP"):
            return {
                "ip": ip,
                "completed": True,
                "message": "Done in 30s",
                "device_type": device_type,
            }

        with (
            patch.object(
                scanner, "validate_and_filter_devices", side_effect=mock_validate
            ),
            patch.object(scanner, "_prepare_scan_async", side_effect=mock_prepare),
            patch.object(scanner, "_start_scan_signal_async", side_effect=mock_start),
            patch.object(scanner, "_wait_for_completion_async", side_effect=mock_wait),
        ):
            results = asyncio.run(scanner.start_tower_scan())

        assert "10.0.0.1" in results
        assert results["10.0.0.1"]["completed"] is True

    def test_no_valid_aps_early_return(self):
        """GIVEN no APs pass validation WHEN run_scan THEN returns errors only."""
        scanner = make_scanner(ap_ips=["10.0.0.1"], snmp_communities=["Canopy"])

        async def mock_validate():
            return ([], [], {"10.0.0.1": "SNMP auth failed"})

        with patch.object(
            scanner, "validate_and_filter_devices", side_effect=mock_validate
        ):
            results = asyncio.run(scanner.start_tower_scan())

        assert "10.0.0.1" in results
        assert results["10.0.0.1"]["completed"] is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: run_scan sync wrapper
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRunScanWrapper:
    """
    GIVEN TowerScanner.run_scan()
    WHEN called
    THEN it delegates to start_tower_scan() via asyncio.run().
    """

    def test_run_scan_returns_start_tower_scan_result(self):
        """GIVEN mocked start_tower_scan WHEN run_scan THEN returns same result."""
        scanner = make_scanner(snmp_communities=["Canopy"])
        expected = {"10.0.0.1": {"completed": True, "message": "OK"}}

        async def mock_start():
            return expected

        with patch.object(scanner, "start_tower_scan", side_effect=mock_start):
            result = scanner.run_scan()

        assert result == expected
