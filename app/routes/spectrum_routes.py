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


@spectrum_bp.route("/api/spectrum/<scan_id>/<ap_ip>")
@login_required
def get_spectrum_for_viewer(scan_id, ap_ip):
    """
    Endpoint dedicado para el visor de espectro.

    Devuelve los datos de espectro del AP y de todos sus SMs asociados
    en el formato que espera spectrum_viewer.html:
      { "ap": [{frequency, vertical, horizontal}, ...],
        "sms": { "ip": [{frequency, vertical, horizontal}, ...], ... } }

    Prioriza in-memory hot cache (scan activo en esta sesion de Flask)
    antes de caer al SQLite storage. Esto evita el problema de truncacion
    de JSON al recuperar resultados grandes de la DB.
    """
    from app.routes.scan_routes import active_scans

    def _extract_spectrum_data(results):
        """Extraer spectrum_data del AP desde el dict de resultados del scan."""
        if not isinstance(results, dict):
            return None
        analysis = results.get("analysis_results", {})
        if ap_ip not in analysis:
            return None
        ap_result = analysis[ap_ip]
        if not isinstance(ap_result, dict):
            return None

        # Mapa ip → site_name para etiquetas del gráfico
        sm_details = ap_result.get("sm_details", [])
        sm_names = {
            d["ip"]: d.get("site_name") or d["ip"]
            for d in sm_details
            if isinstance(d, dict) and d.get("ip")
        }

        # AP_SM_CROSS: spectrum_data tiene ap y sms
        spec = ap_result.get("spectrum_data")
        if spec and isinstance(spec, dict):
            ap_points = spec.get("ap", [])
            sm_points = spec.get("sms", {})
            if ap_points or sm_points:
                return {"ap": ap_points, "sms": sm_points, "sm_names": sm_names}

        # AP_ONLY fallback: spectrum_data puede ser lista o dict con solo ap
        if isinstance(spec, list) and spec:
            return {"ap": spec, "sms": {}, "sm_names": {}}

        # Ultimo fallback: raw_spectrum (lista flat de {freq, noise})
        raw = ap_result.get("raw_spectrum", [])
        if raw:
            ap_points = [
                {"frequency": p["freq"], "vertical": p["noise"], "horizontal": p["noise"]}
                for p in raw
            ]
            return {"ap": ap_points, "sms": {}, "sm_names": {}}

        return None

    # 1. Buscar en hot cache (scan reciente en memoria — sin perdida de datos)
    for _sid, data in active_scans.items():
        if _sid != scan_id:
            continue
        task = data.get("task")
        if task and hasattr(task, "results") and task.results:
            spec = _extract_spectrum_data(task.results)
            if spec:
                logger.info(
                    f"[spectrum] Datos servidos desde hot cache: "
                    f"AP={ap_ip}, SMs={len(spec.get('sms', {}))}"
                )
                return jsonify(spec)

    # 2. Fallback: SQLite storage
    storage_manager = current_app.config.get("scan_storage_manager")
    if storage_manager is not None:
        scan_data = storage_manager.get_scan(scan_id)
        if scan_data and scan_data.get("results"):
            spec = _extract_spectrum_data(scan_data["results"])
            if spec:
                logger.info(
                    f"[spectrum] Datos servidos desde SQLite: "
                    f"AP={ap_ip}, SMs={len(spec.get('sms', {}))}"
                )
                return jsonify(spec)

    logger.warning(
        f"[spectrum] No se encontraron datos de espectro para scan={scan_id}, ap={ap_ip}"
    )
    return jsonify(
        {"error": f"No hay datos de espectro disponibles para este AP ({ap_ip})."}
    ), 404


@spectrum_bp.route("/api/spectrum_data/<ip>")
@login_required
def get_spectrum_data_api(ip):
    """Legacy endpoint — kept for backward compat. Use /api/spectrum/<scan_id>/<ap_ip>."""
    from app.routes.scan_routes import active_scans

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

    for _scan_id, data in active_scans.items():
        task = data.get("task")
        if task and hasattr(task, "results") and task.results:
            resp = _extract_spectrum(task.results.get("analysis_results"))
            if resp:
                return resp

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
