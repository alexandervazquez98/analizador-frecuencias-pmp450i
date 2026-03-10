"""
tests/test_audit_v2.py — Tests para AuditManagerV2 (SQLite-backed).

Cubre:
  - Validaciones (mismas reglas que AuditManager original)
  - Ciclo de vida start_transaction / end_transaction
  - Acción atómica log_action
  - Consultas get_logs / get_log con filtros y paginación
  - Endpoints HTTP GET /api/audit/logs y /api/audit/logs/<id>
"""

import pytest
from app.audit_manager_v2 import AuditManagerV2
from app.audit_manager import AuditLogException
from app.db_manager import DatabaseManager


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    """DatabaseManager con base de datos temporal."""
    return DatabaseManager(str(tmp_path / "audit_test.db"))


@pytest.fixture
def mgr(db):
    """Factory de AuditManagerV2 con parámetros por defecto."""
    return lambda user="admin", ticket_id=42, action_type="SCAN": AuditManagerV2(
        db, user=user, ticket_id=ticket_id, action_type=action_type
    )


# ── T36 — Validaciones ────────────────────────────────────────────────────


class TestAuditManagerV2Validation:
    """Mismas validaciones de usuario y ticket que AuditManager original."""

    def test_scan_requires_ticket_id(self, db):
        """SCAN sin ticket_id debe lanzar AuditLogException."""
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user="admin", ticket_id=None, action_type="SCAN")

    def test_scan_requires_positive_ticket(self, db):
        """SCAN con ticket_id <= 0 debe lanzar AuditLogException."""
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user="admin", ticket_id=0, action_type="SCAN")
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user="admin", ticket_id=-5, action_type="SCAN")

    def test_non_scan_allows_no_ticket(self, db):
        """LOGIN sin ticket_id debe crearse sin excepción."""
        m = AuditManagerV2(db, user="admin", ticket_id=None, action_type="LOGIN")
        assert m.ticket_id is None

    def test_non_scan_accepts_optional_ticket(self, db):
        """LOGIN con ticket_id válido también debe funcionar."""
        m = AuditManagerV2(db, user="admin", ticket_id=99, action_type="LOGIN")
        assert m.ticket_id == 99

    def test_invalid_user_raises(self, db):
        """Usuario inválido (vacío, None) debe lanzar AuditLogException."""
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user=None, ticket_id=42, action_type="SCAN")
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user="", ticket_id=42, action_type="SCAN")
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user="   ", ticket_id=42, action_type="SCAN")

    def test_invalid_action_type_raises(self, db):
        """action_type desconocido debe lanzar AuditLogException."""
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user="admin", ticket_id=42, action_type="INVALID")
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user="admin", ticket_id=42, action_type="")
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user="admin", ticket_id=None, action_type="HACK")

    def test_valid_scan_creation(self, db):
        """SCAN con datos válidos debe crearse correctamente."""
        m = AuditManagerV2(db, user="admin", ticket_id=42, action_type="SCAN")
        assert m.user == "admin"
        assert m.ticket_id == 42
        assert m.action_type == "SCAN"

    def test_all_valid_action_types(self, db):
        """Todos los action_types válidos deben funcionar."""
        non_scan_types = [
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
        ]
        for action in non_scan_types:
            m = AuditManagerV2(db, user="admin", ticket_id=None, action_type=action)
            assert m.action_type == action

    def test_user_gets_stripped(self, db):
        """Usuario con espacios debe quedar stripped."""
        m = AuditManagerV2(db, user="  admin  ", ticket_id=42, action_type="SCAN")
        assert m.user == "admin"

    def test_bool_ticket_id_raises(self, db):
        """ticket_id booleano debe ser rechazado (bool es subclase de int)."""
        with pytest.raises(AuditLogException):
            AuditManagerV2(db, user="admin", ticket_id=True, action_type="SCAN")


# ── T36 — Ciclo de vida ───────────────────────────────────────────────────


class TestAuditManagerV2Lifecycle:
    def test_start_transaction_sets_start_time(self, db):
        """start_transaction() debe asignar start_time."""
        m = AuditManagerV2(db, user="admin", ticket_id=1, action_type="SCAN")
        assert m.start_time is None
        m.start_transaction()
        assert m.start_time is not None

    def test_end_transaction_persists_to_db(self, db):
        """end_transaction() debe insertar un registro en audit_logs."""
        m = AuditManagerV2(db, user="admin", ticket_id=1, action_type="SCAN")
        m.start_transaction()
        m.end_transaction("Completado OK")

        logs = AuditManagerV2.get_logs(db)
        assert len(logs) == 1
        assert logs[0]["username"] == "admin"
        assert logs[0]["result"] == "Completado OK"
        assert logs[0]["action_type"] == "SCAN"
        assert logs[0]["ticket_id"] == 1

    def test_end_transaction_returns_id(self, db):
        """end_transaction() debe retornar el id del registro insertado."""
        m = AuditManagerV2(db, user="admin", ticket_id=1, action_type="SCAN")
        m.start_transaction()
        log_id = m.end_transaction("OK")
        assert isinstance(log_id, int)
        assert log_id > 0

    def test_log_action_atomic(self, db):
        """log_action() debe insertar un registro sin llamar start/end manual."""
        m = AuditManagerV2(db, user="sysadmin", ticket_id=None, action_type="LOGIN")
        log_id = m.log_action("Login exitoso")
        assert isinstance(log_id, int)

        log = AuditManagerV2.get_log(db, log_id)
        assert log is not None
        assert log["username"] == "sysadmin"
        assert log["action_type"] == "LOGIN"
        assert log["result"] == "Login exitoso"
        assert log["ticket_id"] is None

    def test_duration_calculated(self, db):
        """duration_seconds debe ser >= 0 después de end_transaction."""
        import time

        m = AuditManagerV2(db, user="admin", ticket_id=5, action_type="SCAN")
        m.start_transaction()
        time.sleep(0.01)
        log_id = m.end_transaction("OK")

        log = AuditManagerV2.get_log(db, log_id)
        assert log["duration_seconds"] >= 0.0

    def test_end_transaction_with_devices_and_details(self, db):
        """end_transaction() debe persistir devices (como lista) y details (como dict).

        scan_id se deja en None para evitar FK constraint — la integridad
        referencial se prueba a nivel de integración con scans reales.
        """
        m = AuditManagerV2(db, user="admin", ticket_id=7, action_type="SCAN")
        m.start_transaction()
        log_id = m.end_transaction(
            "OK",
            scan_id=None,
            devices=["10.0.0.1", "10.0.0.2"],
            details={"freq": 5280},
        )

        log = AuditManagerV2.get_log(db, log_id)
        assert log["scan_id"] is None
        assert log["devices"] == ["10.0.0.1", "10.0.0.2"]
        assert log["details"] == {"freq": 5280}


# ── T36 — Consultas ───────────────────────────────────────────────────────


class TestAuditManagerV2GetLogs:
    def _insert_sample_logs(self, db):
        """Inserta varios logs de muestra."""
        m1 = AuditManagerV2(db, user="alice", ticket_id=10, action_type="SCAN")
        m1.log_action("Scan OK")

        m2 = AuditManagerV2(db, user="bob", ticket_id=None, action_type="LOGIN")
        m2.log_action("Login exitoso")

        m3 = AuditManagerV2(
            db, user="alice", ticket_id=None, action_type="TOWER_CREATE"
        )
        m3.log_action("Torre creada")

        return 3

    def test_get_all_logs(self, db):
        """get_logs() sin filtros debe retornar todos los registros."""
        count = self._insert_sample_logs(db)
        logs = AuditManagerV2.get_logs(db)
        assert len(logs) == count

    def test_get_logs_returns_list_of_dicts(self, db):
        """get_logs() debe retornar una lista de dicts."""
        self._insert_sample_logs(db)
        logs = AuditManagerV2.get_logs(db)
        assert isinstance(logs, list)
        assert all(isinstance(log, dict) for log in logs)

    def test_get_logs_by_username(self, db):
        """get_logs(username=...) debe filtrar por usuario."""
        self._insert_sample_logs(db)
        logs = AuditManagerV2.get_logs(db, username="alice")
        assert len(logs) == 2
        assert all(log["username"] == "alice" for log in logs)

    def test_get_logs_by_action_type(self, db):
        """get_logs(action_type=...) debe filtrar por tipo de acción."""
        self._insert_sample_logs(db)
        logs = AuditManagerV2.get_logs(db, action_type="LOGIN")
        assert len(logs) == 1
        assert logs[0]["action_type"] == "LOGIN"

    def test_get_logs_combined_filters(self, db):
        """get_logs() con username + action_type debe combinar filtros."""
        self._insert_sample_logs(db)
        logs = AuditManagerV2.get_logs(db, username="alice", action_type="SCAN")
        assert len(logs) == 1
        assert logs[0]["username"] == "alice"
        assert logs[0]["action_type"] == "SCAN"

    def test_get_log_by_id(self, db):
        """get_log(id) debe retornar el registro correcto."""
        m = AuditManagerV2(db, user="admin", ticket_id=1, action_type="SCAN")
        log_id = m.log_action("Resultado X")

        log = AuditManagerV2.get_log(db, log_id)
        assert log is not None
        assert log["id"] == log_id
        assert log["result"] == "Resultado X"

    def test_get_log_not_found(self, db):
        """get_log() con id inexistente debe retornar None."""
        result = AuditManagerV2.get_log(db, 999999)
        assert result is None

    def test_empty_db_returns_empty_list(self, db):
        """get_logs() en BD vacía debe retornar lista vacía."""
        logs = AuditManagerV2.get_logs(db)
        assert logs == []

    def test_pagination_limit(self, db):
        """get_logs(limit=N) debe retornar como máximo N registros."""
        for i in range(5):
            m = AuditManagerV2(db, user=f"user{i}", ticket_id=None, action_type="LOGIN")
            m.log_action("login")

        logs = AuditManagerV2.get_logs(db, limit=3)
        assert len(logs) == 3

    def test_pagination_offset(self, db):
        """get_logs(offset=N) debe saltar N registros."""
        for i in range(5):
            m = AuditManagerV2(db, user=f"user{i}", ticket_id=None, action_type="LOGIN")
            m.log_action("login")

        all_logs = AuditManagerV2.get_logs(db, limit=100)
        page2 = AuditManagerV2.get_logs(db, limit=100, offset=3)
        assert len(page2) == 2
        # Los ids de page2 deben ser distintos de los primeros 3
        first_3_ids = {log["id"] for log in all_logs[:3]}
        page2_ids = {log["id"] for log in page2}
        assert first_3_ids.isdisjoint(page2_ids)


# ── T37 — Endpoints HTTP ──────────────────────────────────────────────────


class TestAuditRoutes:
    """Tests HTTP para los endpoints de consulta de audit."""

    def test_list_logs_empty(self, authenticated_client):
        """GET /api/audit/logs en BD vacía debe retornar lista vacía."""
        resp = authenticated_client.get("/api/audit/logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "logs" in data
        assert "total" in data
        assert data["logs"] == []

    def test_list_logs_requires_login(self, client):
        """GET /api/audit/logs sin sesión debe retornar 401 (JSON) o redirect."""
        resp = client.get(
            "/api/audit/logs",
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_get_log_not_found(self, authenticated_client):
        """GET /api/audit/logs/99999 debe retornar 404."""
        resp = authenticated_client.get("/api/audit/logs/99999")
        assert resp.status_code == 404

    def test_get_log_not_found_requires_login(self, client):
        """GET /api/audit/logs/<id> sin sesión debe retornar 401."""
        resp = client.get(
            "/api/audit/logs/1",
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_list_logs_returns_data(self, authenticated_client):
        """GET /api/audit/logs debe retornar logs insertados."""
        from app.web_app import app, db_manager

        with app.app_context():
            m = AuditManagerV2(
                db_manager, user="admin", ticket_id=99, action_type="SCAN"
            )
            m.log_action("Test scan result")

        resp = authenticated_client.get("/api/audit/logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1
        assert any(log["result"] == "Test scan result" for log in data["logs"])

    def test_get_log_by_id(self, authenticated_client):
        """GET /api/audit/logs/<id> debe retornar el log correcto."""
        from app.web_app import app, db_manager

        with app.app_context():
            m = AuditManagerV2(
                db_manager, user="admin", ticket_id=None, action_type="LOGIN"
            )
            log_id = m.log_action("Login OK")

        resp = authenticated_client.get(f"/api/audit/logs/{log_id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == log_id
        assert data["action_type"] == "LOGIN"
        assert data["result"] == "Login OK"
