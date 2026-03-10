"""
Aplicacion web Flask para Tower Scan Automation
Proporciona API REST e interfaz web para gestionar escaneos

Orchestration-only module (change-004 Blueprint Refactor):
Creates the Flask app, instantiates managers, registers blueprints,
and provides error handlers. All route definitions live in app/routes/.

Backward-compat: re-exports symbols that existing tests import from here.
"""

from app import create_app
from app.audit_manager import AuditManager
from app.auth_manager import AuthManager
from app.config_verification_manager import ConfigVerificationManager
from app.db_manager import DatabaseManager
from app.tower_manager import TowerManager
from app.scan_storage_manager import ScanStorageManager
import logging
import os

# Logger del modulo (configuracion centralizada en app/__init__.py)
logger = logging.getLogger(__name__)

# ==================== APP CREATION ====================

# Crear aplicacion Flask
app = create_app()

# Unified database (change-004): single SQLite DB for all tables
_db_path = os.environ.get("DB_PATH", os.environ.get("AUTH_DB_PATH", "data/analyzer.db"))
db_manager = DatabaseManager(_db_path)

# Migrate users from legacy auth.db if it exists and is a different file
_legacy_auth_path = os.environ.get("AUTH_DB_PATH", "data/auth.db")
if os.path.abspath(_legacy_auth_path) != os.path.abspath(_db_path):
    db_manager.migrate_from_auth_db(_legacy_auth_path)

# Instantiate AuthManager (SQLite-backed user authentication)
auth_manager = AuthManager(db_manager=db_manager)

# Instantiate TowerManager (Tower CRUD operations)
tower_manager = TowerManager(db_manager=db_manager)

# Instantiate ScanStorageManager (SQLite-backed scan persistence)
scan_storage_manager = ScanStorageManager(db_manager)

# Instantiate ConfigVerificationManager (config verification persistence)
config_verification_manager = ConfigVerificationManager(db_manager)

# Store managers in app.config so blueprints can access via current_app
app.config["db_manager"] = db_manager
app.config["auth_manager"] = auth_manager
app.config["tower_manager"] = tower_manager
app.config["scan_storage_manager"] = scan_storage_manager
app.config["config_verification_manager"] = config_verification_manager

# ==================== REGISTER BLUEPRINTS ====================

from app.routes import register_blueprints  # noqa: E402

register_blueprints(app)

# ==================== ERROR HANDLERS ====================


@app.errorhandler(404)
def not_found(error):
    from flask import jsonify

    return jsonify({"error": "Endpoint no encontrado"}), 404


@app.errorhandler(500)
def internal_error(error):
    from flask import jsonify

    logger.error(f"Error interno: {str(error)}")
    return jsonify({"error": "Error interno del servidor"}), 500


# ==================== BACKWARD-COMPAT RE-EXPORTS ====================
# Tests import these symbols from app.web_app — keep them accessible.

from app.routes.auth_routes import login_required  # noqa: E402, F401
from app.routes.scan_routes import (  # noqa: E402, F401
    ScanTask,
    active_scans,
    scan_results,
    STORAGE_FILE,
    get_scan_defaults,
    parse_ip_list,
    requires_audit_ticket,
    get_scan,
    save_scan,
    get_stored_scans,
    load_storage,
    save_storage,
)

# ==================== MAIN ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    logger.info(f"Iniciando servidor en puerto {port}")
    logger.info(f"Acceder a: http://localhost:{port}")

    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
