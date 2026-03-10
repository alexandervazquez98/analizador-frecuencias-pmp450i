"""
app/scan_task.py — Asynchronous scan task with optional SQLite persistence.

Extracted from app/routes/scan_routes.py as part of Phase 5 refactor.
Integrates with ScanStorageManager to persist scan state through the lifecycle.

Design: change-005 design § D4.5 — Scan Module Split
"""

import asyncio
import threading
import logging
from datetime import datetime
from typing import Dict, List, Optional

from app.tower_scan import TowerScanner
from app.frequency_analyzer import FrequencyAnalyzer, analyze_ap
from app.cross_analyzer import APSMCrossAnalyzer, SMSpectrumData
from app.audit_manager import AuditManager

logger = logging.getLogger(__name__)


class ScanTask:
    """Asynchronous scan task with optional SQLite persistence via ScanStorageManager.

    When a storage_manager is provided, this task will:
      - Call update_scan_status() during phase transitions.
      - Call complete_scan() on success.
      - Call fail_scan() on failure.

    All storage calls are wrapped in try/except so a DB failure never kills the scan.
    """

    def __init__(
        self,
        scan_id: str,
        ap_ips: List[str],
        snmp_communities: List[str],
        config: Dict,
        sm_ips: Optional[List[str]] = None,
        audit_manager: "AuditManager | None" = None,
        storage_manager=None,
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
        self.storage_manager = storage_manager
        self._start_time: Optional[float] = None

    def log(self, msg: str, level: str = "info"):
        """Log message to console and internal buffer."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append({"ts": timestamp, "msg": msg, "type": level})

        log_msg = f"[{self.scan_id}] {msg}"
        if level == "error":
            logger.error(log_msg)
        elif level == "warning":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

    def _update_status(self, status: str, progress: int = None, error: str = None):
        """Update status in memory and optionally in DB storage."""
        self.status = status
        if progress is not None:
            self.progress = progress
        if error is not None:
            self.error = error

        if self.storage_manager is not None:
            try:
                self.storage_manager.update_scan_status(
                    self.scan_id, status, progress=progress, error=error
                )
            except Exception as exc:
                logger.warning(
                    "[%s] storage update_scan_status failed: %s", self.scan_id, exc
                )

    async def execute(self):
        """Execute full scan with optional cross-analysis."""
        import time

        self._start_time = time.monotonic()

        try:
            # Fase 0: Validacion Previa
            self._update_status("validating", progress=0)
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

            # Reportar errores de validacion
            if errors:
                error_details = "; ".join(
                    [f"{ip}: {msg}" for ip, msg in errors.items()]
                )
                error_msg = (
                    f"Validacion fallida: {len(errors)} dispositivo(s) no responden "
                    f"o tienen comunidad incorrecta. NO se iniciara el escaneo hasta "
                    f"corregirlo. Detalles: {error_details}"
                )
                logger.error(f"[{self.scan_id}] {error_msg}")
                raise Exception(error_msg)

            if not valid_aps:
                raise Exception(
                    "Ningun AP paso la validacion SNMP (verifique IPs y comunidad)"
                )

            # Todos validos, proceder
            self.ap_ips = valid_aps
            self.sm_ips = valid_sms

            # Fase 1: Tower Scan (SNMP)
            self._update_status("scanning", progress=10)
            self.log(f"Iniciando Tower Scan (Modo: {self.analysis_mode})...")

            scanner = TowerScanner(
                self.ap_ips,
                self.snmp_communities,
                sm_ips=self.sm_ips,
                log_callback=self.log,
            )
            scan_results_data = await scanner.start_tower_scan()

            self.progress = 40
            self.log("Fase de escaneo completada. Procesando resultados...")

            # Extraer resultados por tipo
            ap_scan_results = {
                ip: res for ip, res in scan_results_data.items() if ip in self.ap_ips
            }
            sm_scan_results = {
                ip: res for ip, res in scan_results_data.items() if ip in self.sm_ips
            }

            # Identificar dispositivos no alcanzables
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

            if failed_aps:
                self.log(
                    f"[WARNING] APs no alcanzables o que fallaron: {len(failed_aps)}",
                    "warning",
                )
                for ip in failed_aps:
                    res = ap_scan_results.get(ip)
                    if isinstance(res, dict):
                        reason = res.get("message", "Razon desconocida")
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
                        reason = res.get("message", "Razon desconocida")
                    else:
                        reason = str(res)
                    self.log(f"  - SM {ip}: {reason}", "warning")

            # Verificar que APs completaron
            completed_aps = []
            for ip, result in ap_scan_results.items():
                if isinstance(result, dict) and result.get("completed", False):
                    completed_aps.append(ip)
                elif isinstance(result, str):
                    logger.error(
                        f"Scan result for {ip} is a string (unexpected): {result}"
                    )

            if not completed_aps:
                error_msg = f"Ningun AP completo el escaneo exitosamente. APs fallidos: {len(failed_aps)}"
                if failed_aps:
                    error_msg += "\nDetalles:\n"
                    for ip in failed_aps:
                        res = ap_scan_results[ip]
                        msg = (
                            res.get("message", "Razon desconocida")
                            if isinstance(res, dict)
                            else str(res)
                        )
                        error_msg += f"  - {ip}: {msg}\n"
                raise Exception(error_msg)

            # Verificar que SMs completaron
            completed_sms = [
                ip
                for ip, result in sm_scan_results.items()
                if isinstance(result, dict) and result.get("completed", False)
            ]

            self.log(
                f"[OK] Completados: {len(completed_aps)}/{len(self.ap_ips)} APs, "
                f"{len(completed_sms)}/{len(self.sm_ips)} SMs",
                "success",
            )

            # Fase 2: Descargar espectro XML
            self._update_status("downloading", progress=50)
            self.log("Descargando archivos de espectro XML...")

            ap_xmls = {}
            for ip in completed_aps:
                try:
                    xml_data = await asyncio.to_thread(self._download_spectrum_xml, ip)
                    ap_xmls[ip] = xml_data
                    self.log(f"XML descargado de AP {ip}", "success")
                except Exception as e:
                    self.log(f"Error descargando XML de AP {ip}: {e}", "error")

            sm_xmls = {}
            for ip in completed_sms:
                try:
                    self.log(f"Esperando 5s antes de descargar de SM {ip}...")
                    await asyncio.sleep(5)

                    xml_data = await self._download_sm_xml_with_retries(
                        ip, max_retries=3, retry_delay=10
                    )
                    sm_xmls[ip] = xml_data
                    self.log(f"XML descargado de SM {ip}", "success")
                except Exception as e:
                    self.log(f"Error descargando XML de SM {ip}: {e}", "error")

            if not ap_xmls:
                raise Exception("No se pudieron descargar XMLs de ningun AP")

            self.log(f"XMLs descargados: {len(ap_xmls)} APs, {len(sm_xmls)} SMs")
            self.progress = 60

            # Fase 3: Analisis de frecuencias
            self._update_status("analyzing", progress=60)
            logger.info(f"[{self.scan_id}] Analizando frecuencias...")

            target_rx = self.config.get("target_rx_level", -52)
            analysis_results = {}

            if self.analysis_mode == "AP_SM_CROSS" and sm_xmls:
                # ANALISIS CRUZADO AP-SM
                logger.info(f"[{self.scan_id}] Ejecutando analisis cruzado AP-SM...")

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
                            f"[{self.scan_id}] Analisis cruzado para AP {ap_ip}..."
                        )

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
                            f"[{self.scan_id}] Espectro del AP parseado: "
                            f"{len(ap_spectrum)} puntos"
                        )

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

                        if ap_ip not in analysis_results:
                            analysis_results[ap_ip] = {}
                        analysis_results[ap_ip]["raw_spectrum"] = raw_spectrum_data

                        sm_data = []
                        logger.info(
                            f"[{self.scan_id}] Procesando {len(sm_xmls)} XMLs de SMs. "
                            f"IPs: {list(sm_xmls.keys())}"
                        )

                        for sm_ip in sm_xmls.keys():
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
                                        f"[{self.scan_id}] [OK] Espectro de SM {sm_ip} "
                                        f"parseado: {len(sm_spectrum)} puntos"
                                    )
                                else:
                                    logger.warning(
                                        f"[{self.scan_id}] [WARN] Espectro de SM "
                                        f"{sm_ip} vacio o invalido (puntos: "
                                        f"{len(sm_spectrum) if sm_spectrum else 0})"
                                    )
                            except Exception as e:
                                logger.error(
                                    f"[{self.scan_id}] [ERROR] Fallo parseo de SM "
                                    f"{sm_ip}: {e}"
                                )

                        if not sm_data:
                            logger.warning(
                                f"[{self.scan_id}] No hay datos de SMs validos despues "
                                f"del parseo (XMLs: {len(sm_xmls)}). Usando analisis "
                                f"solo de AP"
                            )
                            report = await asyncio.to_thread(
                                analyze_ap, ap_ip, target_rx
                            )
                            analysis_results[ap_ip] = report.to_dict()

                            sp_data = analysis_results[ap_ip].get("spectrum_data", {})
                            if isinstance(sp_data, list):
                                analysis_results[ap_ip]["spectrum_data"] = {
                                    "ap": sp_data,
                                    "sms": {},
                                }

                            continue

                        logger.info(
                            f"[{self.scan_id}] Ejecutando analisis cruzado con "
                            f"{len(sm_data)} SMs..."
                        )

                        df_combined, cross_results = (
                            cross_analyzer.analyze_multiband_ap_with_sms(
                                ap_spectrum, sm_data, top_n=20
                            )
                        )

                        logger.info(
                            f"[{self.scan_id}] Analisis cruzado completado: "
                            f"{len(cross_results)} frecuencias evaluadas"
                        )

                        best_combined = cross_analyzer.get_best_combined_frequency(
                            cross_results
                        )

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
                            "raw_spectrum": raw_spectrum_data,
                        }

                        logger.info(
                            f"[{self.scan_id}] Resultados almacenados para AP {ap_ip}"
                        )

                    except Exception as e:
                        logger.error(
                            f"[{self.scan_id}] Error en analisis cruzado de AP "
                            f"{ap_ip}: {str(e)}",
                            exc_info=True,
                        )
                        analysis_results[ap_ip] = {
                            "error": f"Error en analisis: {str(e)}"
                        }

                    progress_increment = 30 / len(completed_aps)
                    self.progress = 60 + int((i + 1) * progress_increment)

            else:
                # ANALISIS SOLO DE AP (modo original)
                logger.info(f"[{self.scan_id}] Ejecutando analisis solo de AP...")

                freq_analyzer = FrequencyAnalyzer()

                for i, ip in enumerate(completed_aps):
                    logger.info(f"[{self.scan_id}] Analizando AP {ip}...")

                    report = await asyncio.to_thread(analyze_ap, ip, target_rx)
                    analysis_results[ip] = report.to_dict()

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
                                    f"[{self.scan_id}] Raw spectrum saved for AP "
                                    f"{ip} ({len(spectrum_points)} points)"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Failed to parse raw spectrum for AP {ip}: {e}"
                            )

                    progress_increment = 30 / len(completed_aps)
                    self.progress = 60 + int((i + 1) * progress_increment)

            # Compilar resultados finales
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
                f"[{self.scan_id}] Resultados compilados: "
                f"{len(analysis_results)} APs analizados"
            )
            logger.info(
                f"[{self.scan_id}] Dispositivos alcanzables: "
                f"{len(completed_aps)} APs, {len(completed_sms)} SMs"
            )
            logger.info(
                f"[{self.scan_id}] Dispositivos fallidos: "
                f"{len(failed_aps)} APs, {len(failed_sms)} SMs"
            )

            self.status = "completed"
            self.progress = 100
            logger.info(f"[{self.scan_id}] Escaneo completado exitosamente")

            # Persist to DB storage
            import time as _time

            duration = (
                _time.monotonic() - self._start_time if self._start_time else None
            )
            if self.storage_manager is not None:
                try:
                    self.storage_manager.complete_scan(
                        self.scan_id, self.results, duration_seconds=duration
                    )
                    logger.info(f"[{self.scan_id}] Resultados guardados en DB storage")
                except Exception as exc:
                    logger.warning(
                        "[%s] storage complete_scan failed: %s", self.scan_id, exc
                    )

            # Cerrar auditoria con resultado exitoso
            if self.audit_manager:
                result_summary = (
                    f"Completado: {len(analysis_results)} APs analizados, "
                    f"modo {self.analysis_mode}"
                )
                self.audit_manager.end_transaction(result_summary=result_summary)

        except Exception as e:
            self.status = "failed"
            self.error = str(e)
            logger.error(f"[{self.scan_id}] Error: {str(e)}")

            # Cerrar auditoria con resultado fallido
            if self.audit_manager:
                self.audit_manager.end_transaction(result_summary=f"Fallo: {str(e)}")

            # Persist failure to DB storage
            if self.storage_manager is not None:
                try:
                    self.storage_manager.fail_scan(self.scan_id, self.error)
                except Exception as exc:
                    logger.warning(
                        "[%s] storage fail_scan failed: %s", self.scan_id, exc
                    )

    def _download_spectrum_xml(self, ip: str, timeout: int = 30) -> str:
        """Download spectrum XML file from a radio device."""
        analyzer = FrequencyAnalyzer()
        xml_data = analyzer.download_spectrum_data(
            ip, timeout=timeout, max_retries=3, retry_delay=5
        )

        if not xml_data:
            raise Exception(
                f"No se pudo descargar XML de {ip} despues de multiples intentos"
            )

        return xml_data

    async def _download_sm_xml_with_retries(
        self, ip: str, max_retries: int = 3, retry_delay: int = 10
    ) -> str:
        """Download SM XML with retries due to response delay."""
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    f"[{self.scan_id}] Intento {attempt}/{max_retries} descargando "
                    f"XML de SM {ip}"
                )

                timeout = 30 + (attempt * 15)

                xml_data = await asyncio.to_thread(
                    self._download_spectrum_xml, ip, timeout
                )

                if not xml_data:
                    raise Exception("XML vacio")

                if len(xml_data) < 100:
                    raise Exception(f"XML muy pequeno ({len(xml_data)} bytes)")

                if "<Freq" not in xml_data:
                    raise Exception(
                        "XML no contiene elementos <Freq> - posible escaneo incompleto"
                    )

                freq_count = xml_data.count("<Freq")
                if freq_count < 10:
                    raise Exception(
                        f"XML contiene muy pocos elementos Freq ({freq_count}) "
                        f"- posible escaneo incompleto"
                    )

                logger.info(
                    f"[{self.scan_id}] XML valido descargado de SM {ip} en intento "
                    f"{attempt} ({len(xml_data)} bytes, {freq_count} elementos)"
                )
                return xml_data

            except Exception as e:
                last_error = e
                logger.warning(
                    f"[{self.scan_id}] Intento {attempt}/{max_retries} fallido para "
                    f"SM {ip}: {e}"
                )

                if attempt < max_retries:
                    wait_time = retry_delay * (2 ** (attempt - 1))
                    logger.info(
                        f"[{self.scan_id}] Esperando {wait_time}s antes del siguiente "
                        f"intento (backoff exponencial)..."
                    )
                    await asyncio.sleep(wait_time)

        raise Exception(
            f"No se pudo descargar XML de SM {ip} despues de {max_retries} "
            f"intentos: {last_error}"
        )

    def run_in_thread(self):
        """Execute in a separate thread using asyncio.run()."""
        asyncio.run(self.execute())
