"""
AuthManager — SQLite-backed user authentication for the PMP 450i Analyzer.

Handles user CRUD, password hashing, session validation, and DB auto-init.
Uses werkzeug.security for password hashing, Python's sqlite3 for DB.

Specification: change-003 specs § S3.3 — User Storage SQLite
Design:        change-003 design § D3.1 — SQLite Schema + auth_manager.py
"""

import sqlite3
import logging
from datetime import datetime, timezone
from threading import Lock
from pathlib import Path

from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages user authentication against a SQLite database."""

    _lock = Lock()

    def __init__(self, db_path: str = "/app/data/auth.db"):
        self.db_path = db_path
        self._ensure_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a new SQLite connection with WAL mode."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_db(self) -> None:
        """Create tables and default admin user if needed."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,
                        must_change_password INTEGER DEFAULT 1,
                        created_at TEXT DEFAULT (datetime('now')),
                        last_login TEXT
                    )
                """)
                # Insert default admin if table is empty
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                if count == 0:
                    conn.execute(
                        "INSERT INTO users (username, password_hash, must_change_password) VALUES (?, ?, 1)",
                        ("admin", generate_password_hash("admin")),
                    )
                    logger.info("Default admin user created (password: admin)")
                conn.commit()
            finally:
                conn.close()

    def authenticate(self, username: str, password: str) -> dict | None:
        """Verify credentials. Returns user dict or None."""
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

    def create_user(
        self, username: str, password: str, must_change: bool = True
    ) -> int:
        """Create a new user. Returns user ID."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(
                    "INSERT INTO users (username, password_hash, must_change_password) VALUES (?, ?, ?)",
                    (username, generate_password_hash(password), int(must_change)),
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
        """List all users (without password hashes)."""
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, username, must_change_password, created_at, last_login FROM users"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
