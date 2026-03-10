"""
app/routes/config_routes.py — Endpoints CRUD para verificaciones de configuración.

Proporciona endpoints para crear, listar, obtener, actualizar y eliminar
verificaciones de configuración aplicada post-escaneo.

Especificación: change-004 specs § S4.7 — Verificación de Configuración
"""

import logging
import sqlite3

from flask import Blueprint, jsonify, request, current_app

from app.routes.auth_routes import admin_required, login_required

logger = logging.getLogger(__name__)

config_bp = Blueprint("config", __name__)


# ── Helper ─────────────────────────────────────────────────────────────────


def _get_manager():
    """Shortcut to retrieve the ConfigVerificationManager from app.config."""
    return current_app.config["config_verification_manager"]


# ── Endpoints ──────────────────────────────────────────────────────────────


@config_bp.route("/api/config-verifications", methods=["POST"])
@login_required
def create_verification():
    """Crear una verificación de configuración.

    Body JSON (obligatorios):
        scan_id (str), recommended_freq (int)
    Body JSON (opcionales):
        ap_ip, applied_freq, channel_width, tower_id, notes

    Returns:
        201 + {"id": N, "message": "Verificación creada correctamente"}
        400 si faltan campos obligatorios
        422 si scan_id no existe (FK violation)
    """
    data = request.get_json(silent=True) or {}

    scan_id = data.get("scan_id")
    recommended_freq = data.get("recommended_freq")

    if not scan_id:
        return jsonify({"error": "scan_id es obligatorio"}), 400
    if recommended_freq is None:
        return jsonify({"error": "recommended_freq es obligatorio"}), 400

    manager = _get_manager()
    try:
        verification_id = manager.create_verification(
            scan_id=scan_id,
            recommended_freq=recommended_freq,
            ap_ip=data.get("ap_ip"),
            applied_freq=data.get("applied_freq"),
            channel_width=data.get("channel_width"),
            tower_id=data.get("tower_id"),
            verified_by=data.get("verified_by"),
            notes=data.get("notes"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except sqlite3.IntegrityError:
        return (
            jsonify({"error": f"scan_id '{scan_id}' no existe en la base de datos"}),
            422,
        )

    return (
        jsonify(
            {"id": verification_id, "message": "Verificación creada correctamente"}
        ),
        201,
    )


@config_bp.route("/api/config-verifications", methods=["GET"])
@login_required
def list_verifications():
    """Listar verificaciones con paginación y filtro opcional.

    Query params:
        limit (int): Máximo de registros (default 100).
        offset (int): Desplazamiento para paginación (default 0).
        tower_id (str): Filtrar por torre.

    Returns:
        JSON: {"verifications": [...], "total": N}
    """
    try:
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Parámetros limit/offset deben ser enteros"}), 400

    tower_id = request.args.get("tower_id") or None

    manager = _get_manager()
    verifications = manager.get_all_verifications(
        limit=limit, offset=offset, tower_id=tower_id
    )

    return jsonify({"verifications": verifications, "total": len(verifications)})


@config_bp.route("/api/config-verifications/<int:verification_id>", methods=["GET"])
@login_required
def get_verification(verification_id: int):
    """Obtener una verificación por id.

    Returns:
        JSON con los datos de la verificación, o 404 si no existe.
    """
    manager = _get_manager()
    verification = manager.get_verification(verification_id)

    if verification is None:
        return (
            jsonify({"error": f"Verificación {verification_id} no encontrada"}),
            404,
        )

    return jsonify(verification)


@config_bp.route("/api/scans/<scan_id>/verifications", methods=["GET"])
@login_required
def get_scan_verifications(scan_id: str):
    """Obtener todas las verificaciones de un scan específico.

    Returns:
        JSON: {"verifications": [...], "total": N}
    """
    manager = _get_manager()
    verifications = manager.get_verifications_for_scan(scan_id)

    return jsonify({"verifications": verifications, "total": len(verifications)})


@config_bp.route("/api/config-verifications/<int:verification_id>", methods=["PUT"])
@login_required
def update_verification(verification_id: int):
    """Actualizar una verificación existente.

    Body JSON (todos opcionales):
        applied_freq (int), notes (str), channel_width (int)

    Returns:
        JSON con mensaje de éxito, o 404 si no existe.
    """
    data = request.get_json(silent=True) or {}

    manager = _get_manager()
    updated = manager.update_verification(
        verification_id=verification_id,
        applied_freq=data.get("applied_freq"),
        notes=data.get("notes"),
        channel_width=data.get("channel_width"),
    )

    if not updated:
        return (
            jsonify({"error": f"Verificación {verification_id} no encontrada"}),
            404,
        )

    return jsonify({"message": "Verificación actualizada correctamente"})


@config_bp.route("/api/config-verifications/<int:verification_id>", methods=["DELETE"])
@admin_required
def delete_verification(verification_id: int):
    """Eliminar una verificación. Solo administradores.

    Returns:
        JSON con mensaje de éxito, o 404 si no existe.
    """
    manager = _get_manager()
    deleted = manager.delete_verification(verification_id)

    if not deleted:
        return (
            jsonify({"error": f"Verificación {verification_id} no encontrada"}),
            404,
        )

    return jsonify({"message": "Verificación eliminada correctamente"})
