"""
tests/test_auth_manager.py — Unit tests for AuthManager (SQLite-backed auth).

Specification: change-003 specs § S3.3 — User Storage SQLite
              change-004 specs § S4.1 — Esquema de Base de Datos Unificada
Design:        change-004 design § D4.1 — DatabaseManager + AuthManager refactor

Tests:
  1. DB initialization: table creation, default admin user
  2. Authentication: valid/invalid credentials, last_login update
  3. Password change: hash update, must_change_password flag cleared
  4. User CRUD: create, delete, list, duplicate prevention
  5. Safety: cannot delete last user
  6. Roles: create_user with role, update_role, role in authenticate/list (change-004)
  7. reset_password: admin-initiated password reset (change-004)
"""

import sqlite3
import pytest
from werkzeug.security import check_password_hash


class TestEnsureDB:
    """Tests for _ensure_db() — DB initialization and default user creation."""

    def test_creates_users_table(self, auth_db):
        """GIVEN a fresh DB WHEN AuthManager initializes THEN users table exists."""
        manager, db_path = auth_db
        conn = sqlite3.connect(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1

    def test_creates_default_admin(self, auth_db):
        """GIVEN a fresh DB WHEN AuthManager initializes THEN admin user exists."""
        manager, _ = auth_db
        users = manager.list_users()
        assert len(users) == 1
        assert users[0]["username"] == "admin"

    def test_default_admin_must_change_password(self, auth_db):
        """GIVEN default admin THEN must_change_password == 1."""
        manager, _ = auth_db
        users = manager.list_users()
        assert users[0]["must_change_password"] == 1

    def test_default_admin_password_is_admin(self, auth_db):
        """GIVEN default admin THEN password is 'admin'."""
        manager, _ = auth_db
        user = manager.authenticate("admin", "admin")
        assert user is not None
        assert user["username"] == "admin"

    def test_reinit_does_not_duplicate_admin(self, auth_db):
        """GIVEN an already-initialized DB WHEN _ensure_db runs again THEN no duplicate."""
        manager, db_path = auth_db
        # Re-init
        manager._ensure_db()
        users = manager.list_users()
        assert len(users) == 1

    def test_wal_mode_enabled(self, auth_db):
        """GIVEN AuthManager WHEN connecting THEN WAL journal mode is set."""
        manager, db_path = auth_db
        conn = sqlite3.connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


class TestAuthenticate:
    """Tests for authenticate() — credential verification."""

    def test_valid_credentials_returns_user_dict(self, auth_db):
        """GIVEN valid username/password WHEN authenticate THEN returns user dict."""
        manager, _ = auth_db
        user = manager.authenticate("admin", "admin")
        assert user is not None
        assert user["username"] == "admin"
        assert "id" in user
        assert "password_hash" in user

    def test_invalid_password_returns_none(self, auth_db):
        """GIVEN valid user, wrong password WHEN authenticate THEN returns None."""
        manager, _ = auth_db
        assert manager.authenticate("admin", "wrongpassword") is None

    def test_nonexistent_user_returns_none(self, auth_db):
        """GIVEN non-existent username WHEN authenticate THEN returns None."""
        manager, _ = auth_db
        assert manager.authenticate("nobody", "admin") is None

    def test_empty_credentials_returns_none(self, auth_db):
        """GIVEN empty strings WHEN authenticate THEN returns None."""
        manager, _ = auth_db
        assert manager.authenticate("", "") is None

    def test_authenticate_updates_last_login(self, auth_db):
        """GIVEN valid credentials WHEN authenticate THEN last_login is updated in DB."""
        manager, _ = auth_db
        manager.authenticate("admin", "admin")
        # authenticate() returns the row fetched BEFORE the update;
        # verify via get_user_by_id that last_login was actually persisted.
        user = manager.get_user_by_id(1)
        assert user["last_login"] is not None

    def test_authenticate_returns_role(self, auth_db):
        """GIVEN valid credentials WHEN authenticate THEN result includes 'role' key."""
        manager, _ = auth_db
        user = manager.authenticate("admin", "admin")
        assert user is not None
        assert "role" in user
        assert user["role"] == "admin"


class TestChangePassword:
    """Tests for change_password() — hash update and flag clearing."""

    def test_change_password_clears_flag(self, auth_db):
        """GIVEN admin with must_change=1 WHEN change_password THEN flag cleared."""
        manager, _ = auth_db
        result = manager.change_password(1, "newpassword123")
        assert result is True
        user = manager.get_user_by_id(1)
        assert user["must_change_password"] == 0

    def test_change_password_updates_hash(self, auth_db):
        """GIVEN admin WHEN change_password('newpwd') THEN old password fails, new works."""
        manager, _ = auth_db
        manager.change_password(1, "newpassword123")
        assert manager.authenticate("admin", "admin") is None
        assert manager.authenticate("admin", "newpassword123") is not None

    def test_change_password_new_hash_is_valid(self, auth_db):
        """GIVEN change_password WHEN inspecting DB THEN hash verifies correctly."""
        manager, _ = auth_db
        manager.change_password(1, "securepass")
        user = manager.get_user_by_id(1)
        assert check_password_hash(user["password_hash"], "securepass")


class TestResetPassword:
    """Tests for reset_password() — admin-initiated password reset (change-004)."""

    def test_reset_password_sets_must_change(self, auth_db):
        """GIVEN user with must_change=0 WHEN reset_password THEN must_change=1."""
        manager, _ = auth_db
        # First clear the flag
        manager.change_password(1, "securepass")
        user = manager.get_user_by_id(1)
        assert user["must_change_password"] == 0

        # Now reset
        result = manager.reset_password(1)
        assert result is True
        user = manager.get_user_by_id(1)
        assert user["must_change_password"] == 1

    def test_reset_password_default_is_changeme(self, auth_db):
        """GIVEN reset_password() without explicit pwd THEN password is 'changeme'."""
        manager, _ = auth_db
        manager.reset_password(1)
        user = manager.authenticate("admin", "changeme")
        assert user is not None

    def test_reset_password_custom_password(self, auth_db):
        """GIVEN reset_password(new_password='newpwd') THEN password is 'newpwd'."""
        manager, _ = auth_db
        manager.reset_password(1, new_password="resetme123")
        user = manager.authenticate("admin", "resetme123")
        assert user is not None
        assert user["must_change_password"] == 1

    def test_reset_password_nonexistent_user(self, auth_db):
        """GIVEN non-existent user_id WHEN reset_password THEN returns False."""
        manager, _ = auth_db
        result = manager.reset_password(999)
        assert result is False


class TestGetUserById:
    """Tests for get_user_by_id()."""

    def test_existing_user(self, auth_db):
        """GIVEN admin exists WHEN get_user_by_id(1) THEN returns user dict."""
        manager, _ = auth_db
        user = manager.get_user_by_id(1)
        assert user is not None
        assert user["username"] == "admin"

    def test_nonexistent_user(self, auth_db):
        """GIVEN no user with id=999 WHEN get_user_by_id(999) THEN returns None."""
        manager, _ = auth_db
        assert manager.get_user_by_id(999) is None


class TestCreateUser:
    """Tests for create_user() — new user creation."""

    def test_create_user_returns_id(self, auth_db):
        """GIVEN valid data WHEN create_user THEN returns new user ID."""
        manager, _ = auth_db
        user_id = manager.create_user("newuser", "password123")
        assert isinstance(user_id, int)
        assert user_id > 1  # admin is id=1

    def test_created_user_can_authenticate(self, auth_db):
        """GIVEN newly created user WHEN authenticate THEN succeeds."""
        manager, _ = auth_db
        manager.create_user("testuser", "testpass", must_change=False)
        user = manager.authenticate("testuser", "testpass")
        assert user is not None
        assert user["username"] == "testuser"

    def test_create_user_with_must_change(self, auth_db):
        """GIVEN must_change=True WHEN create_user THEN flag is set."""
        manager, _ = auth_db
        uid = manager.create_user("newuser", "pass", must_change=True)
        user = manager.get_user_by_id(uid)
        assert user["must_change_password"] == 1

    def test_create_user_without_must_change(self, auth_db):
        """GIVEN must_change=False WHEN create_user THEN flag is cleared."""
        manager, _ = auth_db
        uid = manager.create_user("newuser", "pass", must_change=False)
        user = manager.get_user_by_id(uid)
        assert user["must_change_password"] == 0

    def test_duplicate_username_raises_error(self, auth_db):
        """GIVEN 'admin' exists WHEN create_user('admin',...) THEN raises IntegrityError."""
        manager, _ = auth_db
        with pytest.raises(sqlite3.IntegrityError):
            manager.create_user("admin", "anotherpass")

    def test_create_user_default_role_is_operator(self, auth_db):
        """GIVEN no role specified WHEN create_user THEN role is 'operator'."""
        manager, _ = auth_db
        uid = manager.create_user("opuser", "pass")
        user = manager.get_user_by_id(uid)
        assert user["role"] == "operator"

    def test_create_user_with_admin_role(self, auth_db):
        """GIVEN role='admin' WHEN create_user THEN role is 'admin'."""
        manager, _ = auth_db
        uid = manager.create_user("admin2", "pass", role="admin")
        user = manager.get_user_by_id(uid)
        assert user["role"] == "admin"

    def test_create_user_with_operator_role(self, auth_db):
        """GIVEN role='operator' WHEN create_user THEN role is 'operator'."""
        manager, _ = auth_db
        uid = manager.create_user("op2", "pass", role="operator")
        user = manager.get_user_by_id(uid)
        assert user["role"] == "operator"

    def test_create_user_invalid_role_raises_value_error(self, auth_db):
        """GIVEN invalid role WHEN create_user THEN raises ValueError."""
        manager, _ = auth_db
        with pytest.raises(ValueError, match="Invalid role"):
            manager.create_user("baduser", "pass", role="superadmin")


class TestDeleteUser:
    """Tests for delete_user() — user removal with safety checks."""

    def test_delete_existing_user(self, auth_db):
        """GIVEN two users WHEN delete_user THEN returns True and user gone."""
        manager, _ = auth_db
        manager.create_user("todelete", "pass")
        result = manager.delete_user("todelete")
        assert result is True
        assert manager.authenticate("todelete", "pass") is None

    def test_delete_nonexistent_user(self, auth_db):
        """GIVEN no such user WHEN delete_user THEN returns False."""
        manager, _ = auth_db
        # Need 2+ users so we don't hit the last-user guard
        manager.create_user("extra", "pass")
        result = manager.delete_user("ghost")
        assert result is False

    def test_cannot_delete_last_user(self, auth_db):
        """GIVEN only admin exists WHEN delete_user('admin') THEN returns False."""
        manager, _ = auth_db
        result = manager.delete_user("admin")
        assert result is False

    def test_delete_reduces_count(self, auth_db):
        """GIVEN 3 users WHEN delete one THEN count drops to 2."""
        manager, _ = auth_db
        manager.create_user("user1", "pass")
        manager.create_user("user2", "pass")
        assert len(manager.list_users()) == 3
        manager.delete_user("user1")
        assert len(manager.list_users()) == 2


class TestListUsers:
    """Tests for list_users() — listing without password hashes."""

    def test_list_returns_all_users(self, auth_db):
        """GIVEN 3 users WHEN list_users THEN returns 3 entries."""
        manager, _ = auth_db
        manager.create_user("user1", "pass")
        manager.create_user("user2", "pass")
        users = manager.list_users()
        assert len(users) == 3

    def test_list_excludes_password_hash(self, auth_db):
        """GIVEN any user WHEN list_users THEN password_hash not in result."""
        manager, _ = auth_db
        users = manager.list_users()
        for u in users:
            assert "password_hash" not in u

    def test_list_includes_expected_fields(self, auth_db):
        """GIVEN users WHEN list_users THEN each has id, username, must_change, created_at."""
        manager, _ = auth_db
        users = manager.list_users()
        for u in users:
            assert "id" in u
            assert "username" in u
            assert "must_change_password" in u
            assert "created_at" in u

    def test_list_includes_role_field(self, auth_db):
        """GIVEN users WHEN list_users THEN each has 'role' key (change-004)."""
        manager, _ = auth_db
        manager.create_user("op1", "pass", role="operator")
        users = manager.list_users()
        for u in users:
            assert "role" in u
        roles = {u["username"]: u["role"] for u in users}
        assert roles["admin"] == "admin"
        assert roles["op1"] == "operator"


class TestUpdateRole:
    """Tests for update_role() — role management (change-004)."""

    def test_update_role_admin_to_operator(self, auth_db):
        """GIVEN admin user WHEN update_role to operator THEN role is operator."""
        manager, _ = auth_db
        # Create a second admin so we can safely change admin's role
        manager.create_user("admin2", "pass", role="admin")
        result = manager.update_role(1, "operator")
        assert result is True
        user = manager.get_user_by_id(1)
        assert user["role"] == "operator"

    def test_update_role_operator_to_admin(self, auth_db):
        """GIVEN operator user WHEN update_role to admin THEN role is admin."""
        manager, _ = auth_db
        uid = manager.create_user("opuser", "pass", role="operator")
        result = manager.update_role(uid, "admin")
        assert result is True
        user = manager.get_user_by_id(uid)
        assert user["role"] == "admin"

    def test_update_role_invalid_role(self, auth_db):
        """GIVEN valid user WHEN update_role('invalid') THEN raises ValueError."""
        manager, _ = auth_db
        with pytest.raises(ValueError, match="Invalid role"):
            manager.update_role(1, "superuser")

    def test_update_role_nonexistent_user(self, auth_db):
        """GIVEN non-existent user WHEN update_role THEN returns False."""
        manager, _ = auth_db
        result = manager.update_role(999, "admin")
        assert result is False

    def test_update_role_reflected_in_authenticate(self, auth_db):
        """GIVEN user with role changed WHEN authenticate THEN returned dict has new role."""
        manager, _ = auth_db
        uid = manager.create_user("testop", "pass", must_change=False, role="operator")
        manager.update_role(uid, "admin")
        user = manager.authenticate("testop", "pass")
        assert user["role"] == "admin"


class TestDefaultAdminRole:
    """Tests for default admin user having role='admin' (change-004)."""

    def test_default_admin_has_admin_role(self, auth_db):
        """GIVEN fresh DB WHEN AuthManager creates default admin THEN role is 'admin'."""
        manager, _ = auth_db
        user = manager.get_user_by_id(1)
        assert user["role"] == "admin"
