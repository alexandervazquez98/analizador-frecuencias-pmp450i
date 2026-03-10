"""
TowerManager — CRUD operations for the towers table.

Manages tower creation, retrieval, update, deletion, and search.
Tower IDs follow the pattern: ``[A-Z0-9]{2,5}-[A-Z]{2,5}-[A-Z]{2,5}-\\d{3}``

Specification: change-004 specs § S4.5 — Tower CRUD
Design:        change-004 design § D4.5 — TowerManager
"""

import re
import sqlite3
import logging
from threading import Lock

logger = logging.getLogger(__name__)

# ── Tower ID validation ───────────────────────────────────────────────

TOWER_ID_PATTERN = re.compile(r"^[A-Z0-9]{2,5}-[A-Z]{2,5}-[A-Z]{2,5}-\d{3}$")


class TowerValidationError(Exception):
    """Raised when a tower ID fails validation."""

    pass


class TowerManager:
    """Manages CRUD operations on the towers table.

    Thread Safety:
        Uses a class-level Lock for write operations.
        Each call uses a fresh connection from DatabaseManager.

    Args:
        db_manager: A DatabaseManager instance providing get_connection().
    """

    _lock = Lock()

    def __init__(self, db_manager):
        self._db_manager = db_manager

    # ── Validation ───────────────────────────────────────────────────

    @staticmethod
    def validate_tower_id(tower_id: str) -> str:
        """Normalize tower_id to uppercase and validate against TOWER_ID_PATTERN.

        Args:
            tower_id: Raw tower ID string (will be uppercased).

        Returns:
            Normalized (uppercased) tower_id.

        Raises:
            TowerValidationError: If tower_id doesn't match the pattern after normalization.
        """
        if not tower_id or not isinstance(tower_id, str):
            raise TowerValidationError("Tower ID must be a non-empty string")
        normalized = tower_id.strip().upper()
        if not TOWER_ID_PATTERN.match(normalized):
            raise TowerValidationError(
                f"Invalid tower ID '{normalized}'. "
                f"Must match pattern: [A-Z0-9]{{2,5}}-[A-Z]{{2,5}}-[A-Z]{{2,5}}-\\d{{3}}"
            )
        return normalized

    # ── CRUD ─────────────────────────────────────────────────────────

    def create(
        self,
        tower_id: str,
        name: str,
        location: str | None = None,
        notes: str | None = None,
        created_by: int | None = None,
    ) -> dict:
        """Create a new tower.

        Args:
            tower_id: Tower identifier (will be normalized to uppercase).
            name: Human-readable tower name.
            location: Optional location description.
            notes: Optional notes.
            created_by: Optional user ID of the creator.

        Returns:
            Dict with the created tower's data.

        Raises:
            TowerValidationError: If tower_id is invalid.
            sqlite3.IntegrityError: If tower_id already exists.
        """
        normalized_id = self.validate_tower_id(tower_id)
        with self._lock:
            conn = self._db_manager.get_connection()
            try:
                conn.execute(
                    """INSERT INTO towers (tower_id, name, location, notes, created_by)
                       VALUES (?, ?, ?, ?, ?)""",
                    (normalized_id, name, location, notes, created_by),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM towers WHERE tower_id = ?",
                    (normalized_id,),
                ).fetchone()
                logger.info("Tower created: %s (%s)", normalized_id, name)
                return dict(row)
            finally:
                conn.close()

    def get_by_id(self, tower_id: str) -> dict | None:
        """Fetch a single tower by its ID.

        Args:
            tower_id: Tower identifier (will be normalized to uppercase).

        Returns:
            Dict with tower data, or None if not found.

        Raises:
            TowerValidationError: If tower_id is invalid.
        """
        normalized_id = self.validate_tower_id(tower_id)
        conn = self._db_manager.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM towers WHERE tower_id = ?",
                (normalized_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_all(self) -> list[dict]:
        """List all towers ordered by created_at DESC.

        Returns:
            List of tower dicts.
        """
        conn = self._db_manager.get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM towers ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update(
        self,
        tower_id: str,
        name: str | None = None,
        location: str | None = None,
        notes: str | None = None,
    ) -> dict | None:
        """Update an existing tower's fields.

        Only non-None arguments are updated. Always updates updated_at.

        Args:
            tower_id: Tower identifier (will be normalized).
            name: New name (optional).
            location: New location (optional).
            notes: New notes (optional).

        Returns:
            Updated tower dict, or None if tower_id not found.

        Raises:
            TowerValidationError: If tower_id is invalid.
        """
        normalized_id = self.validate_tower_id(tower_id)

        # Build dynamic SET clause
        fields = []
        values = []
        if name is not None:
            fields.append("name = ?")
            values.append(name)
        if location is not None:
            fields.append("location = ?")
            values.append(location)
        if notes is not None:
            fields.append("notes = ?")
            values.append(notes)

        # Always update updated_at
        fields.append("updated_at = datetime('now')")

        values.append(normalized_id)

        with self._lock:
            conn = self._db_manager.get_connection()
            try:
                result = conn.execute(
                    f"UPDATE towers SET {', '.join(fields)} WHERE tower_id = ?",
                    values,
                )
                if result.rowcount == 0:
                    return None
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM towers WHERE tower_id = ?",
                    (normalized_id,),
                ).fetchone()
                logger.info("Tower updated: %s", normalized_id)
                return dict(row)
            finally:
                conn.close()

    def delete(self, tower_id: str) -> bool:
        """Delete a tower. Associated scans get tower_id set to NULL (ON DELETE SET NULL).

        Args:
            tower_id: Tower identifier (will be normalized).

        Returns:
            True if tower was deleted, False if not found.

        Raises:
            TowerValidationError: If tower_id is invalid.
        """
        normalized_id = self.validate_tower_id(tower_id)
        with self._lock:
            conn = self._db_manager.get_connection()
            try:
                result = conn.execute(
                    "DELETE FROM towers WHERE tower_id = ?",
                    (normalized_id,),
                )
                conn.commit()
                if result.rowcount > 0:
                    logger.info("Tower deleted: %s", normalized_id)
                    return True
                return False
            finally:
                conn.close()

    def search(self, query: str) -> list[dict]:
        """Search towers by tower_id or name using LIKE.

        Args:
            query: Search string (matched with %query% on tower_id and name).

        Returns:
            List of matching tower dicts, ordered by created_at DESC.
        """
        conn = self._db_manager.get_connection()
        try:
            like_pattern = f"%{query}%"
            rows = conn.execute(
                """SELECT * FROM towers
                   WHERE tower_id LIKE ? OR name LIKE ?
                   ORDER BY created_at DESC""",
                (like_pattern, like_pattern),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
