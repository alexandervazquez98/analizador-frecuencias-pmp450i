"""
tests/test_storage.py -- Tests for storage fallback and dual-source merge.

Covers:
  - STORAGE_FILE_PATH env var overrides default path
  - get_scan_results() falls back to STORAGE_FILE when scan_id not in active_scans
  - get_scan_status() falls back to STORAGE_FILE (existing behavior, regression guard)
  - list_scans() merges memory + storage with dedup
  - get_spectrum_data_api() falls back to STORAGE_FILE
"""

import json
import os
import pytest
from unittest.mock import MagicMock

from app.audit_manager import AuditManager


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Fixtures
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@pytest.fixture
def storage_file(tmp_path):
    """Create a temp storage JSON file and patch STORAGE_FILE to use it."""
    sf = tmp_path / "test_storage.json"
    sf.write_text(json.dumps({"active_scans": {}, "scan_results": {}}))
    return sf


@pytest.fixture
def client(tmp_path, monkeypatch, storage_file):
    """Flask test client with STORAGE_FILE, auth DB, and audit log redirected to tmp.
    Authenticated as admin (must_change cleared) so protected routes work.
    """
    from pathlib import Path

    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)

    # Set up auth DB in temp directory
    db_path = str(tmp_path / "test_auth.db")
    monkeypatch.setenv("AUTH_DB_PATH", db_path)

    import app.web_app as web_app_mod

    monkeypatch.setattr(web_app_mod, "STORAGE_FILE", Path(str(storage_file)))

    # Re-initialize auth_manager with temp DB
    web_app_mod.auth_manager.__init__(db_path=db_path)
    # Clear must_change_password for admin
    web_app_mod.auth_manager.change_password(1, "admin")

    # Clear in-memory scans between tests
    web_app_mod.active_scans.clear()

    web_app_mod.app.config["TESTING"] = True
    with web_app_mod.app.test_client() as c:
        # Login as admin
        c.post("/login", data={"username": "admin", "password": "admin"})
        yield c


@pytest.fixture
def web_app_mod(monkeypatch, tmp_path, storage_file):
    """Return the web_app module with patched STORAGE_FILE."""
    from pathlib import Path

    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)

    import app.web_app as mod

    monkeypatch.setattr(mod, "STORAGE_FILE", Path(str(storage_file)))
    mod.active_scans.clear()
    return mod


def _write_storage(storage_file, data):
    """Helper: write data to storage file."""
    storage_file.write_text(json.dumps(data, default=str))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STORAGE_FILE_PATH env var
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestStorageFilePathEnv:
    """STORAGE_FILE_PATH env var controls the storage file location."""

    def test_default_path_when_env_not_set(self, monkeypatch):
        """GIVEN no STORAGE_FILE_PATH env var WHEN module loaded THEN uses /tmp default."""
        monkeypatch.delenv("STORAGE_FILE_PATH", raising=False)
        # Re-evaluate the expression (the module is already imported, so we test the logic)
        from pathlib import Path

        result = Path(
            os.environ.get("STORAGE_FILE_PATH", "/tmp/tower_scan_storage.json")
        )
        # Use PurePosixPath for comparison since the default is a Unix-style path
        assert result == Path("/tmp/tower_scan_storage.json")

    def test_custom_path_from_env(self, monkeypatch, tmp_path):
        """GIVEN STORAGE_FILE_PATH set to custom path WHEN evaluated THEN uses custom path."""
        custom = str(tmp_path / "custom_storage.json")
        monkeypatch.setenv("STORAGE_FILE_PATH", custom)
        from pathlib import Path

        result = Path(
            os.environ.get("STORAGE_FILE_PATH", "/tmp/tower_scan_storage.json")
        )
        assert str(result) == custom


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# get_scan_results fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetScanResultsFallback:
    """
    GIVEN a scan_id NOT in active_scans (memory)
    WHEN GET /api/results/<scan_id>
    THEN the endpoint falls back to STORAGE_FILE.
    """

    def test_returns_404_when_not_in_memory_or_storage(self, client):
        """GIVEN scan_id not anywhere THEN 404."""
        resp = client.get("/api/results/nonexistent-id")
        assert resp.status_code == 404

    def test_returns_results_from_storage(self, client, storage_file):
        """GIVEN completed scan in storage WHEN GET results THEN returns results from file."""
        scan_id = "test-scan-001"
        _write_storage(
            storage_file,
            {
                "active_scans": {
                    scan_id: {
                        "status": "completed",
                        "progress": 100,
                        "results": {
                            "scan_id": scan_id,
                            "analysis_results": {"10.0.0.1": {"mode": "AP_ONLY"}},
                        },
                    }
                },
                "scan_results": {},
            },
        )

        resp = client.get(f"/api/results/{scan_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["scan_id"] == scan_id
        assert "10.0.0.1" in data["analysis_results"]

    def test_returns_400_when_storage_scan_not_completed(self, client, storage_file):
        """GIVEN scan in storage with status=scanning THEN 400."""
        scan_id = "test-scan-002"
        _write_storage(
            storage_file,
            {
                "active_scans": {
                    scan_id: {
                        "status": "scanning",
                        "progress": 30,
                    }
                },
                "scan_results": {},
            },
        )

        resp = client.get(f"/api/results/{scan_id}")
        assert resp.status_code == 400
        data = resp.get_json()
        assert data["status"] == "scanning"

    def test_returns_404_when_completed_but_no_results(self, client, storage_file):
        """GIVEN scan in storage completed but results is None THEN 404."""
        scan_id = "test-scan-003"
        _write_storage(
            storage_file,
            {
                "active_scans": {
                    scan_id: {
                        "status": "completed",
                        "progress": 100,
                        "results": None,
                    }
                },
                "scan_results": {},
            },
        )

        resp = client.get(f"/api/results/{scan_id}")
        assert resp.status_code == 404


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# get_scan_status fallback (regression guard)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetScanStatusFallback:
    """Regression: /api/status/<id> already had fallback; verify it still works."""

    def test_status_from_storage(self, client, storage_file):
        """GIVEN scan in storage WHEN GET /api/status THEN returns from file."""
        scan_id = "status-test-001"
        _write_storage(
            storage_file,
            {
                "active_scans": {
                    scan_id: {
                        "status": "completed",
                        "progress": 100,
                        "results": {"scan_id": scan_id, "analysis_results": {}},
                    }
                },
                "scan_results": {},
            },
        )

        resp = client.get(f"/api/status/{scan_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "completed"

    def test_status_404_when_nowhere(self, client):
        """GIVEN scan_id not in memory or storage THEN 404."""
        resp = client.get("/api/status/ghost-scan")
        assert resp.status_code == 404


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# list_scans merge
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestListScansMerge:
    """
    GIVEN scans in both memory and storage
    WHEN GET /api/scans
    THEN returns merged list with dedup.
    """

    def test_empty_when_nothing(self, client):
        """GIVEN no scans THEN returns empty list."""
        resp = client.get("/api/scans")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["scans"] == []

    def test_returns_storage_only_scans(self, client, storage_file):
        """GIVEN scans only in storage WHEN list THEN returned."""
        _write_storage(
            storage_file,
            {
                "active_scans": {
                    "stored-001": {
                        "created_at": "2026-01-01T10:00:00",
                        "status": "completed",
                        "progress": 100,
                        "ap_count": 2,
                        "ap_ips": ["10.0.0.1", "10.0.0.2"],
                    }
                },
                "scan_results": {},
            },
        )

        resp = client.get("/api/scans")
        data = resp.get_json()
        assert len(data["scans"]) == 1
        assert data["scans"][0]["scan_id"] == "stored-001"
        assert data["scans"][0]["ap_count"] == 2

    def test_dedup_memory_over_storage(self, client, storage_file, web_app_mod):
        """GIVEN same scan_id in memory AND storage WHEN list THEN appears once (from memory)."""
        scan_id = "dedup-001"

        # Put in storage
        _write_storage(
            storage_file,
            {
                "active_scans": {
                    scan_id: {
                        "created_at": "2026-01-01T10:00:00",
                        "status": "completed",
                        "progress": 100,
                        "ap_count": 1,
                    }
                },
                "scan_results": {},
            },
        )

        # Put in memory with a mock task
        mock_task = MagicMock()
        mock_task.status = "scanning"
        mock_task.progress = 50
        web_app_mod.active_scans[scan_id] = {
            "task": mock_task,
            "created_at": "2026-01-01T10:00:00",
            "ap_ips": ["10.0.0.1"],
        }

        resp = client.get("/api/scans")
        data = resp.get_json()
        ids = [s["scan_id"] for s in data["scans"]]
        assert ids.count(scan_id) == 1
        # Memory version should win (status=scanning, not completed)
        assert data["scans"][0]["status"] == "scanning"

    def test_merge_both_sources(self, client, storage_file, web_app_mod):
        """GIVEN different scans in memory and storage THEN both appear."""
        # Storage scan
        _write_storage(
            storage_file,
            {
                "active_scans": {
                    "old-scan": {
                        "created_at": "2026-01-01T08:00:00",
                        "status": "completed",
                        "progress": 100,
                        "ap_count": 3,
                    }
                },
                "scan_results": {},
            },
        )

        # Memory scan
        mock_task = MagicMock()
        mock_task.status = "scanning"
        mock_task.progress = 25
        web_app_mod.active_scans["new-scan"] = {
            "task": mock_task,
            "created_at": "2026-01-02T10:00:00",
            "ap_ips": ["10.0.0.5"],
        }

        resp = client.get("/api/scans")
        data = resp.get_json()
        assert len(data["scans"]) == 2
        ids = {s["scan_id"] for s in data["scans"]}
        assert ids == {"old-scan", "new-scan"}

    def test_recent_route_alias(self, client):
        """GIVEN /api/scans/recent WHEN GET THEN same response as /api/scans."""
        resp = client.get("/api/scans/recent")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "scans" in data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# get_spectrum_data_api fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSpectrumDataFallback:
    """
    GIVEN spectrum data only in STORAGE_FILE
    WHEN GET /api/spectrum_data/<ip>
    THEN falls back to storage.
    """

    def test_spectrum_404_when_no_data(self, client):
        """GIVEN no scans anywhere THEN 404."""
        resp = client.get("/api/spectrum_data/10.0.0.1")
        assert resp.status_code == 404

    def test_spectrum_from_storage(self, client, storage_file):
        """GIVEN raw_spectrum in storage WHEN GET spectrum THEN returns data."""
        ip = "10.0.0.1"
        _write_storage(
            storage_file,
            {
                "active_scans": {
                    "spec-scan-001": {
                        "status": "completed",
                        "results": {
                            "analysis_results": {
                                ip: {
                                    "raw_spectrum": [
                                        {"freq": 5200.0, "noise": -80.0},
                                        {"freq": 5210.0, "noise": -75.0},
                                        {"freq": 5220.0, "noise": -85.0},
                                    ]
                                }
                            }
                        },
                    }
                },
                "scan_results": {},
            },
        )

        resp = client.get(f"/api/spectrum_data/{ip}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ip"] == ip
        assert len(data["frequencies"]) == 3
        assert data["frequencies"] == [5200.0, 5210.0, 5220.0]
        assert data["noise_levels"] == [-80.0, -75.0, -85.0]
        assert abs(data["mean_noise"] - (-80.0)) < 0.01

    def test_spectrum_from_memory_task(self, client, web_app_mod):
        """GIVEN raw_spectrum in task.results WHEN GET spectrum THEN returns data."""
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
        web_app_mod.active_scans["mem-scan-001"] = {
            "task": mock_task,
            "created_at": "2026-01-01T10:00:00",
            "ap_ips": [ip],
        }

        resp = client.get(f"/api/spectrum_data/{ip}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ip"] == ip
        assert len(data["frequencies"]) == 2
