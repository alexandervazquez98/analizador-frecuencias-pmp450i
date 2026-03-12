"""
tests/test_db_manager.py — Unit tests for DatabaseManager (unified SQLite DB).

Specification: change-004 specs § S4.1 — Esquema de Base de Datos Unificada
Design:        change-004 design § D4.1 — DatabaseManager

Tests:
  1. Schema creation: all 5 tables created with correct columns
  2. Foreign key constraints: PRAGMA foreign_keys=ON enforced
  3. WAL mode enabled
  4. Migration from legacy auth.db
  5. Role column migration for pre-change-004 DBs
  6. Connection factory: Row factory, new connection per call
  7. Index creation
  8. Idempotent re-init
"""

import sqlite3
import pytest
from werkzeug.security import generate_password_hash

from app.db_manager import DatabaseManager


# ── Schema creation tests ─────────────────────────────────────────────


class TestSchemaCreation:
    """Tests for _ensure_db() — table and index creation."""

    def test_creates_all_five_tables(self, db_manager):
        """GIVEN fresh DB WHEN DatabaseManager initializes THEN all 5 tables exist."""
        conn = sqlite3.connect(db_manager.db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        conn.close()
        assert tables == {
            "users",
            "towers",
            "scans",
            "audit_logs",
            "config_verifications",
        }

    def test_users_table_has_role_column(self, db_manager):
        """GIVEN fresh DB THEN users table has 'role' column."""
        conn = db_manager.get_connection()
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        conn.close()
        assert "role" in columns

    def test_users_table_has_expected_columns(self, db_manager):
        """GIVEN fresh DB THEN users table has all expected columns."""
        conn = db_manager.get_connection()
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        conn.close()
        expected = {
            "id",
            "username",
            "password_hash",
            "role",
            "must_change_password",
            "created_at",
            "last_login",
        }
        assert columns == expected

    def test_towers_table_has_expected_columns(self, db_manager):
        """GIVEN fresh DB THEN towers table has all expected columns."""
        conn = db_manager.get_connection()
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(towers)").fetchall()
        }
        conn.close()
        expected = {
            "tower_id",
            "name",
            "location",
            "notes",
            "created_by",
            "created_at",
            "updated_at",
        }
        assert columns == expected

    def test_scans_table_has_expected_columns(self, db_manager):
        """GIVEN fresh DB THEN scans table has all expected columns."""
        conn = db_manager.get_connection()
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(scans)").fetchall()
        }
        conn.close()
        expected = {
            "id",
            "tower_id",
            "user_id",
            "username",
            "ticket_id",
            "scan_type",
            "status",
            "ap_ips",
            "sm_ips",
            "config",
            "results",
            "recommendations",
            "logs",
            "started_at",
            "completed_at",
            "duration_seconds",
            "error",
        }
        assert columns == expected

    def test_audit_logs_table_has_expected_columns(self, db_manager):
        """GIVEN fresh DB THEN audit_logs table has all expected columns."""
        conn = db_manager.get_connection()
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(audit_logs)").fetchall()
        }
        conn.close()
        expected = {
            "id",
            "user_id",
            "username",
            "ticket_id",
            "action_type",
            "scan_id",
            "tower_id",
            "devices",
            "start_timestamp",
            "end_timestamp",
            "duration_seconds",
            "result",
            "details",
        }
        assert columns == expected

    def test_config_verifications_table_has_expected_columns(self, db_manager):
        """GIVEN fresh DB THEN config_verifications table has all expected columns."""
        conn = db_manager.get_connection()
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(config_verifications)"
            ).fetchall()
        }
        conn.close()
        expected = {
            "id",
            "scan_id",
            "tower_id",
            "ap_ip",
            "recommended_freq",
            "applied_freq",
            "channel_width",
            "verified_by",
            "verified_at",
            "notes",
            "created_at",
        }
        assert columns == expected

    def test_indexes_created(self, db_manager):
        """GIVEN fresh DB THEN expected indexes exist."""
        conn = sqlite3.connect(db_manager.db_path)
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
            ).fetchall()
        }
        conn.close()
        expected = {
            "idx_scans_tower",
            "idx_scans_user",
            "idx_scans_status",
            "idx_scans_started",
            "idx_audit_action",
            "idx_audit_user",
            "idx_audit_scan",
            "idx_audit_timestamp",
            "idx_config_scan",
            "idx_towers_created",
        }
        assert expected.issubset(indexes)

    def test_reinit_is_idempotent(self, db_manager):
        """GIVEN initialized DB WHEN _ensure_db called again THEN no error or duplication."""
        # Should not raise
        db_manager._ensure_db()
        conn = sqlite3.connect(db_manager.db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        conn.close()
        assert len(tables) == 5


# ── Connection tests ─────────────────────────────────────────────────


class TestGetConnection:
    """Tests for get_connection() — connection properties."""

    def test_returns_row_factory(self, db_manager):
        """GIVEN get_connection() THEN row_factory is sqlite3.Row."""
        conn = db_manager.get_connection()
        assert conn.row_factory is sqlite3.Row
        conn.close()

    def test_wal_mode_enabled(self, db_manager):
        """GIVEN get_connection() THEN journal_mode is WAL."""
        conn = db_manager.get_connection()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_foreign_keys_enabled(self, db_manager):
        """GIVEN get_connection() THEN foreign_keys is ON."""
        conn = db_manager.get_connection()
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        conn.close()
        assert fk == 1

    def test_returns_new_connection_each_call(self, db_manager):
        """GIVEN two calls to get_connection() THEN they are different objects."""
        conn1 = db_manager.get_connection()
        conn2 = db_manager.get_connection()
        assert conn1 is not conn2
        conn1.close()
        conn2.close()


# ── Foreign key constraint tests ─────────────────────────────────────


class TestForeignKeyConstraints:
    """Tests for FK enforcement via PRAGMA foreign_keys=ON."""

    def test_scans_fk_rejects_invalid_user_id(self, db_manager):
        """GIVEN FK enabled WHEN inserting scan with non-existent user_id THEN FK violation."""
        conn = db_manager.get_connection()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO scans (id, user_id, username, ticket_id, ap_ips)
                   VALUES ('scan-1', 9999, 'ghost', 1, '1.2.3.4')"""
            )
        conn.close()

    def test_config_verifications_fk_rejects_invalid_scan_id(self, db_manager):
        """GIVEN FK enabled WHEN inserting config_verification with bad scan_id THEN FK violation."""
        conn = db_manager.get_connection()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO config_verifications (scan_id, recommended_freq)
                   VALUES ('nonexistent-scan', 5300)"""
            )
        conn.close()


# ── Migration tests ──────────────────────────────────────────────────


class TestMigrateFromAuthDB:
    """Tests for migrate_from_auth_db() — legacy auth.db migration."""

    def test_migrate_users_from_auth_db(self, tmp_path):
        """GIVEN legacy auth.db with 2 users WHEN migrate THEN users appear in unified DB."""
        # Create legacy auth.db
        auth_db_path = str(tmp_path / "old_auth.db")
        conn = sqlite3.connect(auth_db_path)
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                must_change_password INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                last_login TEXT
            )
        """)
        conn.execute(
            "INSERT INTO users (username, password_hash, must_change_password) VALUES (?, ?, 1)",
            ("admin", generate_password_hash("admin")),
        )
        conn.execute(
            "INSERT INTO users (username, password_hash, must_change_password) VALUES (?, ?, 0)",
            ("operator1", generate_password_hash("pass")),
        )
        conn.commit()
        conn.close()

        # Create unified DB and migrate
        db_path = str(tmp_path / "unified.db")
        dm = DatabaseManager(db_path)
        migrated = dm.migrate_from_auth_db(auth_db_path)

        # admin already exists (created by _ensure_default_admin via AuthManager)
        # but DatabaseManager alone doesn't create admin, so both should migrate
        # Actually, DatabaseManager doesn't create admin. So both should be migrated.
        # Wait — dm._ensure_db() runs in __init__, which creates tables but NOT users.
        # So both users from auth.db should be inserted.
        assert migrated == 2

        new_conn = dm.get_connection()
        users = new_conn.execute(
            "SELECT username, role FROM users ORDER BY id"
        ).fetchall()
        new_conn.close()
        assert len(users) == 2
        assert dict(users[0])["username"] == "admin"
        assert dict(users[0])["role"] == "admin"
        assert dict(users[1])["username"] == "operator1"
        assert dict(users[1])["role"] == "operator"

    def test_migrate_skips_existing_users(self, tmp_path):
        """GIVEN user already in unified DB WHEN migrate THEN skips duplicate."""
        # Create legacy auth.db
        auth_db_path = str(tmp_path / "old_auth.db")
        conn = sqlite3.connect(auth_db_path)
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                must_change_password INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                last_login TEXT
            )
        """)
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("admin", generate_password_hash("admin")),
        )
        conn.commit()
        conn.close()

        # Create unified DB and insert admin first
        db_path = str(tmp_path / "unified.db")
        dm = DatabaseManager(db_path)
        new_conn = dm.get_connection()
        new_conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
            ("admin", generate_password_hash("admin")),
        )
        new_conn.commit()
        new_conn.close()

        migrated = dm.migrate_from_auth_db(auth_db_path)
        assert migrated == 0

    def test_migrate_nonexistent_file_returns_zero(self, tmp_path):
        """GIVEN non-existent auth.db path WHEN migrate THEN returns 0."""
        db_path = str(tmp_path / "unified.db")
        dm = DatabaseManager(db_path)
        migrated = dm.migrate_from_auth_db(str(tmp_path / "does_not_exist.db"))
        assert migrated == 0

    def test_migrate_db_without_users_table(self, tmp_path):
        """GIVEN auth.db with no users table WHEN migrate THEN returns 0."""
        auth_db_path = str(tmp_path / "bad_auth.db")
        conn = sqlite3.connect(auth_db_path)
        conn.execute("CREATE TABLE other (id INTEGER)")
        conn.commit()
        conn.close()

        db_path = str(tmp_path / "unified.db")
        dm = DatabaseManager(db_path)
        migrated = dm.migrate_from_auth_db(auth_db_path)
        assert migrated == 0


# ── Role migration tests ─────────────────────────────────────────────


class TestRoleMigration:
    """Tests for _run_migrations() — adding role column to legacy DBs."""

    def test_adds_role_column_to_legacy_db(self, tmp_path):
        """GIVEN a users table WITHOUT role column WHEN DatabaseManager inits THEN role column added."""
        db_path = str(tmp_path / "legacy.db")
        # Create a legacy schema without role column
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                must_change_password INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                last_login TEXT
            )
        """)
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES ('admin', 'hash123')"
        )
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES ('operator1', 'hash456')"
        )
        conn.commit()
        conn.close()

        # Now init DatabaseManager — it should add role column and set admin's role
        dm = DatabaseManager(db_path)
        new_conn = dm.get_connection()
        columns = {
            row[1] for row in new_conn.execute("PRAGMA table_info(users)").fetchall()
        }
        assert "role" in columns

        admin = new_conn.execute(
            "SELECT role FROM users WHERE username='admin'"
        ).fetchone()
        assert admin["role"] == "admin"

        op = new_conn.execute(
            "SELECT role FROM users WHERE username='operator1'"
        ).fetchone()
        assert op["role"] == "operator"
        new_conn.close()
