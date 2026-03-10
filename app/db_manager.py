"""
DatabaseManager — Unified SQLite database for the PMP 450i Analyzer.

Manages schema creation, migrations, and connection lifecycle for all 5 tables:
users, towers, scans, audit_logs, config_verifications.

Specification: change-004 specs § S4.1 — Esquema de Base de Datos Unificada
Design:        change-004 design § D4.1 — DatabaseManager
"""

import sqlite3
import logging
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

# ── Schema SQL ────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',
    must_change_password INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS towers (
    tower_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    location TEXT,
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scans (
    id TEXT PRIMARY KEY,
    tower_id TEXT REFERENCES towers(tower_id) ON DELETE SET NULL,
    user_id INTEGER REFERENCES users(id),
    username TEXT NOT NULL,
    ticket_id INTEGER NOT NULL,
    scan_type TEXT NOT NULL DEFAULT 'AP_ONLY',
    status TEXT NOT NULL DEFAULT 'initializing',
    ap_ips TEXT NOT NULL,
    sm_ips TEXT,
    config TEXT,
    results TEXT,
    recommendations TEXT,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    duration_seconds REAL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    username TEXT NOT NULL,
    ticket_id INTEGER,
    action_type TEXT NOT NULL,
    scan_id TEXT REFERENCES scans(id) ON DELETE SET NULL,
    tower_id TEXT REFERENCES towers(tower_id) ON DELETE SET NULL,
    devices TEXT,
    start_timestamp TEXT,
    end_timestamp TEXT,
    duration_seconds REAL,
    result TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS config_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    tower_id TEXT REFERENCES towers(tower_id) ON DELETE SET NULL,
    ap_ip TEXT,
    recommended_freq INTEGER NOT NULL,
    applied_freq INTEGER,
    channel_width INTEGER,
    verified_by INTEGER REFERENCES users(id),
    verified_at TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_scans_tower ON scans(tower_id);
CREATE INDEX IF NOT EXISTS idx_scans_user ON scans(user_id);
CREATE INDEX IF NOT EXISTS idx_scans_status ON scans(status);
CREATE INDEX IF NOT EXISTS idx_scans_started ON scans(started_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action_type);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_scan ON audit_logs(scan_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(start_timestamp);
CREATE INDEX IF NOT EXISTS idx_config_scan ON config_verifications(scan_id);
CREATE INDEX IF NOT EXISTS idx_towers_created ON towers(created_at);
"""


class DatabaseManager:
    """Manages the unified SQLite database (schema, migrations, connections).

    Thread Safety:
        Uses a class-level Lock for DDL operations (schema creation, migrations).
        Each call to get_connection() returns a NEW connection — safe for
        per-thread usage in Flask + background scan threads.
    """

    _lock = Lock()

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_db()

    def get_connection(self) -> sqlite3.Connection:
        """Create a new SQLite connection with WAL mode and FK enforcement.

        Returns:
            sqlite3.Connection with Row factory, WAL journal, and foreign_keys=ON.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_db(self) -> None:
        """Create all tables, indexes, and run migrations if needed."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            conn = self.get_connection()
            try:
                conn.executescript(_SCHEMA_SQL)
                conn.executescript(_INDEX_SQL)
                conn.commit()
            finally:
                conn.close()
        # Run migrations after schema is in place
        self._run_migrations()

    def _run_migrations(self) -> None:
        """Run incremental migrations (idempotent).

        Currently handles:
          - Adding 'role' column to users table if missing (upgrade from change-003).
        """
        with self._lock:
            conn = self.get_connection()
            try:
                columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(users)").fetchall()
                }
                if "role" not in columns:
                    conn.execute(
                        "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'operator'"
                    )
                    # Set existing admin users to role='admin'
                    conn.execute(
                        "UPDATE users SET role = 'admin' WHERE username = 'admin'"
                    )
                    conn.commit()
                    logger.info("Migration: added 'role' column to users table")
            finally:
                conn.close()

    def migrate_from_auth_db(self, auth_db_path: str) -> int:
        """Migrate users from a legacy auth.db (change-003) into the unified DB.

        Args:
            auth_db_path: Path to the old auth.db SQLite file.

        Returns:
            Number of users migrated.

        Notes:
            - Skips users whose username already exists in the unified DB.
            - Sets role='admin' for usernames matching 'admin', else 'operator'.
        """
        auth_path = Path(auth_db_path)
        if not auth_path.exists():
            logger.debug(
                "No legacy auth.db found at %s — skipping migration", auth_db_path
            )
            return 0

        migrated = 0
        with self._lock:
            old_conn = sqlite3.connect(auth_db_path)
            old_conn.row_factory = sqlite3.Row
            try:
                # Verify the old DB has a users table
                tables = old_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
                ).fetchall()
                if not tables:
                    logger.warning("Legacy auth.db has no users table — skipping")
                    return 0

                old_users = old_conn.execute(
                    "SELECT username, password_hash, must_change_password, created_at, last_login FROM users"
                ).fetchall()
            finally:
                old_conn.close()

            conn = self.get_connection()
            try:
                for user in old_users:
                    # Skip if username already exists
                    existing = conn.execute(
                        "SELECT id FROM users WHERE username = ?",
                        (user["username"],),
                    ).fetchone()
                    if existing:
                        continue

                    role = "admin" if user["username"] == "admin" else "operator"
                    conn.execute(
                        """INSERT INTO users
                           (username, password_hash, role, must_change_password, created_at, last_login)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            user["username"],
                            user["password_hash"],
                            role,
                            user["must_change_password"],
                            user["created_at"],
                            user["last_login"],
                        ),
                    )
                    migrated += 1

                conn.commit()
                if migrated:
                    logger.info(
                        "Migrated %d user(s) from legacy auth.db (%s)",
                        migrated,
                        auth_db_path,
                    )
            finally:
                conn.close()

        return migrated
