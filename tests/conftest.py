"""
tests/conftest.py — Fixtures compartidos para todos los tests.

Maneja la configuración del entorno de pruebas y mocks necesarios
para que los módulos de la app se importen correctamente en entorno local.
"""

import sys
import os

# Agregar el directorio raíz al path para imports relativos
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app.audit_manager import AuditManager
from app.db_manager import DatabaseManager


@pytest.fixture
def db_manager(tmp_path):
    """Creates a DatabaseManager with a temporary unified SQLite database."""
    db_path = str(tmp_path / "test_analyzer.db")
    return DatabaseManager(db_path)


@pytest.fixture
def auth_db(tmp_path):
    """Creates an AuthManager with a temporary SQLite database.
    Returns (auth_manager, db_path) tuple.

    Updated for change-004: uses DatabaseManager internally.
    """
    db_path = str(tmp_path / "test_auth.db")
    # Set env var for backward compat with any code reading AUTH_DB_PATH
    os.environ["AUTH_DB_PATH"] = db_path

    from app.db_manager import DatabaseManager
    from app.auth_manager import AuthManager

    dm = DatabaseManager(db_path)
    manager = AuthManager(db_manager=dm)
    return manager, db_path


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Creates a Flask test client with auth DB and audit logs in tmp_path.
    The client is NOT logged in — use `login_client()` or `authenticated_client`.
    """
    # Redirect auth DB to temp
    db_path = str(tmp_path / "test_analyzer.db")
    monkeypatch.setenv("AUTH_DB_PATH", db_path)

    # Redirect audit log to temp
    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)

    from app.db_manager import DatabaseManager
    from app.web_app import app, auth_manager

    # Re-initialize auth_manager with the temp DB
    dm = DatabaseManager(db_path)
    auth_manager.__init__(db_manager=dm)
    app.config["auth_manager"] = auth_manager

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def authenticated_client(tmp_path, monkeypatch):
    """Creates a Flask test client already logged in as admin.
    The default admin/admin user has must_change_password cleared for convenience.
    """
    db_path = str(tmp_path / "test_analyzer.db")
    monkeypatch.setenv("AUTH_DB_PATH", db_path)

    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)

    from app.db_manager import DatabaseManager
    from app.web_app import app, auth_manager

    # Re-initialize auth_manager with the temp DB
    dm = DatabaseManager(db_path)
    auth_manager.__init__(db_manager=dm)
    app.config["auth_manager"] = auth_manager

    # Clear must_change_password for admin so tests don't get redirected
    auth_manager.change_password(1, "admin")  # Re-hash same pwd, clears flag

    app.config["TESTING"] = True
    with app.test_client() as c:
        # Login
        c.post("/login", data={"username": "admin", "password": "admin"})
        yield c


def login_client(client, username="admin", password="admin"):
    """Helper: logs a test client in via POST /login."""
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
