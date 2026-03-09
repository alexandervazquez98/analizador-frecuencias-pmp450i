"""
Aplicación web Flask para Tower Scan Automation
Proporciona API REST e interfaz web para gestionar escaneos
"""

from flask import render_template, request, jsonify, send_from_directory
from app import create_app
from app.tower_scan import TowerScanner
from app.frequency_analyzer import FrequencyAnalyzer, analyze_ap
from app.cross_analyzer import APSMCrossAnalyzer, SMSpectrumData
from app.audit_manager import AuditManager, AuditLogException
from functools import wraps
import asyncio
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional
import logging
import os
import json
from pathlib import Path
import requests

# Compatibilidad cross-platform para file locking (fcntl es Unix-only)
try:
    import fcntl

    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

# Logger del módulo (configuración centralizada en app/__init__.py)
logger = logging.getLogger(__name__)

# Crear aplicación Flask
app = create_app()

# Archivo de almacenamiento persistente
STORAGE_FILE = Path("/tmp/tower_scan_storage.json")


def load_storage():
    """Cargar datos desde archivo JSON con lock"""
    if not STORAGE_FILE.exists():
        return {"active_scans": {}, "scan_results": {}}

    try:
        with open(STORAGE_FILE, "r") as f:
            if HAS_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            data = json.load(f)
            if HAS_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return data
    except (json.JSONDecodeError, IOError):
        return {"active_scans": {}, "scan_results": {}}


def save_storage(data):
    """Guardar datos a archivo JSON con lock"""
    try:
        STORAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STORAGE_FILE, "w") as f:
            if HAS_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(data, f, indent=2, default=str)
            if HAS_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except IOError as e:
        logger.error(f"Error guardando storage: {e}")


def get_scan(scan_id: str):
    """Obtener scan desde storage"""
    storage = load_storage()
    return storage["active_scans"].get(scan_id)


def save_scan(scan_id: str, scan_data: dict):
    """Guardar scan en storage"""
    storage = load_storage()
    storage["active_scans"][scan_id] = scan_data
    save_storage(storage)


def get_stored_scans():
    """Listar todos los scans desde storage"""
    storage = load_storage()
    return storage["active_scans"]


# Almacenamiento en memoria de escaneos (en producción usar Redis/DB)
active_scans: Dict[str, Dict] = {}
scan_results: Dict[str, Dict] = {}


# ==================== DECORADOR DE AUDITORÍA ====================


def requires_audit_ticket(f):
    """Decorador que intercepta peticiones para validar credenciales de auditoría.

    Busca X-Audit-User y X-Ticket-ID en headers (prioridad) o en el body JSON.
    Si la validación falla, retorna HTTP 403 y bloquea el escaneo SNMP.
    Si pasa, inyecta 'audit_manager' como kwarg del endpoint.

    Especificación: 02_specs.md § S2 — Contrato de Auditoría
    Diseño:       03_design.md § D2 — Intercepción de Peticiones
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Extraer credenciales (Headers tienen prioridad, luego JSON body)
        user = request.headers.get("X-Audit-User")
        ticket_id = request.headers.get("X-Ticket-ID")

        if request.is_json:
            data = request.get_json(silent=True) or {}
            if not user:
                user = data.get("user")
            if not ticket_id:
                ticket_id = data.get("ticket_id")

        # 2. Instanciar AuditManager (lanza AuditLogException si es inválido)
        try:
            audit = AuditManager(user=user, ticket_id=ticket_id)
        except AuditLogException as e:
            logger.warning(f"Auditoría fallida (Seguridad): {str(e)}")
            return jsonify({"error": "Excepción de Seguridad", "message": str(e)}), 403

        # 3. Iniciar el registro de auditoría
        audit.start_transaction()

        # 4. Inyectar el gestor de auditoría en kwargs
        kwargs["audit_manager"] = audit

        try:
            response = f(*args, **kwargs)

            # Si el endpoint retorna 202 (Accepted), es asíncrono:
            # el hilo (ScanTask) cerrará el audit_manager al terminar.
            # Para respuestas síncronas, cerrar aquí.
            if isinstance(response, tuple):
                status_code = response[1]
            else:
                status_code = getattr(response, "status_code", 200)

            if status_code != 202:
                audit.end_transaction(result_summary="Ejecución síncrona completada.")

            return response

        except Exception as e:
            audit.end_transaction(result_summary=f"Fallo del sistema: {str(e)}")
            raise

    return decorated_function


class ScanTask:
    """Tarea de escaneo asíncrona con soporte para análisis cruzado AP-SM"""

    def __init__(
        self,
        scan_id: str,
        ap_ips: List[str],
        snmp_communities: List[str],
        config: Dict,
        sm_ips: Optional[List[str]] = None,
        audit_manager: "AuditManager | None" = None,
    ):
        self.scan_id = scan_id
        self.ap_ips = ap_ips
        self.sm_ips = sm_ips or []
        self.snmp_communities = snmp_communities
        self.config = config
        self.status = "initializing"
        self.progress = 0
        self.results = {}
        self.error = None
        self.logs = []  # Buffer de logs para frontend
        self.analysis_mode = "AP_SM_CROSS" if self.sm_ips else "AP_ONLY"
        self.audit_manager = audit_manager

    def log(self, msg: str, level: str = "info"):
        """Loggear mensaje a consola y buffer interno"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append({"ts": timestamp, "msg": msg, "type": level})

        # Log to system logger
        log_msg = f"[{self.scan_id}] {msg}"
        if level == "error":
            logger.error(log_msg)
        elif level == "warning":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

    async def execute(self):
        """Ejecutar escaneo completo con análisis cruzado opcional"""
        try:
            # Fase 0: Validación Previa
            self.status = "validating"
            logger.info(f"[{self.scan_id}] Validando dispositivos...")

            scanner_for_validation = TowerScanner(
                self.ap_ips,
                self.snmp_communities,
                sm_ips=self.sm_ips,
                log_callback=lambda msg, lvl: logger.info(f"[{self.scan_id}] {msg}"),
            )

            (
                valid_aps,
                valid_sms,
                errors,
            ) = await scanner_for_validation.validate_and_filter_devices()

            # Reportar errores de validación
            if errors:
                error_details = "; ".join(
                    [f"{ip}: {msg}" for ip, msg in errors.items()]
                )
                error_msg = f"Validación fallida: {len(errors)} dispositivo(s) no responden o tienen comunidad incorrecta. NO se iniciará el escaneo hasta corregirlo. Detalles: {error_details}"
                logger.error(f"[{self.scan_id}] {error_msg}")
                raise Exception(error_msg)

            if not valid_aps:
                raise Exception(
                    "Ningún AP pasó la validación SNMP (verifique IPs y comunidad)"
                )

            # Todos válidos, proceder
            self.ap_ips = valid_aps
            self.sm_ips = valid_sms

            # Fase 1: Tower Scan (SNMP)
            self.status = "scanning"
            self.progress = 10
            self.log(f"Iniciando Tower Scan (Modo: {self.analysis_mode})...")

            # Pasar self.log como callback para recibir logs en tiempo real del scanner
            scanner = TowerScanner(
                self.ap_ips,
                self.snmp_communities,
                sm_ips=self.sm_ips,
                log_callback=self.log,
            )
            scan_results = await scanner.start_tower_scan()

            self.progress = 40
            self.log(f"Fase de escaneo completada. Procesando resultados...")

            # Extraer resultados por tipo (tower_scan devuelve dict plano {ip: result})
            ap_scan_results = {
                ip: res for ip, res in scan_results.items() if ip in self.ap_ips
            }
            sm_scan_results = {
                ip: res for ip, res in scan_results.items() if ip in self.sm_ips
            }

            # Identificar dispositivos no alcanzables o que fallaron
            failed_aps = [
                ip
                for ip, result in ap_scan_results.items()
                if not (isinstance(result, dict) and result.get("completed", False))
            ]
            failed_sms = [
                ip
                for ip, result in sm_scan_results.items()
                if not (isinstance(result, dict) and result.get("completed", False))
            ]

            # Reportar dispositivos no alcanzables
            if failed_aps:
                self.log(
                    f"[WARNING] APs no alcanzables o que fallaron: {len(failed_aps)}",
                    "warning",
                )
                for ip in failed_aps:
                    res = ap_scan_results.get(ip)
                    if isinstance(res, dict):
                        reason = res.get("message", "Razón desconocida")
                    else:
                        reason = str(res)
                    self.log(f"  - AP {ip}: {reason}", "warning")

            if failed_sms:
                self.log(
                    f"[WARNING] SMs no alcanzables o que fallaron: {len(failed_sms)}",
                    "warning",
                )
                for ip in failed_sms:
                    res = sm_scan_results.get(ip)
                    if isinstance(res, dict):
                        reason = res.get("message", "Razón desconocida")
                    else:
                        reason = str(res)
                    self.log(f"  - SM {ip}: {reason}", "warning")

            # Verificar qué APs completaron
            completed_aps = []
            for ip, result in ap_scan_results.items():
                # Bugfix: Validar que result sea un diccionario antes de usar .get()
                if isinstance(result, dict) and result.get("completed", False):
                    completed_aps.append(ip)
                elif isinstance(result, str):
                    logger.error(
                        f"Scan result for {ip} is a string (unexpected): {result}"
                    )

            if not completed_aps:
                error_msg = f"Ningún AP completó el escaneo exitosamente. APs fallidos: {len(failed_aps)}"
                if failed_aps:
                    error_msg += f"\nDetalles:\n"
                    for ip in failed_aps:
                        res = ap_scan_results[ip]
                        msg = (
                            res.get("message", "Razón desconocida")
                            if isinstance(res, dict)
                            else str(res)
                        )
                        error_msg += f"  - {ip}: {msg}\n"
                raise Exception(error_msg)

            # Verificar qué SMs completaron
            completed_sms = [
                ip
                for ip, result in sm_scan_results.items()
                if isinstance(result, dict) and result.get("completed", False)
            ]

            self.log(
                f"[OK] Completados: {len(completed_aps)}/{len(self.ap_ips)} APs, {len(completed_sms)}/{len(self.sm_ips)} SMs",
                "success",
            )

            # Fase 2: Descargar espectro XML
            self.status = "downloading"
            self.progress = 50
            self.log("Descargando archivos de espectro XML...")

            # Descargar XML de APs
            ap_xmls = {}
            for ip in completed_aps:
                try:
                    xml_data = await asyncio.to_thread(self._download_spectrum_xml, ip)
                    ap_xmls[ip] = xml_data
                    self.log(f"XML descargado de AP {ip}", "success")
                except Exception as e:
                    self.log(f"Error descargando XML de AP {ip}: {e}", "error")

            # Descargar XML de SMs - con reintentos
            sm_xmls = {}
            for ip in completed_sms:
                try:
                    # Dar tiempo adicional antes de intentar descargar de SMs
                    self.log(f"Esperando 5s antes de descargar de SM {ip}...")
                    await asyncio.sleep(5)

                    # Intentar descargar con reintentos
                    xml_data = await self._download_sm_xml_with_retries(
                        ip, max_retries=3, retry_delay=10
                    )
                    sm_xmls[ip] = xml_data
                    self.log(f"XML descargado de SM {ip}", "success")
                except Exception as e:
                    self.log(f"Error descargando XML de SM {ip}: {e}", "error")

            # Verificar que se descargaron XMLs
            if not ap_xmls:
                raise Exception("No se pudieron descargar XMLs de ningún AP")

            self.log(f"XMLs descargados: {len(ap_xmls)} APs, {len(sm_xmls)} SMs")

            self.progress = 60

            # Fase 3: Análisis de frecuencias
            self.status = "analyzing"
            logger.info(f"[{self.scan_id}] Analizando frecuencias...")

            target_rx = self.config.get("target_rx_level", -52)
            analysis_results = {}

            if (
                self.analysis_mode == "AP_SM_CROSS" and sm_xmls
            ):  # Cambio: usar sm_xmls en vez de completed_sms
                # ANÁLISIS CRUZADO AP-SM
                logger.info(f"[{self.scan_id}] Ejecutando análisis cruzado AP-SM...")

                cross_analyzer = APSMCrossAnalyzer()
                freq_analyzer = FrequencyAnalyzer()

                for i, ap_ip in enumerate(completed_aps):
                    if ap_ip not in ap_xmls:
                        logger.warning(
                            f"[{self.scan_id}] No hay XML para AP {ap_ip}, saltando..."
                        )
                        continue

                    try:
                        logger.info(
                            f"[{self.scan_id}] Análisis cruzado para AP {ap_ip}..."
                        )

                        # Parsear espectro del AP
                        ap_spectrum = freq_analyzer.parse_spectrum_xml(ap_xmls[ap_ip])

                        if not ap_spectrum:
                            logger.warning(
                                f"No se pudo parsear espectro del AP {ap_ip}"
                            )
                            analysis_results[ap_ip] = {
                                "error": "Error parseando XML del AP"
                            }
                            continue

                        logger.info(
                            f"[{self.scan_id}] Espectro del AP parseado: {len(ap_spectrum)} puntos"
                        )

                        # Guardar espectro RAW para visualización
                        # Serializar objetos SpectrumPoint a dicts para JSON
                        raw_spectrum_data = []
                        for point in ap_spectrum:
                            raw_spectrum_data.append(
                                {
                                    "freq": point.frequency,
                                    "noise": max(
                                        point.vertical_max, point.horizontal_max
                                    ),
                                }
                            )

                        # Almacenar temporalmente en el resultado del análisis (será accesible vía API)
                        if ap_ip not in analysis_results:
                            analysis_results[ap_ip] = {}
                        analysis_results[ap_ip]["raw_spectrum"] = raw_spectrum_data

                        # Parsear espectro de SMs
                        sm_data = []
                        # Loggear las claves disponibles
                        logger.info(
                            f"[{self.scan_id}] Procesando {len(sm_xmls)} XMLs de SMs. IPs: {list(sm_xmls.keys())}"
                        )

                        for sm_ip in (
                            sm_xmls.keys()
                        ):  # Usar sm_xmls.keys() en vez de completed_sms
                            try:
                                sm_spectrum = freq_analyzer.parse_spectrum_xml(
                                    sm_xmls[sm_ip]
                                )
                                if sm_spectrum and len(sm_spectrum) > 0:
                                    sm_data.append(
                                        SMSpectrumData(
                                            ip=sm_ip,
                                            spectrum_points=sm_spectrum,
                                            is_critical=True,
                                        )
                                    )
                                    logger.info(
                                        f"[{self.scan_id}] [OK] Espectro de SM {sm_ip} parseado: {len(sm_spectrum)} puntos"
                                    )
                                else:
                                    logger.warning(
                                        f"[{self.scan_id}] [WARN] Espectro de SM {sm_ip} vacío o inválido (puntos: {len(sm_spectrum) if sm_spectrum else 0})"
                                    )
                            except Exception as e:
                                logger.error(
                                    f"[{self.scan_id}] [ERROR] Falló parseo de SM {sm_ip}: {e}"
                                )

                        if not sm_data:
                            logger.warning(
                                f"[{self.scan_id}] No hay datos de SMs válidos después del parseo (XMLs: {len(sm_xmls)}). Usando análisis solo de AP"
                            )
                            # Fallback a análisis solo de AP
                            report = await asyncio.to_thread(
                                analyze_ap, ap_ip, target_rx
                            )
                            analysis_results[ap_ip] = report.to_dict()

                            # FIX: Add raw_spectrum for AP only fallback
                            # ... (same as before) logic reused via fallthrough or check?
                            # To be safe, let's just allow it to continue or break?
                            # The original code did `continue`.
                            # We should ensure raw_spectrum is attached to the report
                            sp_data = analysis_results[ap_ip].get("spectrum_data", {})
                            if isinstance(sp_data, list):  # Old format check
                                # Convert to new format
                                analysis_results[ap_ip]["spectrum_data"] = {
                                    "ap": sp_data,
                                    "sms": {},
                                }

                            continue

                        logger.info(
                            f"[{self.scan_id}] Ejecutando análisis cruzado con {len(sm_data)} SMs..."
                        )

                        # Ejecutar análisis cruzado multibanda (20, 15, 10, 5 MHz)
                        df_combined, cross_results = (
                            cross_analyzer.analyze_multiband_ap_with_sms(
                                ap_spectrum, sm_data, top_n=20
                            )
                        )

                        logger.info(
                            f"[{self.scan_id}] Análisis cruzado completado: {len(cross_results)} frecuencias evaluadas"
                        )

                        best_combined = cross_analyzer.get_best_combined_frequency(
                            cross_results
                        )

                        # Serializar datos de espectro para visualización
                        serialized_ap_spectrum = [
                            {
                                "frequency": p.frequency,
                                "vertical": p.vertical_max,
                                "horizontal": p.horizontal_max,
                            }
                            for p in ap_spectrum
                        ]

                        serialized_sm_spectrums = {}
                        for sm in sm_data:
                            serialized_sm_spectrums[sm.ip] = [
                                {
                                    "frequency": p.frequency,
                                    "vertical": p.vertical_max,
                                    "horizontal": p.horizontal_max,
                                }
                                for p in sm.spectrum_points
                            ]

                        analysis_results[ap_ip] = {
                            "mode": "AP_SM_CROSS",
                            "spectrum_data": {
                                "ap": serialized_ap_spectrum,
                                "sms": serialized_sm_spectrums,
                            },
                            "sm_count": len(sm_data),
                            "sm_ips": [sm.ip for sm in sm_data],
                            "combined_ranking": df_combined.to_dict("records"),
                            "best_combined_frequency": {
                                "frequency": best_combined.frequency,
                                "ap_score": best_combined.ap_score,
                                "combined_score": best_combined.combined_score,
                                "sm_worst_noise": best_combined.sm_worst_noise,
                                "sm_avg_noise": best_combined.sm_avg_noise,
                                "is_viable": best_combined.is_viable,
                                "veto_reason": best_combined.veto_reason,
                                "quality_level": best_combined.quality_level,
                                "warnings": best_combined.warnings,
                                "recommendations": best_combined.recommendations,
                                "is_optimal": best_combined.is_optimal,
                                "requires_action": best_combined.requires_action,
                                "sm_details": best_combined.sm_details,
                            }
                            if best_combined
                            else None,
                            "all_cross_results": [
                                {
                                    "frequency": r.frequency,
                                    "ap_score": r.ap_score,
                                    "combined_score": r.combined_score,
                                    "sm_worst_noise": r.sm_worst_noise,
                                    "is_viable": r.is_viable,
                                    "sm_details": r.sm_details,
                                }
                                for r in cross_results
                            ],
                            "raw_spectrum": raw_spectrum_data,  # Save raw_spectrum to prevent overwriting
                        }

                        logger.info(
                            f"[{self.scan_id}] Resultados almacenados para AP {ap_ip}"
                        )

                    except Exception as e:
                        logger.error(
                            f"[{self.scan_id}] Error en análisis cruzado de AP {ap_ip}: {str(e)}",
                            exc_info=True,
                        )
                        analysis_results[ap_ip] = {
                            "error": f"Error en análisis: {str(e)}"
                        }

                    # Actualizar progreso
                    progress_increment = 30 / len(completed_aps)
                    self.progress = 60 + int((i + 1) * progress_increment)

            else:
                # ANÁLISIS SOLO DE AP (modo original)
                logger.info(f"[{self.scan_id}] Ejecutando análisis solo de AP...")

                # Instanciar el analizador
                freq_analyzer = FrequencyAnalyzer()

                for i, ip in enumerate(completed_aps):
                    logger.info(f"[{self.scan_id}] Analizando AP {ip}...")

                    report = await asyncio.to_thread(analyze_ap, ip, target_rx)
                    analysis_results[ip] = report.to_dict()

                    # FIX: Guardar también el espectro RAW en modo individual
                    if ip in ap_xmls:
                        try:
                            spectrum_points = freq_analyzer.parse_spectrum_xml(
                                ap_xmls[ip]
                            )
                            if spectrum_points:
                                analysis_results[ip]["raw_spectrum"] = [
                                    {
                                        "freq": p.frequency,
                                        "noise": max(p.vertical_max, p.horizontal_max),
                                    }
                                    for p in spectrum_points
                                ]
                                logger.info(
                                    f"[{self.scan_id}] Raw spectrum saved for AP {ip} ({len(spectrum_points)} points)"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Failed to parse raw spectrum for AP {ip}: {e}"
                            )

                    # Actualizar progreso
                    progress_increment = 30 / len(completed_aps)
                    self.progress = 60 + int((i + 1) * progress_increment)

            # Compilar resultados finales con información detallada de conectividad
            self.results = {
                "scan_id": self.scan_id,
                "timestamp": datetime.now().isoformat(),
                "analysis_mode": self.analysis_mode,
                "ap_count": len(self.ap_ips),
                "sm_count": len(self.sm_ips),
                "completed_aps": len(completed_aps),
                "completed_sms": len(completed_sms),
                "failed_aps": len(failed_aps),
                "failed_sms": len(failed_sms),
                "unreachable_devices": {
                    "aps": failed_aps,
                    "sms": failed_sms,
                    "details": {
                        **{ip: ap_scan_results[ip] for ip in failed_aps},
                        **{ip: sm_scan_results[ip] for ip in failed_sms},
                    },
                },
                "scan_results": {"aps": ap_scan_results, "sms": sm_scan_results},
                "analysis_results": analysis_results,
                "config": self.config,
            }

            logger.info(
                f"[{self.scan_id}] 📊 Resultados compilados: {len(analysis_results)} APs analizados"
            )
            logger.info(
                f"[{self.scan_id}] 📊 Dispositivos alcanzables: {len(completed_aps)} APs, {len(completed_sms)} SMs"
            )
            logger.info(
                f"[{self.scan_id}] 📊 Dispositivos fallidos: {len(failed_aps)} APs, {len(failed_sms)} SMs"
            )

            self.status = "completed"
            self.progress = 100
            logger.info(f"[{self.scan_id}] ✅ Escaneo completado exitosamente")

            # Guardar resultados en storage persistente
            scan_data = get_scan(self.scan_id)
            if scan_data:
                logger.info(
                    f"[{self.scan_id}] 💾 Guardando resultados en storage persistente..."
                )
                scan_data["status"] = self.status
                scan_data["progress"] = self.progress
                scan_data["results"] = self.results
                save_scan(self.scan_id, scan_data)
                logger.info(f"[{self.scan_id}] ✅ Resultados guardados en storage")
            else:
                logger.warning(
                    f"[{self.scan_id}] ⚠️ No se encontró scan_data en storage para guardar resultados"
                )

            # Cerrar auditoría con resultado exitoso
            if self.audit_manager:
                result_summary = f"Completado: {len(analysis_results)} APs analizados, modo {self.analysis_mode}"
                self.audit_manager.end_transaction(result_summary=result_summary)

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error(f"[{self.scan_id}] ❌ Error: {str(e)}")

            # Cerrar auditoría con resultado fallido
            if self.audit_manager:
                self.audit_manager.end_transaction(result_summary=f"Fallo: {str(e)}")

            # Guardar error en storage
            scan_data = get_scan(self.scan_id)
            if scan_data:
                scan_data["status"] = self.status
                scan_data["error"] = self.error
                save_scan(self.scan_id, scan_data)

    def _download_spectrum_xml(self, ip: str, timeout: int = 30) -> str:
        """
        Descargar archivo XML de espectro desde radio
        Usa el método mejorado de FrequencyAnalyzer con reintentos
        """
        analyzer = FrequencyAnalyzer()
        xml_data = analyzer.download_spectrum_data(
            ip, timeout=timeout, max_retries=3, retry_delay=5
        )

        if not xml_data:
            raise Exception(
                f"No se pudo descargar XML de {ip} después de múltiples intentos"
            )

        return xml_data

    async def _download_sm_xml_with_retries(
        self, ip: str, max_retries: int = 3, retry_delay: int = 10
    ) -> str:
        """
        Descargar XML de SM con reintentos debido al delay en la respuesta
        Usa backoff exponencial y timeouts más generosos

        Args:
            ip: Dirección IP del SM
            max_retries: Número máximo de reintentos
            retry_delay: Segundos base de espera entre reintentos

        Returns:
            Contenido XML como string

        Raises:
            Exception si no se puede descargar después de todos los reintentos
        """
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    f"[{self.scan_id}] Intento {attempt}/{max_retries} descargando XML de SM {ip}"
                )

                # Aumentar timeout progresivamente con cada intento
                timeout = 30 + (attempt * 15)  # 45s, 60s, 75s...

                xml_data = await asyncio.to_thread(
                    self._download_spectrum_xml, ip, timeout
                )

                # Validaciones adicionales del XML
                if not xml_data:
                    raise Exception("XML vacío")

                if len(xml_data) < 100:
                    raise Exception(f"XML muy pequeño ({len(xml_data)} bytes)")

                # Verificar que contiene elementos esperados
                if "<Freq" not in xml_data:
                    raise Exception(
                        "XML no contiene elementos <Freq> - posible escaneo incompleto"
                    )

                # Contar elementos Freq (debería haber al menos algunos)
                freq_count = xml_data.count("<Freq")
                if freq_count < 10:
                    raise Exception(
                        f"XML contiene muy pocos elementos Freq ({freq_count}) - posible escaneo incompleto"
                    )

                logger.info(
                    f"[{self.scan_id}] XML válido descargado de SM {ip} en intento {attempt} ({len(xml_data)} bytes, {freq_count} elementos)"
                )
                return xml_data

            except Exception as e:
                last_error = e
                logger.warning(
                    f"[{self.scan_id}] Intento {attempt}/{max_retries} fallido para SM {ip}: {e}"
                )

                # Si no es el último intento, esperar antes de reintentar
                if attempt < max_retries:
                    # Backoff exponencial: retry_delay * 2^(attempt-1)
                    wait_time = retry_delay * (2 ** (attempt - 1))
                    logger.info(
                        f"[{self.scan_id}] Esperando {wait_time}s antes del siguiente intento (backoff exponencial)..."
                    )
                    await asyncio.sleep(wait_time)

        # Si llegamos aquí, todos los intentos fallaron
        raise Exception(
            f"No se pudo descargar XML de SM {ip} después de {max_retries} intentos: {last_error}"
        )

    def run_in_thread(self):
        """Ejecutar en un thread separado"""
        asyncio.run(self.execute())


def parse_ip_list(ip_text: str) -> List[str]:
    """
    Parsear lista de IPs desde texto

    Args:
        ip_text: Texto con IPs separadas por líneas o comas

    Returns:
        Lista de IPs válidas
    """
    if not ip_text:
        return []

    # Separar por líneas y comas
    ips = []
    for line in ip_text.replace(",", "\n").split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            # Validación básica de IP
            parts = line.split(".")
            if len(parts) == 4 and all(
                p.isdigit() and 0 <= int(p) <= 255 for p in parts
            ):
                ips.append(line)

    return ips


# ==================== RUTAS WEB ====================


@app.route("/")
def index():
    """Página principal"""
    return render_template("index.html")


@app.route("/static/<path:path>")
def send_static(path):
    """Servir archivos estáticos"""
    return send_from_directory("../static", path)


# ==================== API REST ====================


@app.route("/api/scan", methods=["POST"])
@requires_audit_ticket
def start_scan(audit_manager=None):
    """
    Iniciar un nuevo Tower Scan

    Requiere credenciales de auditoría (inyectadas por @requires_audit_ticket):
    - X-Audit-User (header) o "user" (JSON body)
    - X-Ticket-ID (header) o "ticket_id" (JSON body)

    Body JSON:
    {
        "ap_ips": ["192.168.1.1", "192.168.1.2"],
        "sm_ips": ["192.168.1.100", "192.168.1.101"],  // OPCIONAL para análisis cruzado
        "snmp_community": "Canopy",
        "config": {
            "target_rx_level": -52,
            "min_snr": 32,
            "max_pol_diff": 5
        }
    }

    Returns:
    {
        "scan_id": "uuid",
        "status": "started",
        "message": "..."
    }
    """
    try:
        data = request.get_json()

        snmp_communities_input = data.get("snmp_community", "MEXI2-BB-RW")

        # Validar datos requeridos
        ap_ips = data.get("ap_ips", [])
        sm_ips = data.get("sm_ips", [])

        # Soportar texto plano también
        if isinstance(ap_ips, str):
            ap_ips = parse_ip_list(ap_ips)

        if isinstance(sm_ips, str):
            sm_ips = parse_ip_list(sm_ips)

        if not ap_ips:
            return jsonify({"error": "Se requiere al menos una IP de AP"}), 400

        # Procesar comunidades (string o lista)
        if isinstance(snmp_communities_input, str):
            snmp_communities = [
                c.strip() for c in snmp_communities_input.split(",") if c.strip()
            ]
        elif isinstance(snmp_communities_input, list):
            snmp_communities = snmp_communities_input
        else:
            snmp_communities = ["MEXI2-BB-RW"]

        # Configuración opcional
        config = data.get("config", {})
        config.setdefault("target_rx_level", -52)
        config.setdefault("min_snr", 32)
        config.setdefault("max_pol_diff", 5)
        config.setdefault("channel_width", 20)  # Default 20MHz

        # Crear tarea de escaneo con SMs opcionales
        scan_id = str(uuid.uuid4())
        # Ahora pasamos la lista de comunidades y el audit_manager para el hilo
        task = ScanTask(
            scan_id,
            ap_ips,
            snmp_communities,
            config,
            sm_ips=sm_ips,
            audit_manager=audit_manager,
        )

        # Guardar en almacenamiento en memoria
        active_scans[scan_id] = {
            "task": task,
            "created_at": datetime.now().isoformat(),
            "ap_ips": ap_ips,
            "sm_ips": sm_ips,
            "snmp_communities": snmp_communities,
            "config": config,  # Guardar config para debug
            "status": "started",
            "progress": 0,
        }

        # Guardar en storage persistente
        save_scan(
            scan_id,
            {
                "created_at": datetime.now().isoformat(),
                "ap_ips": ap_ips,
                "sm_ips": sm_ips,
                "config": config,
                "ap_count": len(ap_ips),
                "sm_count": len(sm_ips),
                "analysis_mode": "AP_SM_CROSS" if sm_ips else "AP_ONLY",
                "status": "started",
                "progress": 0,
            },
        )

        # Ejecutar en thread separado
        thread = threading.Thread(target=task.run_in_thread, daemon=True)
        thread.start()

        logger.info(f"Scan {scan_id} iniciado con {len(ap_ips)} APs")

        return jsonify(
            {
                "scan_id": scan_id,
                "status": "started",
                "message": f"Tower Scan iniciado para {len(ap_ips)} APs"
                + (f" con {len(sm_ips)} SMs (análisis cruzado)" if sm_ips else ""),
                "ap_count": len(ap_ips),
                "sm_count": len(sm_ips),
                "analysis_mode": "AP_SM_CROSS" if sm_ips else "AP_ONLY",
            }
        ), 202

    except Exception as e:
        logger.error(f"Error iniciando scan: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/status/<scan_id>", methods=["GET"])
def get_scan_status(scan_id: str):
    """
    Obtener estado de un escaneo

    Returns:
    {
        "scan_id": "uuid",
        "status": "scanning|analyzing|completed|failed",
        "progress": 75,
        "error": null,
        "results": {...}  // si está completado
    }
    """
    logger.info(f"📡 GET /api/status/{scan_id}")

    # Intentar obtener desde memoria primero
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
                f"📊 Results tiene {len(task.results.get('analysis_results', {}))} APs"
            )
        return jsonify(response)

    # Si no está en memoria, buscar en storage persistente
    logger.info(f"[INFO] Scan {scan_id} no está en memoria, buscando en storage...")
    scan_data = get_scan(scan_id)
    if not scan_data:
        logger.warning(f"❌ Scan {scan_id} no encontrado en storage")
        return jsonify({"error": "Scan no encontrado"}), 404

    logger.info(
        f"✅ Scan {scan_id} encontrado en storage, status: {scan_data.get('status')}"
    )

    response = {
        "scan_id": scan_id,
        "status": scan_data.get("status", "unknown"),
        "progress": scan_data.get("progress", 0),
        "error": scan_data.get("error"),
    }

    if scan_data.get("status") == "completed":
        response["results"] = scan_data.get("results")
        if response["results"]:
            logger.info(
                f"📊 Results desde storage tiene {len(response['results'].get('analysis_results', {}))} APs"
            )
        else:
            logger.warning(f"⚠️ Results es None o vacío en storage para scan {scan_id}")

    return jsonify(response)


from app.cnmaestro_client import CnMaestroClient

# CnMaestro Configuration (desde variables de entorno)
CNMAESTO_URL = os.environ.get("CNMAESTRO_URL", "https://10.3.152.206/api/v1")
CNMAESTRO_ID = os.environ.get("CNMAESTRO_ID", "")
CNMAESTRO_SECRET = os.environ.get("CNMAESTRO_SECRET", "")

cnmaestro_client = CnMaestroClient(CNMAESTO_URL, CNMAESTRO_ID, CNMAESTRO_SECRET)


@app.route("/api/cnmaestro/inventory", methods=["GET"])
def get_cnmaestro_inventory():
    """Get processed inventory from cnMaestro"""
    try:
        force = request.args.get("force") == "true"
        inventory = cnmaestro_client.get_full_inventory(force_refresh=force)
        return jsonify(inventory)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/results/<scan_id>", methods=["GET"])
def get_scan_results(scan_id: str):
    """
    Obtener resultados completos de un escaneo
    """
    if scan_id not in active_scans:
        return jsonify({"error": "Scan no encontrado"}), 404

    task = active_scans[scan_id]["task"]

    if task.status != "completed":
        return jsonify(
            {
                "error": "Scan aún no completado",
                "status": task.status,
                "progress": task.progress,
            }
        ), 400

    return jsonify(task.results)


@app.route("/spectrum/<scan_id>/<ap_ip>")
def spectrum_viewer(scan_id, ap_ip):
    """
    Renderizar visor de espectro en página independiente
    """
    return render_template("spectrum_viewer.html", scan_id=scan_id, ap_ip=ap_ip)


@app.route("/api/recommendations", methods=["GET"])
def get_recommendations():
    """
    Obtener recomendaciones de configuración

    Returns:
    {
        "recommendations": [...]
    }
    """
    analyzer = FrequencyAnalyzer()
    recommendations = analyzer.generate_recommendations()

    return jsonify({"recommendations": recommendations})


@app.route("/spectrum_view/<ip>")
def spectrum_view(ip):
    """Render spectrum view page for specific IP"""
    return render_template("spectrum_viewer.html", ip=ip)


@app.route("/api/spectrum_data/<ip>")
def get_spectrum_data_api(ip):
    """Get spectrum data for specific IP"""

    # 1. Buscar en active_scans (scans en memoria)
    for scan_id, data in active_scans.items():
        # Verificar si hay resultados de análisis
        if "scan_results" in data and data.get("scan_results"):
            # Revisar si analysis_results está dentro de scan_results (depende de cómo lo estructuramos en ScanTask)
            results = data["scan_results"]
            if "analysis_results" in results:
                analysis = results["analysis_results"]
                if ip in analysis and "raw_spectrum" in analysis[ip]:
                    # EUREKA: Encontramos los datos reales
                    raw_data = analysis[ip]["raw_spectrum"]

                    return jsonify(
                        {
                            "ip": ip,
                            "frequencies": [p["freq"] for p in raw_data],
                            "noise_levels": [p["noise"] for p in raw_data],
                            "mean_noise": sum(p["noise"] for p in raw_data)
                            / len(raw_data)
                            if raw_data
                            else -85,
                        }
                    )

    # 2. Si falló lo anterior, intentar buscar si el escaneo terminó pero aún tenemos referencia en active_scans
    # (El loop cubre active_scans, así que si no está ahí, no está en memoria)

    logger.warning(f"No se encontraron datos de espectro para IP {ip} en memoria.")
    return jsonify(
        {
            "error": "No se encontraron datos de espectro para esta IP. (Prueba realizar un nuevo escaneo)"
        }
    ), 404


@app.route("/api/scans", methods=["GET"])
@app.route("/api/scans/recent", methods=["GET"])
def list_scans():
    """
    Listar todos los escaneos

    Returns:
    {
        "scans": [
            {
                "scan_id": "uuid",
                "created_at": "timestamp",
                "status": "...",
                "ap_count": 3
            },
            ...
        ]
    }
    """
    scans = []
    for scan_id, scan_data in active_scans.items():
        task = scan_data["task"]
        scans.append(
            {
                "scan_id": scan_id,
                "created_at": scan_data["created_at"],
                "status": task.status,
                "progress": task.progress,
                "ap_count": len(scan_data["ap_ips"]),
            }
        )

    # Ordenar por fecha (más recientes primero)
    scans.sort(key=lambda x: x["created_at"], reverse=True)

    return jsonify({"scans": scans})


@app.route("/api/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "active_scans": len(active_scans),
        }
    )


# ==================== ERROR HANDLERS ====================


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint no encontrado"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Error interno: {str(error)}")
    return jsonify({"error": "Error interno del servidor"}), 500


# ==================== MAIN ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    logger.info(f"Iniciando servidor en puerto {port}")
    logger.info(f"Acceder a: http://localhost:{port}")

    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
