"""
app/config_verification_manager.py — Gestor de verificaciones de configuración.

Especificación: change-004 specs § S4.7 — Verificación de Configuración
"""

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict | None:
    """Convert a sqlite3.Row to a plain dict. Returns None if row is None."""
    if row is None:
        return None
    return dict(row)


class ConfigVerificationManager:
    """Gestor de verificaciones de configuración aplicada post-escaneo.

    Permite registrar qué frecuencia se recomendó vs cuál se aplicó realmente,
    y consultar el historial de verificaciones.

    Thread Safety:
        Uses DatabaseManager.get_connection() which creates a new connection per call.
        Each method opens a connection, performs its work, and closes it.
    """

    def __init__(self, db_manager):
        """Recibe DatabaseManager ya inicializado.

        Args:
            db_manager: A DatabaseManager instance (already initialized with schema).
        """
        self.db = db_manager

    def create_verification(
        self,
        scan_id: str,
        recommended_freq: int,
        ap_ip: str = None,
        applied_freq: int = None,
        channel_width: int = None,
        tower_id: str = None,
        verified_by: int = None,
        notes: str = None,
    ) -> int:
        """Crear una verificación de configuración.

        Args:
            scan_id: ID del scan al que pertenece (OBLIGATORIO — FK a scans.id).
            recommended_freq: Frecuencia recomendada en MHz (OBLIGATORIO).
            ap_ip: IP del AP.
            applied_freq: Frecuencia que el operador aplicó realmente.
            channel_width: Ancho de canal en MHz.
            tower_id: ID de la torre (FK a towers.tower_id).
            verified_by: user_id del operador que verificó.
            notes: Notas adicionales.

        Returns:
            int: id del registro creado.

        Raises:
            ValueError: Si scan_id o recommended_freq no se proporcionan.
            sqlite3.IntegrityError: Si scan_id no existe en la tabla scans.
        """
        if not scan_id:
            raise ValueError("scan_id es obligatorio")
        if recommended_freq is None:
            raise ValueError("recommended_freq es obligatorio")

        verified_at = datetime.now(timezone.utc).isoformat()

        conn = self.db.get_connection()
        try:
            cursor = conn.execute(
                """INSERT INTO config_verifications
                   (scan_id, tower_id, ap_ip, recommended_freq, applied_freq,
                    channel_width, verified_by, verified_at, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    tower_id,
                    ap_ip,
                    recommended_freq,
                    applied_freq,
                    channel_width,
                    verified_by,
                    verified_at,
                    notes,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.warning(
                "create_verification FK violation: scan_id=%s may not exist", scan_id
            )
            raise
        except Exception:
            logger.exception("create_verification failed for scan_id=%s", scan_id)
            raise
        finally:
            conn.close()

    def get_verification(self, verification_id: int) -> dict | None:
        """Obtener una verificación por id.

        Args:
            verification_id: ID de la verificación.

        Returns:
            Dict con todos los campos de la verificación, o None si no existe.
        """
        conn = self.db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM config_verifications WHERE id = ?",
                (verification_id,),
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()

    def get_verifications_for_scan(self, scan_id: str) -> list[dict]:
        """Obtener todas las verificaciones de un scan, ordenadas por created_at DESC.

        Args:
            scan_id: ID del scan.

        Returns:
            Lista de dicts de verificaciones (puede ser vacía).
        """
        conn = self.db.get_connection()
        try:
            rows = conn.execute(
                """SELECT * FROM config_verifications
                   WHERE scan_id = ?
                   ORDER BY created_at DESC""",
                (scan_id,),
            ).fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def get_all_verifications(
        self,
        limit: int = 100,
        offset: int = 0,
        tower_id: str = None,
    ) -> list[dict]:
        """Obtener todas las verificaciones con paginación y filtro opcional por tower_id.

        Args:
            limit: Máximo de registros a devolver (default 100).
            offset: Registros a saltar para paginación (default 0).
            tower_id: Filtrar por torre (opcional).

        Returns:
            Lista de dicts de verificaciones ordenadas por created_at DESC.
        """
        conn = self.db.get_connection()
        try:
            if tower_id is not None:
                rows = conn.execute(
                    """SELECT * FROM config_verifications
                       WHERE tower_id = ?
                       ORDER BY created_at DESC
                       LIMIT ? OFFSET ?""",
                    (tower_id, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM config_verifications
                       ORDER BY created_at DESC
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def update_verification(
        self,
        verification_id: int,
        applied_freq: int = None,
        notes: str = None,
        channel_width: int = None,
    ) -> bool:
        """Actualizar campos de una verificación existente.

        Args:
            verification_id: ID de la verificación a actualizar.
            applied_freq: Nueva frecuencia aplicada (opcional).
            notes: Nuevas notas (opcional).
            channel_width: Nuevo ancho de canal (opcional).

        Returns:
            True si el registro existía y fue actualizado, False si no existe.
        """
        sets = []
        params: list = []

        if applied_freq is not None:
            sets.append("applied_freq = ?")
            params.append(applied_freq)
        if notes is not None:
            sets.append("notes = ?")
            params.append(notes)
        if channel_width is not None:
            sets.append("channel_width = ?")
            params.append(channel_width)

        if not sets:
            # Nothing to update — check existence only
            return self.get_verification(verification_id) is not None

        params.append(verification_id)

        conn = self.db.get_connection()
        try:
            cursor = conn.execute(
                f"UPDATE config_verifications SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            logger.exception("update_verification failed for id=%s", verification_id)
            return False
        finally:
            conn.close()

    def delete_verification(self, verification_id: int) -> bool:
        """Eliminar una verificación.

        Args:
            verification_id: ID de la verificación a eliminar.

        Returns:
            True si el registro existía y fue eliminado, False si no existía.
        """
        conn = self.db.get_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM config_verifications WHERE id = ?",
                (verification_id,),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception:
            logger.exception("delete_verification failed for id=%s", verification_id)
            return False
        finally:
            conn.close()
