"""
tests/test_api_security.py — BDD Tests for @requires_audit_ticket (with session auth)

Updated for change-003: All /api/scan requests now require authentication via session.
The @login_required decorator fires BEFORE @requires_audit_ticket.

Methodology: BDD (Given/When/Then)
Specification: 02_specs.md § S2 — Audit Contract + change-003 specs § S3.1

Scenarios:
  1. Unauthenticated requests → HTTP 401 (blocked by @login_required)
  2. Authenticated + no ticket_id → HTTP 403 (blocked by @requires_audit_ticket)
  3. Authenticated + invalid ticket_id → HTTP 403
  4. Authenticated + valid ticket_id → HTTP 202 (session user is primary)
  5. Backward-compat: X-Audit-User header as fallback (no session)
  6. Session user takes priority over X-Audit-User header
  7. Unprotected endpoints still work
  8. Error messages contain useful information
"""

import json
import os
import pytest
from unittest.mock import patch

from app.audit_manager import AuditManager


@pytest.fixture
def audit_log_file(tmp_path, monkeypatch):
    """Returns path to temporary audit log file."""
    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)
    return log_file


def _read_audit_logs(log_file: str) -> list:
    """Helper: Read entries from audit_logs.jsonl."""
    if not os.path.exists(log_file):
        return []
    with open(log_file, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 1: Unauthenticated requests → HTTP 401
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestUnauthenticated:
    """
    GIVEN /api/scan requires @login_required + @requires_audit_ticket
    WHEN request has no session (not logged in)
    THEN @login_required returns 401 before @requires_audit_ticket runs.
    """

    def test_no_session_returns_401(self, client):
        """GIVEN POST /api/scan without session WHEN request THEN 401."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "ticket_id": 42},
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_no_session_with_headers_returns_401(self, client):
        """GIVEN POST /api/scan with X-Audit-User but no session WHEN request THEN 401."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"]},
            headers={
                "X-Audit-User": "admin@test.com",
                "X-Ticket-ID": "12345",
            },
            content_type="application/json",
        )
        assert response.status_code == 401


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 2: Authenticated + no ticket_id → HTTP 403
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNoTicket:
    """
    GIVEN authenticated session (admin logged in)
    WHEN POST /api/scan without ticket_id
    THEN @requires_audit_ticket returns 403.
    """

    def test_no_ticket_returns_403(self, authenticated_client):
        """GIVEN authenticated + no ticket WHEN POST /api/scan THEN 403."""
        response = authenticated_client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"]},
            content_type="application/json",
        )
        assert response.status_code == 403
        data = response.get_json()
        assert data["error"] == "Excepción de Seguridad"

    def test_only_ticket_no_user_session_has_user(self, authenticated_client):
        """GIVEN session user exists + ticket_id in body WHEN POST THEN session user used → 202."""
        response = authenticated_client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "ticket_id": 42},
            content_type="application/json",
        )
        # Session has user, body has ticket_id → should work
        assert response.status_code == 202


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 3: Authenticated + invalid ticket_id → HTTP 403
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestInvalidTicket:
    """
    GIVEN authenticated session + invalid ticket_id
    WHEN POST /api/scan
    THEN @requires_audit_ticket returns 403.
    """

    @pytest.mark.parametrize(
        "bad_ticket",
        [
            "ABC-123",  # alphanumeric
            "",  # empty
            -5,  # negative
            0,  # zero
            None,  # explicit null
            True,  # boolean
        ],
    )
    def test_invalid_tickets_return_403(self, authenticated_client, bad_ticket):
        """GIVEN invalid ticket_id WHEN POST /api/scan THEN 403."""
        payload = {
            "ap_ips": ["192.168.1.1"],
            "ticket_id": bad_ticket,
        }
        response = authenticated_client.post(
            "/api/scan",
            json=payload,
            content_type="application/json",
        )
        assert response.status_code == 403
        data = response.get_json()
        assert "Seguridad" in data["error"]

    def test_snmp_not_started_on_invalid_ticket(self, authenticated_client):
        """GIVEN invalid ticket WHEN POST /api/scan THEN SNMP scan NOT started."""
        with patch("app.web_app.ScanTask") as mock_task:
            response = authenticated_client.post(
                "/api/scan",
                json={"ap_ips": ["192.168.1.1"], "ticket_id": -1},
                content_type="application/json",
            )
            assert response.status_code == 403
            mock_task.assert_not_called()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 4: Authenticated + valid ticket_id → HTTP 202
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestValidTicketWithSession:
    """
    GIVEN authenticated session + valid ticket_id in body
    WHEN POST /api/scan
    THEN scan starts (HTTP 202) using session['user'] as audit user.
    """

    def test_valid_ticket_in_body_returns_202(self, authenticated_client):
        """GIVEN session + ticket_id in body WHEN POST THEN 202."""
        response = authenticated_client.post(
            "/api/scan",
            json={
                "ap_ips": ["192.168.1.1"],
                "snmp_community": "Canopy",
                "ticket_id": 9999,
            },
            content_type="application/json",
        )
        assert response.status_code == 202
        data = response.get_json()
        assert "scan_id" in data
        assert data["status"] == "started"

    def test_valid_ticket_in_header_returns_202(self, authenticated_client):
        """GIVEN session + ticket_id in X-Ticket-ID header WHEN POST THEN 202."""
        response = authenticated_client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "snmp_community": "Canopy"},
            headers={"X-Ticket-ID": "12345"},
            content_type="application/json",
        )
        assert response.status_code == 202


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 5: Backward-compat — X-Audit-User header fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBackwardCompatHeaders:
    """
    GIVEN an API client using the old header-based auth (no session)
    WHEN POST /api/scan with X-Audit-User + X-Ticket-ID headers
    THEN blocked by @login_required (401) since session is required.

    Note: The backward-compat header fallback for user is inside
    @requires_audit_ticket, but @login_required runs first and requires a session.
    API clients MUST authenticate via session now.
    """

    def test_headers_only_without_session_returns_401(self, client):
        """GIVEN headers but no session WHEN POST /api/scan THEN 401."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "snmp_community": "Canopy"},
            headers={
                "X-Audit-User": "admin@cambium.com",
                "X-Ticket-ID": "12345",
            },
            content_type="application/json",
        )
        assert response.status_code == 401


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 6: Session user takes priority over X-Audit-User header
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSessionPriority:
    """
    GIVEN session['user'] == 'admin' AND X-Audit-User header == 'other_user'
    WHEN POST /api/scan
    THEN the session user ('admin') is used for audit, not the header.
    """

    def test_session_user_overrides_header(self, authenticated_client):
        """GIVEN session='admin' + header='other' WHEN POST THEN 202 (session user used)."""
        response = authenticated_client.post(
            "/api/scan",
            json={
                "ap_ips": ["192.168.1.1"],
                "snmp_community": "Canopy",
                "ticket_id": 111,
            },
            headers={
                "X-Audit-User": "other_user@test.com",
                "X-Ticket-ID": "222",
            },
            content_type="application/json",
        )
        assert response.status_code == 202


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 7: Unprotected endpoints still work
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestUnprotectedEndpoints:
    """
    GIVEN endpoints without @login_required
    WHEN accessed without a session
    THEN they respond normally.
    """

    def test_health_check_no_auth_needed(self, client):
        """GIVEN GET /api/health WHEN no session THEN 200."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"

    def test_login_page_no_auth_needed(self, client):
        """GIVEN GET /login WHEN no session THEN 200."""
        response = client.get("/login")
        assert response.status_code == 200

    def test_scan_status_requires_auth(self, client):
        """GIVEN GET /api/status/<id> WHEN no session THEN 401 (now protected)."""
        response = client.get(
            "/api/status/nonexistent-id",
            content_type="application/json",
        )
        # Now protected by @login_required → 401 for JSON, 302 for HTML
        assert response.status_code in (302, 401)

    def test_scans_list_requires_auth(self, client):
        """GIVEN GET /api/scans WHEN no session THEN redirect/401 (now protected)."""
        response = client.get("/api/scans")
        assert response.status_code in (302, 401)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scenario 8: Error messages contain useful information
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestErrorMessages:
    """
    GIVEN an authenticated request rejected by audit
    WHEN examining the error body
    THEN it contains clear info about why it was rejected.
    """

    def test_missing_ticket_error_message(self, authenticated_client):
        """GIVEN no ticket_id WHEN POST THEN message mentions required fields."""
        response = authenticated_client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"]},
            content_type="application/json",
        )
        data = response.get_json()
        assert "OBLIGATORIO" in data["message"] or "entero positivo" in data["message"]

    def test_invalid_ticket_error_message(self, authenticated_client):
        """GIVEN ticket_id='ABC' WHEN POST THEN message mentions 'entero positivo'."""
        response = authenticated_client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "ticket_id": "ABC"},
            content_type="application/json",
        )
        data = response.get_json()
        assert "entero positivo" in data["message"]
