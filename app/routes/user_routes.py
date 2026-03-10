"""
app/routes/user_routes.py — User management blueprint for the PMP 450i Analyzer.

Admin-only CRUD routes for user management: list, create, get, update, delete,
and password reset.

Design: change-004 design § D4.4 — User CRUD + Roles
Spec:   change-004 specs § S4.2 — User CRUD API
"""

from flask import (
    Blueprint,
    request,
    jsonify,
    session,
    current_app,
)
from app.routes.auth_routes import admin_required
import sqlite3
import logging

logger = logging.getLogger(__name__)

user_bp = Blueprint("users", __name__)

_VALID_ROLES = ("admin", "operator")


# ==================== USER MANAGEMENT ROUTES ====================


@user_bp.route("/api/users", methods=["GET"])
@admin_required
def list_users():
    """List all users (without password_hash). Admin only."""
    auth_manager = current_app.config["auth_manager"]
    users = auth_manager.list_users()
    return jsonify(users), 200


@user_bp.route("/api/users", methods=["POST"])
@admin_required
def create_user():
    """Create a new user. Admin only.

    Body: {username, password, role?, must_change_password?}
    Returns 201 with created user (no password_hash).
    Returns 409 if username already exists.
    """
    auth_manager = current_app.config["auth_manager"]
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Request body required"}), 400

    username = (data.get("username") or "").strip()
    password = data.get("password", "")
    role = data.get("role", "operator")
    must_change = data.get("must_change_password", True)

    # Validation
    if not username:
        return jsonify({"error": "Username is required"}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    if role not in _VALID_ROLES:
        return jsonify(
            {"error": f"Role must be one of: {', '.join(_VALID_ROLES)}"}
        ), 400

    try:
        user_id = auth_manager.create_user(
            username=username,
            password=password,
            must_change=bool(must_change),
            role=role,
        )
    except sqlite3.IntegrityError:
        return jsonify({"error": f"Username '{username}' already exists"}), 409

    # Return the created user (without password_hash)
    user = auth_manager.get_user_by_id(user_id)
    if user:
        user.pop("password_hash", None)
    return jsonify(user), 201


@user_bp.route("/api/users/<int:user_id>", methods=["GET"])
@admin_required
def get_user(user_id):
    """Get a single user by ID. Admin only. Returns 404 if not found."""
    auth_manager = current_app.config["auth_manager"]
    user = auth_manager.get_user_by_id(user_id)

    if not user:
        return jsonify({"error": "User not found"}), 404

    user.pop("password_hash", None)
    return jsonify(user), 200


@user_bp.route("/api/users/<int:user_id>", methods=["PUT"])
@admin_required
def update_user(user_id):
    """Update user fields (role, username). Admin only.

    Cannot remove admin role from the last admin user.
    Returns 400 if trying to make the last admin into operator.
    Returns 404 if user not found.
    """
    auth_manager = current_app.config["auth_manager"]
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Request body required"}), 400

    user = auth_manager.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Handle role update
    new_role = data.get("role")
    if new_role is not None:
        if new_role not in _VALID_ROLES:
            return jsonify(
                {"error": f"Role must be one of: {', '.join(_VALID_ROLES)}"}
            ), 400

        # Check if this would remove the last admin
        if user["role"] == "admin" and new_role != "admin":
            all_users = auth_manager.list_users()
            admin_count = sum(1 for u in all_users if u["role"] == "admin")
            if admin_count <= 1:
                return jsonify(
                    {"error": "Cannot remove admin role from the last admin user"}
                ), 400

        auth_manager.update_role(user_id, new_role)

    # Return updated user
    updated_user = auth_manager.get_user_by_id(user_id)
    if updated_user:
        updated_user.pop("password_hash", None)
    return jsonify(updated_user), 200


@user_bp.route("/api/users/<int:user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    """Delete a user by ID. Admin only.

    Cannot delete self (session user).
    Cannot delete the last remaining user.
    """
    auth_manager = current_app.config["auth_manager"]

    # Cannot delete self
    if user_id == session.get("user_id"):
        return jsonify({"error": "Cannot delete your own account"}), 400

    user = auth_manager.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Cannot delete last user
    all_users = auth_manager.list_users()
    if len(all_users) <= 1:
        return jsonify({"error": "Cannot delete the last remaining user"}), 400

    # delete_user expects username
    success = auth_manager.delete_user(user["username"])
    if not success:
        return jsonify({"error": "Failed to delete user"}), 400

    return jsonify({"message": f"User '{user['username']}' deleted"}), 200


@user_bp.route("/api/users/<int:user_id>/reset-password", methods=["PUT"])
@admin_required
def reset_user_password(user_id):
    """Reset a user's password. Admin only.

    Body: {new_password?} (default 'changeme')
    Sets must_change_password=1 to force first-login flow.
    """
    auth_manager = current_app.config["auth_manager"]

    user = auth_manager.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json(silent=True) or {}
    new_password = data.get("new_password", "changeme")

    success = auth_manager.reset_password(user_id, new_password)
    if not success:
        return jsonify({"error": "Failed to reset password"}), 400

    return jsonify({"message": f"Password reset for user '{user['username']}'"}), 200
