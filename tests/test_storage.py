"""
tests/test_storage.py -- Tests for ScanStorageManager (SQLite-backed scan persistence).

Covers:
  - ScanStorageManager unit tests (save, get, update, complete, fail, delete, list)
  - HTTP endpoint tests verifying scan_routes uses SQLite storage (no JSON files)
"""

import pytest
from unittest.mock import MagicMock

from app.scan_storage_manager import ScanStorageManager
from app.db_manager import DatabaseManager
from app.audit_manager import AuditManager


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Unit-test fixtures
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture
def db(tmp_path):
    """DatabaseManager backed by a temporary SQLite file."""
    dm = DatabaseManager(str(tmp_path / "test.db"))
    return dm


@pytest.fixture
def storage(db):
    """ScanStorageManager using the test DatabaseManager."""
    return ScanStorageManager(db)


def _minimal_scan(scan_id="scan-001"):
    """Return the minimum required fields to save a scan."""
    return {
        "username": "testuser",
        "ticket_id": 42,
        "scan_type": "AP_ONLY",
        "ap_ips": ["10.0.0.1"],
        "status": "initializing",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestSaveScan
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSaveScan:
    """save_scan() inserts and retrieves correctly."""

    def test_save_and_get_basic(self, storage):
        """GIVEN minimal data WHEN save_scan THEN get_scan returns a record."""
        storage.save_scan("scan-001", _minimal_scan())
        row = storage.get_scan("scan-001")
        assert row is not None
        assert row["id"] == "scan-001"
        assert row["username"] == "testuser"
        assert row["status"] == "initializing"

    def test_save_with_json_fields(self, storage):
        """GIVEN ap_ips as list and config as dict WHEN save THEN stored and deserialized."""
        data = _minimal_scan()
        data["ap_ips"] = ["10.0.0.1", "10.0.0.2"]
        data["config"] = {"target_rx_level": -52, "channel_width": 20}
        storage.save_scan("scan-002", data)

        row = storage.get_scan("scan-002")
        assert row["ap_ips"] == ["10.0.0.1", "10.0.0.2"]
        assert row["config"]["target_rx_level"] == -52

    def test_save_upsert_updates_existing(self, storage):
        """GIVEN existing scan WHEN save_scan again THEN record is updated."""
        storage.save_scan("scan-003", _minimal_scan())
        updated = _minimal_scan()
        updated["status"] = "scanning"
        storage.save_scan("scan-003", updated)

        row = storage.get_scan("scan-003")
        assert row["status"] == "scanning"

    def test_save_minimal_fields(self, storage):
        """GIVEN only required fields WHEN save_scan THEN succeeds without error."""
        storage.save_scan("scan-minimal", _minimal_scan("scan-minimal"))
        row = storage.get_scan("scan-minimal")
        assert row is not None
        assert row["id"] == "scan-minimal"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestGetScan
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetScan:
    """get_scan() returns correct data or None."""

    def test_returns_none_for_unknown_id(self, storage):
        """GIVEN no scan stored WHEN get_scan THEN returns None."""
        assert storage.get_scan("does-not-exist") is None

    def test_deserializes_ap_ips_as_list(self, storage):
        """GIVEN ap_ips stored as JSON list WHEN get_scan THEN returns Python list."""
        data = _minimal_scan()
        data["ap_ips"] = ["192.168.1.1", "192.168.1.2"]
        storage.save_scan("scan-d1", data)
        row = storage.get_scan("scan-d1")
        assert isinstance(row["ap_ips"], list)
        assert row["ap_ips"] == ["192.168.1.1", "192.168.1.2"]

    def test_deserializes_config_as_dict(self, storage):
        """GIVEN config stored as JSON dict WHEN get_scan THEN returns Python dict."""
        data = _minimal_scan()
        data["config"] = {"key": "value", "num": 123}
        storage.save_scan("scan-d2", data)
        row = storage.get_scan("scan-d2")
        assert isinstance(row["config"], dict)
        assert row["config"]["num"] == 123

    def test_deserializes_results_as_dict(self, storage):
        """GIVEN results stored as JSON dict WHEN get_scan THEN returns Python dict."""
        data = _minimal_scan()
        data["results"] = {"analysis_results": {"10.0.0.1": {"mode": "AP_ONLY"}}}
        storage.save_scan("scan-d3", data)
        row = storage.get_scan("scan-d3")
        assert isinstance(row["results"], dict)
        assert "10.0.0.1" in row["results"]["analysis_results"]

    def test_handles_null_optional_fields(self, storage):
        """GIVEN sm_ips/config/results are None WHEN get_scan THEN returns None for those fields."""
        data = _minimal_scan()
        # sm_ips, config, results not set → should be None
        storage.save_scan("scan-d4", data)
        row = storage.get_scan("scan-d4")
        assert row["sm_ips"] is None
        assert row["config"] is None
        assert row["results"] is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestUpdateScanStatus
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestUpdateScanStatus:
    """update_scan_status() modifies only the status/progress/error columns."""

    def test_updates_status(self, storage):
        """GIVEN saved scan WHEN update_scan_status THEN status changes."""
        storage.save_scan("s1", _minimal_scan())
        storage.update_scan_status("s1", "scanning")
        row = storage.get_scan("s1")
        assert row["status"] == "scanning"

    def test_updates_error(self, storage):
        """GIVEN saved scan WHEN update_scan_status with error THEN error stored."""
        storage.save_scan("s2", _minimal_scan())
        storage.update_scan_status("s2", "failed", error="Connection timeout")
        row = storage.get_scan("s2")
        assert row["error"] == "Connection timeout"

    def test_does_not_fail_on_unknown_id(self, storage):
        """GIVEN no scan with given id WHEN update_scan_status THEN no exception raised."""
        # Should be a no-op without raising
        storage.update_scan_status("ghost-scan", "scanning")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestCompleteScan
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCompleteScan:
    """complete_scan() marks the scan completed and stores results."""

    def test_sets_status_completed(self, storage):
        """GIVEN active scan WHEN complete_scan THEN status = 'completed'."""
        storage.save_scan("c1", _minimal_scan())
        storage.complete_scan("c1", {"analysis_results": {}})
        row = storage.get_scan("c1")
        assert row["status"] == "completed"

    def test_saves_results(self, storage):
        """GIVEN active scan WHEN complete_scan THEN results persisted."""
        results = {"analysis_results": {"10.0.0.1": {"mode": "AP_ONLY"}}}
        storage.save_scan("c2", _minimal_scan())
        storage.complete_scan("c2", results)
        row = storage.get_scan("c2")
        assert isinstance(row["results"], dict)
        assert "10.0.0.1" in row["results"]["analysis_results"]

    def test_saves_duration(self, storage):
        """GIVEN active scan WHEN complete_scan with duration THEN duration_seconds stored."""
        storage.save_scan("c3", _minimal_scan())
        storage.complete_scan("c3", {}, duration_seconds=42.5)
        row = storage.get_scan("c3")
        assert abs(row["duration_seconds"] - 42.5) < 0.01


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestFailScan
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFailScan:
    """fail_scan() marks the scan failed and stores the error."""

    def test_sets_status_failed(self, storage):
        """GIVEN active scan WHEN fail_scan THEN status = 'failed'."""
        storage.save_scan("f1", _minimal_scan())
        storage.fail_scan("f1", "SNMP timeout")
        row = storage.get_scan("f1")
        assert row["status"] == "failed"

    def test_saves_error_message(self, storage):
        """GIVEN active scan WHEN fail_scan THEN error message persisted."""
        storage.save_scan("f2", _minimal_scan())
        storage.fail_scan("f2", "Device unreachable")
        row = storage.get_scan("f2")
        assert row["error"] == "Device unreachable"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestGetAllScans
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetAllScans:
    """get_all_scans() returns scans with pagination, sorted by started_at DESC."""

    def test_empty_list(self, storage):
        """GIVEN no scans WHEN get_all_scans THEN returns empty list."""
        assert storage.get_all_scans() == []

    def test_returns_multiple_scans(self, storage):
        """GIVEN multiple saved scans WHEN get_all_scans THEN all returned."""
        storage.save_scan("ga1", _minimal_scan())
        storage.save_scan("ga2", _minimal_scan())
        storage.save_scan("ga3", _minimal_scan())
        rows = storage.get_all_scans()
        assert len(rows) == 3

    def test_ordered_by_started_at_desc(self, storage):
        """GIVEN scans with different started_at WHEN get_all_scans THEN most recent first."""
        storage.save_scan(
            "ord-a", {**_minimal_scan(), "started_at": "2026-01-01 08:00:00"}
        )
        storage.save_scan(
            "ord-b", {**_minimal_scan(), "started_at": "2026-01-03 12:00:00"}
        )
        storage.save_scan(
            "ord-c", {**_minimal_scan(), "started_at": "2026-01-02 10:00:00"}
        )
        rows = storage.get_all_scans()
        ids = [r["id"] for r in rows]
        assert ids == ["ord-b", "ord-c", "ord-a"]

    def test_pagination(self, storage):
        """GIVEN 5 scans WHEN get_all_scans(limit=2, offset=2) THEN returns 2 middle records."""
        for i in range(5):
            storage.save_scan(
                f"pg-{i:02d}",
                {**_minimal_scan(), "started_at": f"2026-01-{i + 1:02d} 10:00:00"},
            )
        page = storage.get_all_scans(limit=2, offset=2)
        assert len(page) == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TestDeleteScan
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDeleteScan:
    """delete_scan() removes a record and reports its existence."""

    def test_deletes_existing(self, storage):
        """GIVEN saved scan WHEN delete_scan THEN returns True and scan gone."""
        storage.save_scan("del-1", _minimal_scan())
        result = storage.delete_scan("del-1")
        assert result is True
        assert storage.get_scan("del-1") is None

    def test_returns_false_for_unknown(self, storage):
        """GIVEN no scan with id WHEN delete_scan THEN returns False."""
        assert storage.delete_scan("ghost") is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HTTP endpoint tests (SQLite-backed)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture
def http_client(tmp_path, monkeypatch):
    """Flask test client with SQLite-backed ScanStorageManager, authenticated as admin."""
    db_path = str(tmp_path / "test_analyzer.db")
    monkeypatch.setenv("AUTH_DB_PATH", db_path)

    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)

    from app.db_manager import DatabaseManager
    from app.scan_storage_manager import ScanStorageManager
    import app.web_app as web_app_mod
    import app.routes.scan_routes as scan_routes_mod

    # Re-initialize with temp DB
    dm = DatabaseManager(db_path)
    web_app_mod.auth_manager.__init__(db_manager=dm)
    web_app_mod.app.config["auth_manager"] = web_app_mod.auth_manager
    web_app_mod.auth_manager.change_password(1, "admin")

    # Wire up a fresh ScanStorageManager
    ssm = ScanStorageManager(dm)
    web_app_mod.app.config["scan_storage_manager"] = ssm

    # Clear in-memory scans
    scan_routes_mod.active_scans.clear()

    web_app_mod.app.config["TESTING"] = True
    with web_app_mod.app.test_client() as c:
        c.post("/login", data={"username": "admin", "password": "admin"})
        yield c, ssm


class TestHTTPGetResultsFallback:
    """HTTP: /api/results/<id> falls back to SQLite when scan not in memory."""

    def test_returns_404_when_not_in_db(self, http_client):
        """GIVEN scan_id not in memory or DB THEN 404."""
        c, _ = http_client
        resp = c.get("/api/results/nonexistent-id")
        assert resp.status_code == 404

    def test_returns_results_from_db(self, http_client):
        """GIVEN completed scan in DB WHEN GET results THEN 200 with results."""
        c, ssm = http_client
        scan_id = "http-res-001"
        ssm.save_scan(scan_id, _minimal_scan(scan_id))
        ssm.complete_scan(
            scan_id,
            {
                "scan_id": scan_id,
                "analysis_results": {"10.0.0.1": {"mode": "AP_ONLY"}},
            },
        )
        resp = c.get(f"/api/results/{scan_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["scan_id"] == scan_id
        assert "10.0.0.1" in data["analysis_results"]

    def test_returns_400_when_scan_not_completed(self, http_client):
        """GIVEN scan in DB with status=scanning THEN 400."""
        c, ssm = http_client
        scan_id = "http-res-002"
        ssm.save_scan(scan_id, {**_minimal_scan(scan_id), "status": "scanning"})
        resp = c.get(f"/api/results/{scan_id}")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["status"] == "scanning"


class TestHTTPGetStatusFallback:
    """HTTP: /api/status/<id> falls back to SQLite when scan not in memory."""

    def test_status_from_db(self, http_client):
        """GIVEN completed scan in DB WHEN GET status THEN 200."""
        c, ssm = http_client
        scan_id = "http-stat-001"
        ssm.save_scan(scan_id, _minimal_scan(scan_id))
        ssm.complete_scan(scan_id, {"scan_id": scan_id, "analysis_results": {}})
        resp = c.get(f"/api/status/{scan_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "completed"

    def test_status_404_when_nowhere(self, http_client):
        """GIVEN scan_id not in memory or DB THEN 404."""
        c, _ = http_client
        resp = c.get("/api/status/ghost-scan")
        assert resp.status_code == 404


class TestHTTPListScansMerge:
    """HTTP: /api/scans merges in-memory + SQLite with dedup."""

    def test_empty_when_nothing(self, http_client):
        """GIVEN no scans THEN returns empty list."""
        c, _ = http_client
        resp = c.get("/api/scans")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["scans"] == []

    def test_returns_db_scans(self, http_client):
        """GIVEN scan in DB WHEN list THEN returned."""
        c, ssm = http_client
        ssm.save_scan("list-001", _minimal_scan("list-001"))
        resp = c.get("/api/scans")
        data = resp.get_json()
        ids = [s["scan_id"] for s in data["scans"]]
        assert "list-001" in ids

    def test_dedup_memory_over_db(self, http_client):
        """GIVEN same scan_id in memory AND DB WHEN list THEN appears once (memory wins)."""
        import app.routes.scan_routes as sr

        c, ssm = http_client
        scan_id = "dedup-001"

        # Put in DB
        ssm.save_scan(scan_id, _minimal_scan(scan_id))

        # Put in memory with mock task
        mock_task = MagicMock()
        mock_task.status = "scanning"
        mock_task.progress = 50
        sr.active_scans[scan_id] = {
            "task": mock_task,
            "created_at": "2026-01-01T10:00:00",
            "ap_ips": ["10.0.0.1"],
        }

        resp = c.get("/api/scans")
        data = resp.get_json()
        ids = [s["scan_id"] for s in data["scans"]]
        assert ids.count(scan_id) == 1
        match = next(s for s in data["scans"] if s["scan_id"] == scan_id)
        assert match["status"] == "scanning"

        sr.active_scans.clear()

    def test_recent_route_alias(self, http_client):
        """GIVEN /api/scans/recent WHEN GET THEN same structure as /api/scans."""
        c, _ = http_client
        resp = c.get("/api/scans/recent")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "scans" in data


class TestHTTPSpectrumDataFallback:
    """HTTP: /api/spectrum_data/<ip> falls back to SQLite when not in memory."""

    def test_spectrum_404_when_no_data(self, http_client):
        """GIVEN no scans THEN 404."""
        c, _ = http_client
        resp = c.get("/api/spectrum_data/10.0.0.1")
        assert resp.status_code == 404

    def test_spectrum_from_db(self, http_client):
        """GIVEN scan in DB with raw_spectrum WHEN GET spectrum THEN 200."""
        c, ssm = http_client
        ip = "10.0.0.1"
        scan_id = "spec-db-001"
        ssm.save_scan(scan_id, _minimal_scan(scan_id))
        ssm.complete_scan(
            scan_id,
            {
                "analysis_results": {
                    ip: {
                        "raw_spectrum": [
                            {"freq": 5200.0, "noise": -80.0},
                            {"freq": 5210.0, "noise": -75.0},
                        ]
                    }
                }
            },
        )
        resp = c.get(f"/api/spectrum_data/{ip}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ip"] == ip
        assert len(data["frequencies"]) == 2
        assert data["frequencies"] == [5200.0, 5210.0]

    def test_spectrum_from_memory_task(self, http_client):
        """GIVEN raw_spectrum in task.results in memory WHEN GET spectrum THEN 200."""
        import app.routes.scan_routes as sr

        c, _ = http_client
        ip = "10.0.0.2"
        mock_task = MagicMock()
        mock_task.results = {
            "analysis_results": {
                ip: {
                    "raw_spectrum": [
                        {"freq": 5300.0, "noise": -70.0},
                        {"freq": 5310.0, "noise": -72.0},
                    ]
                }
            }
        }
        mock_task.status = "completed"
        sr.active_scans["mem-scan-001"] = {
            "task": mock_task,
            "created_at": "2026-01-01T10:00:00",
            "ap_ips": [ip],
        }

        resp = c.get(f"/api/spectrum_data/{ip}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ip"] == ip
        assert len(data["frequencies"]) == 2

        sr.active_scans.clear()
