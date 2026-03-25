"""
app/audit_manager_v2.py — AuditManagerV2 con persistencia SQLite.

AuditManagerV2 es un reemplazo de AuditManager para change-004.
Mantiene la misma API de validación (AuditLogException) y ciclo de vida
(start_transaction / end_transaction) pero persiste en SQLite en vez de JSONL.

Especificación: change-004 specs § S4.6 — Motor de Auditoría v2
Diseño:        change-004 design § D4.6 — AuditManagerV2
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.audit_manager import AuditLogException, AuditManager

logger = logging.getLogger(__name__)

# ── Tipos de acción válidos ────────────────────────────────────────────────
VALID_ACTION_TYPES = frozenset(
    {
        "SCAN",
        "LOGIN",
        "LOGOUT",
        "USER_CREATE",
        "USER_UPDATE",
        "USER_DELETE",
        "USER_RESET_PASSWORD",
        "TOWER_CREATE",
        "TOWER_UPDATE",
        "TOWER_DELETE",
        "CONFIG_VERIFY",
        "APPLY_FREQUENCY",  # change-006: frequency apply events
    }
)


class AuditManagerV2:
    """Gestor de auditoría con persistencia en SQLite (tabla audit_logs).

    Mantiene el mismo ciclo de vida que AuditManager (start_transaction /
    end_transaction) y reutiliza sus mismas validaciones de usuario y ticket.

    Diferencias con AuditManager original:
      - Persiste en la tabla ``audit_logs`` de SQLite en lugar de JSONL.
      - Soporta múltiples ``action_type`` (no solo SCAN).
      - ``ticket_id`` es obligatorio solo para action_type="SCAN".
      - Expone ``log_action()`` para registros atómicos (sin start/end manual).
      - Expone métodos de clase ``get_logs()`` y ``get_log()`` para consultas.

    Thread Safety:
        Cada llamada a ``end_transaction`` / ``log_action`` abre y cierra una
        conexión propia vía ``db_manager.get_connection()``. SQLite con WAL
        mode garantiza seguridad concurrente.
    """

    def __init__(
        self,
        db_manager,
        user: Any,
        ticket_id: Any = None,
        action_type: str = "SCAN",
    ):
        """
        Parámetros:
          db_manager: DatabaseManager ya inicializado.
          user: username validado (string no vacío).
          ticket_id: entero positivo. Solo obligatorio para action_type="SCAN".
                     Para otras acciones (LOGIN, TOWER_CREATE, etc.) puede ser None.
          action_type: uno de los tipos definidos en VALID_ACTION_TYPES.

        Raises:
            AuditLogException: Si user, ticket_id (para SCAN) o action_type son inválidos.
        """
        # Validar action_type primero
        if action_type not in VALID_ACTION_TYPES:
            raise AuditLogException(
                f"action_type inválido: '{action_type}'. "
                f"Valores permitidos: {sorted(VALID_ACTION_TYPES)}"
            )

        # Reutilizar validación de usuario del AuditManager original
        self.user: str = AuditManager._validate_user(user)

        # ticket_id: solo obligatorio para SCAN
        if action_type == "SCAN":
            self.ticket_id: Optional[int] = AuditManager._validate_ticket(ticket_id)
        else:
            # Para otros tipos: validar solo si se proporcionó (no None)
            if ticket_id is not None:
                self.ticket_id = AuditManager._validate_ticket(ticket_id)
            else:
                self.ticket_id = None

        self.action_type: str = action_type
        self._db_manager = db_manager

        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None

    # ── Ciclo de vida ──────────────────────────────────────────────────────

    def start_transaction(self) -> None:
        """Inicia el cronómetro de la auditoría."""
        self.start_time = datetime.now(timezone.utc)
        logger.info(
            "AuditV2 iniciada — user=%s, action=%s, ticket=%s, start=%s",
            self.user,
            self.action_type,
            self.ticket_id,
            self.start_time.isoformat(),
        )

    def end_transaction(
        self,
        result_summary: str,
        scan_id: str = None,
        tower_id: str = None,
        devices: list = None,
        details: dict = None,
    ) -> int:
        """Finaliza la transacción y persiste en audit_logs.

        Retorna el id del registro insertado.
        Requiere que start_transaction() haya sido llamado previamente.
        """
        self.end_time = datetime.now(timezone.utc)

        # Si no se llamó start_transaction, usar end como start también
        if self.start_time is None:
            logger.warning(
                "end_transaction llamado sin start_transaction previo — "
                "usando end_time como start_time."
            )
            self.start_time = self.end_time

        duration = (self.end_time - self.start_time).total_seconds()

        log_id = self._insert_log(
            result_summary=result_summary,
            scan_id=scan_id,
            tower_id=tower_id,
            devices=devices,
            details=details,
            duration=duration,
        )
        logger.info(
            "AuditV2 finalizada — user=%s, action=%s, result=%s, id=%d",
            self.user,
            self.action_type,
            result_summary,
            log_id,
        )
        return log_id

    def log_action(
        self,
        result_summary: str,
        scan_id: str = None,
        tower_id: str = None,
        devices: list = None,
        details: dict = None,
    ) -> int:
        """Registrar acción atómica: start + end en un solo llamado.

        Útil para operaciones instantáneas como LOGIN, TOWER_CREATE, etc.
        Retorna el id del registro insertado.
        """
        self.start_transaction()
        return self.end_transaction(
            result_summary=result_summary,
            scan_id=scan_id,
            tower_id=tower_id,
            devices=devices,
            details=details,
        )

    # ── Persistencia ──────────────────────────────────────────────────────

    def _insert_log(
        self,
        result_summary: str,
        scan_id: Optional[str],
        tower_id: Optional[str],
        devices: Optional[list],
        details: Optional[dict],
        duration: float,
    ) -> int:
        """Inserta el registro en la tabla audit_logs y retorna el id."""
        devices_json = (
            json.dumps(devices, ensure_ascii=False) if devices is not None else None
        )
        details_json = (
            json.dumps(details, ensure_ascii=False) if details is not None else None
        )

        start_iso = (
            self.start_time.isoformat().replace("+00:00", "Z")
            if self.start_time
            else None
        )
        end_iso = (
            self.end_time.isoformat().replace("+00:00", "Z") if self.end_time else None
        )

        conn = self._db_manager.get_connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO audit_logs
                    (username, ticket_id, action_type, scan_id, tower_id,
                     devices, start_timestamp, end_timestamp, duration_seconds,
                     result, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.user,
                    self.ticket_id,
                    self.action_type,
                    scan_id,
                    tower_id,
                    devices_json,
                    start_iso,
                    end_iso,
                    round(duration, 2),
                    result_summary,
                    details_json,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    # ── Consultas ─────────────────────────────────────────────────────────

    @classmethod
    def get_logs(
        cls,
        db_manager,
        limit: int = 100,
        offset: int = 0,
        username: str = None,
        action_type: str = None,
    ) -> list:
        """Obtener logs de auditoría con filtros opcionales.

        Ordena por start_timestamp DESC (más recientes primero).

        Args:
            db_manager: DatabaseManager ya inicializado.
            limit: Máximo de registros a retornar.
            offset: Desplazamiento para paginación.
            username: Filtrar por nombre de usuario (exacto).
            action_type: Filtrar por tipo de acción.

        Returns:
            Lista de dicts con todos los campos de audit_logs.
        """
        conditions = []
        params: list = []

        if username:
            conditions.append("username = ?")
            params.append(username)
        if action_type:
            conditions.append("action_type = ?")
            params.append(action_type)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params += [limit, offset]

        sql = f"""
            SELECT id, user_id, username, ticket_id, action_type, scan_id,
                   tower_id, devices, start_timestamp, end_timestamp,
                   duration_seconds, result, details
            FROM audit_logs
            {where_clause}
            ORDER BY start_timestamp DESC
            LIMIT ? OFFSET ?
        """

        conn = db_manager.get_connection()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [cls._row_to_dict(row) for row in rows]
        finally:
            conn.close()

    @classmethod
    def get_log(cls, db_manager, log_id: int):
        """Obtener un registro por id. Retorna dict o None si no existe."""
        conn = db_manager.get_connection()
        try:
            row = conn.execute(
                """
                SELECT id, user_id, username, ticket_id, action_type, scan_id,
                       tower_id, devices, start_timestamp, end_timestamp,
                       duration_seconds, result, details
                FROM audit_logs
                WHERE id = ?
                """,
                (log_id,),
            ).fetchone()
            return cls._row_to_dict(row) if row else None
        finally:
            conn.close()

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row) -> dict:
        """Convierte un sqlite3.Row a dict, deserializando campos JSON."""
        d = dict(row)
        # Deserializar campos JSON almacenados como texto
        for field in ("devices", "details"):
            if d.get(field) is not None:
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
