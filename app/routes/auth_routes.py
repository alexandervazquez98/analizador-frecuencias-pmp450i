"""
app/routes/auth_routes.py — Authentication blueprint for the PMP 450i Analyzer.

Contains login, logout, change-password routes and the login_required / admin_required
decorators. Uses current_app.config to access shared managers.

Design: change-004 design § D4.2 — Flask Blueprint Refactor
Spec:   change-003 specs § S3.1 — User Authentication
"""

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    session,
    redirect,
    url_for,
    current_app,
)
from werkzeug.security import check_password_hash
from functools import wraps
import logging

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


# ==================== DECORADORES DE AUTENTICACION ====================


def login_required(f):
    """Decorator: redirects to /login if no valid session.
    For API routes (Accept: application/json or XHR), returns 401 JSON.
    For routes: if must_change_password, redirects to /change-password.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user"):
            if (
                request.is_json
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            ):
                return jsonify({"error": "No autenticado", "redirect": "/login"}), 401
            return redirect(url_for("auth.login"))
        if session.get("must_change_password"):
            if request.is_json:
                return (
                    jsonify(
                        {
                            "error": "Debe cambiar su contrasena",
                            "redirect": "/change-password",
                        }
                    ),
                    403,
                )
            return redirect(url_for("auth.change_password"))
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    """Decorator: requires admin role after login_required check.
    Returns 403 JSON for API or redirects to login for HTML.
    """

    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            if (
                request.is_json
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            ):
                return (
                    jsonify(
                        {"error": "Acceso denegado: se requiere rol de administrador"}
                    ),
                    403,
                )
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated


# ==================== RUTAS DE AUTENTICACION ====================


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Login page and handler."""
    if request.method == "GET":
        if session.get("user"):
            return redirect(url_for("auth.index"))
        return render_template("login.html")

    # POST
    auth_manager = current_app.config["auth_manager"]
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    user = auth_manager.authenticate(username, password)

    if not user:
        return render_template("login.html", error="Usuario o contrasena incorrectos")

    session["user"] = user["username"]
    session["user_id"] = user["id"]
    session["role"] = user.get("role", "operator")
    session["must_change_password"] = bool(user["must_change_password"])

    if user["must_change_password"]:
        return redirect(url_for("auth.change_password"))

    return redirect(url_for("auth.index"))


@auth_bp.route("/logout")
def logout():
    """Clear session and redirect to login."""
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
def change_password():
    """Force password change on first login or voluntary change."""
    if not session.get("user"):
        return redirect(url_for("auth.login"))

    if request.method == "GET":
        return render_template("change_password.html")

    auth_manager = current_app.config["auth_manager"]
    current = request.form.get("current_password", "")
    new_pwd = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    # Validation
    user = auth_manager.get_user_by_id(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("auth.login"))

    if not check_password_hash(user["password_hash"], current):
        return render_template(
            "change_password.html", error="Contrasena actual incorrecta"
        )
    if new_pwd != confirm:
        return render_template(
            "change_password.html", error="Las contrasenas no coinciden"
        )
    if len(new_pwd) < 6:
        return render_template(
            "change_password.html",
            error="La contrasena debe tener al menos 6 caracteres",
        )
    if current == new_pwd:
        return render_template(
            "change_password.html", error="La nueva contrasena debe ser diferente"
        )

    auth_manager.change_password(session["user_id"], new_pwd)
    session["must_change_password"] = False
    return redirect(url_for("auth.index"))


# ---- Protected Routes ----


@auth_bp.route("/")
@login_required
def index():
    """Pagina principal"""
    return render_template("index.html")


@auth_bp.route("/static/<path:path>")
def send_static(path):
    """Servir archivos estaticos"""
    from flask import send_from_directory

    return send_from_directory("../static", path)
