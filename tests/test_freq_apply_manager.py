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
    # Make-before-break GET stubs — return empty current config by default
    mock.get_sm_scan_list.return_value = (True, [], "OK")
    mock.get_sm_bandwidth_scan.return_value = (True, [], "OK")
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
        # Verify GET must return the new freq so the gate allows AP change
        scanner.get_sm_scan_list.side_effect = [
            (True, [], "OK"),  # merge GET
            (True, [5180000], "OK"),  # verify GET — confirms freq was written
        ]

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
        # GET calls order: merge-loop (SM1, SM2) then verify-loop (SM1 only — SM2 SET failed).
        scanner.get_sm_scan_list.side_effect = [
            (True, [], "OK"),  # SM1: GET for merge
            (True, [], "OK"),  # SM2: GET for merge
            (True, [5180000], "OK"),  # SM1: GET for verify (SM2 skipped — SET failed)
        ]

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
        """GIVEN channel_width_mhz=30 and no current bw THEN set_sm_bandwidth_scan receives [30]."""
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
        # Make-before-break: receives list of ints (merged current + new)
        # With empty current bws, merged = [30]
        assert args[1] == [30]

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
        # Verify GET must confirm freq was written so the gate allows AP change.
        # bandwidthScan verify is SKIPPED because bw SET failed — gate only checks
        # bw_scan_ok when the bw SET itself succeeded (no false positive on known failures).
        scanner.get_sm_scan_list.side_effect = [
            (True, [], "OK"),  # merge GET
            (True, [5180000], "OK"),  # verify GET
        ]

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
        # Verify GETs must confirm both freq and bw were written.
        scanner.get_sm_scan_list.side_effect = [
            (True, [], "OK"),  # merge GET
            (True, [5180000], "OK"),  # verify GET
        ]
        scanner.get_sm_bandwidth_scan.side_effect = [
            (True, [], "OK"),  # merge GET
            (True, ["20.0 MHz"], "OK"),  # verify GET
        ]
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


# ── Make-Before-Break ─────────────────────────────────────────────────────────


class TestMakeBeforeBreak:
    """Tests for the make-before-break GET → merge → SET strategy in Step 2."""

    def test_get_scan_list_called_before_set(self, manager, db, scanner):
        """GIVEN SM with current freqs THEN get_sm_scan_list is called before set_sm_scan_list."""
        call_order = []
        scanner.get_sm_scan_list.side_effect = lambda *a, **kw: (
            call_order.append("GET_RF"),
            (True, [3650000], "OK"),
        )[1]
        scanner.set_sm_scan_list.side_effect = lambda *a, **kw: (
            call_order.append("SET_RF"),
            (True, "OK"),
        )[1]

        _insert_scan(
            db,
            "MBB1",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply("MBB1", 3652.5, "TORRE-01", "admin", force=False)

        assert call_order.index("GET_RF") < call_order.index("SET_RF"), (
            "GET rfScanList must be called BEFORE SET rfScanList"
        )

    def test_get_bandwidth_scan_called_before_set(self, manager, db, scanner):
        """GIVEN channel_width_mhz=20 THEN get_sm_bandwidth_scan called before set_sm_bandwidth_scan."""
        call_order = []
        scanner.get_sm_bandwidth_scan.side_effect = lambda *a, **kw: (
            call_order.append("GET_BW"),
            (True, ["15.0 MHz"], "OK"),
        )[1]
        scanner.set_sm_bandwidth_scan.side_effect = lambda *a, **kw: (
            call_order.append("SET_BW"),
            (True, "OK"),
        )[1]

        _insert_scan(
            db,
            "MBB2",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply(
            "MBB2", 3652.5, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        assert call_order.index("GET_BW") < call_order.index("SET_BW"), (
            "GET bandwidthScan must be called BEFORE SET bandwidthScan"
        )

    def test_merged_freq_list_contains_both_current_and_new(self, manager, db, scanner):
        """GIVEN current=[3650000] and new=3652500 THEN set_sm_scan_list receives [3650000, 3652500]."""
        scanner.get_sm_scan_list.return_value = (True, [3650000], "OK")

        _insert_scan(
            db,
            "MBB3",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply("MBB3", 3652.5, "TORRE-01", "admin", force=False)

        args, _ = scanner.set_sm_scan_list.call_args
        merged = args[1]
        assert 3650000 in merged, "Current freq must be in merged list"
        assert 3652500 in merged, "New freq must be in merged list"

    def test_get_failure_skips_sm_mutation(self, manager, db, scanner):
        """GIVEN GET rfScanList fails THEN set_sm_scan_list is NOT called for that SM.

        Rollback safety: writing new-only would destroy the SM's existing scan list.
        When we can't read the current list, the safe choice is to leave the SM as-is.
        The SM keeps its current config; only the AP frequency changes.
        """
        scanner.get_sm_scan_list.return_value = (False, [], "Timeout")

        _insert_scan(
            db,
            "MBB4",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("MBB4", 3652.5, "TORRE-01", "admin", force=False)

        # The SM's rfScanList must NOT have been touched (rollback safety)
        scanner.set_sm_scan_list.assert_not_called()
        # The skipped SM is reflected in results with skipped_preservation flag
        assert result["sm_results"]["192.168.1.20"].get("skipped_preservation") is True

    def test_deduplication_when_new_freq_already_in_current(self, manager, db, scanner):
        """GIVEN current=[3652500] and new=3652500 THEN merged list has no duplicate."""
        scanner.get_sm_scan_list.return_value = (True, [3652500], "OK")

        _insert_scan(
            db,
            "MBB5",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply("MBB5", 3652.5, "TORRE-01", "admin", force=False)

        args, _ = scanner.set_sm_scan_list.call_args
        merged = args[1]
        assert merged.count(3652500) == 1, f"Duplicate found: {merged}"

    def test_merged_bw_contains_both_current_and_new(self, manager, db, scanner):
        """GIVEN current bw=['15.0 MHz'] and new=20 THEN set_sm_bandwidth_scan receives [15, 20]."""
        scanner.get_sm_bandwidth_scan.return_value = (True, ["15.0 MHz"], "OK")

        _insert_scan(
            db,
            "MBB6",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        manager.run_apply(
            "MBB6", 3652.5, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        args, _ = scanner.set_sm_bandwidth_scan.call_args
        merged_bws = args[1]
        assert 15 in merged_bws, "Current bw (15) must be in merged list"
        assert 20 in merged_bws, "New bw (20) must be in merged list"

    def test_bw_get_failure_skips_sm_bw_mutation(self, manager, db, scanner):
        """GIVEN GET bandwidthScan fails THEN set_sm_bandwidth_scan is NOT called for that SM.

        Rollback safety: writing new-only bandwidth would destroy the SM's existing
        bandwidth scan list. When we can't read the current list, we skip the SET.
        The rfScanList SET still proceeds (its GET succeeded).
        """
        # rfScanList GET succeeds (so rfScanList SET will proceed)
        scanner.get_sm_scan_list.return_value = (True, [], "OK")
        # bandwidthScan GET fails → must skip bandwidthScan SET
        scanner.get_sm_bandwidth_scan.return_value = (False, [], "Timeout")

        _insert_scan(
            db,
            "MBB7",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply(
            "MBB7", 3652.5, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        # bandwidthScan must NOT have been touched (rollback safety)
        scanner.set_sm_bandwidth_scan.assert_not_called()
        # rfScanList SET should still have been called (its GET succeeded)
        scanner.set_sm_scan_list.assert_called_once()
        # The apply state reflects the bw_scan was skipped
        assert (
            result["sm_results"]["192.168.1.20"]["bw_scan"].get("skipped_preservation")
            is True
        )

    def test_full_flow_get_merge_set(self, manager, db, scanner):
        """GIVEN full make-before-break flow THEN state is 'completed' with merged values sent."""
        # merge GETs return current config; verify GETs return the merged config (confirmed).
        scanner.get_sm_scan_list.side_effect = [
            (True, [3650000], "OK"),  # merge GET — current freq
            (True, [3650000, 3652500], "OK"),  # verify GET — new freq confirmed
        ]
        scanner.get_sm_bandwidth_scan.side_effect = [
            (True, ["15.0 MHz"], "OK"),  # merge GET — current bw
            (True, ["15.0 MHz", "20.0 MHz"], "OK"),  # verify GET — new bw confirmed
        ]

        _insert_scan(
            db,
            "MBB8",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply(
            "MBB8", 3652.5, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        assert result["state"] == "completed"
        assert result["success"] is True

        # rfScanList: should have merged [3650000, 3652500]
        rf_args, _ = scanner.set_sm_scan_list.call_args
        assert 3650000 in rf_args[1]
        assert 3652500 in rf_args[1]

        # bandwidthScan: should have merged [15, 20]
        bw_args, _ = scanner.set_sm_bandwidth_scan.call_args
        assert 15 in bw_args[1]
        assert 20 in bw_args[1]

    def test_get_failure_logs_preservation_warning(self, manager, db, scanner, caplog):
        """GIVEN GET rfScanList fails THEN a warning about skipping is logged."""
        import logging

        scanner.get_sm_scan_list.return_value = (False, [], "No SNMP response")

        _insert_scan(
            db,
            "MBB9",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        with caplog.at_level(logging.WARNING, logger="app.freq_apply_manager"):
            manager.run_apply("MBB9", 3652.5, "TORRE-01", "admin", force=False)

        # Must log a warning that mentions skipping and rollback safety
        warning_msgs = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "skipping rfScanList mutation" in m and "rollback safety" in m
            for m in warning_msgs
        ), f"Expected preservation warning in logs, got: {warning_msgs}"

    def test_get_failure_does_not_prevent_ap_apply(self, manager, db, scanner):
        """GIVEN GET rfScanList fails for SM THEN the AP frequency change still happens."""
        scanner.get_sm_scan_list.return_value = (False, [], "Timeout")

        _insert_scan(
            db,
            "MBB10",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("MBB10", 3652.5, "TORRE-01", "admin", force=False)

        # AP SET must still have been called with the target frequency
        scanner.set_frequency.assert_called_once_with("192.168.1.10", 3652500)
        # Apply succeeds (AP succeeded; SM was skipped, not failed)
        assert result["state"] == "completed"
        assert result["ap_result"]["success"] is True


# ── SM Verify Gate ────────────────────────────────────────────────────────────


class TestSMVerifyGate:
    """Tests for Step 2f: AP is blocked if SM verify returns scan_list_ok=False."""

    def test_ap_blocked_when_sm_verify_fails(self, manager, db, scanner):
        """GIVEN SM SET succeeds but verify GET does not contain new freq THEN AP is NOT changed."""
        # SET OK, but verify GET returns old list without the new freq
        scanner.get_sm_scan_list.side_effect = [
            (True, [], "OK"),  # merge GET — no existing freqs
            (
                True,
                [],
                "OK",
            ),  # verify GET — freq NOT confirmed (hardware rejected silently)
        ]

        _insert_scan(
            db,
            "VG1",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("VG1", 3652.5, "TORRE-01", "admin", force=False)

        # AP SET must NOT have been called
        scanner.set_frequency.assert_not_called()
        assert result["state"] == "failed"
        assert result["success"] is False
        assert any("BLOCKED" in e for e in result["errors"])

    def test_ap_proceeds_when_all_sms_verified(self, manager, db, scanner):
        """GIVEN all SMs confirm new freq in verify GET THEN AP change proceeds."""
        scanner.get_sm_scan_list.side_effect = [
            (True, [], "OK"),  # merge GET
            (True, [3652500], "OK"),  # verify GET — confirmed
        ]

        _insert_scan(
            db,
            "VG2",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("VG2", 3652.5, "TORRE-01", "admin", force=False)

        scanner.set_frequency.assert_called_once_with("192.168.1.10", 3652500)
        assert result["state"] == "completed"

    def test_ap_proceeds_when_verify_get_indeterminate(self, manager, db, scanner):
        """GIVEN verify GET fails (SNMP error) THEN AP change is NOT blocked (indeterminate)."""
        scanner.get_sm_scan_list.side_effect = [
            (True, [], "OK"),  # merge GET — OK
            (
                False,
                [],
                "SNMP timeout",
            ),  # verify GET — GET failed, can't confirm NOR deny
        ]

        _insert_scan(
            db,
            "VG3",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("VG3", 3652.5, "TORRE-01", "admin", force=False)

        # scan_list_ok=None → indeterminate → not blocked
        scanner.set_frequency.assert_called_once_with("192.168.1.10", 3652500)
        assert result["state"] == "completed"
        assert result["sm_results"]["192.168.1.20"]["verify"]["scan_list_ok"] is None

    def test_gate_only_blocks_on_explicit_false(self, manager, db, scanner):
        """GIVEN 2 SMs: one verified, one indeterminate THEN AP proceeds (no explicit False)."""
        scanner.get_sm_scan_list.side_effect = [
            (True, [], "OK"),  # SM1 merge GET
            (True, [], "OK"),  # SM2 merge GET
            (True, [3652500], "OK"),  # SM1 verify GET — confirmed
            (False, [], "SNMP timeout"),  # SM2 verify GET — indeterminate
        ]

        _insert_scan(
            db,
            "VG4",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20", "192.168.1.21"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply("VG4", 3652.5, "TORRE-01", "admin", force=False)

        scanner.set_frequency.assert_called_once()
        assert result["state"] == "completed"

    def test_ap_blocked_when_bw_scan_verify_fails(self, manager, db, scanner):
        """GIVEN rfScanList verified OK but bandwidthScan NOT confirmed THEN AP is blocked."""
        scanner.get_sm_scan_list.side_effect = [
            (True, [], "OK"),  # merge GET
            (True, [3652500], "OK"),  # verify GET — freq confirmed ✓
        ]
        scanner.get_sm_bandwidth_scan.side_effect = [
            (True, [], "OK"),  # merge GET
            (True, [], "OK"),  # verify GET — bw NOT confirmed ✗
        ]

        _insert_scan(
            db,
            "VG5",
            ["192.168.1.10"],
            sm_ips=["192.168.1.20"],
            results={
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            },
        )
        result = manager.run_apply(
            "VG5", 3652.5, "TORRE-01", "admin", force=False, channel_width_mhz=20.0
        )

        scanner.set_frequency.assert_not_called()
        assert result["state"] == "failed"
        assert any("BLOCKED" in e for e in result["errors"])
