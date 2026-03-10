"""
app/routes/tower_routes.py — Tower CRUD API blueprint.

Provides RESTful endpoints for managing towers:
  POST   /api/towers           — Create a tower
  GET    /api/towers           — List all towers
  GET    /api/towers/search    — Search towers by query
  GET    /api/towers/<id>      — Get a single tower
  PUT    /api/towers/<id>      — Update a tower
  DELETE /api/towers/<id>      — Delete a tower (admin only)

Specification: change-004 specs § S4.5 — Tower CRUD
Design:        change-004 design § D4.5 — TowerManager + Tower Routes
"""

import sqlite3
from flask import Blueprint, request, jsonify, current_app

from app.routes.auth_routes import login_required, admin_required
from app.tower_manager import TowerValidationError

tower_bp = Blueprint("towers", __name__)


@tower_bp.route("/api/towers", methods=["POST"])
@login_required
def create_tower():
    """Create a new tower.

    Body JSON: {tower_id, name, location?, notes?}
    Returns 201 with tower dict on success.
    Returns 400 on validation error, 409 on duplicate tower_id.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    tower_id = data.get("tower_id")
    name = data.get("name")

    if not tower_id or not name:
        return jsonify({"error": "tower_id and name are required"}), 400

    tower_manager = current_app.config["tower_manager"]
    try:
        tower = tower_manager.create(
            tower_id=tower_id,
            name=name,
            location=data.get("location"),
            notes=data.get("notes"),
            created_by=None,  # Could use session["user_id"] if needed
        )
        return jsonify(tower), 201
    except TowerValidationError as e:
        return jsonify({"error": str(e)}), 400
    except sqlite3.IntegrityError:
        return jsonify(
            {"error": f"Tower ID '{tower_id.strip().upper()}' already exists"}
        ), 409


@tower_bp.route("/api/towers", methods=["GET"])
@login_required
def list_towers():
    """List all towers.

    Returns 200 with list of tower dicts.
    """
    tower_manager = current_app.config["tower_manager"]
    towers = tower_manager.list_all()
    return jsonify(towers), 200


@tower_bp.route("/api/towers/search", methods=["GET"])
@login_required
def search_towers():
    """Search towers by query string.

    Query param: q=<search term>
    Returns 200 with list of matching tower dicts.
    Returns 400 if q parameter is missing.
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    tower_manager = current_app.config["tower_manager"]
    results = tower_manager.search(query)
    return jsonify(results), 200


@tower_bp.route("/api/towers/<tower_id>", methods=["GET"])
@login_required
def get_tower(tower_id):
    """Get a single tower by ID.

    Returns 200 with tower dict, or 404 if not found.
    Returns 400 on invalid tower_id format.
    """
    tower_manager = current_app.config["tower_manager"]
    try:
        tower = tower_manager.get_by_id(tower_id)
    except TowerValidationError as e:
        return jsonify({"error": str(e)}), 400

    if tower is None:
        return jsonify({"error": f"Tower '{tower_id}' not found"}), 404
    return jsonify(tower), 200


@tower_bp.route("/api/towers/<tower_id>", methods=["PUT"])
@login_required
def update_tower(tower_id):
    """Update a tower's fields.

    Body JSON: {name?, location?, notes?}
    Returns 200 with updated tower dict.
    Returns 404 if tower not found, 400 on validation error.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    tower_manager = current_app.config["tower_manager"]
    try:
        tower = tower_manager.update(
            tower_id=tower_id,
            name=data.get("name"),
            location=data.get("location"),
            notes=data.get("notes"),
        )
    except TowerValidationError as e:
        return jsonify({"error": str(e)}), 400

    if tower is None:
        return jsonify({"error": f"Tower '{tower_id}' not found"}), 404
    return jsonify(tower), 200


@tower_bp.route("/api/towers/<tower_id>", methods=["DELETE"])
@admin_required
def delete_tower(tower_id):
    """Delete a tower (admin only).

    Returns 200 on success, 404 if tower not found, 400 on validation error.
    """
    tower_manager = current_app.config["tower_manager"]
    try:
        deleted = tower_manager.delete(tower_id)
    except TowerValidationError as e:
        return jsonify({"error": str(e)}), 400

    if not deleted:
        return jsonify({"error": f"Tower '{tower_id}' not found"}), 404
    return jsonify({"message": f"Tower '{tower_id}' deleted"}), 200
