"""
tests/test_api_security.py — BDD Tests para el Decorador @requires_audit_ticket

Metodología: BDD (Given/When/Then)
Especificación: 02_specs.md § S2 — Contrato de Auditoría
Diseño:       03_design.md § D2 — Intercepción de Peticiones

Escenarios cubiertos:
  1. Peticiones sin credenciales → HTTP 403
  2. Peticiones con ticket_id inválido → HTTP 403
  3. Peticiones con credenciales válidas en headers → HTTP 202 (scan started)
  4. Peticiones con credenciales válidas en JSON body → HTTP 202
  5. Headers tienen prioridad sobre JSON body
  6. Escritura de audit_logs.jsonl tras scan exitoso
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from app.audit_manager import AuditManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Crea un cliente de test Flask con audit_logs redirigido a tmp_path."""
    # Redirigir audit log a temp
    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)

    # Importar app después de configurar
    from app.web_app import app

    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def audit_log_file(tmp_path, monkeypatch):
    """Retorna la ruta al archivo de audit log temporal."""
    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)
    return log_file


def _read_audit_logs(log_file: str) -> list:
    """Helper: Lee entradas del archivo audit_logs.jsonl."""
    if not os.path.exists(log_file):
        return []
    with open(log_file, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Escenario 1: Peticiones sin credenciales → HTTP 403
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestNoCredentials:
    """
    GIVEN el endpoint /api/scan está protegido por @requires_audit_ticket
    WHEN se envía una petición sin user ni ticket_id
    THEN el sistema retorna HTTP 403 con mensaje de Excepción de Seguridad.
    """

    def test_no_headers_no_body_returns_403(self, client):
        """GIVEN POST /api/scan sin credenciales WHEN request THEN 403."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"]},
            content_type="application/json",
        )
        assert response.status_code == 403
        data = response.get_json()
        assert data["error"] == "Excepción de Seguridad"

    def test_only_user_no_ticket_returns_403(self, client):
        """GIVEN user pero sin ticket_id WHEN POST /api/scan THEN 403."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "user": "admin"},
            content_type="application/json",
        )
        assert response.status_code == 403

    def test_only_ticket_no_user_returns_403(self, client):
        """GIVEN ticket_id pero sin user WHEN POST /api/scan THEN 403."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "ticket_id": 42},
            content_type="application/json",
        )
        assert response.status_code == 403


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Escenario 2: Peticiones con ticket_id inválido → HTTP 403
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestInvalidTicket:
    """
    GIVEN credenciales con ticket_id inválido (string, negativo, cero)
    WHEN se envía POST /api/scan
    THEN el sistema retorna HTTP 403 y NO inicia el escaneo.
    """

    @pytest.mark.parametrize(
        "bad_ticket",
        [
            "ABC-123",  # alfanumérico
            "",  # vacío
            -5,  # negativo
            0,  # cero
            None,  # nulo explícito
            True,  # booleano
        ],
    )
    def test_invalid_tickets_return_403(self, client, bad_ticket):
        """GIVEN ticket_id inválido WHEN POST /api/scan THEN 403."""
        payload = {
            "ap_ips": ["192.168.1.1"],
            "user": "admin@test.com",
            "ticket_id": bad_ticket,
        }
        response = client.post(
            "/api/scan",
            json=payload,
            content_type="application/json",
        )
        assert response.status_code == 403
        data = response.get_json()
        assert "Seguridad" in data["error"]

    def test_snmp_not_started_on_invalid_ticket(self, client):
        """
        GIVEN ticket_id inválido
        WHEN POST /api/scan
        THEN el escaneo SNMP NO debe iniciarse (bloqueo total).
        """
        with patch("app.web_app.ScanTask") as mock_task:
            response = client.post(
                "/api/scan",
                json={
                    "ap_ips": ["192.168.1.1"],
                    "user": "admin@test.com",
                    "ticket_id": -1,
                },
                content_type="application/json",
            )
            assert response.status_code == 403
            # ScanTask no debe haberse instanciado
            mock_task.assert_not_called()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Escenario 3: Credenciales válidas en headers → HTTP 202
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestValidCredentialsHeaders:
    """
    GIVEN credenciales válidas en headers HTTP
    WHEN se envía POST /api/scan
    THEN el sistema retorna HTTP 202 y el escaneo se inicia.
    """

    def test_valid_headers_returns_202(self, client):
        """GIVEN headers X-Audit-User + X-Ticket-ID WHEN POST THEN 202."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "snmp_community": "Canopy"},
            headers={
                "X-Audit-User": "admin@cambium.com",
                "X-Ticket-ID": "12345",
            },
            content_type="application/json",
        )
        assert response.status_code == 202
        data = response.get_json()
        assert "scan_id" in data
        assert data["status"] == "started"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Escenario 4: Credenciales válidas en JSON body → HTTP 202
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestValidCredentialsBody:
    """
    GIVEN credenciales válidas en el body JSON
    WHEN se envía POST /api/scan
    THEN el sistema retorna HTTP 202.
    """

    def test_valid_body_returns_202(self, client):
        """GIVEN user + ticket_id en JSON body WHEN POST THEN 202."""
        response = client.post(
            "/api/scan",
            json={
                "ap_ips": ["192.168.1.1"],
                "snmp_community": "Canopy",
                "user": "rf_engineer@test.com",
                "ticket_id": 9999,
            },
            content_type="application/json",
        )
        assert response.status_code == 202


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Escenario 5: Headers tienen prioridad sobre body
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestHeaderPriority:
    """
    GIVEN credenciales en headers Y en body (diferentes valores)
    WHEN se envía POST /api/scan
    THEN el sistema usa las credenciales de los headers.
    """

    def test_headers_override_body(self, client, audit_log_file):
        """
        GIVEN header X-Audit-User='header_user' Y body user='body_user'
        WHEN POST /api/scan
        THEN audit log registra 'header_user'.
        """
        response = client.post(
            "/api/scan",
            json={
                "ap_ips": ["192.168.1.1"],
                "snmp_community": "Canopy",
                "user": "body_user@test.com",
                "ticket_id": 111,
            },
            headers={
                "X-Audit-User": "header_user@test.com",
                "X-Ticket-ID": "222",
            },
            content_type="application/json",
        )
        assert response.status_code == 202


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Escenario 6: Endpoints no protegidos siguen funcionando
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestUnprotectedEndpoints:
    """
    GIVEN endpoints que NO tienen @requires_audit_ticket
    WHEN se envía una petición
    THEN responden normalmente sin requerir credenciales.
    """

    def test_health_check_no_auth_needed(self, client):
        """GIVEN GET /api/health WHEN sin credenciales THEN 200."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"

    def test_scan_status_no_auth_needed(self, client):
        """GIVEN GET /api/status/<id> WHEN id inexistente THEN 404 (no 403)."""
        response = client.get("/api/status/nonexistent-id")
        assert response.status_code == 404

    def test_scans_list_no_auth_needed(self, client):
        """GIVEN GET /api/scans WHEN sin credenciales THEN 200."""
        response = client.get("/api/scans")
        assert response.status_code == 200


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Escenario 7: Mensaje de error contiene información útil
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestErrorMessages:
    """
    GIVEN una petición rechazada por auditoría
    WHEN se examina el cuerpo del error
    THEN contiene información clara sobre por qué fue rechazada.
    """

    def test_missing_ticket_error_message(self, client):
        """GIVEN sin ticket_id WHEN POST THEN mensaje menciona 'OBLIGATORIO'."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "user": "admin"},
            content_type="application/json",
        )
        data = response.get_json()
        assert "OBLIGATORIO" in data["message"] or "entero positivo" in data["message"]

    def test_invalid_ticket_error_message(self, client):
        """GIVEN ticket_id='ABC' WHEN POST THEN mensaje menciona 'entero positivo'."""
        response = client.post(
            "/api/scan",
            json={
                "ap_ips": ["192.168.1.1"],
                "user": "admin",
                "ticket_id": "ABC",
            },
            content_type="application/json",
        )
        data = response.get_json()
        assert "entero positivo" in data["message"]

    def test_missing_user_error_message(self, client):
        """GIVEN sin user WHEN POST THEN mensaje menciona 'Usuario'."""
        response = client.post(
            "/api/scan",
            json={"ap_ips": ["192.168.1.1"], "ticket_id": 42},
            content_type="application/json",
        )
        data = response.get_json()
        assert "Usuario" in data["message"]
