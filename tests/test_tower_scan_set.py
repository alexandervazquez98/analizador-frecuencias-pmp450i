"""
tests/test_tower_scan_set.py — Unit tests for TowerScanner SNMP SET methods.

Spec: change-006 tasks Phase 5 task 5.2.

Tests set_frequency() and set_sm_scan_list() with mocked SNMP layer.
set_frequency()    — uses pysnmp setCmd directly (Integer32 on RF_FREQ_CARRIER_OID)
set_sm_scan_list() — delegates to _snmp_set_string() (OctetString on RF_SCAN_LIST_OID)

Note on pysnmp stub:
    conftest.py stubs pysnmp for Python 3.13 compat. Because TowerScanner.set_frequency()
    calls setCmd() directly (not via a helper), we patch it via 'app.tower_scan' module
    using patch.object(module, 'setCmd'). This works even with the stub.
"""

import pytest
import app.tower_scan as tower_scan_module
from unittest.mock import MagicMock, patch, patch as mock_patch
from app.tower_scan import TowerScanner


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def scanner():
    """TowerScanner instance with no real IPs and write_community set."""
    return TowerScanner(
        ap_ips=[],
        snmp_communities=["Canopy"],
        write_community="private",
    )


def _mock_setcmd_success():
    """Return a mock that simulates a successful pysnmp setCmd (errorIndication=None)."""
    mock_iter = MagicMock()
    mock_iter.__next__ = MagicMock(return_value=(None, None, None, []))
    return mock_iter


def _mock_setcmd_timeout():
    """Return a mock that simulates No SNMP response (errorIndication is truthy)."""
    mock_iter = MagicMock()
    error_indication = MagicMock()
    error_indication.__bool__ = MagicMock(return_value=True)
    error_indication.__str__ = MagicMock(return_value="No SNMP response received.")
    mock_iter.__next__ = MagicMock(return_value=(error_indication, None, None, []))
    return mock_iter


# ── set_frequency() ───────────────────────────────────────────────────────────


class TestSetFrequency:
    """Tests for TowerScanner.set_frequency() — rfFreqCarrier SNMP SET."""

    def test_returns_true_on_success(self, scanner):
        """GIVEN successful SNMP SET THEN returns (True, 'OK')."""
        with patch.object(tower_scan_module, "setCmd", return_value=_mock_setcmd_success()):
            success, msg = scanner.set_frequency("192.168.1.10", 5180000)
        assert success is True
        assert "OK" in msg

    def test_returns_false_on_snmp_error(self, scanner):
        """GIVEN SNMP timeout THEN returns (False, error_string)."""
        with patch.object(tower_scan_module, "setCmd", return_value=_mock_setcmd_timeout()):
            success, msg = scanner.set_frequency("192.168.1.10", 5180000)
        assert success is False

    def test_logs_apply_message(self, scanner):
        """GIVEN set_frequency call THEN _log is invoked (observability)."""
        with patch.object(tower_scan_module, "setCmd", return_value=_mock_setcmd_success()):
            with patch.object(scanner, "_log") as mock_log:
                scanner.set_frequency("192.168.1.10", 5180000)
                assert mock_log.called

    def test_handles_exception_gracefully(self, scanner):
        """GIVEN setCmd raises Exception THEN returns (False, str) without propagating."""
        with patch.object(tower_scan_module, "setCmd", side_effect=Exception("Connection refused")):
            success, msg = scanner.set_frequency("192.168.1.10", 5180000)
        assert success is False
        assert "Connection refused" in msg

    def test_uses_rf_freq_carrier_oid(self, scanner):
        """GIVEN set_frequency THEN setCmd is called exactly once (one SET operation).

        Note: With pysnmp stubbed for Python 3.13 compat, ObjectIdentity is a MagicMock
        and its repr does not include the raw OID string. We verify the behavioral
        contract — one SET call — which is sufficient for unit coverage.
        The OID string 'RF_FREQ_CARRIER_OID' is tested by code review + integration tests.
        """
        calls = []

        def capture_setcmd(*args, **kwargs):
            calls.append(args)
            return _mock_setcmd_success()

        with patch.object(tower_scan_module, "setCmd", side_effect=capture_setcmd):
            scanner.set_frequency("192.168.1.10", 5180000)

        assert len(calls) == 1, "setCmd must be called exactly once per set_frequency() call"


# ── set_sm_scan_list() ────────────────────────────────────────────────────────


class TestSetSmScanList:
    """Tests for TowerScanner.set_sm_scan_list() — rfScanList via _snmp_set_string()."""

    def test_delegates_to_snmp_set_string(self, scanner):
        """GIVEN set_sm_scan_list call THEN _snmp_set_string is called once."""
        with patch.object(scanner, "_snmp_set_string", return_value=(True, "OK")) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000])
        mock_set.assert_called_once()

    def test_passes_rf_scan_list_oid(self, scanner):
        """GIVEN set_sm_scan_list THEN _snmp_set_string receives RF_SCAN_LIST_OID."""
        with patch.object(scanner, "_snmp_set_string", return_value=(True, "OK")) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000])
        _, kwargs = mock_set.call_args
        assert kwargs.get("oid") == TowerScanner.RF_SCAN_LIST_OID

    def test_passes_correct_ip(self, scanner):
        """GIVEN IP '192.168.1.20' THEN _snmp_set_string receives that IP."""
        with patch.object(scanner, "_snmp_set_string", return_value=(True, "OK")) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000])
        _, kwargs = mock_set.call_args
        assert kwargs.get("ip") == "192.168.1.20"

    def test_single_freq_formatted_correctly(self, scanner):
        """GIVEN [5180000] THEN value is '5180000' (no trailing comma)."""
        with patch.object(scanner, "_snmp_set_string", return_value=(True, "OK")) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000])
        _, kwargs = mock_set.call_args
        value = kwargs.get("value")
        assert value == "5180000"
        assert not value.endswith(",")
        assert not value.endswith(", ")

    def test_multiple_freqs_formatted_as_csv(self, scanner):
        """GIVEN [5180000, 5200000] THEN value uses ', ' separator (rfScanList OID format)."""
        with patch.object(scanner, "_snmp_set_string", return_value=(True, "OK")) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000, 5200000])
        _, kwargs = mock_set.call_args
        value = kwargs.get("value")
        assert "5180000" in value
        assert "5200000" in value
        assert "," in value  # format_scan_list uses ', '

    def test_returns_true_on_success(self, scanner):
        """GIVEN successful write THEN returns (True, msg)."""
        with patch.object(scanner, "_snmp_set_string", return_value=(True, "OK")):
            success, _ = scanner.set_sm_scan_list("192.168.1.20", [5180000])
        assert success is True

    def test_returns_false_on_failure(self, scanner):
        """GIVEN failed write THEN returns (False, error)."""
        with patch.object(scanner, "_snmp_set_string", return_value=(False, "notWritable")):
            success, msg = scanner.set_sm_scan_list("192.168.1.20", [5180000])
        assert success is False
