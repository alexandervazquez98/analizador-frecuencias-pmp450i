"""
Módulo para realizar Tower Scan en radios Cambium PMP 450i
Utiliza SNMP para orquestar escaneos de espectro simultáneos
"""

import asyncio
import re
import time
import os
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from pysnmp.hlapi import *
from pysnmp.proto.rfc1902 import Integer32, OctetString
import logging

# Regex para validar IPs v4 — usado en discovery para filtrar valores binarios
# que pysnmp puede retornar cuando str() se aplica sobre IpAddress objects.
_IPV4_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")

from app.freq_utils import format_scan_list, parse_scan_list


@dataclass
class SMDiscoveryResult:
    """Resultado del auto-discovery SNMP de un SM registrado en un AP.

    Campos obtenidos del linkTable del AP (OID base: 1.3.6.1.4.1.161.19.3.1.4.1):
      luid      — Logical Unit ID asignado por el AP (entero, empieza en 2)
      ip        — IP de gestión del SM (linkManagementIP .69)
      mac       — MAC address del SM (linkPhysAddress .3)
      site_name — Nombre del sitio configurado en el SM (linkSiteName .33)
      state     — Estado de sesión (linkSessState .19): 1 = IN SESSION
    """

    luid: int
    ip: str
    mac: str
    site_name: str
    state: int


# Logger del módulo (configuración centralizada en app/__init__.py)
logger = logging.getLogger(__name__)


class TowerScanner:
    """
    Clase para gestionar escaneos de espectro en múltiples APs simultáneamente
    """

    # OID para control de Spectrum Analysis (PMP 450i Legacy/V1)
    SPECTRUM_ACTION_OID = "1.3.6.1.4.1.161.19.3.3.2.221.0"
    SPECTRUM_DURATION_OID = "1.3.6.1.4.1.161.19.3.3.2.222.0"

    # OIDs para aplicación de frecuencia (change-006)
    RF_FREQ_CARRIER_OID = "1.3.6.1.4.1.161.19.3.1.1.2.0"  # AP — Integer32, kHz
    RF_SCAN_LIST_OID = (
        "1.3.6.1.4.1.161.19.3.2.1.1.0"  # SM — OctetString, kHz separado por coma
    )
    SM_BW_SCAN_OID = (
        "1.3.6.1.4.1.161.19.3.2.1.131.0"  # SM — bandwidthScan.0, OctetString
    )

    # Valores de control
    SET_FULL_SCAN = 8
    START_ANALYSIS = 1
    STATUS_IDLE = 4  # 4 = Idle/Done in V1

    # Timeouts y reintentos

    # Timeouts y reintentos
    SNMP_TIMEOUT = 5  # Aumentado para redes lentas
    SNMP_RETRIES = 3
    STATUS_CHECK_INTERVAL = 10  # segundos
    MAX_WAIT_TIME = 300  # 5 minutos máximo para APs

    # Timeouts específicos para SMs (tienen más delay)
    SM_SNMP_TIMEOUT = 8  # Mayor timeout para SMs (High Latency support)
    SM_SNMP_RETRIES = 3  # Reintentos
    SM_STATUS_CHECK_INTERVAL = 15
    SM_MAX_WAIT_TIME = 600
    SM_INITIAL_DELAY = 5

    SYSTEM_NAME_OID = "1.3.6.1.2.1.1.5.0"

    def __init__(
        self,
        ap_ips: List[str],
        snmp_communities: List[str] = None,
        sm_ips: List[str] = None,
        log_callback=None,
        write_community: str = None,
    ):
        """
        Inicializar el scanner

        Args:
            ap_ips: Lista de direcciones IP de los APs
            snmp_communities: Lista de comunidades SNMP a probar (default: ['Canopy'])
            sm_ips: Lista opcional de IPs de SMs para análisis cruzado
            log_callback: Función(msg, level) para logs externos
            write_community: Comunidad SNMP de escritura para operaciones SET (apply).
                             Si no se provee, se usa SNMP_WRITE_COMMUNITY de entorno,
                             o la primera entrada de snmp_communities como fallback.
        """
        self.ap_ips = ap_ips
        self.sm_ips = sm_ips or []

        # Handle string input or list
        if isinstance(snmp_communities, str):
            self.snmp_communities = [
                c.strip() for c in snmp_communities.split(",") if c.strip()
            ]
        else:
            # Fallback: leer de .env → "Canopy" si no hay nada
            if snmp_communities:
                self.snmp_communities = snmp_communities
            else:
                raw = os.environ.get("SNMP_COMMUNITIES", "Canopy")
                self.snmp_communities = [c.strip() for c in raw.split(",") if c.strip()]

        # Comunidad de escritura para operaciones SET (apply de frecuencia)
        if write_community:
            self.write_community = write_community
        else:
            env_write = os.environ.get("SNMP_WRITE_COMMUNITY", "").strip()
            self.write_community = env_write if env_write else self.snmp_communities[0]

        self.scan_results = {}
        self.log_callback = log_callback

        # Map: IP -> Working Community
        self.device_community_map = {}

    def _get_community(self, ip: str) -> str:
        """Obtener la comunidad correcta para una IP conocida"""
        return self.device_community_map.get(ip, self.snmp_communities[0])

    def _log(self, msg: str, level: str = "info"):
        """Helper para loggear a sistema y callback"""
        # Log del sistema
        if level == "error":
            logger.error(msg)
        elif level == "warning":
            logger.warning(msg)
        else:
            logger.info(msg)

        # Log al callback si existe
        if self.log_callback:
            try:
                # El callback de ScanTask espera (msg, level)
                self.log_callback(msg, level)
            except Exception:
                pass  # Evitar que un error de log rompa el scan

    def _snmp_set(
        self,
        ip: str,
        value: int,
        timeout: int = None,
        retries: int = None,
        oid: str = None,
    ) -> Tuple[bool, str]:
        """
        Enviar comando SNMP SET a un dispositivo
        """
        timeout = timeout or self.SNMP_TIMEOUT
        retries = retries or self.SNMP_RETRIES
        community = self._get_community(ip)
        target_oid = oid or self.SPECTRUM_ACTION_OID

        try:
            iterator = setCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=1),  # SNMPv2c
                UdpTransportTarget((ip, 161), timeout=timeout, retries=retries),
                ContextData(),
                ObjectType(ObjectIdentity(target_oid), Integer32(value)),
            )

            errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

            if errorIndication:
                return False, f"SNMP Error: {errorIndication}"
            elif errorStatus:
                status_str = errorStatus.prettyPrint()
                if "notWritable" in status_str:
                    return (
                        False,
                        f"Error de Permisos: La comunidad '{community}' es de SOLO LECTURA.",
                    )
                return False, f"SNMP Error: {status_str}"
            else:
                logger.debug(f"[OK] {ip}: SET value={value} exitoso")
                return True, "OK"

        except Exception as e:
            logger.error(f"[ERROR] {ip}: Excepción SNMP SET - {str(e)}")
            return False, str(e)

    def _snmp_get(
        self, ip: str, timeout: int = None, retries: int = None, oid: str = None
    ) -> Tuple[bool, int, str]:
        """
        Obtener valor SNMP GET de un dispositivo
        """
        timeout = timeout or self.SNMP_TIMEOUT
        retries = retries or self.SNMP_RETRIES
        community = self._get_community(ip)
        target_oid = (
            oid or self.SPECTRUM_ACTION_OID
        )  # Default to Action if not specified (legacy)

        try:
            iterator = getCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=1),
                UdpTransportTarget((ip, 161), timeout=timeout, retries=retries),
                ContextData(),
                ObjectType(ObjectIdentity(target_oid)),
            )

            errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

            if errorIndication:
                return False, 0, f"SNMP Error: {errorIndication}"
            elif errorStatus:
                return False, 0, f"SNMP Error: {errorStatus.prettyPrint()}"
            else:
                value = int(varBinds[0][1])
                return True, value, "OK"

        except Exception as e:
            logger.error(f"[ERROR] {ip}: Excepción SNMP GET - {str(e)}")
            return False, 0, str(e)

    def _verify_connectivity(
        self, ip: str, timeout: int = 2, retries: int = 1
    ) -> Tuple[bool, str]:
        """
        Verificar conectividad básica SNMP usando la comunidad mapeada
        """
        community = self._get_community(ip)
        try:
            # sysName
            sys_name_oid = "1.3.6.1.2.1.1.5.0"

            iterator = getCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=1),
                UdpTransportTarget((ip, 161), timeout=timeout, retries=retries),
                ContextData(),
                ObjectType(ObjectIdentity(sys_name_oid)),
            )

            errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

            if errorIndication:
                return False, f"Sin respuesta SNMP ({errorIndication})"
            elif errorStatus:
                return False, f"Error SNMP ({errorStatus.prettyPrint()})"
            else:
                return True, "Conectividad SNMP OK"

        except Exception as e:
            return False, f"Excepción de red: {str(e)}"

    async def _prepare_scan_async(self, ip: str, device_type: str = "AP") -> Dict:
        """
        FASE 1: Preparar dispositivo (Configurar Duración y Modo)
        NO inicia el escaneo.
        """
        result = {"ip": ip, "success": False, "message": "", "device_type": device_type}

        timeout = self.SM_SNMP_TIMEOUT if device_type == "SM" else self.SNMP_TIMEOUT
        retries = self.SM_SNMP_RETRIES if device_type == "SM" else self.SNMP_RETRIES

        # 1. Verificar conectividad
        is_reachable, reach_msg = await asyncio.to_thread(
            self._verify_connectivity, ip, 3, 1
        )
        if not is_reachable:
            result["message"] = f"No alcanzable: {reach_msg}"
            self._log(f"[{device_type}] {ip}: No responde - {reach_msg}", "warning")
            return result

        # 2. Configurar Duración (No bloqueante)
        scan_duration = 60 if device_type == "SM" else 40
        await asyncio.to_thread(
            self._snmp_set,
            ip,
            scan_duration,
            timeout,
            retries,
            oid=self.SPECTRUM_DURATION_OID,
        )

        # 3. Configurar Full Scan (8)
        success_full, msg_full = await asyncio.to_thread(
            self._snmp_set,
            ip,
            self.SET_FULL_SCAN,
            timeout,
            retries,
            oid=self.SPECTRUM_ACTION_OID,
        )

        if success_full:
            self._log(f"[{device_type}] {ip}: Preparado (Modo Espectro OK)", "info")
            result["success"] = True
            result["message"] = "Preparado correctamente"
        else:
            self._log(f"[{device_type}] {ip}: Falló preparación - {msg_full}", "error")
            result["message"] = f"Error configurando modo: {msg_full}"

        return result

    async def _start_scan_signal_async(self, ip: str, device_type: str = "AP") -> Dict:
        """
        FASE 2: Enviar señal de INICIO (Start Analysis)
        """
        result = {"ip": ip, "success": False, "message": "", "device_type": device_type}

        timeout = self.SM_SNMP_TIMEOUT if device_type == "SM" else self.SNMP_TIMEOUT
        retries = self.SM_SNMP_RETRIES if device_type == "SM" else self.SNMP_RETRIES

        # Reintentos agresivos para el comando de inicio (CRÍTICO)
        # Si esto falla, la sincronización se rompe
        start_retries = 3
        success_start = False
        msg_start = ""

        for attempt in range(start_retries):
            success_start, msg_start = await asyncio.to_thread(
                self._snmp_set,
                ip,
                self.START_ANALYSIS,
                timeout,
                retries,
                oid=self.SPECTRUM_ACTION_OID,
            )
            if success_start:
                break

            # Espera breve antes de reintentar
            if attempt < start_retries - 1:
                await asyncio.sleep(1.5)

        if success_start:
            self._log(f"[{device_type}] {ip}: INICIADO (Start OK)", "success")
            result["success"] = True
            result["message"] = "Iniciado correctamente"
        else:
            self._log(f"[{device_type}] {ip}: FALLÓ INICIO - {msg_start}", "error")
            result["message"] = f"Error enviando Start: {msg_start}"

        return result

    async def _wait_for_completion_async(
        self, ip: str, device_type: str = "AP"
    ) -> Dict:
        """
        Esperar a que el escaneo se complete (async)
        """
        result = {
            "ip": ip,
            "completed": False,
            "message": "",
            "device_type": device_type,
        }

        # Configurar parámetros
        if device_type == "SM":
            max_wait = self.SM_MAX_WAIT_TIME
            check_interval = self.SM_STATUS_CHECK_INTERVAL
            timeout = self.SM_SNMP_TIMEOUT
            retries = self.SM_SNMP_RETRIES
            initial_delay = self.SM_INITIAL_DELAY

            logger.info(
                f"[SM] {ip}: Esperando {initial_delay}s antes de verificar estado..."
            )
            await asyncio.sleep(initial_delay)
        else:
            max_wait = self.MAX_WAIT_TIME
            check_interval = self.STATUS_CHECK_INTERVAL
            timeout = self.SNMP_TIMEOUT
            retries = self.SNMP_RETRIES
            initial_delay = 0

        start_time = time.time()
        consecutive_errors = 0
        snmp_errors = 0

        # SMs no responden a SNMP mientras escanean espectro (comportamiento normal Cambium)
        # NO cuenta como error - solo esperamos hasta que respondan otimeout global
        max_consecutive_errors = 5 if device_type == "AP" else 999999  # SMs = infinito

        while (time.time() - start_time) < max_wait:
            # Usar STATUS OID para verificar estado
            success, value, msg = await asyncio.to_thread(
                self._snmp_get, ip, timeout, retries, oid=self.SPECTRUM_ACTION_OID
            )

            if not success:
                consecutive_errors += 1
                snmp_errors += 1

                # Para SMs, solo loguear cada N errores para no saturar logs
                if device_type == "SM" and snmp_errors % 10 == 1:
                    elapsed = int(time.time() - start_time)
                    self._log(
                        f"[{device_type}] {ip}: Sin respuesta SNMP ({elapsed}s) - SM en modo espectro (normal)",
                        "info",
                    )
                elif device_type == "AP":
                    self._log(
                        f"[{device_type}] {ip}: Error verificando estado ({consecutive_errors}/{max_consecutive_errors}) - {msg}",
                        "warning",
                    )

                if device_type == "SM":
                    # SMs: esperar sin importar timeouts (es normal que no respondan)
                    await asyncio.sleep(check_interval)
                    continue
                elif consecutive_errors >= max_consecutive_errors:
                    result["message"] = f"Demasiados errores consecutivos: {msg}"
                    self._log(
                        f"[{device_type}] {ip}: {max_consecutive_errors} errores consecutivos",
                        "error",
                    )
                    return result

                await asyncio.sleep(check_interval)
                continue

            consecutive_errors = 0
            snmp_errors = 0

            # Loggear estado cada vez para depuración
            if int(time.time()) % 5 == 0:
                self._log(
                    f"[{device_type}] {ip}: Estado leido={value} (Esperando Inactivo/IDLE)...",
                    "info",
                )

            # Si el valor indica fin de escaneo (4=IDLE legacy, 0=Stop)
            if value == 4 or value == 0:
                elapsed = time.time() - start_time
                result["completed"] = True
                result["message"] = f"Completado en {elapsed:.1f}s"
                self._log(
                    f"[{device_type}] {ip}: Escaneo completado en {elapsed:.1f}s",
                    "success",
                )
                return result

            # Si devuelve 1 (Active), seguimos esperando
            await asyncio.sleep(check_interval)

        elapsed = time.time() - start_time
        if device_type == "SM":
            # SM puede no haber terminado de reportar estado cuando el AP ya terminó
            result["completed"] = True
            result["message"] = f"Escaneo AP finalizado ({elapsed:.1f}s)"
            self._log(
                f"[{device_type}] {ip}: Timeout pero escaneo completado",
                "info",
            )
        else:
            result["message"] = f"Timeout esperando completar escaneo ({max_wait}s)"
            self._log(f"[{device_type}] {ip}: Timeout ({max_wait}s)", "error")
        return result

    async def validate_and_filter_devices(
        self,
    ) -> Tuple[List[str], List[str], Dict[str, str]]:
        """
        Auto-descubrimiento y validación:
        Prueba todas las comunidades SNMP en todos los dispositivos.
        Guarda la comunidad correcta para cada IP.

        Returns:
            (valid_aps, valid_sms, errors_dict)
        """
        valid_aps = []
        valid_sms = []
        errors = {}

        self._log(
            f"Comunidades configuradas ({len(self.snmp_communities)}): {self.snmp_communities}"
        )

        # Helper para probar comunidades
        sys_name_oid = self.SYSTEM_NAME_OID
        sys_descr_oid = "1.3.6.1.2.1.1.1.0"  # SysDescr como fallback
        cambium_sw_oid = "1.3.6.1.4.1.161.19.3.3.1.1.0"  # Cambium Software Version

        async def find_working_community(ip):
            msgs = []
            for community in self.snmp_communities:
                # Intento 1: sysName
                success, _, msg = await asyncio.to_thread(
                    self._snmp_get_oid_raw, ip, sys_name_oid, community
                )

                if success:
                    self._log(f"  [DEBUG] {ip}: Auth OK con '{community}' (sysName)")
                    return ip, True, community, "OK"

                # Intento 2: sysDescr (Fallback Standard)
                success_descr, _, msg_descr = await asyncio.to_thread(
                    self._snmp_get_oid_raw, ip, sys_descr_oid, community
                )
                if success_descr:
                    self._log(f"  [DEBUG] {ip}: Auth OK con '{community}' (sysDescr)")
                    return ip, True, community, "OK"

                # Intento 3: Cambium OID (Fallback Vendor)
                # Si es un equipo con MIB restringida que solo muestra cosas de Cambium
                success_cambium, _, msg_cambium = await asyncio.to_thread(
                    self._snmp_get_oid_raw, ip, cambium_sw_oid, community
                )
                if success_cambium:
                    self._log(
                        f"  [DEBUG] {ip}: Auth OK con '{community}' (Cambium SW Version)"
                    )
                    return ip, True, community, "OK"

                msgs.append(f"'{community}': {msg}")

            # Si ninguna funcionó
            failure_detail = "; ".join(msgs)
            self._log(
                f"  [ERROR] {ip}: Fallaron todas las comunidades. Detalles: {failure_detail}",
                "warning",
            )
            return (
                ip,
                False,
                None,
                f"Falló auth con todas las comunidades ({len(self.snmp_communities)} probadas). Detalles: {failure_detail}",
            )

        # Validar APs
        if self.ap_ips:
            self._log(
                f"[FASE 0-AUTH] Validando acceso a {len(self.ap_ips)} APs (timeout=5s, retries=2)..."
            )
            tasks = [find_working_community(ip) for ip in self.ap_ips]
            results = await asyncio.gather(*tasks)

            for ip, success, comm, msg in results:
                if success:
                    valid_aps.append(ip)
                    self.device_community_map[ip] = comm
                else:
                    errors[ip] = msg
                    logger.warning(f"[FASE 0-AUTH][OMITIDO] AP {ip}: {msg}")

        # Validar SMs
        if self.sm_ips:
            self._log(
                f"[FASE 0-AUTH] Validando acceso a {len(self.sm_ips)} SMs (timeout=5s, retries=2)..."
            )
            tasks = [find_working_community(ip) for ip in self.sm_ips]
            results = await asyncio.gather(*tasks)

            for ip, success, comm, msg in results:
                if success:
                    valid_sms.append(ip)
                    self.device_community_map[ip] = comm
                else:
                    errors[ip] = msg
                    logger.warning(f"[FASE 0-AUTH][OMITIDO] SM {ip}: {msg}")

        return valid_aps, valid_sms, errors

    async def start_tower_scan(self) -> Dict[str, Dict]:
        """
        Orquesta el proceso con CANDADO DE SEGURIDAD ESTRICTO (2 Fases)
        """
        # 1. Validación de conectividad y filtrado
        valid_aps, valid_sms, errors = await self.validate_and_filter_devices()
        results = {}
        for ip, msg in errors.items():
            results[ip] = {"completed": False, "message": msg}

        if not valid_aps:
            self._log("No hay APs válidos.", "error")
            return results

        # CANDADO 0: Todos los SMs descubiertos deben responder SNMP
        # Si hay SMs que no respondieron en AUTH, abortar
        if errors:
            failed_auth = list(errors.keys())
            self._log(
                f"ABORTO DE SEGURIDAD (Fase 0): {len(failed_auth)} SMs no respondieron SNMP: {failed_auth}",
                "error",
            )
            self._log(
                f"Se requieren {len(valid_sms) + len(failed_auth)} SMs operativos, pero solo {len(valid_sms)} respondieron.",
                "warning",
            )
            return results

        # =========================================================================
        # FASE 1: PREPARACIÓN DE SMs (Configurar Modo)
        # =========================================================================
        active_sms_prepared = []
        if valid_sms:
            self._log(
                f"[FASE 1-PREP] Preparando {len(valid_sms)} SMs (timeout=8s, retries=3)..."
            )
            prep_tasks = [self._prepare_scan_async(ip, "SM") for ip in valid_sms]
            prep_results = await asyncio.gather(*prep_tasks)

            failed_preps = []
            for res in prep_results:
                if res["success"]:
                    active_sms_prepared.append(res["ip"])
                else:
                    failed_preps.append(f"{res['ip']} ({res['message']})")
                    results[res["ip"]] = {"completed": False, "message": res["message"]}

            # CANDADO 1: Si algun SM falla en preparación, ABORTAR TODO.
            if failed_preps:
                err_msg = ", ".join(failed_preps)
                self._log(
                    f"ABORTO DE SEGURIDAD (Fase 1): Uno o más SMs fallaron preparación: {err_msg}",
                    "error",
                )
                self._log(
                    "Cancelando operación para mantener integridad de red.", "warning"
                )
                return results

        # =========================================================================
        # FASE 2: INICIO SINCRONIZADO DE SMs
        # =========================================================================
        active_sms_started = []
        if active_sms_prepared:
            self._log("[FASE 2-START] Iniciando escaneo en SMs (sincronizado)...")
            # Pequeña pausa para asegurar que todos procesaron la preparación
            await asyncio.sleep(1)

            start_tasks = [
                self._start_scan_signal_async(ip, "SM") for ip in active_sms_prepared
            ]
            axis_results = await asyncio.gather(*start_tasks)

            failed_starts = []
            for res in axis_results:
                if res["success"]:
                    active_sms_started.append(res["ip"])
                else:
                    # FALLO CRÍTICO: Un SM preparado no inició.
                    failed_starts.append(f"{res['ip']}")
                    results[res["ip"]] = {"completed": False, "message": res["message"]}

            # CANDADO 2: Si algun SM falla al iniciar, NO INICIAR AP.
            if failed_starts:
                err_msg = ", ".join(failed_starts)
                self._log(
                    f"ABORTO DE SEGURIDAD (Fase 2): Falló comando START en SMs: {err_msg}",
                    "error",
                )
                self._log(
                    "EL AP NO INICIARÁ EL ESCANEO para proteger a los SMs desconectados.",
                    "critical",
                )
                return results

            self._log("Todos los SMs iniciados correctamente.")

        # =========================================================================
        # FASE 3: INICIO DE AP (Solo si superamos los candados)
        # =========================================================================
        active_aps = []
        if valid_aps:
            self._log("[FASE 3-AP] Iniciando AP...")
            # Usar prepare + start para AP también por consistencia, o el helper viejo
            # Usaremos el flow directo
            for ip in valid_aps:
                # Secuencial para AP para asegurar control
                res_prep = await self._prepare_scan_async(ip, "AP")
                if res_prep["success"]:
                    res_start = await self._start_scan_signal_async(ip, "AP")
                    if res_start["success"]:
                        active_aps.append(ip)
                    else:
                        results[ip] = {
                            "completed": False,
                            "message": res_start["message"],
                        }
                else:
                    results[ip] = {"completed": False, "message": res_prep["message"]}

        if not active_aps and not active_sms_started:
            return results

        self._log(
            f"[FASE 4-WAIT] Esperando completación: {len(active_aps)} AP(s), {len(active_sms_started)} SMs (intervalo={self.SM_STATUS_CHECK_INTERVAL}s)..."
        )

        # 4. Esperar finalización
        wait_tasks = []
        wait_tasks.extend(
            [self._wait_for_completion_async(ip, "AP") for ip in active_aps]
        )
        wait_tasks.extend(
            [self._wait_for_completion_async(ip, "SM") for ip in active_sms_started]
        )

        scan_completion_results = await asyncio.gather(*wait_tasks)

        completed_sms = []
        failed_sms_results = []
        for res in scan_completion_results:
            results[res["ip"]] = res
            if res.get("device_type") == "SM":
                if res.get("completed"):
                    completed_sms.append(res["ip"])
                else:
                    failed_sms_results.append(res["ip"])

        expected_sms_count = len(active_sms_started)
        actual_sms_count = len(completed_sms)

        if actual_sms_count < expected_sms_count:
            missing = expected_sms_count - actual_sms_count
            pct = (missing / expected_sms_count * 100) if expected_sms_count > 0 else 0

            if pct > 20:
                self._log(
                    f"ALERTA: Solo {actual_sms_count}/{expected_sms_count} SMs completaron escaneo ({pct:.0f}% falla)",
                    "error",
                )
                self._log(
                    f"SMs que no completaron: {failed_sms_results[:5]}...",
                    "warning",
                )
            else:
                self._log(
                    f"WARNING: {actual_sms_count}/{expected_sms_count} SMs completaron ({pct:.0f}% no respondió)",
                    "warning",
                )

        self._log(
            f"Tower Scan finalizado: {len(completed_sms)} SMs, {len(active_aps)} AP(s)"
        )
        return results

    def _snmp_get_oid_raw(
        self, ip: str, oid: str, community: str, timeout: int = 5, retries: int = 2
    ) -> Tuple[bool, str, str]:
        """Helper para probar una comunidad específica"""
        try:
            iterator = getCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=1),
                UdpTransportTarget((ip, 161), timeout=timeout, retries=retries),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
            )
            errorIndication, errorStatus, _, varBinds = next(iterator)

            if errorIndication:
                return False, "", str(errorIndication)
            elif errorStatus:
                return False, "", str(errorStatus)
            return True, str(varBinds[0][1]), "OK"
        except Exception:
            return False, "", "Excepción"

    def _snmp_get_oid(
        self, ip: str, oid: str, timeout: int = 2, retries: int = 1
    ) -> Tuple[bool, str, str]:
        """Helper genérico para GET OID usando la comunidad mapeada"""
        community = self._get_community(ip)
        return self._snmp_get_oid_raw(
            ip, oid, community, timeout=timeout, retries=retries
        )

    # =========================================================================
    # MÉTODOS DE APPLY DE FRECUENCIA (change-006)
    # =========================================================================

    def _snmp_set_string(
        self,
        ip: str,
        oid: str,
        value: str,
        community: str = None,
        timeout: int = None,
        retries: int = None,
    ) -> Tuple[bool, str]:
        """
        Enviar comando SNMP SET con valor OctetString a un dispositivo.

        Equivalente a _snmp_set() pero para tipos OctetString (e.g. rfScanList).
        Usa self.write_community por defecto (no la comunidad de lectura).

        Args:
            ip: Dirección IP del dispositivo.
            oid: OID completo a setear.
            value: Valor string a escribir (se codifica como OctetString).
            community: Comunidad SNMP de escritura. Si es None, usa self.write_community.
            timeout: Timeout en segundos. Si es None, usa SNMP_TIMEOUT.
            retries: Reintentos. Si es None, usa SNMP_RETRIES.

        Returns:
            Tuple (success: bool, message: str).
        """
        timeout = timeout or self.SNMP_TIMEOUT
        retries = retries or self.SNMP_RETRIES
        comm = community or self.write_community

        try:
            iterator = setCmd(
                SnmpEngine(),
                CommunityData(comm, mpModel=1),  # SNMPv2c
                UdpTransportTarget((ip, 161), timeout=timeout, retries=retries),
                ContextData(),
                ObjectType(ObjectIdentity(oid), OctetString(value)),
            )

            errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

            if errorIndication:
                return False, f"SNMP Error: {errorIndication}"
            elif errorStatus:
                status_str = errorStatus.prettyPrint()
                if "notWritable" in status_str:
                    return (
                        False,
                        f"Error de Permisos: La comunidad '{comm}' es de SOLO LECTURA.",
                    )
                return False, f"SNMP Error: {status_str}"
            else:
                logger.debug(f"[OK] {ip}: SET OctetString oid={oid} exitoso")
                return True, "OK"

        except Exception as e:
            logger.error(f"[ERROR] {ip}: Excepción SNMP SET string - {str(e)}")
            return False, str(e)

    def set_frequency(self, ip: str, freq_khz: int) -> Tuple[bool, str]:
        """
        Aplicar frecuencia portadora al AP via SNMP SET (rfFreqCarrier).

        OID: 1.3.6.1.4.1.161.19.3.1.1.2.0 (Integer32, valor en kHz).
        Usa la comunidad de escritura (self.write_community).

        Args:
            ip: Dirección IP del AP.
            freq_khz: Frecuencia objetivo en kHz. Ejemplo: 3554000.

        Returns:
            Tuple (success: bool, message: str).
        """
        self._log(
            f"[APPLY] {ip}: SET rfFreqCarrier = {freq_khz} kHz (OID {self.RF_FREQ_CARRIER_OID})",
            "info",
        )

        # _snmp_set usa self._get_community(ip) — necesitamos la write_community.
        # La seteamos como comunidad de esta IP temporalmente vía override directo.
        try:
            iterator = setCmd(
                SnmpEngine(),
                CommunityData(self.write_community, mpModel=1),
                UdpTransportTarget(
                    (ip, 161), timeout=self.SNMP_TIMEOUT, retries=self.SNMP_RETRIES
                ),
                ContextData(),
                ObjectType(
                    ObjectIdentity(self.RF_FREQ_CARRIER_OID), Integer32(freq_khz)
                ),
            )

            errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

            if errorIndication:
                msg = f"SNMP Error: {errorIndication}"
                self._log(f"[APPLY] {ip}: FALLÓ set_frequency — {msg}", "error")
                return False, msg
            elif errorStatus:
                status_str = errorStatus.prettyPrint()
                if "notWritable" in status_str:
                    msg = f"Error de Permisos: La comunidad '{self.write_community}' es de SOLO LECTURA."
                else:
                    msg = f"SNMP Error: {status_str}"
                self._log(f"[APPLY] {ip}: FALLÓ set_frequency — {msg}", "error")
                return False, msg
            else:
                self._log(
                    f"[APPLY] {ip}: SET rfFreqCarrier = {freq_khz} kHz OK", "info"
                )
                return True, "OK"

        except Exception as e:
            msg = str(e)
            self._log(f"[APPLY] {ip}: Excepción set_frequency — {msg}", "error")
            return False, msg

    def set_sm_scan_list(self, ip: str, freqs_khz: list) -> Tuple[bool, str]:
        """
        Configurar lista de escaneo del SM via SNMP SET (rfScanList).

        OID: 1.3.6.1.4.1.161.19.3.2.1.1.0 (OctetString, kHz separados por coma).
        La lista de frecuencias se formatea con format_scan_list() de freq_utils.

        Args:
            ip: Dirección IP del SM.
            freqs_khz: Lista de frecuencias en kHz. Ejemplo: [3550000, 3555000].

        Returns:
            Tuple (success: bool, message: str).
        """
        scan_list_str = format_scan_list(freqs_khz)
        self._log(
            f"[APPLY] {ip}: SET rfScanList = '{scan_list_str}' (OID {self.RF_SCAN_LIST_OID})",
            "info",
        )

        success, msg = self._snmp_set_string(
            ip=ip,
            oid=self.RF_SCAN_LIST_OID,
            value=scan_list_str,
            timeout=self.SM_SNMP_TIMEOUT,
            retries=self.SM_SNMP_RETRIES,
        )

        if success:
            self._log(f"[APPLY] {ip}: SET rfScanList OK — '{scan_list_str}'", "info")
        else:
            self._log(f"[APPLY] {ip}: FALLÓ set_sm_scan_list — {msg}", "error")

        return success, msg

    def set_sm_bandwidth_scan(
        self, ip: str, width_mhz: "int | List[int]"
    ) -> Tuple[bool, str]:
        """SET bandwidthScan.0 on SM via SNMP — configures allowed channel widths.

        OID: .1.3.6.1.4.1.161.19.3.2.1.131.0 (bandwidthScan.0, OctetString)
        Value format: "20.0 MHz" or "15.0 MHz,20.0 MHz" (comma-separated, no space).

        MUST be called BEFORE changing AP bandwidth so the SM knows which
        channel widths to scan for when re-registering after reboot.

        Make-before-break: pass a LIST of bandwidths (current + new) so that the
        SM can still re-register on the OLD bandwidth if the AP rolls back.

        Args:
            ip:        SM IP address.
            width_mhz: Single bandwidth int (e.g. 20) OR list of ints (e.g. [15, 20]).
                       Valid values: 5, 7, 10, 15, 20, 30, 40.

        Returns:
            Tuple (success: bool, message: str).
        """
        VALID_BWS = [5, 7, 10, 15, 20, 30, 40]

        # Normalise to list for uniform handling — backward compat with single int
        if isinstance(width_mhz, list):
            widths = [int(w) for w in width_mhz]
        else:
            widths = [int(width_mhz)]

        for w in widths:
            if w not in VALID_BWS:
                return False, (
                    f"Ancho de canal {w} MHz no soportado para SM. Válidos: {VALID_BWS}"
                )

        bw_str = ",".join(
            f"{float(w):.1f} MHz" for w in widths
        )  # → "20.0 MHz" or "15.0 MHz,20.0 MHz"
        self._log(
            f"[APPLY] {ip}: SET bandwidthScan = '{bw_str}' (OID {self.SM_BW_SCAN_OID})",
            "info",
        )

        success, msg = self._snmp_set_string(
            ip=ip,
            oid=self.SM_BW_SCAN_OID,
            value=bw_str,
            timeout=self.SM_SNMP_TIMEOUT,
            retries=self.SM_SNMP_RETRIES,
        )

        if success:
            self._log(f"[APPLY] {ip}: SET bandwidthScan='{bw_str}' OK", "info")
        else:
            self._log(f"[APPLY] {ip}: FALLÓ set_sm_bandwidth_scan — {msg}", "error")

        return success, msg

    def _snmp_get_oid_sm(self, ip: str, oid: str) -> Tuple[bool, str, str]:
        """GET an OctetString OID from a SM, trying ALL communities until one succeeds.

        Unlike _snmp_get_oid() (which uses a single mapped community), this helper
        iterates self.snmp_communities so it works even when device_community_map is
        empty — e.g. when FrequencyApplyManager creates a scanner without running
        the full discovery/validation flow first (Issue #2).

        Uses SM_SNMP_TIMEOUT and SM_SNMP_RETRIES (Issue #3) — SMs have higher
        latency than APs so the AP-level 2-second timeout is insufficient.

        Args:
            ip:  SM IP address.
            oid: OID to GET (OctetString expected).

        Returns:
            Tuple (success: bool, raw_value: str, message: str).
            On all-communities failure returns (False, '', "all communities failed").
        """
        last_msg = "no communities configured"
        for community in self.snmp_communities:
            ok, raw, msg = self._snmp_get_oid_raw(
                ip,
                oid,
                community,
                timeout=self.SM_SNMP_TIMEOUT,
                retries=self.SM_SNMP_RETRIES,
            )
            if ok:
                return True, raw, "OK"
            last_msg = msg

        # All communities exhausted
        return False, "", f"all communities failed: {last_msg}"

    def get_sm_scan_list(self, ip: str) -> Tuple[bool, List[int], str]:
        """GET current rfScanList.0 from SM via SNMP.

        OID: .1.3.6.1.4.1.161.19.3.2.1.1.0 (RF_SCAN_LIST_OID, OctetString)
        Response format: "3650000, 3660000" (frequencies in kHz, comma-separated).

        Used by make-before-break strategy: read current scan list before merging
        with the new frequency so that SM can still find the AP if it rolls back.

        Tries ALL configured SNMP communities (not just the mapped one) so it works
        correctly even when device_community_map is empty (Issue #2).
        Uses SM_SNMP_TIMEOUT / SM_SNMP_RETRIES for higher-latency SM links (Issue #3).

        Args:
            ip: SM IP address.

        Returns:
            Tuple (success: bool, freqs_khz: List[int], message: str).
            On failure returns (False, [], error_message).
        """
        ok, raw, msg = self._snmp_get_oid_sm(ip, self.RF_SCAN_LIST_OID)
        if not ok:
            self._log(
                f"[APPLY] {ip}: GET rfScanList FAILED — {msg}",
                "warning",
            )
            return False, [], msg

        try:
            freqs = parse_scan_list(raw)
        except Exception as exc:
            err = f"parse_scan_list failed: {exc}"
            self._log(f"[APPLY] {ip}: GET rfScanList parse error — {err}", "warning")
            return False, [], err
        self._log(
            f"[APPLY] {ip}: GET rfScanList = '{raw}' → {freqs}",
            "info",
        )
        return True, freqs, "OK"

    def get_sm_bandwidth_scan(self, ip: str) -> Tuple[bool, List[str], str]:
        """GET current bandwidthScan.0 from SM via SNMP.

        OID: .1.3.6.1.4.1.161.19.3.2.1.131.0 (SM_BW_SCAN_OID, OctetString)
        Response format: "5.0 MHz, 20.0 MHz" (bandwidth strings, comma-separated).

        Used by make-before-break strategy: read current bandwidth scan list before
        merging with the new bandwidth so that SM can still re-register if AP rolls back.

        Tries ALL configured SNMP communities (not just the mapped one) so it works
        correctly even when device_community_map is empty (Issue #2).
        Uses SM_SNMP_TIMEOUT / SM_SNMP_RETRIES for higher-latency SM links (Issue #3).

        Args:
            ip: SM IP address.

        Returns:
            Tuple (success: bool, bws: List[str], message: str).
            On failure returns (False, [], error_message).
        """
        ok, raw, msg = self._snmp_get_oid_sm(ip, self.SM_BW_SCAN_OID)
        if not ok:
            self._log(
                f"[APPLY] {ip}: GET bandwidthScan FAILED — {msg}",
                "warning",
            )
            return False, [], msg

        if not raw or not raw.strip():
            self._log(
                f"[APPLY] {ip}: GET bandwidthScan = '' (empty)",
                "info",
            )
            return True, [], "OK"

        bws = [part.strip() for part in raw.split(",") if part.strip()]
        self._log(
            f"[APPLY] {ip}: GET bandwidthScan = '{raw}' → {bws}",
            "info",
        )
        return True, bws, "OK"

    def set_channel_width(
        self, ip: str, width_mhz: int, ap_freq_mhz: float = None
    ) -> Tuple[bool, str]:
        """SET channel bandwidth on AP via SNMP.

        OID priority (confirmed by Cambium MIB field testing):
          1. .1.3.6.1.4.1.161.19.3.3.2.83.0  — channelBandwidth.0 (OctetString)
             Format: "5.0 MHz", "7.0 MHz", "10.0 MHz", "20.0 MHz", etc.
          2. .1.3.6.1.4.1.161.19.3.3.2.91.0  — bandwidth.0 (Integer fallback)

        IMPORTANT — Universal Integer map (all Cambium PMP450i hardware):
          1=5MHz, 2=7MHz, 3=10MHz, 4=15MHz, 5=20MHz, 6=30MHz, 7=40MHz
          The 7MHz slot exists in ALL firmware enum tables regardless of band.
          Band only restricts which values are VALID to select — not the enum.
          7MHz is physically only available on 3GHz hardware (band 3000-3900 MHz).

        NOTE: OID 221.0 is SPECTRUM_ACTION_OID — do NOT use for channel bandwidth.

        Args:
            ip:          AP IP address.
            width_mhz:   Channel width in MHz (5, 7, 10, 15, 20, 30 or 40).
            ap_freq_mhz: Kept for API compatibility — not used for band detection.

        Returns:
            Tuple (success: bool, message: str).
        """
        # ── Universal Integer map (all PMP450i firmware) ─────────────
        # 7MHz occupies position 2 on ALL hardware — band only restricts UI selection
        BW_INT_MAP = {5: 1, 7: 2, 10: 3, 15: 4, 20: 5, 30: 6, 40: 7}
        valid_bws = list(BW_INT_MAP.keys())

        bw_int = BW_INT_MAP.get(int(width_mhz))
        if bw_int is None:
            return False, (
                f"Ancho de canal {width_mhz} MHz no soportado. Válidos: {valid_bws}"
            )

        # OID 1: OctetString — único con SET confirmado por Cambium
        CHANNEL_BW_OID_STR = "1.3.6.1.4.1.161.19.3.3.2.83.0"  # channelBandwidth.0
        # OID 2: Integer fallback — mapa universal
        CHANNEL_BW_OID_INT = "1.3.6.1.4.1.161.19.3.3.2.91.0"  # bandwidth.0
        bw_str = f"{float(width_mhz):.1f} MHz"  # → "20.0 MHz", "7.0 MHz", etc.

        self._log(
            f"[APPLY] {ip}: SET channelBandwidth = {width_mhz} MHz "
            f"(str='{bw_str}', int={bw_int})",
            "info",
        )

        # Try 1: OctetString OID .83.0 — valor "X.0 MHz" (PRIMARIO, confirmado escribible)
        success, msg = self._snmp_set_string(
            ip=ip,
            oid=CHANNEL_BW_OID_STR,
            value=bw_str,
        )
        if success:
            self._log(
                f"[APPLY] {ip}: SET channelBandwidth='{bw_str}' OK (OID .83.0 OctetString)",
                "info",
            )
            return True, "OK"
        self._log(
            f"[APPLY] {ip}: OID .83.0 falló ({msg}) — probando Integer .91.0 (universal map)",
            "warning",
        )

        # Try 2: Integer OID .91.0 (fallback) — mapa universal todos los PMP450i
        try:
            iterator = setCmd(
                SnmpEngine(),
                CommunityData(self.write_community, mpModel=1),
                UdpTransportTarget(
                    (ip, 161), timeout=self.SNMP_TIMEOUT, retries=self.SNMP_RETRIES
                ),
                ContextData(),
                ObjectType(ObjectIdentity(CHANNEL_BW_OID_INT), Integer32(bw_int)),
            )
            errInd, errStat, _, _ = next(iterator)
            if not errInd and not errStat:
                self._log(
                    f"[APPLY] {ip}: SET channelBandwidth={width_mhz}MHz OK "
                    f"(OID .91.0 int={bw_int} universal map)",
                    "warning",
                )
                return True, "OK (via .91.0)"
            msg = f"SNMP Error: {errInd or errStat.prettyPrint()}"
            self._log(
                f"[APPLY] {ip}: FALLÓ ambos OIDs de channelBandwidth — {msg}", "error"
            )
            return False, msg
        except Exception as e:
            msg = str(e)
            self._log(f"[APPLY] {ip}: Excepción OID .91.0 — {msg}", "error")
            return False, msg

    def set_contention_slots(self, ip: str) -> Tuple[bool, str]:
        """SET numCtlSlotsHW = 4 (hardcoded, OBLIGATORIO).

        OID primario: .1.3.6.1.4.1.161.19.3.1.1.42.0 (numCtlSlotsHW.0, Integer)
        OID alternativo: .1.3.6.1.4.1.161.19.3.1.10.1.1.4.1 (radioControlSlots.1)

        Args:
            ip: AP IP address.

        Returns:
            Tuple (success: bool, message: str).
        """
        CONTENTION_OID_PRIMARY = "1.3.6.1.4.1.161.19.3.1.1.42.0"  # numCtlSlotsHW.0
        CONTENTION_OID_ALT = "1.3.6.1.4.1.161.19.3.1.10.1.1.4.1"  # radioControlSlots.1
        VALUE = 4

        self._log(
            f"[APPLY] {ip}: SET numCtlSlotsHW = {VALUE} (contention slots)", "info"
        )

        for oid, label in [
            (CONTENTION_OID_PRIMARY, "primary"),
            (CONTENTION_OID_ALT, "alt"),
        ]:
            try:
                iterator = setCmd(
                    SnmpEngine(),
                    CommunityData(self.write_community, mpModel=1),
                    UdpTransportTarget(
                        (ip, 161), timeout=self.SNMP_TIMEOUT, retries=self.SNMP_RETRIES
                    ),
                    ContextData(),
                    ObjectType(ObjectIdentity(oid), Integer32(VALUE)),
                )
                errInd, errStat, _, _ = next(iterator)
                if not errInd and not errStat:
                    self._log(
                        f"[APPLY] {ip}: SET contention_slots=4 OK (OID {label})", "info"
                    )
                    return True, "OK"
                self._log(
                    f"[APPLY] {ip}: OID {label} falló ({errInd or errStat})", "warning"
                )
            except Exception as e:
                self._log(
                    f"[APPLY] {ip}: Excepción contention OID {label} — {e}", "warning"
                )

        return False, "FALLÓ SET contention_slots en todos los OIDs"

    def set_broadcast_retry(self, ip: str) -> Tuple[bool, str]:
        """SET broadcastRetryCount.0 = 0 (hardcoded, OBLIGATORIO).

        Evita saturar la bajada con paquetes repetidos en redes CCTV.
        Por defecto el equipo usa 2 (3 envíos totales). Se fija en 0.

        OID: .1.3.6.1.4.1.161.19.3.1.1.35.0 (broadcastRetryCount.0, Integer)

        Args:
            ip: AP IP address.

        Returns:
            Tuple (success: bool, message: str).
        """
        BROADCAST_OID = "1.3.6.1.4.1.161.19.3.1.1.35.0"  # broadcastRetryCount.0
        VALUE = 0

        self._log(f"[APPLY] {ip}: SET broadcastRetryCount = {VALUE}", "info")
        try:
            iterator = setCmd(
                SnmpEngine(),
                CommunityData(self.write_community, mpModel=1),
                UdpTransportTarget(
                    (ip, 161), timeout=self.SNMP_TIMEOUT, retries=self.SNMP_RETRIES
                ),
                ContextData(),
                ObjectType(ObjectIdentity(BROADCAST_OID), Integer32(VALUE)),
            )
            errInd, errStat, _, _ = next(iterator)
            if not errInd and not errStat:
                self._log(f"[APPLY] {ip}: SET broadcastRetryCount=0 OK", "info")
                return True, "OK"
            msg = f"SNMP Error: {errInd or errStat.prettyPrint()}"
            self._log(f"[APPLY] {ip}: FALLÓ set_broadcast_retry — {msg}", "error")
            return False, msg
        except Exception as e:
            msg = str(e)
            self._log(f"[APPLY] {ip}: Excepción set_broadcast_retry — {msg}", "error")
            return False, msg

    def reboot_if_required(self, ip: str) -> Tuple[bool, str]:
        """Trigger conditional reboot on AP via SNMP SET rebootIfRequired.0 = 1.

        OID: .1.3.6.1.4.1.161.19.3.3.3.4.0 (rebootIfRequired.0, Integer)
        Value: 1 — the device evaluates pending changes and reboots automatically
                     if any parameter (frequency, channel width, color code, etc.)
                     requires it.

        MUST be called LAST in the apply sequence, after all other SETs.
        The AP will become unreachable for ~30-60 s during reboot.

        Args:
            ip: AP IP address.

        Returns:
            Tuple (success: bool, message: str).
            NOTE: Even if SET succeeds, subsequent SNMP to this IP will time out
            while the device reboots — this is expected behaviour.
        """
        REBOOT_OID = "1.3.6.1.4.1.161.19.3.3.3.4.0"  # rebootIfRequired.0
        VALUE = 1

        self._log(
            f"[APPLY] {ip}: SET rebootIfRequired = {VALUE} (reinicio condicional)",
            "info",
        )
        try:
            iterator = setCmd(
                SnmpEngine(),
                CommunityData(self.write_community, mpModel=1),
                UdpTransportTarget(
                    (ip, 161), timeout=self.SNMP_TIMEOUT, retries=self.SNMP_RETRIES
                ),
                ContextData(),
                ObjectType(ObjectIdentity(REBOOT_OID), Integer32(VALUE)),
            )
            errInd, errStat, _, _ = next(iterator)
            if not errInd and not errStat:
                self._log(
                    f"[APPLY] {ip}: rebootIfRequired=1 enviado — el equipo reiniciará si es necesario",
                    "info",
                )
                return True, "Reboot iniciado"
            msg = f"SNMP Error: {errInd or errStat.prettyPrint()}"
            self._log(f"[APPLY] {ip}: FALLÓ reboot_if_required — {msg}", "error")
            return False, msg
        except Exception as e:
            msg = str(e)
            self._log(f"[APPLY] {ip}: Excepción reboot_if_required — {msg}", "error")
            return False, msg

    # =========================================================================
    # MÉTODOS DE AUTO-DISCOVERY (change: ap-sm-autodiscovery)
    # =========================================================================

    def _snmp_walk_oid(
        self, ap_ip: str, base_oid: str, community: str
    ) -> Dict[int, str]:
        """SNMP WALK sobre un OID base del linkTable del AP.

        Itera con GETNEXT hasta salir del subárbol (lexicographicMode=False).
        Extrae el LUID del último componente del OID resultante.

        Args:
            ap_ip:    IP del Access Point a consultar.
            base_oid: OID raíz del WALK (e.g. '1.3.6.1.4.1.161.19.3.1.4.1.69').
            community: Comunidad SNMP de lectura.

        Returns:
            Dict[luid, value_str] — valor como string para cada LUID encontrado.
        """
        result: Dict[int, str] = {}
        try:
            for (
                errorIndication,
                errorStatus,
                errorIndex,
                varBinds,
            ) in nextCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=1),
                UdpTransportTarget(
                    (ap_ip, 161),
                    timeout=self.SNMP_TIMEOUT,
                    retries=self.SNMP_RETRIES,
                ),
                ContextData(),
                ObjectType(ObjectIdentity(base_oid)),
                lexicographicMode=False,  # Detener al salir del subárbol
            ):
                if errorIndication:
                    logger.warning(
                        f"[DISCOVERY] WALK {base_oid} en {ap_ip}: {errorIndication}"
                    )
                    break
                if errorStatus:
                    logger.warning(
                        f"[DISCOVERY] WALK {base_oid} en {ap_ip}: "
                        f"{errorStatus.prettyPrint()}"
                    )
                    break
                for varBind in varBinds:
                    oid_str = str(varBind[0])
                    value = varBind[1]
                    try:
                        luid = int(oid_str.split(".")[-1])
                        # Usar prettyPrint() para que IpAddress renderice como
                        # dotted-decimal (10.53.5.79) en lugar de bytes raw
                        # (ï¿½5Oï¿½). str() sobre IpAddress en pysnmp devuelve bytes ASCII.
                        result[luid] = value.prettyPrint()
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            logger.warning(f"[DISCOVERY] Excepción WALK {base_oid} en {ap_ip}: {e}")
        return result

    async def discover_registered_sms_from_ap(
        self, ap_ip: str
    ) -> List[SMDiscoveryResult]:
        """Descubre los SMs registrados en un AP vía SNMP WALK sobre linkTable.

        Realiza WALK sobre 4 OIDs en paralelo (asyncio.gather) todos indexados
        por LUID. Solo retorna SMs con linkSessState == 1 (IN SESSION).

        OIDs del linkTable (base: 1.3.6.1.4.1.161.19.3.1.4.1):
          .19 — linkSessState  (1 = IN SESSION)
          .69 — linkManagementIP
          .3  — linkPhysAddress (MAC)
          .33 — linkSiteName

        Args:
            ap_ip: IP del Access Point.

        Returns:
            Lista de SMDiscoveryResult con state == 1. Lista vacía si el AP
            no tiene SMs registrados o no responde.
        """
        BASE = "1.3.6.1.4.1.161.19.3.1.4.1"
        OID_STATE = f"{BASE}.19"
        OID_IP = f"{BASE}.69"
        OID_MAC = f"{BASE}.3"
        OID_NAME = f"{BASE}.33"

        community = self._get_community(ap_ip)

        self._log(f"[DISCOVERY] {ap_ip}: WALK linkTable (state/ip/mac/site_name)...")

        # WALK los 4 OIDs en paralelo — todos independientes entre sí
        state_map, ip_map, mac_map, name_map = await asyncio.gather(
            asyncio.to_thread(self._snmp_walk_oid, ap_ip, OID_STATE, community),
            asyncio.to_thread(self._snmp_walk_oid, ap_ip, OID_IP, community),
            asyncio.to_thread(self._snmp_walk_oid, ap_ip, OID_MAC, community),
            asyncio.to_thread(self._snmp_walk_oid, ap_ip, OID_NAME, community),
        )

        results: List[SMDiscoveryResult] = []
        for luid, state_raw in state_map.items():
            try:
                state = int(state_raw)
            except (ValueError, TypeError):
                continue

            # Filtrar solo SMs activos (IN SESSION = 1)
            if state != 1:
                continue

            sm_ip = ip_map.get(luid, "")
            mac = mac_map.get(luid, "")
            site_name = name_map.get(luid, f"SM-LUID-{luid}")

            # Validar que sm_ip sea una IP v4 real (descarta basura binaria
            # que pysnmp puede devolver si prettyPrint() falla o el OID
            # retorna un tipo inesperado)
            if not sm_ip or not _IPV4_RE.match(sm_ip):
                logger.warning(
                    f"[DISCOVERY] LUID {luid}: IP invalida '{sm_ip}' — omitido"
                )
                continue

            results.append(
                SMDiscoveryResult(
                    luid=luid,
                    ip=sm_ip,
                    mac=mac,
                    site_name=site_name,
                    state=state,
                )
            )

        self._log(
            f"[DISCOVERY] {ap_ip}: {len(results)} SMs activos encontrados "
            f"(de {len(state_map)} LUIDs en linkTable)",
            "success" if results else "warning",
        )
        return results

    def run_scan(self) -> Dict[str, Dict]:
        """Ejecutar Tower Scan (wrapper síncrono).

        Returns:
            Diccionario con resultados por IP
        """
        return asyncio.run(self.start_tower_scan())


def main():
    """Función principal para testing"""
    import sys

    if len(sys.argv) < 2:
        print("Uso: python tower_scan.py <IP1> <IP2> ... [comunidad_snmp]")
        sys.exit(1)

    # Parsear argumentos
    ips = []
    community = os.environ.get("SNMP_COMMUNITIES", "Canopy").split(",")[0].strip()

    for arg in sys.argv[1:]:
        if "." in arg and arg.replace(".", "").isdigit():
            ips.append(arg)
        else:
            community = arg

    if not ips:
        print("ERROR: Debe proporcionar al menos una IP")
        sys.exit(1)

    print(f"\n{'=' * 60}")
    print("  TOWER SCAN - Cambium PMP 450i")
    print(f"{'=' * 60}")
    print(f"APs a escanear: {len(ips)}")
    print(f"Comunidad SNMP: {community}")
    print(f"{'=' * 60}\n")

    # Ejecutar scan
    scanner = TowerScanner(ips, community)
    results = scanner.run_scan()

    # Mostrar resultados
    print(f"\n{'=' * 60}")
    print("  RESULTADOS DEL TOWER SCAN")
    print(f"{'=' * 60}\n")

    for ip, result in results.items():
        status = "[OK] COMPLETADO" if result["completed"] else "[ERROR] FALLIDO"
        print(f"{ip}: {status}")
        print(f"  └─ {result['message']}\n")


if __name__ == "__main__":
    main()
