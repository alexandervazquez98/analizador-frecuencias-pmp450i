"""
Utilidades de conversión de frecuencia para Cambium PMP 450i.

Reglas de conversión:
  - Todos los valores SNMP SET/GET de frecuencia usan kHz (Integer32).
  - Toda la UI y análisis usan MHz (float).
  - Esta es la ÚNICA fuente de conversión — no duplicar en otras capas.
"""

MHZ_TO_KHZ = 1000  # Factor de conversión: 1 MHz = 1000 kHz
KHZ_PER_MHZ = 1000  # Alias semántico usado en design


def mhz_to_khz(mhz: float) -> int:
    """Convierte frecuencia de MHz a kHz.

    Usa int(round(...)) para evitar errores de precisión flotante.
    Ejemplo: 3556.25 MHz → 3556250 kHz (no 3556249 por float truncation).

    Args:
        mhz: Frecuencia en MHz (float). Ejemplo: 3554.0, 3556.25.

    Returns:
        Frecuencia en kHz como entero. Ejemplo: 3554000, 3556250.
    """
    return int(round(mhz * MHZ_TO_KHZ))


def khz_to_mhz(khz: int) -> float:
    """Convierte frecuencia de kHz a MHz.

    Args:
        khz: Frecuencia en kHz (entero). Ejemplo: 3554000.

    Returns:
        Frecuencia en MHz como float. Ejemplo: 3554.0.
    """
    return khz / MHZ_TO_KHZ


def format_scan_list(freqs_khz: list) -> str:
    """Formatea lista de frecuencias en kHz como OctetString para rfScanList.

    El OID rfScanList (1.3.6.1.4.1.161.19.3.2.1.1.0) acepta una cadena
    de texto con frecuencias separadas por coma SIN espacio. El hardware
    PMP 450i (banda 5 GHz) rechaza con wrongValue si hay espacio tras la coma.

    Args:
        freqs_khz: Lista de frecuencias en kHz. Ejemplo: [3550000, 3555000].

    Returns:
        String formateado. Ejemplo: "3550000,3555000".
        Retorna "" para lista vacía.
    """
    if not freqs_khz:
        return ""
    return ",".join(str(f) for f in freqs_khz)


def parse_scan_list(scan_list_str: str) -> list:
    """Parsea un OctetString de rfScanList a lista de frecuencias en kHz.

    Operación inversa de format_scan_list().

    Args:
        scan_list_str: String de frecuencias separadas por coma.
                       Ejemplo: "3550000, 3555000".

    Returns:
        Lista de enteros en kHz. Ejemplo: [3550000, 3555000].
        Retorna [] para string vacío o None.
    """
    if not scan_list_str or not scan_list_str.strip():
        return []
    parts = scan_list_str.split(",")
    result = []
    for part in parts:
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError:
                pass  # Ignorar valores no numéricos
    return result
