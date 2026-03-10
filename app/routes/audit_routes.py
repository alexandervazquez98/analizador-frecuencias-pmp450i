"""
app/routes/audit_routes.py — Endpoints de consulta de logs de auditoría.

Proporciona endpoints GET (read-only) para consultar la tabla audit_logs
persistida por AuditManagerV2.

Especificación: change-004 specs § S4.6 — Motor de Auditoría v2
Diseño:        change-004 design § D4.6 — AuditManagerV2
"""

import logging
from flask import Blueprint, jsonify, request, current_app

from app.routes.auth_routes import login_required
from app.audit_manager_v2 import AuditManagerV2

logger = logging.getLogger(__name__)

audit_bp = Blueprint("audit", __name__)


@audit_bp.route("/api/audit/logs", methods=["GET"])
@login_required
def list_audit_logs():
    """Lista logs de auditoría con filtros y paginación opcionales.

    Query params:
        limit (int): Máximo de registros (default 100, max 500).
        offset (int): Desplazamiento para paginación (default 0).
        username (str): Filtrar por nombre de usuario.
        action_type (str): Filtrar por tipo de acción.

    Returns:
        JSON: {"logs": [...], "total": N}
    """
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
        offset = int(request.args.get("offset", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "Parámetros limit/offset deben ser enteros"}), 400

    username = request.args.get("username") or None
    action_type = request.args.get("action_type") or None

    db_manager = current_app.config["db_manager"]
    logs = AuditManagerV2.get_logs(
        db_manager,
        limit=limit,
        offset=offset,
        username=username,
        action_type=action_type,
    )

    return jsonify({"logs": logs, "total": len(logs)})


@audit_bp.route("/api/audit/logs/<int:log_id>", methods=["GET"])
@login_required
def get_audit_log(log_id: int):
    """Retorna un log de auditoría por id.

    Returns:
        JSON: el registro, o 404 si no existe.
    """
    db_manager = current_app.config["db_manager"]
    log = AuditManagerV2.get_log(db_manager, log_id)

    if log is None:
        return jsonify({"error": f"Log {log_id} no encontrado"}), 404

    return jsonify(log)
