"""
tests/test_scan_task_auto_apply.py — Unit tests for ScanTask._run_auto_apply() hook.

Spec: change-006 tasks Phase 5 task 5.5.

Tests the auto-apply hook in isolation by constructing a minimal ScanTask
with mocked storage_manager and patched FrequencyApplyManager.

Key invariants:
  1. _run_auto_apply never raises (safety contract).
  2. run_apply is only called for APs with viable best_combined_frequency.
  3. Non-viable APs are skipped silently.
  4. force=False is always passed (auto-apply never bypasses gate).
  5. applied_by='auto' is always passed.
  6. tower_id taken from config; fallback is ap_ip.
"""

import pytest
from unittest.mock import MagicMock, patch, call


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_scan_task(scan_id="SCAN-AUTO-001", config=None, snmp_communities=None):
    """
    Build a minimal ScanTask for testing _run_auto_apply() — NO real SNMP,
    NO real DB, NO real scan lifecycle. Just what the hook needs.
    """
    from app.scan_task import ScanTask

    task = object.__new__(ScanTask)  # bypass __init__

    task.scan_id = scan_id
    task.config = config or {}
    task.snmp_communities = snmp_communities or ["Canopy"]

    # Mock storage_manager with a db attribute
    task.storage_manager = MagicMock()
    task.storage_manager.db = MagicMock()

    # Minimal log callback — collect messages for inspection
    task._log_messages = []
    task.log = lambda msg, level="info": task._log_messages.append((level, msg))

    return task


def _viable_result(freq_mhz=5180.0, score=0.90):
    """Build a minimal viable AP analysis result."""
    return {
        "best_combined_frequency": {
            "is_viable": True,
            "combined_score": score,
            "frequency": freq_mhz,
        }
    }


def _non_viable_result(score=0.40):
    """Build a non-viable AP analysis result."""
    return {
        "best_combined_frequency": {
            "is_viable": False,
            "combined_score": score,
            "frequency": 5180.0,
        }
    }


def _no_best_result():
    """AP result without best_combined_frequency (AP-only mode)."""
    return {"best_frequency": {"Frecuencia Central (MHz)": 5180}}


MOCK_SUCCESS_RESULT = {
    "success": True,
    "apply_id": 42,
    "state": "completed",
    "freq_khz": 5180000,
    "errors": [],
    "sm_results": {},
    "ap_result": {"success": True, "error": None},
}

MOCK_FAIL_RESULT = {
    "success": False,
    "apply_id": 99,
    "state": "failed",
    "freq_khz": 5180000,
    "errors": ["AP 192.168.1.10: Timeout"],
    "sm_results": {},
    "ap_result": {"success": False, "error": "Timeout"},
}


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestRunAutoApply:
    """Tests for ScanTask._run_auto_apply()."""

    def test_calls_run_apply_for_viable_ap(self):
        """GIVEN 1 viable AP THEN run_apply called once with force=False, applied_by='auto'."""
        task = _make_scan_task()
        analysis = {"192.168.1.10": _viable_result()}

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            instance.run_apply.return_value = MOCK_SUCCESS_RESULT
            with patch("app.scan_task.TowerScanner"):
                task._run_auto_apply(analysis)

        instance.run_apply.assert_called_once()
        _, kwargs = instance.run_apply.call_args
        assert kwargs["force"] is False
        assert kwargs["applied_by"] == "auto"

    def test_skips_non_viable_ap(self):
        """GIVEN 1 non-viable AP THEN run_apply NOT called."""
        task = _make_scan_task()
        analysis = {"192.168.1.10": _non_viable_result()}

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            with patch("app.scan_task.TowerScanner"):
                task._run_auto_apply(analysis)

        instance.run_apply.assert_not_called()

    def test_skips_ap_with_low_score(self):
        """GIVEN score=0.50 (below 0.65 threshold) THEN run_apply NOT called."""
        task = _make_scan_task()
        analysis = {"192.168.1.10": _viable_result(score=0.50)}
        # Override: is_viable=True but score below threshold
        analysis["192.168.1.10"]["best_combined_frequency"]["combined_score"] = 0.50

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            with patch("app.scan_task.TowerScanner"):
                task._run_auto_apply(analysis)

        instance.run_apply.assert_not_called()

    def test_skips_ap_without_best_combined_frequency(self):
        """GIVEN AP-only mode result (no best_combined_frequency) THEN run_apply NOT called."""
        task = _make_scan_task()
        analysis = {"192.168.1.10": _no_best_result()}

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            with patch("app.scan_task.TowerScanner"):
                task._run_auto_apply(analysis)

        instance.run_apply.assert_not_called()

    def test_does_not_raise_when_run_apply_raises(self):
        """GIVEN run_apply raises RuntimeError THEN _run_auto_apply swallows it (safety contract)."""
        task = _make_scan_task()
        analysis = {"192.168.1.10": _viable_result()}

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            instance.run_apply.side_effect = RuntimeError("SNMP exploded")
            with patch("app.scan_task.TowerScanner"):
                # Must not raise — this is the safety contract
                task._run_auto_apply(analysis)

    def test_does_not_raise_when_scanner_init_fails(self):
        """GIVEN TowerScanner init raises THEN _run_auto_apply swallows it."""
        task = _make_scan_task()
        analysis = {"192.168.1.10": _viable_result()}

        with patch("app.scan_task.TowerScanner", side_effect=Exception("pysnmp missing")):
            # Must not raise
            task._run_auto_apply(analysis)

    def test_processes_multiple_viable_aps(self):
        """GIVEN 3 viable APs THEN run_apply called 3 times."""
        task = _make_scan_task()
        analysis = {
            "192.168.1.10": _viable_result(5180.0),
            "192.168.1.11": _viable_result(5200.0),
            "192.168.1.12": _viable_result(5220.0),
        }

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            instance.run_apply.return_value = MOCK_SUCCESS_RESULT
            with patch("app.scan_task.TowerScanner"):
                task._run_auto_apply(analysis)

        assert instance.run_apply.call_count == 3

    def test_uses_tower_id_from_config(self):
        """GIVEN config has tower_id='TORRE-01' THEN run_apply called with tower_id='TORRE-01'."""
        task = _make_scan_task(config={"tower_id": "TORRE-01"})
        analysis = {"192.168.1.10": _viable_result()}

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            instance.run_apply.return_value = MOCK_SUCCESS_RESULT
            with patch("app.scan_task.TowerScanner"):
                task._run_auto_apply(analysis)

        _, kwargs = instance.run_apply.call_args
        assert kwargs["tower_id"] == "TORRE-01"

    def test_fallback_tower_id_is_ap_ip(self):
        """GIVEN config has NO tower_id THEN run_apply called with tower_id == ap IP."""
        task = _make_scan_task(config={})
        analysis = {"192.168.1.10": _viable_result()}

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            instance.run_apply.return_value = MOCK_SUCCESS_RESULT
            with patch("app.scan_task.TowerScanner"):
                task._run_auto_apply(analysis)

        _, kwargs = instance.run_apply.call_args
        assert kwargs["tower_id"] == "192.168.1.10"

    def test_mixed_viable_and_non_viable_aps(self):
        """GIVEN 1 viable and 1 non-viable AP THEN run_apply called exactly once."""
        task = _make_scan_task()
        analysis = {
            "192.168.1.10": _viable_result(),
            "192.168.1.11": _non_viable_result(),
        }

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            instance.run_apply.return_value = MOCK_SUCCESS_RESULT
            with patch("app.scan_task.TowerScanner"):
                task._run_auto_apply(analysis)

        assert instance.run_apply.call_count == 1

    def test_failed_apply_does_not_stop_next_ap(self):
        """GIVEN first AP apply fails THEN second AP still gets run_apply called."""
        task = _make_scan_task()
        analysis = {
            "192.168.1.10": _viable_result(5180.0),
            "192.168.1.11": _viable_result(5200.0),
        }

        with patch("app.scan_task.FrequencyApplyManager") as MockFAM:
            instance = MockFAM.return_value
            # First call raises, second succeeds
            instance.run_apply.side_effect = [
                RuntimeError("AP1 dead"),
                MOCK_SUCCESS_RESULT,
            ]
            with patch("app.scan_task.TowerScanner"):
                task._run_auto_apply(analysis)

        # Both should have been attempted
        assert instance.run_apply.call_count == 2
