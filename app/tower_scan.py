"""
Módulo para realizar Tower Scan en radios Cambium PMP 450i
Utiliza SNMP para orquestar escaneos de espectro simultáneos
"""

import asyncio
import time
import os
from typing import List, Dict, Tuple
from pysnmp.hlapi import *
from pysnmp.proto.rfc1902 import Integer32
import logging

# Logger del módulo (configuración centralizada en app/__init__.py)
logger = logging.getLogger(__name__)


class TowerScanner:
    """
    Clase para gestionar escaneos de espectro en múltiples APs simultáneamente
    """

    # OID para control de Spectrum Analysis (PMP 450i Legacy/V1)
    SPECTRUM_ACTION_OID = "1.3.6.1.4.1.161.19.3.3.2.221.0"
    SPECTRUM_DURATION_OID = "1.3.6.1.4.1.161.19.3.3.2.222.0"

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
    ):
        """
        Inicializar el scanner

        Args:
            ap_ips: Lista de direcciones IP de los APs
            snmp_communities: Lista de comunidades SNMP a probar (default: ['Canopy'])
            sm_ips: Lista opcional de IPs de SMs para análisis cruzado
            log_callback: Función(msg, level) para logs externos
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

        # Mayor tolerancia para SMs que suelen desconectarse al escanear
        max_consecutive_errors = 15 if device_type == "SM" else 5

        while (time.time() - start_time) < max_wait:
            # Usar STATUS OID para verificar estado
            success, value, msg = await asyncio.to_thread(
                self._snmp_get, ip, timeout, retries, oid=self.SPECTRUM_ACTION_OID
            )

            if not success:
                consecutive_errors += 1
                self._log(
                    f"[{device_type}] {ip}: Error verificando estado ({consecutive_errors}/{max_consecutive_errors}) - {msg}",
                    "warning",
                )

                if device_type == "SM" and consecutive_errors < max_consecutive_errors:
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
                f"Validando acceso a {len(self.ap_ips)} APs (Probando {len(self.snmp_communities)} comunidades)..."
            )
            tasks = [find_working_community(ip) for ip in self.ap_ips]
            results = await asyncio.gather(*tasks)

            for ip, success, comm, msg in results:
                if success:
                    valid_aps.append(ip)
                    self.device_community_map[ip] = comm
                else:
                    errors[ip] = msg
                    logger.warning(f"[OMITIDO] AP {ip}: {msg}")

        # Validar SMs
        if self.sm_ips:
            self._log(f"Validando acceso a {len(self.sm_ips)} SMs...")
            tasks = [find_working_community(ip) for ip in self.sm_ips]
            results = await asyncio.gather(*tasks)

            for ip, success, comm, msg in results:
                if success:
                    valid_sms.append(ip)
                    self.device_community_map[ip] = comm
                else:
                    errors[ip] = msg
                    logger.warning(f"[OMITIDO] SM {ip}: {msg}")

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

        # =========================================================================
        # FASE 1: PREPARACIÓN DE SMs (Configurar Modo)
        # =========================================================================
        active_sms_prepared = []
        if valid_sms:
            self._log(f"--- FASE 1: Preparando {len(valid_sms)} SMs ---")
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
            self._log("--- FASE 2: Iniciando Escaneo en SMs (Sincronizado) ---")
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
            self._log("--- FASE 3: Iniciando AP ---")
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
            f"Esperando finalización ({len(active_aps)} APs, {len(active_sms_started)} SMs)..."
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

        for res in scan_completion_results:
            results[res["ip"]] = res

        self._log("Tower Scan finalizado.")
        return results

    def _snmp_get_oid_raw(
        self, ip: str, oid: str, community: str
    ) -> Tuple[bool, str, str]:
        """Helper para probar una comunidad específica"""
        try:
            iterator = getCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=1),
                UdpTransportTarget(
                    (ip, 161), timeout=5, retries=2
                ),  # Timeout aumentado a 5s, 2 retries
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
        return self._snmp_get_oid_raw(ip, oid, community)

    def run_scan(self) -> Dict[str, Dict]:
        """
        Ejecutar Tower Scan (wrapper síncrono)

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
