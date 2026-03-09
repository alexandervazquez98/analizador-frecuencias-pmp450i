"""
AuditManager — Gestor centralizado de auditoría para el Analizador de Frecuencias PMP450i.

Responsabilidades:
  - Validar que ticket_id sea un entero positivo y no nulo.
  - Validar que el usuario sea un string no vacío.
  - Registrar timestamps de inicio y fin, calculando duración total.
  - Escribir (append) registros en formato JSON Lines (audit_logs.jsonl) de forma thread-safe.

Especificación: 02_specs.md § S2 — Contrato de Auditoría
Diseño:       03_design.md § D1 — Diseño del Sistema de Auditoría
"""

import json
import os
import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AuditLogException(Exception):
    """Excepción lanzada cuando la validación del ticket o usuario falla.

    Esta excepción actúa como mecanismo de bloqueo de seguridad:
    si se lanza, el escaneo SNMP NO debe iniciarse.
    """

    pass


class AuditManager:
    """Gestor de ciclo de vida de un registro de auditoría.

    Se instancia al inicio de una petición HTTP (por el decorador @requires_audit_ticket)
    y se cierra al finalizar la operación (síncrona o asíncrona en hilo).

    Thread Safety:
        Utiliza un Lock de clase para serializar las escrituras al archivo JSONL.
        Esto es necesario porque Flask puede despachar escaneos en hilos separados
        (ScanTask.run_in_thread) y múltiples hilos podrían intentar escribir
        simultáneamente.
    """

    _lock = Lock()
    LOG_FILE = "audit_logs.jsonl"

    def __init__(self, user: Any, ticket_id: Any):
        """Inicializa el AuditManager validando usuario y ticket.

        Args:
            user: Identidad del ingeniero (se valida como string no vacío).
            ticket_id: Número de ticket del sistema de mesa de ayuda
                       (se valida como entero positivo).

        Raises:
            AuditLogException: Si el usuario o ticket_id son inválidos.
        """
        self.user: str = self._validate_user(user)
        self.ticket_id: int = self._validate_ticket(ticket_id)
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.result: Optional[str] = None

    # ── Validaciones ──────────────────────────────────────────────────

    @staticmethod
    def _validate_user(user: Any) -> str:
        """Valida que el usuario sea un string no vacío.

        Raises:
            AuditLogException: Si el usuario es None, no es string, o está vacío.
        """
        if not user or not isinstance(user, str) or not user.strip():
            raise AuditLogException("Usuario inválido o no proporcionado.")
        return user.strip()

    @staticmethod
    def _validate_ticket(ticket_id: Any) -> int:
        """Valida que el ticket_id sea un entero positivo.

        Reglas (02_specs.md § S2):
          - OBLIGATORIO y NO NULO.
          - Debe ser un número entero positivo.
          - No se aceptan: None, strings vacíos, negativos, cero, alfanuméricos.

        Raises:
            AuditLogException: Si el ticket_id incumple las reglas.
        """
        if ticket_id is None:
            raise AuditLogException("El ticket_id es OBLIGATORIO y NO NULO.")

        # Rechazar booleanos explícitamente (bool es subclase de int en Python)
        if isinstance(ticket_id, bool):
            raise AuditLogException(
                "Acceso denegado: Es obligatorio proporcionar un ticket ID numérico "
                "(entero positivo) para intervenir la red."
            )

        try:
            ticket_int = int(ticket_id)
            if ticket_int <= 0:
                raise ValueError()
            return ticket_int
        except (ValueError, TypeError):
            raise AuditLogException(
                "Acceso denegado: Es obligatorio proporcionar un ticket ID numérico "
                "(entero positivo) para intervenir la red."
            )

    # ── Ciclo de vida de la transacción ───────────────────────────────

    def start_transaction(self) -> None:
        """Inicia el cronómetro de la auditoría.

        Registra el timestamp UTC de inicio. Debe llamarse ANTES de que
        el escaneo SNMP o análisis de frecuencias comience.
        """
        self.start_time = datetime.now(timezone.utc)
        logger.info(
            "Auditoría iniciada — user=%s, ticket=%d, start=%s",
            self.user,
            self.ticket_id,
            self.start_time.isoformat(),
        )

    def end_transaction(self, result_summary: str) -> None:
        """Finaliza la transacción y persiste el log.

        Args:
            result_summary: Descripción del resultado de la operación.
                Ej: "Completado, Frecuencia Sugerida 5280 MHz"
                    "Fallo SNMP: Equipo no alcanzable"

        Side Effects:
            Escribe una línea al archivo audit_logs.jsonl de forma thread-safe.
        """
        self.end_time = datetime.now(timezone.utc)
        self.result = result_summary
        self._write_log()
        logger.info(
            "Auditoría finalizada — user=%s, ticket=%d, result=%s",
            self.user,
            self.ticket_id,
            result_summary,
        )

    # ── Persistencia ──────────────────────────────────────────────────

    def _write_log(self) -> None:
        """Escribe el registro de auditoría en formato JSONL (thread-safe).

        Formato del registro (02_specs.md § S2.2):
          - user: Identidad del ingeniero.
          - ticket_id: Entero positivo del ticket.
          - start_timestamp: ISO 8601 UTC.
          - end_timestamp: ISO 8601 UTC.
          - duration_seconds: Duración total en segundos (redondeado a 2 decimales).
          - result: Resumen de la acción.
        """
        if self.start_time is None or self.end_time is None:
            logger.error(
                "Intento de escribir log de auditoría sin start/end time. "
                "Asegúrate de llamar start_transaction() y end_transaction()."
            )
            return

        duration = (self.end_time - self.start_time).total_seconds()

        log_entry = {
            "user": self.user,
            "ticket_id": self.ticket_id,
            "start_timestamp": self.start_time.isoformat().replace("+00:00", "Z"),
            "end_timestamp": self.end_time.isoformat().replace("+00:00", "Z"),
            "duration_seconds": round(duration, 2),
            "result": self.result,
        }

        with self._lock:
            with open(self.LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
