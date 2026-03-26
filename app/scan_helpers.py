"""
app/scan_helpers.py — Utility helpers for scan configuration and IP parsing.

Extracted from app/routes/scan_routes.py as part of Phase 5 refactor.

Design: change-005 design § D4.5 — Scan Module Split
"""

import os
from typing import List


def parse_ip_list(ip_text: str) -> List[str]:
    """Parse a list of IP addresses from a text string.

    Accepts IPs separated by newlines or commas.  Lines beginning with '#'
    are treated as comments and ignored.  Each candidate is validated as a
    dotted-quad (four octets in 0–255).

    Args:
        ip_text: Raw text containing IP addresses.

    Returns:
        List of valid IPv4 address strings.
    """
    if not ip_text:
        return []

    ips = []
    for line in ip_text.replace(",", "\n").split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            parts = line.split(".")
            if len(parts) == 4 and all(
                p.isdigit() and 0 <= int(p) <= 255 for p in parts
            ):
                ips.append(line)

    return ips


def get_scan_defaults() -> dict:
    """Read scan defaults from environment variables (.env).

    This is the single source of truth for all scan configuration defaults.

    Returns:
        dict with keys:
            snmp_communities (list[str]),
            target_rx_level (int),
            min_snr (int),
            max_polarization_diff (int),
            channel_width (int).
    """
    raw_communities = os.environ.get("SNMP_COMMUNITIES", "Canopy")
    communities = [c.strip() for c in raw_communities.split(",") if c.strip()]

    return {
        "snmp_communities": communities,
        "target_rx_level": int(os.environ.get("DEFAULT_TARGET_RX_LEVEL", "-52")),
        "min_snr": int(os.environ.get("DEFAULT_MIN_SNR", "32")),
        "max_polarization_diff": int(
            os.environ.get("DEFAULT_MAX_POLARIZATION_DIFF", "5")
        ),
        "channel_width": int(os.environ.get("DEFAULT_CHANNEL_WIDTH", "20")),
        # Ancho de canal mínimo permitido en el análisis multibanda.
        # El sistema no recomendará BWs menores a este valor.
        # Operativamente usar 15 MHz como piso (latencia + headroom de capacidad).
        "min_channel_width": int(os.environ.get("MIN_CHANNEL_WIDTH", "15")),
    }
