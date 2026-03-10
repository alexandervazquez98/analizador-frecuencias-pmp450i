"""
tests/test_auth_routes.py — BDD Tests for Authentication Routes.

Specification: change-003 specs § S3.1 — User Authentication (Login, Logout, Session)
Design:        change-003 design § D3.2 — Flask Routes + login_required decorator

Scenarios:
  1. Login page renders correctly
  2. Login with valid/invalid credentials
  3. First-login redirect to /change-password
  4. Change-password validation (short, mismatch, same, wrong current)
  5. Change-password success flow
  6. Logout clears session
  7. Protected routes redirect/return 401 when no session
  8. /api/health works without auth
"""

import pytest


class TestLoginPage:
    """Tests for GET /login — login page rendering."""

    def test_login_page_returns_200(self, client):
        """GIVEN unauthenticated user WHEN GET /login THEN 200."""
        response = client.get("/login")
        assert response.status_code == 200

    def test_login_page_contains_form(self, client):
        """GIVEN GET /login THEN response contains username/password fields."""
        response = client.get("/login")
        html = response.data.decode()
        assert 'name="username"' in html
        assert 'name="password"' in html

    def test_already_logged_in_redirects_to_index(self, authenticated_client):
        """GIVEN logged-in user WHEN GET /login THEN redirect to /."""
        response = authenticated_client.get("/login")
        assert response.status_code == 302
        assert response.location.endswith("/") or "/" in response.location


class TestLoginPost:
    """Tests for POST /login — authentication flow."""

    def test_valid_credentials_redirect_to_index(self, client):
        """GIVEN valid admin/admin (must_change cleared) WHEN POST /login THEN 302 to /."""
        # First, clear must_change flag
        from app.web_app import auth_manager

        auth_manager.change_password(1, "admin")

        response = client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
        )
        assert response.status_code == 302
        assert "/" in response.location

    def test_invalid_credentials_shows_error(self, client):
        """GIVEN wrong password WHEN POST /login THEN 200 with error message."""
        response = client.post(
            "/login",
            data={"username": "admin", "password": "wrongpass"},
        )
        assert response.status_code == 200
        html = response.data.decode()
        assert "incorrectos" in html.lower() or "error" in html.lower()

    def test_nonexistent_user_shows_error(self, client):
        """GIVEN non-existent user WHEN POST /login THEN 200 with error."""
        response = client.post(
            "/login",
            data={"username": "nobody", "password": "anything"},
        )
        assert response.status_code == 200

    def test_first_login_redirects_to_change_password(self, client):
        """GIVEN admin with must_change_password=1 WHEN POST /login THEN redirect to /change-password."""
        response = client.post(
            "/login",
            data={"username": "admin", "password": "admin"},
        )
        assert response.status_code == 302
        assert "change-password" in response.location


class TestLogout:
    """Tests for GET /logout — session clearing."""

    def test_logout_redirects_to_login(self, authenticated_client):
        """GIVEN logged-in user WHEN GET /logout THEN redirect to /login."""
        response = authenticated_client.get("/logout")
        assert response.status_code == 302
        assert "login" in response.location

    def test_logout_clears_session(self, authenticated_client):
        """GIVEN logged-in user WHEN logout THEN subsequent protected route redirects."""
        authenticated_client.get("/logout")
        response = authenticated_client.get("/")
        assert response.status_code == 302
        assert "login" in response.location


class TestChangePassword:
    """Tests for /change-password — password change flow."""

    def test_change_password_page_renders(self, authenticated_client):
        """GIVEN logged-in user WHEN GET /change-password THEN 200."""
        response = authenticated_client.get("/change-password")
        assert response.status_code == 200

    def test_change_password_requires_login(self, client):
        """GIVEN unauthenticated WHEN GET /change-password THEN redirect to /login."""
        response = client.get("/change-password")
        assert response.status_code == 302
        assert "login" in response.location

    def test_wrong_current_password(self, authenticated_client):
        """GIVEN wrong current password WHEN POST /change-password THEN error."""
        response = authenticated_client.post(
            "/change-password",
            data={
                "current_password": "wrongcurrent",
                "new_password": "newpassword123",
                "confirm_password": "newpassword123",
            },
        )
        assert response.status_code == 200
        html = response.data.decode()
        assert "incorrecta" in html.lower() or "error" in html.lower()

    def test_password_mismatch(self, authenticated_client):
        """GIVEN mismatched new/confirm WHEN POST /change-password THEN error."""
        response = authenticated_client.post(
            "/change-password",
            data={
                "current_password": "admin",
                "new_password": "newpassword123",
                "confirm_password": "differentpassword",
            },
        )
        assert response.status_code == 200
        html = response.data.decode()
        assert "no coinciden" in html.lower() or "error" in html.lower()

    def test_password_too_short(self, authenticated_client):
        """GIVEN password < 6 chars WHEN POST /change-password THEN error."""
        response = authenticated_client.post(
            "/change-password",
            data={
                "current_password": "admin",
                "new_password": "abc",
                "confirm_password": "abc",
            },
        )
        assert response.status_code == 200
        html = response.data.decode()
        assert "6 caracteres" in html.lower() or "error" in html.lower()

    def test_same_as_current_password(self, authenticated_client):
        """GIVEN new == current WHEN POST /change-password THEN error."""
        # First change to a 6+ char password so the "too short" check doesn't fire first
        authenticated_client.post(
            "/change-password",
            data={
                "current_password": "admin",
                "new_password": "longenough",
                "confirm_password": "longenough",
            },
        )
        # Re-login with new password
        authenticated_client.get("/logout")
        authenticated_client.post(
            "/login", data={"username": "admin", "password": "longenough"}
        )
        # Now try to change to the same password
        response = authenticated_client.post(
            "/change-password",
            data={
                "current_password": "longenough",
                "new_password": "longenough",
                "confirm_password": "longenough",
            },
        )
        assert response.status_code == 200
        html = response.data.decode()
        assert "diferente" in html.lower()

    def test_successful_password_change(self, authenticated_client):
        """GIVEN valid new password WHEN POST /change-password THEN redirect to /."""
        response = authenticated_client.post(
            "/change-password",
            data={
                "current_password": "admin",
                "new_password": "newsecurepass",
                "confirm_password": "newsecurepass",
            },
        )
        assert response.status_code == 302
        assert "/" in response.location


class TestProtectedRoutes:
    """Tests for @login_required — protected routes behavior."""

    @pytest.mark.parametrize(
        "route",
        [
            "/",
            "/api/config",
            "/api/scans",
            "/api/recommendations",
        ],
    )
    def test_html_routes_redirect_to_login(self, client, route):
        """GIVEN unauthenticated WHEN GET protected route THEN redirect to /login."""
        response = client.get(route)
        assert response.status_code == 302
        assert "login" in response.location

    @pytest.mark.parametrize(
        "route",
        [
            "/api/config",
            "/api/scans",
            "/api/recommendations",
        ],
    )
    def test_api_routes_return_401_json(self, client, route):
        """GIVEN unauthenticated JSON request WHEN GET protected API THEN 401."""
        response = client.get(
            route,
            headers={"Accept": "application/json"},
            content_type="application/json",
        )
        assert response.status_code == 401
        data = response.get_json()
        assert data is not None
        assert "redirect" in data

    def test_scan_post_returns_401_without_session(self, client):
        """GIVEN unauthenticated WHEN POST /api/scan THEN 401 (not 403)."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "ticket_id": 42},
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_protected_routes_work_when_authenticated(self, authenticated_client):
        """GIVEN authenticated user WHEN GET /api/config THEN 200."""
        response = authenticated_client.get("/api/config")
        assert response.status_code == 200


class TestHealthEndpoint:
    """Tests for /api/health — must work without auth."""

    def test_health_no_auth_needed(self, client):
        """GIVEN unauthenticated WHEN GET /api/health THEN 200."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"


class TestMustChangePasswordRedirect:
    """Tests for must_change_password session flag behavior."""

    def test_must_change_blocks_protected_html_routes(self, client):
        """GIVEN user with must_change=1 WHEN GET / THEN redirect to /change-password."""
        # Login as admin (must_change_password=1 by default)
        client.post("/login", data={"username": "admin", "password": "admin"})
        response = client.get("/")
        assert response.status_code == 302
        assert "change-password" in response.location

    def test_must_change_blocks_api_with_403(self, client):
        """GIVEN user with must_change=1 WHEN JSON GET /api/config THEN 403."""
        client.post("/login", data={"username": "admin", "password": "admin"})
        response = client.get(
            "/api/config",
            headers={"Accept": "application/json"},
            content_type="application/json",
        )
        assert response.status_code == 403
        data = response.get_json()
        assert "cambiar" in data["error"].lower() or "change" in data["error"].lower()
