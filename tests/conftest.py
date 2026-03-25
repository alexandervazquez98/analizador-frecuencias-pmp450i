"""
tests/conftest.py — Fixtures compartidos para todos los tests.

Maneja la configuración del entorno de pruebas y mocks necesarios
para que los módulos de la app se importen correctamente en entorno local.
"""

import sys
import os
from unittest.mock import MagicMock

# ── pysnmp stub para Python 3.13 ──────────────────────────────────────────────
# pysnmp 4.4.12 depende de 'asyncore' que fue eliminado en Python 3.13.
# Producción corre en Docker (Python 3.11) donde pysnmp funciona correctamente.
# Para los tests locales en 3.13, stubeamos los submódulos necesarios.
_PYSNMP_MODULES = [
    "pysnmp",
    "pysnmp.hlapi",
    "pysnmp.proto",
    "pysnmp.proto.rfc1902",
    "pysnmp.entity",
    "pysnmp.entity.rfc3413",
    "pysnmp.entity.rfc3413.oneliner",
    "pysnmp.carrier",
    "pysnmp.carrier.asyncore",
    "pysnmp.carrier.asyncore.dgram",
    "pysnmp.carrier.asyncore.dgram.udp",
]

for _mod in _PYSNMP_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

# Asegurar que los nombres usados en el wildcard import de tower_scan funcionen
_pysnmp_hlapi = sys.modules["pysnmp.hlapi"]
for _name in [
    "SnmpEngine", "CommunityData", "UdpTransportTarget",
    "ContextData", "ObjectType", "ObjectIdentity",
    "setCmd", "getCmd",
]:
    if not hasattr(_pysnmp_hlapi, _name):
        setattr(_pysnmp_hlapi, _name, MagicMock())

# Integer32 y OctetString en proto.rfc1902
_rfc1902 = sys.modules["pysnmp.proto.rfc1902"]
for _name in ["Integer32", "OctetString"]:
    if not hasattr(_rfc1902, _name):
        setattr(_rfc1902, _name, MagicMock())
# ──────────────────────────────────────────────────────────────────────────────

# Agregar el directorio raíz al path para imports relativos
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app.audit_manager import AuditManager
from app.db_manager import DatabaseManager

# ── Inyectar atributos pysnmp en app.tower_scan ───────────────────────────────
# 'from pysnmp.hlapi import *' en tower_scan.py no puede exportar nombres desde
# un MagicMock stub (sin __all__ real). Los inyectamos manualmente AQUÍ,
# después del import, para que patch.object(tower_scan_module, 'setCmd') funcione.
import app.tower_scan as _tower_scan_mod

for _attr in ["setCmd", "getCmd", "SnmpEngine", "CommunityData",
              "UdpTransportTarget", "ContextData", "ObjectType", "ObjectIdentity"]:
    if not hasattr(_tower_scan_mod, _attr):
        setattr(_tower_scan_mod, _attr, MagicMock(name=_attr))

for _attr in ["Integer32", "OctetString"]:
    if not hasattr(_tower_scan_mod, _attr):
        setattr(_tower_scan_mod, _attr, MagicMock(name=_attr))
# ─────────────────────────────────────────────────────────────────────────────




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
