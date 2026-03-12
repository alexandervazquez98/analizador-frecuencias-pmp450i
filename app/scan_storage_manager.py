"""
app/scan_storage_manager.py — SQLite-backed persistent storage for scan records.

Provides CRUD operations for scans: save, retrieve, update, complete, fail, delete.
Serializes/deserializes JSON fields (ap_ips, sm_ips, config, results) transparently.

Specification: change-005 specs § S4.5 — Almacenamiento Persistente de Escaneos
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Fields that are stored as JSON in the DB and deserialized on read
_JSON_FIELDS = ("ap_ips", "sm_ips", "config", "results", "recommendations", "logs")


def _serialize(value):
    """Serialize a value to JSON string if it's a list or dict; else return as-is."""
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value


def _deserialize_row(row) -> Optional[dict]:
    """Convert a sqlite3.Row to a plain dict, deserializing JSON fields."""
    if row is None:
        return None
    d = dict(row)
    for field in _JSON_FIELDS:
        if field in d and d[field] is not None:
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass  # Leave as-is if not valid JSON
    return d


class ScanStorageManager:
    """SQLite-backed persistence layer for scan records.

    Specification: change-005 specs § S4.5 — Almacenamiento Persistente de Escaneos

    Thread Safety:
        Uses DatabaseManager.get_connection() which creates a new connection per call.
        Each method opens a connection, performs its work, and closes it.
        This is safe for multi-threaded Flask + background scan threads.
    """

    def __init__(self, db_manager):
        """Initialize with an already-configured DatabaseManager.

        Args:
            db_manager: A DatabaseManager instance (already initialized with schema).
        """
        self.db = db_manager

    def save_scan(self, scan_id: str, data: dict) -> None:
        """Insert or update a scan record (UPSERT).

        Args:
            scan_id: Unique scan identifier (UUID string).
            data: Dict that may contain any of:
                username, ticket_id, scan_type, ap_ips, sm_ips, config,
                status, progress, results, error, completed_at,
                duration_seconds, tower_id, user_id, recommendations, logs.
                ap_ips/sm_ips are serialized as JSON if they are lists.
                config/results/recommendations/logs are serialized as JSON if dicts/lists.

        Notes:
            - Uses INSERT OR REPLACE (UPSERT) semantics.
            - Fields not present in data are set to NULL (full replace).
            - 'progress' is stored in the config JSON column (not a DB column).
            - 'started_at' defaults to now() if not provided in data.
        """
        username = data.get("username", "unknown")
        ticket_id = data.get("ticket_id", 0)
        scan_type = data.get("scan_type", "AP_ONLY")
        status = data.get("status", "initializing")
        ap_ips = _serialize(data.get("ap_ips", []))
        sm_ips = (
            _serialize(data.get("sm_ips")) if data.get("sm_ips") is not None else None
        )
        config = (
            _serialize(data.get("config")) if data.get("config") is not None else None
        )
        results = (
            _serialize(data.get("results")) if data.get("results") is not None else None
        )
        recommendations = (
            _serialize(data.get("recommendations"))
            if data.get("recommendations") is not None
            else None
        )
        logs = _serialize(data.get("logs")) if data.get("logs") is not None else None
        tower_id = data.get("tower_id")
        user_id = data.get("user_id")
        completed_at = data.get("completed_at")
        duration_seconds = data.get("duration_seconds")
        error = data.get("error")
        started_at = data.get("started_at")

        with self.db._lock:
            conn = self.db.get_connection()
            try:
                if started_at:
                    conn.execute(
                        """INSERT OR REPLACE INTO scans
                           (id, tower_id, user_id, username, ticket_id, scan_type,
                            status, ap_ips, sm_ips, config, results, recommendations,
                            logs, started_at, completed_at, duration_seconds, error)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            scan_id,
                            tower_id,
                            user_id,
                            username,
                            ticket_id,
                            scan_type,
                            status,
                            ap_ips,
                            sm_ips,
                            config,
                            results,
                            recommendations,
                            logs,
                            started_at,
                            completed_at,
                            duration_seconds,
                            error,
                        ),
                    )
                else:
                    conn.execute(
                        """INSERT OR REPLACE INTO scans
                           (id, tower_id, user_id, username, ticket_id, scan_type,
                            status, ap_ips, sm_ips, config, results, recommendations,
                            logs, completed_at, duration_seconds, error)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            scan_id,
                            tower_id,
                            user_id,
                            username,
                            ticket_id,
                            scan_type,
                            status,
                            ap_ips,
                            sm_ips,
                            config,
                            results,
                            recommendations,
                            logs,
                            completed_at,
                            duration_seconds,
                            error,
                        ),
                    )
                conn.commit()
            except Exception:
                logger.exception("save_scan failed for scan_id=%s", scan_id)
                raise
            finally:
                conn.close()

    def get_scan(self, scan_id: str) -> Optional[dict]:
        """Retrieve a scan by ID.

        Args:
            scan_id: Unique scan identifier.

        Returns:
            Dict with all scan fields (JSON fields deserialized), or None if not found.
        """
        conn = self.db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM scans WHERE id = ?", (scan_id,)
            ).fetchone()
            return _deserialize_row(row)
        finally:
            conn.close()

    def get_all_scans(self, limit: int = 100, offset: int = 0) -> List[dict]:
        """Retrieve all scans ordered by started_at DESC (most recent first).

        Args:
            limit:  Maximum number of records to return (default 100).
            offset: Number of records to skip for pagination (default 0).

        Returns:
            List of scan dicts with JSON fields deserialized.
        """
        conn = self.db.get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM scans ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [_deserialize_row(row) for row in rows]
        finally:
            conn.close()

    def update_scan_status(
        self,
        scan_id: str,
        status: str,
        progress: int = None,
        error: str = None,
    ) -> None:
        """Update the status (and optionally progress/error) of an active scan.

        Args:
            scan_id:  Unique scan identifier.
            status:   New status string (e.g. 'scanning', 'analyzing').
            progress: Optional integer progress percentage (0–100).
            error:    Optional error message to store.

        Notes:
            - Only updates provided fields; does not touch results or other columns.
            - Does NOT fail if scan_id does not exist (no-op).
        """
        # Build dynamic SET clause based on what's provided
        sets = ["status = ?"]
        params: list = [status]

        if progress is not None:
            sets.append(
                "config = json_patch(COALESCE(config, '{}'), json_object('progress', ?))"
            )
            params.append(progress)

        if error is not None:
            sets.append("error = ?")
            params.append(error)

        params.append(scan_id)

        with self.db._lock:
            conn = self.db.get_connection()
            try:
                conn.execute(
                    f"UPDATE scans SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
                conn.commit()
            except Exception:
                logger.exception("update_scan_status failed for scan_id=%s", scan_id)
            finally:
                conn.close()

    def complete_scan(
        self,
        scan_id: str,
        results: dict,
        duration_seconds: float = None,
        logs: list = None,
    ) -> None:
        """Mark a scan as completed and persist its results.

        Args:
            scan_id:          Unique scan identifier.
            results:          Final results dict to store (serialized as JSON).
            duration_seconds: Optional elapsed time in seconds.
            logs:             Optional list of log entries to persist (Issue #7).
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        results_json = _serialize(results)
        logs_json = _serialize(logs) if logs is not None else None

        sets = [
            "status = 'completed'",
            "results = ?",
            "completed_at = ?",
            "duration_seconds = ?",
        ]
        params = [results_json, now, duration_seconds]

        if logs_json is not None:
            sets.append("logs = ?")
            params.append(logs_json)

        params.append(scan_id)

        with self.db._lock:
            conn = self.db.get_connection()
            try:
                conn.execute(
                    f"UPDATE scans SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
                conn.commit()
            except Exception:
                logger.exception("complete_scan failed for scan_id=%s", scan_id)
            finally:
                conn.close()

    def fail_scan(self, scan_id: str, error: str) -> None:
        """Mark a scan as failed and persist the error message.

        Args:
            scan_id: Unique scan identifier.
            error:   Error message string.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        with self.db._lock:
            conn = self.db.get_connection()
            try:
                conn.execute(
                    """UPDATE scans
                       SET status = 'failed',
                           error = ?,
                           completed_at = ?
                       WHERE id = ?""",
                    (error, now, scan_id),
                )
                conn.commit()
            except Exception:
                logger.exception("fail_scan failed for scan_id=%s", scan_id)
            finally:
                conn.close()

    def delete_scan(self, scan_id: str) -> bool:
        """Delete a scan record from the database.

        Args:
            scan_id: Unique scan identifier.

        Returns:
            True if the record existed and was deleted, False if not found.
        """
        with self.db._lock:
            conn = self.db.get_connection()
            try:
                cursor = conn.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
                conn.commit()
                return cursor.rowcount > 0
            except Exception:
                logger.exception("delete_scan failed for scan_id=%s", scan_id)
                return False
            finally:
                conn.close()
