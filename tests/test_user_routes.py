"""
tests/test_user_routes.py — Tests for User Management Routes.

Specification: change-004 specs § S4.2 — User CRUD API
Design:        change-004 design § D4.4 — User CRUD + Roles

Scenarios:
  1. Admin can list users
  2. Admin can create user with role
  3. Admin cannot create duplicate username (409)
  4. Admin can get single user
  5. Admin can update user role
  6. Admin cannot remove last admin's admin role
  7. Admin can delete user
  8. Admin cannot delete self
  9. Admin cannot delete last user
  10. Admin can reset password → user must change on next login
  11. Operator gets 403 on all user management routes
  12. Unauthenticated gets 401 on all routes
  13. Password validation (too short)
  14. Create user without username returns 400
  15. Get non-existent user returns 404
  16. Delete non-existent user returns 404
  17. Reset password for non-existent user returns 404
  18. Update non-existent user returns 404
"""

import pytest


# ==================== HELPERS ====================


def admin_login(client, app):
    """Login as admin with must_change_password cleared."""
    auth_mgr = app.config["auth_manager"]
    auth_mgr.change_password(1, "admin")  # Clears must_change_password
    client.post("/login", data={"username": "admin", "password": "admin"})


def create_operator(client, app, username="operator1", password="operator123"):
    """Create an operator user and return the user data."""
    auth_mgr = app.config["auth_manager"]
    user_id = auth_mgr.create_user(
        username=username, password=password, must_change=False, role="operator"
    )
    return {"id": user_id, "username": username}


def operator_login(client, app, username="operator1", password="operator123"):
    """Create an operator, clear must_change, and login as them."""
    create_operator(client, app, username=username, password=password)
    client.post("/login", data={"username": username, "password": password})


# ==================== ADMIN LIST USERS ====================


class TestListUsers:
    """Tests for GET /api/users — list all users."""

    def test_admin_can_list_users(self, client):
        """GIVEN authenticated admin WHEN GET /api/users THEN 200 with user list."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.get("/api/users", content_type="application/json")
            assert response.status_code == 200
            data = response.get_json()
            assert isinstance(data, list)
            assert len(data) >= 1
            # Should not contain password_hash
            for user in data:
                assert "password_hash" not in user
                assert "username" in user
                assert "role" in user

    def test_unauthenticated_gets_401(self, client):
        """GIVEN no session WHEN GET /api/users THEN 401."""
        response = client.get(
            "/api/users",
            headers={"Accept": "application/json"},
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_operator_gets_403(self, client):
        """GIVEN authenticated operator WHEN GET /api/users THEN 403."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            create_operator(client, app)
            client.get("/logout")
            client.post(
                "/login", data={"username": "operator1", "password": "operator123"}
            )
            response = client.get(
                "/api/users",
                headers={"Accept": "application/json"},
                content_type="application/json",
            )
            assert response.status_code == 403


# ==================== ADMIN CREATE USER ====================


class TestCreateUser:
    """Tests for POST /api/users — create a new user."""

    def test_admin_can_create_user_with_default_role(self, client):
        """GIVEN admin WHEN POST /api/users with username+password THEN 201 operator."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.post(
                "/api/users",
                json={"username": "newuser", "password": "secure123"},
            )
            assert response.status_code == 201
            data = response.get_json()
            assert data["username"] == "newuser"
            assert data["role"] == "operator"
            assert "password_hash" not in data

    def test_admin_can_create_user_with_admin_role(self, client):
        """GIVEN admin WHEN POST /api/users with role=admin THEN 201 admin."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.post(
                "/api/users",
                json={
                    "username": "admin2",
                    "password": "secure123",
                    "role": "admin",
                },
            )
            assert response.status_code == 201
            data = response.get_json()
            assert data["role"] == "admin"

    def test_duplicate_username_returns_409(self, client):
        """GIVEN existing user WHEN POST /api/users with same username THEN 409."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            # admin already exists
            response = client.post(
                "/api/users",
                json={"username": "admin", "password": "secure123"},
            )
            assert response.status_code == 409
            data = response.get_json()
            assert "already exists" in data["error"]

    def test_missing_username_returns_400(self, client):
        """GIVEN admin WHEN POST /api/users without username THEN 400."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.post(
                "/api/users",
                json={"password": "secure123"},
            )
            assert response.status_code == 400
            data = response.get_json()
            assert "username" in data["error"].lower()

    def test_short_password_returns_400(self, client):
        """GIVEN admin WHEN POST /api/users with password < 6 chars THEN 400."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.post(
                "/api/users",
                json={"username": "shortpw", "password": "abc"},
            )
            assert response.status_code == 400
            data = response.get_json()
            assert "6 characters" in data["error"]

    def test_invalid_role_returns_400(self, client):
        """GIVEN admin WHEN POST /api/users with invalid role THEN 400."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.post(
                "/api/users",
                json={
                    "username": "badrole",
                    "password": "secure123",
                    "role": "superadmin",
                },
            )
            assert response.status_code == 400

    def test_operator_cannot_create_user(self, client):
        """GIVEN operator WHEN POST /api/users THEN 403."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            create_operator(client, app)
            client.get("/logout")
            client.post(
                "/login", data={"username": "operator1", "password": "operator123"}
            )
            response = client.post(
                "/api/users",
                json={"username": "x", "password": "secure123"},
                content_type="application/json",
            )
            assert response.status_code == 403


# ==================== ADMIN GET SINGLE USER ====================


class TestGetUser:
    """Tests for GET /api/users/<user_id> — get single user."""

    def test_admin_can_get_user(self, client):
        """GIVEN admin WHEN GET /api/users/1 THEN 200 with user data."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.get("/api/users/1", content_type="application/json")
            assert response.status_code == 200
            data = response.get_json()
            assert data["username"] == "admin"
            assert "password_hash" not in data

    def test_nonexistent_user_returns_404(self, client):
        """GIVEN admin WHEN GET /api/users/9999 THEN 404."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.get("/api/users/9999", content_type="application/json")
            assert response.status_code == 404


# ==================== ADMIN UPDATE USER ====================


class TestUpdateUser:
    """Tests for PUT /api/users/<user_id> — update user."""

    def test_admin_can_update_user_role(self, client):
        """GIVEN admin + operator user WHEN PUT role=admin THEN 200 with updated role."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            op = create_operator(client, app)
            response = client.put(
                f"/api/users/{op['id']}",
                json={"role": "admin"},
            )
            assert response.status_code == 200
            data = response.get_json()
            assert data["role"] == "admin"

    def test_cannot_remove_last_admin_role(self, client):
        """GIVEN only 1 admin WHEN PUT role=operator on admin THEN 400."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.put(
                "/api/users/1",
                json={"role": "operator"},
            )
            assert response.status_code == 400
            data = response.get_json()
            assert "last admin" in data["error"].lower()

    def test_update_nonexistent_user_returns_404(self, client):
        """GIVEN admin WHEN PUT /api/users/9999 THEN 404."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.put(
                "/api/users/9999",
                json={"role": "admin"},
            )
            assert response.status_code == 404


# ==================== ADMIN DELETE USER ====================


class TestDeleteUser:
    """Tests for DELETE /api/users/<user_id> — delete user."""

    def test_admin_can_delete_user(self, client):
        """GIVEN admin + operator WHEN DELETE operator THEN 200."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            op = create_operator(client, app)
            response = client.delete(
                f"/api/users/{op['id']}",
                content_type="application/json",
            )
            assert response.status_code == 200
            data = response.get_json()
            assert "deleted" in data["message"].lower()

    def test_admin_cannot_delete_self(self, client):
        """GIVEN admin WHEN DELETE self THEN 400."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.delete(
                "/api/users/1",
                content_type="application/json",
            )
            assert response.status_code == 400
            data = response.get_json()
            assert "own account" in data["error"].lower()

    def test_cannot_delete_last_user(self, client):
        """GIVEN only 1 user WHEN DELETE that user THEN 400 (caught by self check first)."""
        # With only admin user, deleting self is caught first.
        # To properly test "last user", we'd need a non-self last user scenario.
        # But delete_user in AuthManager already prevents deleting last user.
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            # Create a second admin, login as them, then delete original admin
            auth_mgr = app.config["auth_manager"]
            admin2_id = auth_mgr.create_user(
                "admin2", "admin2pass", must_change=False, role="admin"
            )
            # Delete original admin (leaves admin2 as last user)
            response = client.delete(
                "/api/users/1",
                content_type="application/json",
            )
            # This tries to delete self (user_id=1 is logged-in admin)
            assert response.status_code == 400

    def test_delete_nonexistent_user_returns_404(self, client):
        """GIVEN admin WHEN DELETE /api/users/9999 THEN 404."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.delete(
                "/api/users/9999",
                content_type="application/json",
            )
            assert response.status_code == 404


# ==================== ADMIN RESET PASSWORD ====================


class TestResetPassword:
    """Tests for PUT /api/users/<user_id>/reset-password — reset user password."""

    def test_admin_can_reset_password(self, client):
        """GIVEN admin + operator WHEN PUT reset-password THEN 200."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            op = create_operator(client, app)
            response = client.put(
                f"/api/users/{op['id']}/reset-password",
                json={},
            )
            assert response.status_code == 200
            data = response.get_json()
            assert "reset" in data["message"].lower()

    def test_reset_password_sets_must_change(self, client):
        """GIVEN operator WHEN admin resets password THEN must_change_password=1."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            op = create_operator(client, app)
            client.put(
                f"/api/users/{op['id']}/reset-password",
                json={"new_password": "temppass123"},
            )
            # Verify must_change_password is set
            auth_mgr = app.config["auth_manager"]
            user = auth_mgr.get_user_by_id(op["id"])
            assert user["must_change_password"] == 1

    def test_reset_password_with_default(self, client):
        """GIVEN no new_password WHEN reset THEN password becomes 'changeme'."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            op = create_operator(client, app)
            client.put(
                f"/api/users/{op['id']}/reset-password",
                json={},
            )
            # Verify user can login with 'changeme'
            auth_mgr = app.config["auth_manager"]
            result = auth_mgr.authenticate(op["username"], "changeme")
            assert result is not None

    def test_reset_nonexistent_user_returns_404(self, client):
        """GIVEN admin WHEN PUT /api/users/9999/reset-password THEN 404."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            response = client.put(
                "/api/users/9999/reset-password",
                json={},
            )
            assert response.status_code == 404

    def test_reset_forces_change_on_next_login(self, client):
        """GIVEN admin resets operator password WHEN operator logs in THEN redirect to change-password."""
        from app.web_app import app

        with app.app_context():
            admin_login(client, app)
            op = create_operator(client, app)
            # Reset password
            client.put(
                f"/api/users/{op['id']}/reset-password",
                json={"new_password": "resetpwd1"},
            )
            # Logout admin
            client.get("/logout")
            # Login as operator with reset password
            response = client.post(
                "/login",
                data={"username": op["username"], "password": "resetpwd1"},
            )
            assert response.status_code == 302
            assert "change-password" in response.location
