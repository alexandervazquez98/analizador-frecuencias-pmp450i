"""
app/routes/spectrum_routes.py — Spectrum visualization blueprint for the PMP 450i Analyzer.

Contains spectrum viewer pages and spectrum data API routes.

Design: change-004 design § D4.2 — Flask Blueprint Refactor
"""

from flask import (
    Blueprint,
    render_template,
    request,
    jsonify,
    current_app,
)
from app.routes.auth_routes import login_required
from app.frequency_analyzer import FrequencyAnalyzer
import logging

logger = logging.getLogger(__name__)

spectrum_bp = Blueprint("spectrum", __name__)


# ==================== RUTAS DE ESPECTRO ====================


@spectrum_bp.route("/spectrum/<scan_id>/<ap_ip>")
@login_required
def spectrum_viewer(scan_id, ap_ip):
    """Renderizar visor de espectro en pagina independiente"""
    return render_template("spectrum_viewer.html", scan_id=scan_id, ap_ip=ap_ip)


@spectrum_bp.route("/spectrum_view/<ip>")
@login_required
def spectrum_view(ip):
    """Render spectrum view page for specific IP"""
    return render_template("spectrum_viewer.html", ip=ip)


@spectrum_bp.route("/api/spectrum_data/<ip>")
@login_required
def get_spectrum_data_api(ip):
    """Get spectrum data for specific IP"""
    # Import scan module-level state lazily to avoid circular imports
    from app.routes.scan_routes import active_scans

    # Helper to extract spectrum response from analysis_results dict
    def _extract_spectrum(analysis_results):
        if not isinstance(analysis_results, dict):
            return None
        if ip in analysis_results and "raw_spectrum" in analysis_results[ip]:
            raw_data = analysis_results[ip]["raw_spectrum"]
            if raw_data:
                return jsonify(
                    {
                        "ip": ip,
                        "frequencies": [p["freq"] for p in raw_data],
                        "noise_levels": [p["noise"] for p in raw_data],
                        "mean_noise": sum(p["noise"] for p in raw_data) / len(raw_data),
                    }
                )
        return None

    # 1. Buscar en active_scans (scans en memoria)
    for _scan_id, data in active_scans.items():
        task = data.get("task")
        if task and hasattr(task, "results") and task.results:
            resp = _extract_spectrum(task.results.get("analysis_results"))
            if resp:
                return resp

    # 2. Fallback: buscar en SQLite storage
    storage_manager = current_app.config.get("scan_storage_manager")
    if storage_manager is not None:
        stored_list = storage_manager.get_all_scans()
        for scan_row in stored_list:
            results = scan_row.get("results")
            if results and isinstance(results, dict):
                resp = _extract_spectrum(results.get("analysis_results"))
                if resp:
                    return resp

    logger.warning(f"No se encontraron datos de espectro para IP {ip}.")
    return jsonify(
        {
            "error": "No se encontraron datos de espectro para esta IP. (Prueba realizar un nuevo escaneo)"
        }
    ), 404


@spectrum_bp.route("/api/recommendations", methods=["GET"])
@login_required
def get_recommendations():
    """Obtener recomendaciones de configuracion"""
    analyzer = FrequencyAnalyzer()
    recommendations = analyzer.generate_recommendations()

    return jsonify({"recommendations": recommendations})
