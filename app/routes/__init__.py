"""
app/routes/__init__.py — Blueprint registry for the PMP 450i Analyzer.

Registers all route blueprints with the Flask application.

Design: change-004 design § D4.2 — Flask Blueprint Refactor
"""

from .auth_routes import auth_bp
from .scan_routes import scan_bp
from .spectrum_routes import spectrum_bp
from .tower_routes import tower_bp
from .user_routes import user_bp
from .audit_routes import audit_bp
from .config_routes import config_bp


def register_blueprints(app):
    """Register all blueprints with the Flask application.

    Called once during app setup in web_app.py.
    Blueprints have no url_prefix — all URLs remain at root level.
    """
    app.register_blueprint(auth_bp)
    app.register_blueprint(scan_bp)
    app.register_blueprint(spectrum_bp)
    app.register_blueprint(tower_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(audit_bp)
    app.register_blueprint(config_bp)
