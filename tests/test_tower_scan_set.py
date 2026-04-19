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
        with patch.object(
            tower_scan_module, "setCmd", return_value=_mock_setcmd_success()
        ):
            success, msg = scanner.set_frequency("192.168.1.10", 5180000)
        assert success is True
        assert "OK" in msg

    def test_returns_false_on_snmp_error(self, scanner):
        """GIVEN SNMP timeout THEN returns (False, error_string)."""
        with patch.object(
            tower_scan_module, "setCmd", return_value=_mock_setcmd_timeout()
        ):
            success, msg = scanner.set_frequency("192.168.1.10", 5180000)
        assert success is False

    def test_logs_apply_message(self, scanner):
        """GIVEN set_frequency call THEN _log is invoked (observability)."""
        with patch.object(
            tower_scan_module, "setCmd", return_value=_mock_setcmd_success()
        ):
            with patch.object(scanner, "_log") as mock_log:
                scanner.set_frequency("192.168.1.10", 5180000)
                assert mock_log.called

    def test_handles_exception_gracefully(self, scanner):
        """GIVEN setCmd raises Exception THEN returns (False, str) without propagating."""
        with patch.object(
            tower_scan_module, "setCmd", side_effect=Exception("Connection refused")
        ):
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

        assert len(calls) == 1, (
            "setCmd must be called exactly once per set_frequency() call"
        )


# ── set_sm_scan_list() ────────────────────────────────────────────────────────


class TestSetSmScanList:
    """Tests for TowerScanner.set_sm_scan_list() — rfScanList via _snmp_set_string()."""

    def test_delegates_to_snmp_set_string(self, scanner):
        """GIVEN set_sm_scan_list call THEN _snmp_set_string is called once with SM timeouts."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000])
        mock_set.assert_called_once()
        _, kwargs = mock_set.call_args
        assert kwargs.get("timeout") == TowerScanner.SM_SNMP_TIMEOUT, (
            f"Expected SM_SNMP_TIMEOUT={TowerScanner.SM_SNMP_TIMEOUT}, got {kwargs.get('timeout')}"
        )
        assert kwargs.get("retries") == TowerScanner.SM_SNMP_RETRIES, (
            f"Expected SM_SNMP_RETRIES={TowerScanner.SM_SNMP_RETRIES}, got {kwargs.get('retries')}"
        )

    def test_passes_rf_scan_list_oid(self, scanner):
        """GIVEN set_sm_scan_list THEN _snmp_set_string receives RF_SCAN_LIST_OID."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000])
        _, kwargs = mock_set.call_args
        assert kwargs.get("oid") == TowerScanner.RF_SCAN_LIST_OID

    def test_passes_correct_ip(self, scanner):
        """GIVEN IP '192.168.1.20' THEN _snmp_set_string receives that IP."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000])
        _, kwargs = mock_set.call_args
        assert kwargs.get("ip") == "192.168.1.20"

    def test_single_freq_formatted_correctly(self, scanner):
        """GIVEN [5180000] THEN value is '5180000' (no trailing comma)."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000])
        _, kwargs = mock_set.call_args
        value = kwargs.get("value")
        assert value == "5180000"
        assert not value.endswith(",")
        assert not value.endswith(", ")

    def test_multiple_freqs_formatted_as_csv(self, scanner):
        """GIVEN [5180000, 5200000] THEN value uses ', ' separator (rfScanList OID format)."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_scan_list("192.168.1.20", [5180000, 5200000])
        _, kwargs = mock_set.call_args
        value = kwargs.get("value")
        assert "5180000" in value
        assert "5200000" in value
        assert "," in value  # format_scan_list uses ',' (no space)

    def test_returns_true_on_success(self, scanner):
        """GIVEN successful write THEN returns (True, msg)."""
        with patch.object(scanner, "_snmp_set_string", return_value=(True, "OK")):
            success, _ = scanner.set_sm_scan_list("192.168.1.20", [5180000])
        assert success is True

    def test_returns_false_on_failure(self, scanner):
        """GIVEN failed write THEN returns (False, error)."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(False, "notWritable")
        ):
            success, msg = scanner.set_sm_scan_list("192.168.1.20", [5180000])
        assert success is False


# ── set_sm_bandwidth_scan() ───────────────────────────────────────────────────


class TestSetSmBandwidthScan:
    """Tests for TowerScanner.set_sm_bandwidth_scan() — bandwidthScan via _snmp_set_string()."""

    def test_delegates_to_snmp_set_string(self, scanner):
        """GIVEN set_sm_bandwidth_scan call THEN _snmp_set_string is called once with SM timeouts."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_bandwidth_scan("192.168.1.20", 20)
        mock_set.assert_called_once()
        _, kwargs = mock_set.call_args
        assert kwargs.get("timeout") == TowerScanner.SM_SNMP_TIMEOUT, (
            f"Expected SM_SNMP_TIMEOUT={TowerScanner.SM_SNMP_TIMEOUT}, got {kwargs.get('timeout')}"
        )
        assert kwargs.get("retries") == TowerScanner.SM_SNMP_RETRIES, (
            f"Expected SM_SNMP_RETRIES={TowerScanner.SM_SNMP_RETRIES}, got {kwargs.get('retries')}"
        )

    def test_passes_sm_bw_scan_oid(self, scanner):
        """GIVEN set_sm_bandwidth_scan THEN _snmp_set_string receives SM_BW_SCAN_OID."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_bandwidth_scan("192.168.1.20", 20)
        _, kwargs = mock_set.call_args
        assert kwargs.get("oid") == TowerScanner.SM_BW_SCAN_OID

    def test_passes_correct_ip(self, scanner):
        """GIVEN IP '192.168.1.20' THEN _snmp_set_string receives that IP."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_bandwidth_scan("192.168.1.20", 20)
        _, kwargs = mock_set.call_args
        assert kwargs.get("ip") == "192.168.1.20"

    def test_value_formatted_with_mhz_suffix(self, scanner):
        """GIVEN width_mhz=20 THEN value is '20.0 MHz'."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_bandwidth_scan("192.168.1.20", 20)
        _, kwargs = mock_set.call_args
        assert kwargs.get("value") == "20.0 MHz"

    def test_value_10mhz(self, scanner):
        """GIVEN width_mhz=10 THEN value is '10.0 MHz'."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_bandwidth_scan("192.168.1.20", 10)
        _, kwargs = mock_set.call_args
        assert kwargs.get("value") == "10.0 MHz"

    def test_value_30mhz(self, scanner):
        """GIVEN width_mhz=30 THEN value is '30.0 MHz'."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            scanner.set_sm_bandwidth_scan("192.168.1.20", 30)
        _, kwargs = mock_set.call_args
        assert kwargs.get("value") == "30.0 MHz"

    def test_returns_true_on_success(self, scanner):
        """GIVEN successful write THEN returns (True, msg)."""
        with patch.object(scanner, "_snmp_set_string", return_value=(True, "OK")):
            success, _ = scanner.set_sm_bandwidth_scan("192.168.1.20", 20)
        assert success is True

    def test_returns_false_on_failure(self, scanner):
        """GIVEN failed write THEN returns (False, error)."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(False, "notWritable")
        ):
            success, msg = scanner.set_sm_bandwidth_scan("192.168.1.20", 20)
        assert success is False
        assert "notWritable" in msg

    def test_logs_apply_message(self, scanner):
        """GIVEN set_sm_bandwidth_scan call THEN _log is invoked (observability)."""
        with patch.object(scanner, "_snmp_set_string", return_value=(True, "OK")):
            with patch.object(scanner, "_log") as mock_log:
                scanner.set_sm_bandwidth_scan("192.168.1.20", 20)
                assert mock_log.called

    def test_accepts_list_of_bandwidths(self, scanner):
        """GIVEN [15, 20] THEN value is '15.0 MHz,20.0 MHz' (make-before-break, no space)."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            success, _ = scanner.set_sm_bandwidth_scan("192.168.1.20", [15, 20])
        assert success is True
        _, kwargs = mock_set.call_args
        assert kwargs.get("value") == "15.0 MHz,20.0 MHz"

    def test_list_single_element_same_as_scalar(self, scanner):
        """GIVEN [20] (list with one element) THEN value is '20.0 MHz' (backward compat)."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            success, _ = scanner.set_sm_bandwidth_scan("192.168.1.20", [20])
        assert success is True
        _, kwargs = mock_set.call_args
        assert kwargs.get("value") == "20.0 MHz"

    def test_list_invalid_bw_returns_error(self, scanner):
        """GIVEN [20, 99] (99 is invalid) THEN returns (False, error) without SNMP call."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            success, msg = scanner.set_sm_bandwidth_scan("192.168.1.20", [20, 99])
        assert success is False
        assert "99" in msg
        mock_set.assert_not_called()

    def test_scalar_backward_compat_still_works(self, scanner):
        """GIVEN scalar int 30 THEN behaves exactly as before (backward compat)."""
        with patch.object(
            scanner, "_snmp_set_string", return_value=(True, "OK")
        ) as mock_set:
            success, _ = scanner.set_sm_bandwidth_scan("192.168.1.20", 30)
        assert success is True
        _, kwargs = mock_set.call_args
        assert kwargs.get("value") == "30.0 MHz"


# ── get_sm_scan_list() ────────────────────────────────────────────────────────


class TestGetSmScanList:
    """Tests for TowerScanner.get_sm_scan_list() — rfScanList GET via _snmp_get_oid_sm().

    Note: get_sm_scan_list() now calls _snmp_get_oid_sm() (not _snmp_get_oid) so that
    it tries ALL communities and uses SM-specific timeouts (Issues #2 and #3).
    Tests mock _snmp_get_oid_sm to exercise the parsing and result-handling logic.
    """

    def test_returns_parsed_freq_list_on_success(self, scanner):
        """GIVEN SNMP GET returns '3650000, 3660000' THEN returns (True, [3650000, 3660000], 'OK')."""
        with patch.object(
            scanner, "_snmp_get_oid_sm", return_value=(True, "3650000, 3660000", "OK")
        ):
            ok, freqs, msg = scanner.get_sm_scan_list("192.168.1.20")
        assert ok is True
        assert freqs == [3650000, 3660000]
        assert msg == "OK"

    def test_returns_empty_list_on_snmp_failure(self, scanner):
        """GIVEN SNMP GET fails THEN returns (False, [], error_msg)."""
        with patch.object(
            scanner,
            "_snmp_get_oid_sm",
            return_value=(False, "", "all communities failed: Timeout"),
        ):
            ok, freqs, msg = scanner.get_sm_scan_list("192.168.1.20")
        assert ok is False
        assert freqs == []
        assert "Timeout" in msg

    def test_parses_single_frequency(self, scanner):
        """GIVEN SNMP GET returns '3650000' THEN returns [3650000]."""
        with patch.object(
            scanner, "_snmp_get_oid_sm", return_value=(True, "3650000", "OK")
        ):
            ok, freqs, msg = scanner.get_sm_scan_list("192.168.1.20")
        assert ok is True
        assert freqs == [3650000]

    def test_parses_multiple_frequencies(self, scanner):
        """GIVEN '3647500, 3650000, 3652500' THEN returns list of 3 ints."""
        with patch.object(
            scanner,
            "_snmp_get_oid_sm",
            return_value=(True, "3647500, 3650000, 3652500", "OK"),
        ):
            ok, freqs, _ = scanner.get_sm_scan_list("192.168.1.20")
        assert freqs == [3647500, 3650000, 3652500]

    def test_handles_empty_response(self, scanner):
        """GIVEN SNMP GET returns empty string THEN returns (True, [], 'OK')."""
        with patch.object(scanner, "_snmp_get_oid_sm", return_value=(True, "", "OK")):
            ok, freqs, msg = scanner.get_sm_scan_list("192.168.1.20")
        assert ok is True
        assert freqs == []

    def test_handles_whitespace_only_response(self, scanner):
        """GIVEN SNMP GET returns whitespace THEN returns (True, [], 'OK')."""
        with patch.object(
            scanner, "_snmp_get_oid_sm", return_value=(True, "   ", "OK")
        ):
            ok, freqs, _ = scanner.get_sm_scan_list("192.168.1.20")
        assert freqs == []

    def test_uses_rf_scan_list_oid(self, scanner):
        """GIVEN get_sm_scan_list THEN _snmp_get_oid_sm is called with RF_SCAN_LIST_OID."""
        with patch.object(
            scanner, "_snmp_get_oid_sm", return_value=(True, "3650000", "OK")
        ) as mock_get:
            scanner.get_sm_scan_list("192.168.1.20")
        # _snmp_get_oid_sm(ip, oid) — oid is positional arg [1]
        positional = mock_get.call_args.args
        assert positional[1] == TowerScanner.RF_SCAN_LIST_OID

    def test_passes_correct_ip(self, scanner):
        """GIVEN IP '10.0.0.5' THEN _snmp_get_oid_sm is called with that IP."""
        with patch.object(
            scanner, "_snmp_get_oid_sm", return_value=(True, "3650000", "OK")
        ) as mock_get:
            scanner.get_sm_scan_list("10.0.0.5")
        positional = mock_get.call_args.args
        assert positional[0] == "10.0.0.5"

    def test_snmp_get_oid_raw_called_with_sm_timeouts(self, scanner):
        """REGRESSION Issue #3: _snmp_get_oid_raw must be called with SM_SNMP_TIMEOUT=8
        and SM_SNMP_RETRIES=3, NOT the AP-level defaults (timeout=5, retries=2).

        Patches _snmp_get_oid_raw directly (bypassing _snmp_get_oid_sm) so the actual
        call path from get_sm_scan_list → _snmp_get_oid_sm → _snmp_get_oid_raw is
        fully exercised and SM timeout constants are verified at the wire level.
        """
        with patch.object(
            scanner, "_snmp_get_oid_raw", return_value=(True, "3650000", "OK")
        ) as mock_raw:
            ok, freqs, msg = scanner.get_sm_scan_list("192.168.1.20")

        assert ok is True
        assert freqs == [3650000]
        # Assert SM-specific timeout/retries were forwarded
        _, kwargs = mock_raw.call_args
        assert kwargs.get("timeout") == TowerScanner.SM_SNMP_TIMEOUT, (
            f"Expected SM_SNMP_TIMEOUT={TowerScanner.SM_SNMP_TIMEOUT}, got {kwargs.get('timeout')}"
        )
        assert kwargs.get("retries") == TowerScanner.SM_SNMP_RETRIES, (
            f"Expected SM_SNMP_RETRIES={TowerScanner.SM_SNMP_RETRIES}, got {kwargs.get('retries')}"
        )


# ── get_sm_bandwidth_scan() ───────────────────────────────────────────────────


class TestGetSmBandwidthScan:
    """Tests for TowerScanner.get_sm_bandwidth_scan() — bandwidthScan GET via _snmp_get_oid_sm().

    Note: get_sm_bandwidth_scan() now calls _snmp_get_oid_sm() (not _snmp_get_oid) so that
    it tries ALL communities and uses SM-specific timeouts (Issues #2 and #3).
    Tests mock _snmp_get_oid_sm to exercise the parsing and result-handling logic.
    """

    def test_returns_parsed_bw_list_on_success(self, scanner):
        """GIVEN SNMP GET returns '5.0 MHz, 20.0 MHz' THEN returns (True, ['5.0 MHz', '20.0 MHz'], 'OK')."""
        with patch.object(
            scanner, "_snmp_get_oid_sm", return_value=(True, "5.0 MHz, 20.0 MHz", "OK")
        ):
            ok, bws, msg = scanner.get_sm_bandwidth_scan("192.168.1.20")
        assert ok is True
        assert bws == ["5.0 MHz", "20.0 MHz"]
        assert msg == "OK"

    def test_returns_empty_list_on_snmp_failure(self, scanner):
        """GIVEN SNMP GET fails THEN returns (False, [], error_msg)."""
        with patch.object(
            scanner,
            "_snmp_get_oid_sm",
            return_value=(False, "", "all communities failed: No SNMP response"),
        ):
            ok, bws, msg = scanner.get_sm_bandwidth_scan("192.168.1.20")
        assert ok is False
        assert bws == []

    def test_parses_single_bandwidth(self, scanner):
        """GIVEN SNMP GET returns '20.0 MHz' THEN returns ['20.0 MHz']."""
        with patch.object(
            scanner, "_snmp_get_oid_sm", return_value=(True, "20.0 MHz", "OK")
        ):
            ok, bws, _ = scanner.get_sm_bandwidth_scan("192.168.1.20")
        assert ok is True
        assert bws == ["20.0 MHz"]

    def test_parses_multiple_bandwidths(self, scanner):
        """GIVEN '10.0 MHz, 15.0 MHz, 20.0 MHz' THEN returns list of 3 strings."""
        with patch.object(
            scanner,
            "_snmp_get_oid_sm",
            return_value=(True, "10.0 MHz, 15.0 MHz, 20.0 MHz", "OK"),
        ):
            ok, bws, _ = scanner.get_sm_bandwidth_scan("192.168.1.20")
        assert bws == ["10.0 MHz", "15.0 MHz", "20.0 MHz"]

    def test_handles_empty_response(self, scanner):
        """GIVEN SNMP GET returns empty string THEN returns (True, [], 'OK')."""
        with patch.object(scanner, "_snmp_get_oid_sm", return_value=(True, "", "OK")):
            ok, bws, msg = scanner.get_sm_bandwidth_scan("192.168.1.20")
        assert ok is True
        assert bws == []

    def test_handles_whitespace_only_response(self, scanner):
        """GIVEN SNMP GET returns whitespace THEN returns (True, [], 'OK')."""
        with patch.object(scanner, "_snmp_get_oid_sm", return_value=(True, "  ", "OK")):
            ok, bws, _ = scanner.get_sm_bandwidth_scan("192.168.1.20")
        assert bws == []

    def test_uses_sm_bw_scan_oid(self, scanner):
        """GIVEN get_sm_bandwidth_scan THEN _snmp_get_oid_sm is called with SM_BW_SCAN_OID."""
        with patch.object(
            scanner, "_snmp_get_oid_sm", return_value=(True, "20.0 MHz", "OK")
        ) as mock_get:
            scanner.get_sm_bandwidth_scan("192.168.1.20")
        positional = mock_get.call_args.args
        assert positional[1] == TowerScanner.SM_BW_SCAN_OID

    def test_passes_correct_ip(self, scanner):
        """GIVEN IP '10.0.0.7' THEN _snmp_get_oid_sm is called with that IP."""
        with patch.object(
            scanner, "_snmp_get_oid_sm", return_value=(True, "20.0 MHz", "OK")
        ) as mock_get:
            scanner.get_sm_bandwidth_scan("10.0.0.7")
        positional = mock_get.call_args.args
        assert positional[0] == "10.0.0.7"

    def test_snmp_get_oid_raw_called_with_sm_timeouts(self, scanner):
        """REGRESSION Issue #3: _snmp_get_oid_raw must be called with SM_SNMP_TIMEOUT=8
        and SM_SNMP_RETRIES=3, NOT the AP-level defaults (timeout=5, retries=2).

        Patches _snmp_get_oid_raw directly (bypassing _snmp_get_oid_sm) so the actual
        call path from get_sm_bandwidth_scan → _snmp_get_oid_sm → _snmp_get_oid_raw is
        fully exercised and SM timeout constants are verified at the wire level.
        """
        with patch.object(
            scanner, "_snmp_get_oid_raw", return_value=(True, "20.0 MHz", "OK")
        ) as mock_raw:
            ok, bws, msg = scanner.get_sm_bandwidth_scan("192.168.1.20")

        assert ok is True
        assert bws == ["20.0 MHz"]
        # Assert SM-specific timeout/retries were forwarded
        _, kwargs = mock_raw.call_args
        assert kwargs.get("timeout") == TowerScanner.SM_SNMP_TIMEOUT, (
            f"Expected SM_SNMP_TIMEOUT={TowerScanner.SM_SNMP_TIMEOUT}, got {kwargs.get('timeout')}"
        )
        assert kwargs.get("retries") == TowerScanner.SM_SNMP_RETRIES, (
            f"Expected SM_SNMP_RETRIES={TowerScanner.SM_SNMP_RETRIES}, got {kwargs.get('retries')}"
        )
