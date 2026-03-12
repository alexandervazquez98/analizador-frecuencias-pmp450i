"""
app/routes/scan_routes.py — Scan management blueprint for the PMP 450i Analyzer.

Contains scan initiation, status, results, listing, health, config, and cnMaestro routes.
ScanTask is now in app/scan_task.py.
Helpers (parse_ip_list, get_scan_defaults) are now in app/scan_helpers.py.
JSON file storage replaced by ScanStorageManager (SQLite) via app.config.

Design: change-005 design § D4.5 — Scan Module Split
"""

from flask import (
    Blueprint,
    request,
    jsonify,
    session,
    current_app,
)
from app.routes.auth_routes import login_required
from app.scan_task import ScanTask
from app.scan_helpers import parse_ip_list, get_scan_defaults
from app.audit_manager import AuditManager, AuditLogException
from app.cnmaestro_client import CnMaestroClient
from functools import wraps
import threading
import uuid
from datetime import datetime
from typing import Dict
import logging
import os

logger = logging.getLogger(__name__)

scan_bp = Blueprint("scan", __name__)

# ── In-memory store (hot cache for scans active in this process) ───────────
active_scans: Dict[str, Dict] = {}
scan_results: Dict[str, Dict] = {}

# ── Legacy JSON storage symbols (kept for backward compat; they are no-ops) ─
# Some tests import these symbols from web_app / scan_routes.
# They are retained as stubs so imports don't break.
import os as _os
from pathlib import Path as _Path

STORAGE_FILE = _Path(
    _os.environ.get("STORAGE_FILE_PATH", "/tmp/tower_scan_storage.json")
)


def load_storage():
    """Legacy stub — returns empty structure (JSON file storage removed)."""
    return {"active_scans": {}, "scan_results": {}}


def save_storage(data):
    """Legacy stub — no-op (JSON file storage removed)."""
    pass


def get_scan(scan_id: str):
    """Legacy stub — returns None (use ScanStorageManager via app.config instead)."""
    return None


def save_scan(scan_id: str, scan_data: dict):
    """Legacy stub — no-op (use ScanStorageManager via app.config instead)."""
    pass


def get_stored_scans():
    """Legacy stub — returns empty dict (use ScanStorageManager via app.config instead)."""
    return {}


# ==================== DECORADOR DE AUDITORIA ====================


def requires_audit_ticket(f):
    """Decorator that intercepts requests to validate audit credentials.

    Modified for change-003: gets user from session['user'] (primary) with
    fallback to X-Audit-User header for backward-compatible API clients.
    Only ticket_id needs to come from the request (body or header).

    Especificacion: 02_specs.md S2 -- Contrato de Auditoria
    Diseno:       03_design.md D2 -- Intercepcion de Peticiones
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Get user from session (primary) or header (backward compat)
        user = session.get("user")
        if not user:
            user = request.headers.get("X-Audit-User")

        # 2. Get ticket_id from body (primary) or header
        ticket_id = None
        if request.is_json:
            data = request.get_json(silent=True) or {}
            ticket_id = data.get("ticket_id")
        if not ticket_id:
            ticket_id = request.headers.get("X-Ticket-ID")

        # 3. Instanciar AuditManager (lanza AuditLogException si es invalido)
        try:
            audit = AuditManager(user=user, ticket_id=ticket_id)
        except AuditLogException as e:
            logger.warning(f"Auditoria fallida (Seguridad): {str(e)}")
            return jsonify({"error": "Excepción de Seguridad", "message": str(e)}), 403

        # 4. Iniciar el registro de auditoria
        audit.start_transaction()

        # 5. Inyectar el gestor de auditoria en kwargs
        kwargs["audit_manager"] = audit

        try:
            response = f(*args, **kwargs)

            if isinstance(response, tuple):
                status_code = response[1]
            else:
                status_code = getattr(response, "status_code", 200)

            if status_code != 202:
                audit.end_transaction(result_summary="Ejecucion sincrona completada.")

            return response

        except Exception as e:
            audit.end_transaction(result_summary=f"Fallo del sistema: {str(e)}")
            raise

    return decorated_function


# CnMaestro Configuration
CNMAESTO_URL = os.environ.get("CNMAESTRO_URL", "https://10.3.152.206/api/v1")
CNMAESTRO_ID = os.environ.get("CNMAESTRO_ID", "")
CNMAESTRO_SECRET = os.environ.get("CNMAESTRO_SECRET", "")

cnmaestro_client = CnMaestroClient(CNMAESTO_URL, CNMAESTRO_ID, CNMAESTRO_SECRET)


# ==================== RUTAS API ====================


@scan_bp.route("/api/config", methods=["GET"])
@login_required
def get_config():
    """Return scan configuration defaults loaded from .env."""
    defaults = get_scan_defaults()
    return jsonify(
        {
            "snmp_communities": ", ".join(defaults["snmp_communities"]),
            "target_rx_level": defaults["target_rx_level"],
            "min_snr": defaults["min_snr"],
            "max_polarization_diff": defaults["max_polarization_diff"],
            "channel_width": defaults["channel_width"],
        }
    )


@scan_bp.route("/api/scan", methods=["POST"])
@login_required
@requires_audit_ticket
def start_scan(audit_manager=None):
    """Start a new Tower Scan."""
    try:
        data = request.get_json()
        defaults = get_scan_defaults()

        snmp_communities_input = data.get("snmp_community", "")

        ap_ips = data.get("ap_ips", [])
        sm_ips = data.get("sm_ips", [])

        if isinstance(ap_ips, str):
            ap_ips = parse_ip_list(ap_ips)

        if isinstance(sm_ips, str):
            sm_ips = parse_ip_list(sm_ips)

        if not ap_ips:
            return jsonify({"error": "Se requiere al menos una IP de AP"}), 400

        if isinstance(snmp_communities_input, str) and snmp_communities_input.strip():
            snmp_communities = [
                c.strip() for c in snmp_communities_input.split(",") if c.strip()
            ]
        elif isinstance(snmp_communities_input, list) and snmp_communities_input:
            snmp_communities = snmp_communities_input
        else:
            snmp_communities = defaults["snmp_communities"]

        config = data.get("config", {})
        config.setdefault("target_rx_level", defaults["target_rx_level"])
        config.setdefault("min_snr", defaults["min_snr"])
        config.setdefault("max_pol_diff", defaults["max_polarization_diff"])
        config.setdefault("channel_width", defaults["channel_width"])

        scan_id = str(uuid.uuid4())

        # Resolve username / ticket_id from session and audit_manager
        username = session.get("user", "unknown")
        ticket_id_val = 0
        if audit_manager is not None:
            ticket_id_val = int(getattr(audit_manager, "ticket_id", 0) or 0)

        # Get storage_manager from app config (may be None in tests without DB)
        storage_manager = current_app.config.get("scan_storage_manager")

        # Build task with optional storage manager
        task = ScanTask(
            scan_id,
            ap_ips,
            snmp_communities,
            config,
            sm_ips=sm_ips,
            audit_manager=audit_manager,
            storage_manager=storage_manager,
        )

        # Register in in-memory hot cache
        active_scans[scan_id] = {
            "task": task,
            "created_at": datetime.now().isoformat(),
            "ap_ips": ap_ips,
            "sm_ips": sm_ips,
            "snmp_communities": snmp_communities,
            "config": config,
            "status": "started",
            "progress": 0,
            "ticket_id": ticket_id_val,
        }

        # Persist initial scan record to SQLite
        if storage_manager is not None:
            try:
                storage_manager.save_scan(
                    scan_id,
                    {
                        "username": username,
                        "ticket_id": ticket_id_val,
                        "scan_type": "AP_SM_CROSS" if sm_ips else "AP_ONLY",
                        "ap_ips": ap_ips,
                        "sm_ips": sm_ips if sm_ips else None,
                        "config": config,
                        "status": "started",
                    },
                )
            except Exception as exc:
                logger.warning(
                    "[%s] Failed to save initial scan record: %s", scan_id, exc
                )

        thread = threading.Thread(target=task.run_in_thread, daemon=True)
        thread.start()

        logger.info(f"Scan {scan_id} iniciado con {len(ap_ips)} APs")

        return jsonify(
            {
                "scan_id": scan_id,
                "status": "started",
                "message": f"Tower Scan iniciado para {len(ap_ips)} APs"
                + (f" con {len(sm_ips)} SMs (analisis cruzado)" if sm_ips else ""),
                "ap_count": len(ap_ips),
                "sm_count": len(sm_ips),
                "analysis_mode": "AP_SM_CROSS" if sm_ips else "AP_ONLY",
            }
        ), 202

    except Exception as e:
        logger.error(f"Error iniciando scan: {str(e)}")
        return jsonify({"error": str(e)}), 500


@scan_bp.route("/api/status/<scan_id>", methods=["GET"])
@login_required
def get_scan_status(scan_id: str):
    """Get the status of a scan."""
    logger.info(f"GET /api/status/{scan_id}")

    # 1. Check in-memory hot cache first
    if scan_id in active_scans:
        task = active_scans[scan_id]["task"]
        response = {
            "scan_id": scan_id,
            "status": task.status,
            "progress": task.progress,
            "error": task.error,
            "logs": task.logs,
        }
        if task.status == "completed":
            response["results"] = task.results
            logger.info(f"[OK] Devolviendo resultados desde memoria para {scan_id}")
            logger.info(
                f"Results tiene {len(task.results.get('analysis_results', {}))} APs"
            )
        return jsonify(response)

    # 2. Fallback: look up in SQLite storage
    logger.info(f"[INFO] Scan {scan_id} no esta en memoria, buscando en storage...")
    storage_manager = current_app.config.get("scan_storage_manager")
    scan_data = storage_manager.get_scan(scan_id) if storage_manager else None

    if not scan_data:
        logger.warning(f"Scan {scan_id} no encontrado en storage")
        return jsonify({"error": "Scan no encontrado"}), 404

    logger.info(
        f"Scan {scan_id} encontrado en storage, status: {scan_data.get('status')}"
    )

    response = {
        "scan_id": scan_id,
        "status": scan_data.get("status", "unknown"),
        "progress": scan_data.get("progress", 0),
        "error": scan_data.get("error"),
        "logs": scan_data.get("logs") or [],
    }

    if scan_data.get("status") == "completed":
        response["results"] = scan_data.get("results")
        if response["results"]:
            logger.info(
                f"Results desde storage tiene "
                f"{len(response['results'].get('analysis_results', {}))} APs"
            )
        else:
            logger.warning(f"Results es None o vacio en storage para scan {scan_id}")

    return jsonify(response)


@scan_bp.route("/api/cnmaestro/inventory", methods=["GET"])
@login_required
def get_cnmaestro_inventory():
    """Get processed inventory from cnMaestro."""
    try:
        force = request.args.get("force") == "true"
        inventory = cnmaestro_client.get_full_inventory(force_refresh=force)
        return jsonify(inventory)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@scan_bp.route("/api/results/<scan_id>", methods=["GET"])
@login_required
def get_scan_results(scan_id: str):
    """Get full results of a completed scan."""
    # 1. Try in-memory hot cache
    if scan_id in active_scans:
        task = active_scans[scan_id]["task"]

        if task.status != "completed":
            return jsonify(
                {
                    "error": "Scan aun no completado",
                    "status": task.status,
                    "progress": task.progress,
                }
            ), 400

        return jsonify(task.results)

    # 2. Fallback: look up in SQLite storage
    storage_manager = current_app.config.get("scan_storage_manager")
    scan_data = storage_manager.get_scan(scan_id) if storage_manager else None

    if not scan_data:
        return jsonify({"error": "Scan no encontrado"}), 404

    if scan_data.get("status") != "completed":
        return jsonify(
            {
                "error": "Scan aun no completado",
                "status": scan_data.get("status", "unknown"),
                "progress": scan_data.get("progress", 0),
            }
        ), 400

    results = scan_data.get("results")
    if not results:
        return jsonify({"error": "Resultados no disponibles"}), 404

    return jsonify(results)


@scan_bp.route("/api/scans", methods=["GET"])
@scan_bp.route("/api/scans/recent", methods=["GET"])
@login_required
def list_scans():
    """List all scans (merge of in-memory hot cache + SQLite storage)."""
    scans = []
    seen_ids = set()

    # 1. Scans from in-memory (real-time state)
    for scan_id, scan_data in active_scans.items():
        task = scan_data["task"]
        scans.append(
            {
                "scan_id": scan_id,
                "created_at": scan_data["created_at"],
                "status": task.status,
                "progress": task.progress,
                "ap_count": len(scan_data["ap_ips"]),
                "ticket_id": scan_data.get("ticket_id", 0),
            }
        )
        seen_ids.add(scan_id)

    # 2. Merge from SQLite storage (dedup by scan_id)
    storage_manager = current_app.config.get("scan_storage_manager")
    if storage_manager is not None:
        stored_list = storage_manager.get_all_scans()
        for scan_row in stored_list:
            s_id = scan_row.get("id")
            if s_id in seen_ids:
                continue
            ap_ips = scan_row.get("ap_ips") or []
            ap_count = len(ap_ips) if isinstance(ap_ips, list) else 0
            scans.append(
                {
                    "scan_id": s_id,
                    "created_at": scan_row.get("started_at", ""),
                    "status": scan_row.get("status", "unknown"),
                    "progress": 0,
                    "ap_count": ap_count,
                    "ticket_id": scan_row.get("ticket_id", 0),
                }
            )

    scans.sort(key=lambda x: x.get("created_at") or "", reverse=True)

    return jsonify({"scans": scans})


@scan_bp.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    return jsonify(
        {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "active_scans": len(active_scans),
        }
    )
