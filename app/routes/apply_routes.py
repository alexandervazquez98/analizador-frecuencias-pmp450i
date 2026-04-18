"""
app/routes/apply_routes.py — Frequency apply API blueprint.

Exposes two endpoints for the manual frequency apply workflow:

  POST /api/apply-frequency
      Apply a frequency to a tower (SM-first → AP-last).
      RBAC: admin and operator only. Viewer → 403.

  GET /api/apply-history/<tower_id>
      Return the apply history for a tower (last 50 records).
      RBAC: all authenticated users.

The heavy lifting is delegated to FrequencyApplyManager. The blueprint
stays thin: validate auth, parse body, call manager, return JSON.

Manager access:
    FrequencyApplyManager is instantiated on demand using the managers
    stored in current_app.config (db_manager, tower_scanner).
    TowerScanner is also stored in app.config['tower_scanner'] if available,
    or instantiated with empty IPs for apply-only use (no scan state needed).

Specification: change-006 spec — Domain 4 (Manual Override API), Domain 7 (RBAC).
Design:        change-006 design — apply_routes.py blueprint.
"""

import logging
import os
from typing import List

from flask import Blueprint, request, jsonify, session, current_app

from app.routes.auth_routes import login_required
from app.freq_apply_manager import FrequencyApplyManager
from app.tower_scan import TowerScanner

logger = logging.getLogger(__name__)

apply_bp = Blueprint("apply", __name__)


# ── RBAC helper ──────────────────────────────────────────────────────────────


def _require_operator_or_admin():
    """Return a 403 JSON response if the current session role is 'viewer'.

    Returns None if access is allowed.
    Matches the inline role-check pattern used in auth_routes (session['role']).
    """
    role = session.get("role", "viewer")
    if role == "viewer":
        return jsonify({"error": "Insufficient permissions"}), 403
    return None


# ── Manager factory ──────────────────────────────────────────────────────────


def _get_freq_apply_manager() -> FrequencyApplyManager:
    """Instantiate FrequencyApplyManager from app.config managers.

    TowerScanner is instantiated with empty AP IPs — sufficient for SET operations
    since the manager does not need scan state, only write_community and pysnmp.
    """
    db_manager = current_app.config["db_manager"]

    # Reuse a cached scanner if available, otherwise build a minimal one
    scanner = current_app.config.get("tower_scanner")
    if scanner is None:
        snmp_communities_raw = os.environ.get("SNMP_COMMUNITIES", "Canopy")
        snmp_communities: List[str] = [
            c.strip() for c in snmp_communities_raw.split(",") if c.strip()
        ]
        write_community = os.environ.get("SNMP_WRITE_COMMUNITY", "").strip() or None
        scanner = TowerScanner(
            ap_ips=[],
            snmp_communities=snmp_communities,
            write_community=write_community,
        )

    return FrequencyApplyManager(db_manager=db_manager, tower_scanner=scanner)


# ── Routes ───────────────────────────────────────────────────────────────────


@apply_bp.route("/api/apply-frequency", methods=["POST"])
@login_required
def apply_frequency():
    """Apply a frequency to a tower via SNMP.

    Body JSON:
        scan_id (str, required)         — completed scan to use as source
        freq_mhz (float, required)      — target frequency in MHz
        tower_id (str, required)        — tower identifier
        channel_width_mhz (float, opt) — channel width in MHz (SET on SMs + AP)
        force (bool, opt, default false)— bypass viability check (admin only)

    Returns:
        200: {success, apply_id, state, freq_khz, sm_results, ap_result, errors}
        400: {error} — missing or invalid fields
        403: {error} — viewer role or non-admin requesting force
        422: {error} — viability gate blocked apply
        500: {error} — unexpected server error
    """
    # ── RBAC check ────────────────────────────────────────────────────────────
    denied = _require_operator_or_admin()
    if denied is not None:
        return denied

    # ── Parse body ────────────────────────────────────────────────────────────
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    scan_id = data.get("scan_id")
    freq_mhz = data.get("freq_mhz")
    # tower_id es OPCIONAL — campo de auditoría, no requerido para el apply SNMP
    tower_id = data.get("tower_id") or None

    if not scan_id:
        return jsonify({"error": "scan_id is required"}), 400
    if freq_mhz is None:
        return jsonify({"error": "freq_mhz is required"}), 400

    try:
        freq_mhz = float(freq_mhz)
    except (TypeError, ValueError):
        return jsonify({"error": "freq_mhz must be a number"}), 400

    channel_width_mhz = data.get("channel_width_mhz")
    force = bool(data.get("force", False))

    # Only admins can use force=True
    if force and session.get("role") != "admin":
        return jsonify({"error": "force=true requires admin role"}), 403

    applied_by = session.get("user", "unknown")

    # ── Execute apply ─────────────────────────────────────────────────────────
    try:
        manager = _get_freq_apply_manager()
        result = manager.run_apply(
            scan_id=scan_id,
            freq_mhz=freq_mhz,
            tower_id=tower_id,
            applied_by=applied_by,
            channel_width_mhz=channel_width_mhz,
            force=force,
        )
        return jsonify(result), 200

    except ValueError as exc:
        msg = str(exc)
        # Viability gate messages are 422; missing scan is also 422
        logger.warning("[apply_frequency] Validation error: %s", msg)
        return jsonify({"error": msg}), 422

    except Exception as exc:
        logger.exception("[apply_frequency] Unexpected error: %s", exc)
        return jsonify({"error": "Apply failed", "detail": str(exc)}), 500


@apply_bp.route("/api/apply-history/<tower_id>", methods=["GET"])
@login_required
def get_apply_history(tower_id: str):
    """Return frequency apply history for a tower.

    URL param:
        tower_id — tower identifier

    Returns:
        200: {applies: [{id, scan_id, freq_khz, freq_mhz, state, applied_by_username,
                         created_at, completed_at, error, sm_results, ap_result}]}
        500: {error}
    """
    try:
        manager = _get_freq_apply_manager()
        applies = manager.get_apply_history(tower_id=tower_id, limit=50)
        return jsonify({"applies": applies}), 200

    except Exception as exc:
        logger.exception("[get_apply_history] tower_id=%s error: %s", tower_id, exc)
        return jsonify(
            {"error": "Failed to retrieve apply history", "detail": str(exc)}
        ), 500
