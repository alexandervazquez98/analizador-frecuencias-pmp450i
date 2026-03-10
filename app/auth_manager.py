"""
AuthManager — SQLite-backed user authentication for the PMP 450i Analyzer.

Handles user CRUD, password hashing, session validation, and role management.
Uses werkzeug.security for password hashing.

Specification: change-003 specs § S3.3 — User Storage SQLite
              change-004 specs § S4.1 — Esquema de Base de Datos Unificada
Design:        change-004 design § D4.1 — DatabaseManager + AuthManager refactor
"""

import sqlite3
import logging
from datetime import datetime, timezone
from threading import Lock
from pathlib import Path

from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

_VALID_ROLES = ("admin", "operator")


class AuthManager:
    """Manages user authentication against a SQLite database.

    Accepts either a DatabaseManager instance (change-004+) or a raw db_path
    (backward-compatible with change-003 callers like conftest re-init).
    """

    _lock = Lock()

    def __init__(self, db_manager=None, *, db_path: str | None = None):
        """Initialize AuthManager.

        Args:
            db_manager: A DatabaseManager instance (preferred, change-004+).
            db_path:    Legacy path to SQLite file. Creates a minimal
                        standalone setup when no db_manager is provided.
                        Provided for backward compatibility with existing tests
                        and the re-init pattern in conftest.py.
        """
        if db_manager is not None:
            self._db_manager = db_manager
        elif db_path is not None:
            # Backward-compatible: create a DatabaseManager on the fly
            from app.db_manager import DatabaseManager

            self._db_manager = DatabaseManager(db_path)
        else:
            raise ValueError("AuthManager requires either db_manager or db_path")

        self._ensure_default_admin()

    # ── Connection delegate ──────────────────────────────────────────

    def _get_connection(self) -> sqlite3.Connection:
        """Delegate connection creation to DatabaseManager."""
        return self._db_manager.get_connection()

    # ── Backward-compat shims ────────────────────────────────────────

    def _ensure_db(self) -> None:
        """No-op — DatabaseManager handles schema creation.

        Kept so that existing code calling ``manager._ensure_db()`` does not
        break (e.g. test_auth_manager.py::test_reinit_does_not_duplicate_admin).
        """
        pass

    # ── Default admin ────────────────────────────────────────────────

    def _ensure_default_admin(self) -> None:
        """Create the default admin user if the users table is empty."""
        with self._lock:
            conn = self._get_connection()
            try:
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                if count == 0:
                    conn.execute(
                        "INSERT INTO users (username, password_hash, role, must_change_password) "
                        "VALUES (?, ?, 'admin', 1)",
                        ("admin", generate_password_hash("admin")),
                    )
                    logger.info("Default admin user created (password: admin)")
                    conn.commit()
            finally:
                conn.close()

    # ── Authentication ───────────────────────────────────────────────

    def authenticate(self, username: str, password: str) -> dict | None:
        """Verify credentials. Returns user dict (including 'role') or None."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row and check_password_hash(row["password_hash"], password):
                # Update last_login
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE users SET last_login = ? WHERE id = ?",
                    (now, row["id"]),
                )
                conn.commit()
                return dict(row)
            return None
        finally:
            conn.close()

    # ── Password management ──────────────────────────────────────────

    def change_password(self, user_id: int, new_password: str) -> bool:
        """Change password and clear must_change_password flag."""
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute(
                    "UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                    (generate_password_hash(new_password), user_id),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def reset_password(self, user_id: int, new_password: str = "changeme") -> bool:
        """Admin-initiated password reset — sets must_change_password=1.

        Args:
            user_id: Target user ID.
            new_password: New password (default 'changeme').

        Returns:
            True if user was found and updated, False otherwise.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                result = conn.execute(
                    "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
                    (generate_password_hash(new_password), user_id),
                )
                conn.commit()
                return result.rowcount > 0
            finally:
                conn.close()

    # ── User queries ─────────────────────────────────────────────────

    def get_user_by_id(self, user_id: int) -> dict | None:
        """Fetch user by ID."""
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ── User CRUD ────────────────────────────────────────────────────

    def create_user(
        self,
        username: str,
        password: str,
        must_change: bool = True,
        role: str = "operator",
    ) -> int:
        """Create a new user. Returns user ID.

        Args:
            username: Unique username.
            password: Plaintext password (will be hashed).
            must_change: Whether user must change password on first login.
            role: 'admin' or 'operator' (default 'operator').

        Raises:
            ValueError: If role is not 'admin' or 'operator'.
            sqlite3.IntegrityError: If username already exists.
        """
        if role not in _VALID_ROLES:
            raise ValueError(f"Invalid role '{role}'. Must be one of: {_VALID_ROLES}")
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(
                    "INSERT INTO users (username, password_hash, role, must_change_password) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        username,
                        generate_password_hash(password),
                        role,
                        int(must_change),
                    ),
                )
                conn.commit()
                return cursor.lastrowid
            finally:
                conn.close()

    def delete_user(self, username: str) -> bool:
        """Delete a user by username. Cannot delete last remaining user."""
        with self._lock:
            conn = self._get_connection()
            try:
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                if count <= 1:
                    return False  # Don't delete last user
                result = conn.execute(
                    "DELETE FROM users WHERE username = ?", (username,)
                )
                conn.commit()
                return result.rowcount > 0
            finally:
                conn.close()

    def list_users(self) -> list[dict]:
        """List all users (without password hashes), including role."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, username, role, must_change_password, created_at, last_login FROM users"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── Role management (new in change-004) ──────────────────────────

    def update_role(self, user_id: int, role: str) -> bool:
        """Update a user's role.

        Args:
            user_id: Target user ID.
            role: 'admin' or 'operator'.

        Returns:
            True if user was found and updated, False otherwise.

        Raises:
            ValueError: If role is not valid.
        """
        if role not in _VALID_ROLES:
            raise ValueError(f"Invalid role '{role}'. Must be one of: {_VALID_ROLES}")
        with self._lock:
            conn = self._get_connection()
            try:
                result = conn.execute(
                    "UPDATE users SET role = ? WHERE id = ?",
                    (role, user_id),
                )
                conn.commit()
                return result.rowcount > 0
            finally:
                conn.close()
