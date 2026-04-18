"""
tests/test_freq_apply_manager.py — Unit + integration tests for FrequencyApplyManager.

Spec: change-006 tasks Phase 5 task 5.3.

Tests cover:
  - Viability gate: raises ValueError when is_viable=False and force=False
  - Score gate: raises ValueError when combined_score < 0.65 and force=False
  - force=True: bypasses gate and proceeds
  - SM-first → AP-last order (state machine)
  - AP failure → final state 'failed'
  - Partial SM failure → final state 'completed' (AP OK)
  - No SMs: skips SM step, applies AP only
  - get_apply_history: returns correct rows ordered by date desc
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from app.db_manager import DatabaseManager
from app.freq_apply_manager import FrequencyApplyManager


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    """DatabaseManager with a fresh temp SQLite DB (includes frequency_applies table)."""
    return DatabaseManager(str(tmp_path / "test.db"))


@pytest.fixture
def scanner():
    """Mock TowerScanner with all SNMP methods stubbed to succeed."""
    mock = MagicMock()
    mock.set_sm_scan_list.return_value = (True, "OK")
    mock.set_sm_bandwidth_scan.return_value = (True, "OK")
    mock.set_frequency.return_value = (True, "OK")
    mock.set_channel_width.return_value = (True, "OK")
    mock.set_contention_slots.return_value = (True, "OK")
    mock.set_broadcast_retry.return_value = (True, "OK")
    mock.reboot_if_required.return_value = (True, "OK")
    mock._snmp_get.return_value = (True, 5180000, "OK")
    return mock


@pytest.fixture
def manager(db, scanner):
    """FrequencyApplyManager wired to the temp DB and mock scanner."""
    return FrequencyApplyManager(db_manager=db, tower_scanner=scanner)


def _insert_scan(db, scan_id, ap_ips, sm_ips=None, results=None, tower_id="TORRE-01"):
    """Helper: insert a minimal scan record into the DB for tests.
    Also ensures the tower_id exists in the towers table (FK constraint).
    """
    conn = db.get_connection()
    try:
        # Ensure user exists (FK for scans.user_id)
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, role) "
            "VALUES (1, 'admin', 'hash', 'admin')"
        )
        # Ensure tower exists (FK for frequency_applies.tower_id)
        conn.execute(
            "INSERT OR IGNORE INTO towers (tower_id, name, created_by) "
            "VALUES (?, ?, 1)",
            (tower_id, tower_id),
        )
        conn.execute(
            """INSERT OR IGNORE INTO scans
               (id, user_id, username, ticket_id, ap_ips, sm_ips, status, results)
               VALUES (?, 1, 'admin', 1, ?, ?, 'completed', ?)""",
            (
                scan_id,
                json.dumps(ap_ips),
                json.dumps(sm_ips or []),
                json.dumps(results or {}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ── Viability gate ────────────────────────────────────────────────────────────


class TestViabilityGate:
    """Tests for run_apply() viability gate logic."""

    def test_raises_when_not_viable(self, manager, db):
        """GIVEN is_viable=False and force=False THEN ValueError raised."""
        _insert_scan(
            db,
            "S1",
            ["192.168.1.10"],
            results={
                "best_combined_frequency": {"is_viable": False, "combined_score": 0.80}
            },
        )
        with pytest.raises(ValueError, match="not viable"):
            manager.run_apply("S1", 5180.0, "TORRE-01", "admin", force=False)

    def test_raises_when_score_below_threshold(self, manager, db):
        """GIVEN combined_score=0.50 and force=False THEN ValueError raised."""
        _insert_scan(
            db,
            "S2",
            ["192.168.1.10"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.50}
            },
        )
        with pytest.raises(ValueError, match="0.50"):
            manager.run_apply("S2", 5180.0, "TORRE-01", "admin", force=False)

    def test_force_bypasses_viability(self, manager, db, scanner):
        """GIVEN is_viable=False and force=True THEN apply proceeds without error."""
        _insert_scan(
            db,
            "S3",
            ["192.168.1.10"],
            results={
                "best_combined_frequency": {"is_viable": False, "combined_score": 0.30}
            },
        )
        result = manager.run_apply("S3", 5180.0, "TORRE-01", "admin", force=True)
        assert result["state"] == "completed"

    def test_raises_when_scan_not_found(self, manager):
        """GIVEN non-existent scan_id THEN ValueError raised."""
        with pytest.raises(ValueError, match="not found"):
            manager.run_apply("nonexistent-scan", 5180.0, "T1", "admin", force=True)

    def test_passes_when_viable_and_good_score(self, manager, db, scanner):
        """GIVEN is_viable=True and combined_score=0.80 THEN apply proceeds."""
        _insert_scan(
            db,
            "S4",
            ["192.168.1.10"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.80}
            },
        )
        result = manager.run_apply("S4", 5180.0, "TORRE-01", "admin", force=False)
        assert result["state"] == "completed"


# ── State machine ─────────────────────────────────────────────────────────────


class TestStateMachine:
    """Tests for the SM-first → AP-last apply sequence."""

    def test_sm_first_ap_last_order(self, manager, db, scanner):
        """GIVEN scan with 1 SM THEN set_sm_scan_list called BEFORE set_frequency."""
        call_order = []
        scanner.set_sm_scan_list.side_effect = lambda *a, **kw: (
            call_order.append("SM"),
            (True, "OK"),
        )[1]
        scanner.set_frequency.side_effect = lambda *a, **kw: (
            call_order.append("AP"),
            (True, "OK"),
        )[1]

        _insert_scan(
            db,
            "S5",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply("S5", 5180.0, "TORRE-01", "admin", force=False)

        assert call_order == ["SM", "AP"], f"Expected SM then AP, got: {call_order}"

    def test_ap_failure_yields_failed_state(self, manager, db, scanner):
        """GIVEN AP SET fails THEN final state is 'failed'."""
        scanner.set_frequency.return_value = (False, "Timeout")

        _insert_scan(
            db,
            "S6",
            ["192.168.1.10"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("S6", 5180.0, "TORRE-01", "admin", force=False)
        assert result["state"] == "failed"
        assert not result["success"]

    def test_partial_sm_failure_ap_ok_yields_completed(self, manager, db, scanner):
        """GIVEN 1 SM fails and AP succeeds THEN state is 'completed' but errors not empty."""
        scanner.set_sm_scan_list.side_effect = [
            (True, "OK"),  # SM1 OK
            (False, "Timeout"),  # SM2 fails
        ]
        scanner.set_frequency.return_value = (True, "OK")

        _insert_scan(
            db,
            "S7",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20", "192.168.1.21"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("S7", 5180.0, "TORRE-01", "admin", force=False)
        assert result["state"] == "completed"
        assert len(result["errors"]) == 1
        assert "192.168.1.21" in result["errors"][0]

    def test_no_sm_skips_sm_step(self, manager, db, scanner):
        """GIVEN scan with no SMs THEN set_sm_scan_list not called."""
        _insert_scan(
            db,
            "S8",
            ["192.168.1.10"],
            sm_ips=[],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply("S8", 5180.0, "TORRE-01", "admin", force=False)
        scanner.set_sm_scan_list.assert_not_called()

    def test_result_dict_contains_required_keys(self, manager, db, scanner):
        """GIVEN successful apply THEN result has all expected keys."""
        _insert_scan(
            db,
            "S9",
            ["192.168.1.10"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("S9", 5180.0, "TORRE-01", "admin", force=False)
        for key in (
            "success",
            "apply_id",
            "state",
            "freq_khz",
            "sm_results",
            "ap_result",
            "errors",
        ):
            assert key in result, f"Missing key: {key}"

    def test_freq_khz_is_conversion_of_mhz(self, manager, db, scanner):
        """GIVEN freq_mhz=5180.0 THEN result.freq_khz == 5180000."""
        _insert_scan(
            db,
            "S10",
            ["192.168.1.10"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("S10", 5180.0, "TORRE-01", "admin", force=False)
        assert result["freq_khz"] == 5180000

    def test_apply_id_is_persisted_in_db(self, manager, db, scanner):
        """GIVEN successful apply THEN apply record exists in frequency_applies table."""
        _insert_scan(
            db,
            "S11",
            ["192.168.1.10"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("S11", 5180.0, "TORRE-01", "admin", force=False)
        apply_id = result["apply_id"]

        conn = db.get_connection()
        row = conn.execute(
            "SELECT state FROM frequency_applies WHERE id=?", (apply_id,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["state"] == "completed"


# ── get_apply_history() ───────────────────────────────────────────────────────


class TestGetApplyHistory:
    """Tests for FrequencyApplyManager.get_apply_history()."""

    def test_returns_empty_list_when_no_applies(self, manager):
        """GIVEN no applies for tower THEN returns []."""
        result = manager.get_apply_history("NONTEXISTENT-TORRE")
        assert result == []

    def test_returns_applies_for_tower(self, manager, db, scanner):
        """GIVEN 2 applies for TORRE-01 THEN get_apply_history returns 2 rows."""
        _insert_scan(
            db,
            "HA1",
            ["192.168.1.10"],
            sm_ips=[],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        _insert_scan(
            db,
            "HA2",
            ["192.168.1.10"],
            sm_ips=[],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply("HA1", 5180.0, "TORRE-01", "admin", force=False)
        manager.run_apply("HA2", 5200.0, "TORRE-01", "admin", force=False)

        history = manager.get_apply_history("TORRE-01")
        assert len(history) == 2

    def test_history_ordered_by_date_desc(self, manager, db, scanner):
        """GIVEN 2 applies THEN both records are returned (ORDER BY created_at DESC)."""
        _insert_scan(
            db,
            "HB1",
            ["192.168.1.10"],
            sm_ips=[],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        _insert_scan(
            db,
            "HB2",
            ["192.168.1.10"],
            sm_ips=[],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply("HB1", 5180.0, "TORRE-01", "admin", force=False)
        manager.run_apply("HB2", 5200.0, "TORRE-01", "admin", force=False)

        history = manager.get_apply_history("TORRE-01")
        # Verify both records are returned (ORDER BY created_at DESC is DB-level)
        assert len(history) == 2
        freq_set = {h["freq_khz"] for h in history}
        assert 5180000 in freq_set
        assert 5200000 in freq_set

    def test_history_does_not_mix_towers(self, manager, db, scanner):
        """GIVEN applies for TORRE-01 and TORRE-02 THEN histories are separate."""
        _insert_scan(
            db,
            "HC1",
            ["192.168.1.10"],
            sm_ips=[],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
            tower_id="TORRE-01",
        )
        _insert_scan(
            db,
            "HC2",
            ["192.168.1.10"],
            sm_ips=[],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
            tower_id="TORRE-02",
        )
        manager.run_apply("HC1", 5180.0, "TORRE-01", "admin", force=False)
        manager.run_apply("HC2", 5200.0, "TORRE-02", "admin", force=False)

        h1 = manager.get_apply_history("TORRE-01")
        h2 = manager.get_apply_history("TORRE-02")
        assert len(h1) == 1
        assert len(h2) == 1
        assert h1[0]["freq_khz"] == 5180000
        assert h2[0]["freq_khz"] == 5200000

    def test_history_records_have_freq_mhz(self, manager, db, scanner):
        """GIVEN apply at 5180.0 MHz THEN history record has freq_mhz=5180.0."""
        _insert_scan(
            db,
            "HD1",
            ["192.168.1.10"],
            sm_ips=[],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply("HD1", 5180.0, "TORRE-01", "admin", force=False)
        history = manager.get_apply_history("TORRE-01")
        assert history[0]["freq_mhz"] == pytest.approx(5180.0)


# ── bandwidthScan apply ───────────────────────────────────────────────────────


class TestBandwidthApply:
    """Tests for bandwidthScan SET on SMs when channel_width_mhz is provided."""

    def test_bw_scan_called_before_rf_scan_list(self, manager, db, scanner):
        """GIVEN channel_width_mhz=20 THEN bandwidthScan SET happens before rfScanList per SM."""
        call_order = []
        scanner.set_sm_bandwidth_scan.side_effect = lambda *a, **kw: (
            call_order.append("bw"),
            (True, "OK"),
        )[1]
        scanner.set_sm_scan_list.side_effect = lambda *a, **kw: (
            call_order.append("rf"),
            (True, "OK"),
        )[1]

        _insert_scan(
            db,
            "BW1",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply(
            "BW1", 5180.0, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        assert call_order == ["bw", "rf"], f"Expected bw then rf, got: {call_order}"

    def test_bw_scan_passes_width_mhz(self, manager, db, scanner):
        """GIVEN channel_width_mhz=30 THEN set_sm_bandwidth_scan receives 30."""
        _insert_scan(
            db,
            "BW2",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply(
            "BW2", 5180.0, "TORRE-01", "admin", force=False, channel_width_mhz=30.0
        )

        args, _ = scanner.set_sm_bandwidth_scan.call_args
        assert args[1] == 30.0

    def test_bw_scan_not_called_when_no_channel_width(self, manager, db, scanner):
        """GIVEN channel_width_mhz=None THEN set_sm_bandwidth_scan is NOT called."""
        _insert_scan(
            db,
            "BW3",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply(
            "BW3", 5180.0, "TORRE-01", "admin", force=False, channel_width_mhz=None
        )

        scanner.set_sm_bandwidth_scan.assert_not_called()

    def test_bw_scan_not_called_when_no_sms(self, manager, db, scanner):
        """GIVEN no SMs THEN set_sm_bandwidth_scan is NOT called even with channel_width."""
        _insert_scan(
            db,
            "BW4",
            ["192.168.1.10"],
            sm_ips=[],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply(
            "BW4", 5180.0, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        scanner.set_sm_bandwidth_scan.assert_not_called()

    def test_bw_scan_failure_is_non_fatal(self, manager, db, scanner):
        """GIVEN bandwidthScan fails but rfScanList succeeds THEN state is 'completed'."""
        scanner.set_sm_bandwidth_scan.return_value = (False, "notWritable")
        scanner.set_sm_scan_list.return_value = (True, "OK")

        _insert_scan(
            db,
            "BW5",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply(
            "BW5", 5180.0, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        assert result["state"] == "completed"
        assert result["success"] is True

    def test_bw_scan_failure_adds_to_errors(self, manager, db, scanner):
        """GIVEN bandwidthScan fails THEN result.errors contains the SM IP."""
        scanner.set_sm_bandwidth_scan.return_value = (False, "timeout")

        _insert_scan(
            db,
            "BW6",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply(
            "BW6", 5180.0, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        assert any("192.168.1.20" in e for e in result["errors"])

    def test_bw_scan_called_for_each_sm(self, manager, db, scanner):
        """GIVEN 2 SMs THEN set_sm_bandwidth_scan is called twice."""
        _insert_scan(
            db,
            "BW7",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20", "192.168.1.21"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply(
            "BW7", 5180.0, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        assert scanner.set_sm_bandwidth_scan.call_count == 2

    def test_apply_succeeds_with_channel_width(self, manager, db, scanner):
        """GIVEN full apply with channel_width_mhz=20 THEN state is 'completed'."""
        _insert_scan(
            db,
            "BW8",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply(
            "BW8", 5180.0, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        assert result["state"] == "completed"
        assert result["success"] is True
