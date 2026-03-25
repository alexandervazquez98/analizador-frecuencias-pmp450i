"""
tests/test_apply_routes.py — Integration tests for /api/apply-frequency and
/api/apply-history/<tower_id> endpoints.

Spec: change-006 tasks Phase 5 task 5.4.

Uses the Flask test client from conftest.authenticated_client fixture.
All SNMP operations are mocked via patch on FrequencyApplyManager.run_apply
and FrequencyApplyManager.get_apply_history to avoid real network calls.
"""

import json
import pytest
from unittest.mock import patch

from app.db_manager import DatabaseManager


# ── Helper ────────────────────────────────────────────────────────────────────


def _insert_scan(client, scan_id, ap_ips, sm_ips=None):
    """Helper: insert a minimal completed scan record for use in apply tests."""
    import sqlite3

    db_path = client.application.config["db_manager"].db_path
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")

    # Ensure user 1 exists (admin was created by conftest)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash, role) "
            "VALUES (1, 'admin', 'hash', 'admin')"
        )
    except Exception:
        pass

    conn.execute(
        """INSERT OR IGNORE INTO scans
           (id, user_id, username, ticket_id, ap_ips, sm_ips, status, results)
           VALUES (?, 1, 'admin', 1, ?, ?, 'completed', ?)""",
        (
            scan_id,
            json.dumps(ap_ips),
            json.dumps(sm_ips or []),
            json.dumps({
                "best_combined_frequency": {"is_viable": True, "combined_score": 0.90}
            }),
        ),
    )
    conn.commit()
    conn.close()


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def auth_client(authenticated_client):
    """
    A Flask test client already logged in as admin.
    The authenticated_client fixture from conftest provides this.
    """
    return authenticated_client


MOCK_APPLY_RESULT = {
    "success": True,
    "apply_id": 1,
    "state": "completed",
    "freq_khz": 5180000,
    "sm_results": {},
    "ap_result": {"success": True, "error": None},
    "errors": [],
}


# ── POST /api/apply-frequency ─────────────────────────────────────────────────


class TestApplyFrequencyEndpoint:
    """Tests for POST /api/apply-frequency."""

    def test_returns_200_on_success(self, auth_client):
        """GIVEN valid body and mocked manager THEN returns 200 with apply result."""
        _insert_scan(auth_client, "SCAN-1", ["192.168.1.10"])
        with patch("app.routes.apply_routes.FrequencyApplyManager.run_apply",
                   return_value=MOCK_APPLY_RESULT):
            res = auth_client.post(
                "/api/apply-frequency",
                json={"scan_id": "SCAN-1", "freq_mhz": 5180.0, "tower_id": "T1"},
                content_type="application/json",
            )
        assert res.status_code == 200
        data = res.get_json()
        assert data["state"] == "completed"

    def test_returns_400_when_scan_id_missing(self, auth_client):
        """GIVEN body without scan_id THEN returns 400."""
        res = auth_client.post(
            "/api/apply-frequency",
            json={"freq_mhz": 5180.0, "tower_id": "T1"},
        )
        assert res.status_code == 400

    def test_returns_400_when_freq_mhz_missing(self, auth_client):
        """GIVEN body without freq_mhz THEN returns 400."""
        res = auth_client.post(
            "/api/apply-frequency",
            json={"scan_id": "SCAN-1", "tower_id": "T1"},
        )
        assert res.status_code == 400

    def test_returns_400_when_tower_id_missing(self, auth_client):
        """GIVEN body without tower_id THEN returns 400."""
        res = auth_client.post(
            "/api/apply-frequency",
            json={"scan_id": "SCAN-1", "freq_mhz": 5180.0},
        )
        assert res.status_code == 400

    def test_returns_400_when_freq_mhz_not_numeric(self, auth_client):
        """GIVEN freq_mhz='abc' THEN returns 400."""
        res = auth_client.post(
            "/api/apply-frequency",
            json={"scan_id": "SCAN-1", "freq_mhz": "abc", "tower_id": "T1"},
        )
        assert res.status_code == 400

    def test_returns_422_when_viability_gate_blocks(self, auth_client):
        """GIVEN manager raises ValueError (gate) THEN returns 422."""
        with patch("app.routes.apply_routes.FrequencyApplyManager.run_apply",
                   side_effect=ValueError("Analysis not viable")):
            res = auth_client.post(
                "/api/apply-frequency",
                json={"scan_id": "SCAN-X", "freq_mhz": 5180.0, "tower_id": "T1"},
            )
        assert res.status_code == 422
        data = res.get_json()
        assert "error" in data

    def test_returns_422_when_scan_not_found(self, auth_client):
        """GIVEN non-existent scan_id and no mock THEN returns 422 (ValueError from manager)."""
        with patch("app.routes.apply_routes.FrequencyApplyManager.run_apply",
                   side_effect=ValueError("Scan 'X' not found")):
            res = auth_client.post(
                "/api/apply-frequency",
                json={"scan_id": "X", "freq_mhz": 5180.0, "tower_id": "T1"},
            )
        assert res.status_code == 422

    def test_returns_500_on_unexpected_error(self, auth_client):
        """GIVEN manager raises unexpected Exception THEN returns 500."""
        with patch("app.routes.apply_routes.FrequencyApplyManager.run_apply",
                   side_effect=RuntimeError("SNMP exploded")):
            res = auth_client.post(
                "/api/apply-frequency",
                json={"scan_id": "SCAN-1", "freq_mhz": 5180.0, "tower_id": "T1"},
            )
        assert res.status_code == 500

    def test_unauthenticated_returns_401_redirect(self, client):
        """GIVEN unauthenticated client THEN request is redirected or 401."""
        res = client.post(
            "/api/apply-frequency",
            json={"scan_id": "S1", "freq_mhz": 5180.0, "tower_id": "T1"},
        )
        assert res.status_code in (401, 302)

    def test_viewer_role_returns_403(self, tmp_path, monkeypatch):
        """GIVEN session role=viewer THEN returns 403.

        Note: AuthManager._VALID_ROLES = ('admin', 'operator') — 'viewer' cannot
        be created via create_user(). We insert directly via SQL to test the
        RBAC check at the route level.
        """
        import sqlite3 as _sqlite3
        from app.audit_manager import AuditManager

        db_path = str(tmp_path / "test.db")
        monkeypatch.setenv("AUTH_DB_PATH", db_path)
        log_file = str(tmp_path / "audit_logs.jsonl")
        monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)

        from app.db_manager import DatabaseManager
        from app.web_app import app, auth_manager
        from werkzeug.security import generate_password_hash

        dm = DatabaseManager(db_path)
        auth_manager.__init__(db_manager=dm)
        app.config["auth_manager"] = auth_manager
        app.config["TESTING"] = True

        # Insert viewer user directly (bypasses AuthManager role validation)
        raw = _sqlite3.connect(db_path)
        raw.execute("PRAGMA foreign_keys=ON")
        raw.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, must_change_password) "
            "VALUES ('viewer_user', ?, 'viewer', 0)",
            (generate_password_hash("viewpass"),),
        )
        raw.commit()
        raw.close()

        with app.test_client() as c:
            c.post("/login", data={"username": "viewer_user", "password": "viewpass"})
            res = c.post(
                "/api/apply-frequency",
                json={"scan_id": "X", "freq_mhz": 5180.0, "tower_id": "T1"},
            )
        assert res.status_code == 403

    def test_force_true_rejected_for_non_admin(self, tmp_path, monkeypatch):
        """GIVEN operator session and force=True THEN returns 403."""
        from app.audit_manager import AuditManager
        db_path = str(tmp_path / "test.db")
        monkeypatch.setenv("AUTH_DB_PATH", db_path)
        monkeypatch.setattr(AuditManager, "LOG_FILE", str(tmp_path / "audit.jsonl"))

        from app.db_manager import DatabaseManager
        from app.web_app import app, auth_manager

        dm = DatabaseManager(db_path)
        auth_manager.__init__(db_manager=dm)
        app.config["auth_manager"] = auth_manager
        app.config["TESTING"] = True

        auth_manager.create_user("op_user", "oppass", role="operator")
        auth_manager.change_password(
            auth_manager.authenticate("op_user", "oppass")["id"], "oppass"
        )

        with app.test_client() as c:
            c.post("/login", data={"username": "op_user", "password": "oppass"})
            res = c.post(
                "/api/apply-frequency",
                json={"scan_id": "X", "freq_mhz": 5180.0, "tower_id": "T1", "force": True},
            )
        assert res.status_code == 403


# ── GET /api/apply-history/<tower_id> ────────────────────────────────────────


class TestApplyHistoryEndpoint:
    """Tests for GET /api/apply-history/<tower_id>."""

    def test_returns_200_with_applies_list(self, auth_client):
        """GIVEN mocked history THEN returns 200 with 'applies' key."""
        mock_history = [
            {"id": 1, "freq_khz": 5180000, "state": "completed", "applied_by_username": "admin"}
        ]
        with patch("app.routes.apply_routes.FrequencyApplyManager.get_apply_history",
                   return_value=mock_history):
            res = auth_client.get("/api/apply-history/TORRE-01")
        assert res.status_code == 200
        data = res.get_json()
        assert "applies" in data
        assert len(data["applies"]) == 1

    def test_returns_empty_list_when_no_history(self, auth_client):
        """GIVEN no applies for tower THEN returns 200 with applies=[]."""
        with patch("app.routes.apply_routes.FrequencyApplyManager.get_apply_history",
                   return_value=[]):
            res = auth_client.get("/api/apply-history/NONEXISTENT-TOWER")
        assert res.status_code == 200
        assert res.get_json()["applies"] == []

    def test_unauthenticated_returns_401_or_redirect(self, client):
        """GIVEN unauthenticated client THEN request is refused."""
        res = client.get("/api/apply-history/TORRE-01")
        assert res.status_code in (401, 302)

    def test_returns_500_on_manager_error(self, auth_client):
        """GIVEN manager raises exception THEN returns 500."""
        with patch("app.routes.apply_routes.FrequencyApplyManager.get_apply_history",
                   side_effect=RuntimeError("DB exploded")):
            res = auth_client.get("/api/apply-history/TORRE-01")
        assert res.status_code == 500
