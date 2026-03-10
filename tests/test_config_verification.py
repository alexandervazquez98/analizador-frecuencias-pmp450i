"""
tests/test_config_verification.py — Tests para ConfigVerificationManager y rutas HTTP.

Cubre:
  - Operaciones CRUD del manager (T39)
  - Validaciones y manejo de errores
  - Endpoints HTTP /api/config-verifications (T40)
  - Control de acceso (login_required, admin_required)
"""

import pytest
import sqlite3

from app.config_verification_manager import ConfigVerificationManager
from app.db_manager import DatabaseManager


# ── Fixtures ──────────────────────────────────────────────────────────────

SCAN_ID = "scan-test-001"


def _insert_scan(db_manager, scan_id=SCAN_ID):
    """Inserta un scan mínimo válido en la tabla scans (satisface FK)."""
    conn = db_manager.get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO scans
               (id, username, ticket_id, scan_type, ap_ips)
               VALUES (?, ?, ?, ?, ?)""",
            (scan_id, "testuser", 42, "AP_ONLY", '["10.0.0.1"]'),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def storage(tmp_path):
    """DatabaseManager + ConfigVerificationManager sin scan previo."""
    db = DatabaseManager(str(tmp_path / "cv_test.db"))
    return ConfigVerificationManager(db)


@pytest.fixture
def storage_with_scan(tmp_path):
    """Devuelve (ConfigVerificationManager, scan_id) con un scan insertado."""
    db = DatabaseManager(str(tmp_path / "cv_test.db"))
    _insert_scan(db)
    return ConfigVerificationManager(db), SCAN_ID


# ── T39 — Tests unitarios del manager ────────────────────────────────────


class TestConfigVerificationManager:
    """Tests unitarios de ConfigVerificationManager."""

    # ── create ──────────────────────────────────────────────────────────

    def test_create_basic(self, storage_with_scan):
        """create_verification() debe retornar un id entero positivo."""
        mgr, scan_id = storage_with_scan
        vid = mgr.create_verification(scan_id=scan_id, recommended_freq=5745)
        assert isinstance(vid, int)
        assert vid > 0

    def test_create_without_scan_id_raises(self, storage):
        """create_verification() sin scan_id debe lanzar ValueError."""
        with pytest.raises(ValueError, match="scan_id"):
            storage.create_verification(scan_id=None, recommended_freq=5745)

    def test_create_without_recommended_freq_raises(self, storage):
        """create_verification() sin recommended_freq debe lanzar ValueError."""
        with pytest.raises(ValueError, match="recommended_freq"):
            storage.create_verification(scan_id="any-id", recommended_freq=None)

    def test_create_with_invalid_scan_id_raises(self, storage):
        """create_verification() con scan_id inexistente debe lanzar IntegrityError."""
        with pytest.raises(sqlite3.IntegrityError):
            storage.create_verification(
                scan_id="nonexistent-scan-999", recommended_freq=5745
            )

    def test_create_stores_all_optional_fields(self, storage_with_scan):
        """Los campos opcionales deben persistirse correctamente."""
        mgr, scan_id = storage_with_scan
        vid = mgr.create_verification(
            scan_id=scan_id,
            recommended_freq=5745,
            ap_ip="10.0.0.5",
            applied_freq=5760,
            channel_width=20,
            notes="Verificado en campo",
        )
        v = mgr.get_verification(vid)
        assert v["ap_ip"] == "10.0.0.5"
        assert v["applied_freq"] == 5760
        assert v["channel_width"] == 20
        assert v["notes"] == "Verificado en campo"
        assert v["recommended_freq"] == 5745

    # ── get_verification ─────────────────────────────────────────────────

    def test_get_verification_existing(self, storage_with_scan):
        """get_verification() debe retornar el registro correcto."""
        mgr, scan_id = storage_with_scan
        vid = mgr.create_verification(scan_id=scan_id, recommended_freq=5480)
        v = mgr.get_verification(vid)
        assert v is not None
        assert v["id"] == vid
        assert v["recommended_freq"] == 5480
        assert v["scan_id"] == scan_id

    def test_get_verification_not_found(self, storage):
        """get_verification() con id inexistente debe retornar None."""
        result = storage.get_verification(99999)
        assert result is None

    def test_get_verification_returns_dict(self, storage_with_scan):
        """get_verification() debe retornar un dict (no sqlite3.Row)."""
        mgr, scan_id = storage_with_scan
        vid = mgr.create_verification(scan_id=scan_id, recommended_freq=5600)
        v = mgr.get_verification(vid)
        assert isinstance(v, dict)

    # ── get_verifications_for_scan ───────────────────────────────────────

    def test_get_verifications_for_scan(self, storage_with_scan):
        """get_verifications_for_scan() debe retornar sólo las del scan dado."""
        mgr, scan_id = storage_with_scan
        mgr.create_verification(scan_id=scan_id, recommended_freq=5745)
        mgr.create_verification(scan_id=scan_id, recommended_freq=5760)
        results = mgr.get_verifications_for_scan(scan_id)
        assert len(results) == 2
        assert all(v["scan_id"] == scan_id for v in results)

    def test_get_verifications_for_scan_empty(self, storage):
        """get_verifications_for_scan() con scan sin verificaciones retorna lista vacía."""
        results = storage.get_verifications_for_scan("nonexistent-scan")
        assert results == []

    # ── get_all_verifications ────────────────────────────────────────────

    def test_get_all_verifications(self, storage_with_scan):
        """get_all_verifications() debe retornar todos los registros."""
        mgr, scan_id = storage_with_scan
        mgr.create_verification(scan_id=scan_id, recommended_freq=5745)
        mgr.create_verification(scan_id=scan_id, recommended_freq=5760)
        all_v = mgr.get_all_verifications()
        assert len(all_v) == 2

    def test_get_all_verifications_empty(self, storage):
        """get_all_verifications() en BD vacía debe retornar lista vacía."""
        assert storage.get_all_verifications() == []

    def test_get_all_with_tower_filter(self, storage_with_scan, tmp_path):
        """get_all_verifications(tower_id=...) debe filtrar por torre."""
        mgr, scan_id = storage_with_scan
        # Insert a tower so FK is satisfied
        conn = mgr.db.get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO towers (tower_id, name) VALUES (?, ?)",
                ("T1", "Torre 1"),
            )
            conn.commit()
        finally:
            conn.close()

        mgr.create_verification(scan_id=scan_id, recommended_freq=5745, tower_id="T1")
        mgr.create_verification(scan_id=scan_id, recommended_freq=5760)  # no tower

        filtered = mgr.get_all_verifications(tower_id="T1")
        assert len(filtered) == 1
        assert filtered[0]["tower_id"] == "T1"

        unfiltered = mgr.get_all_verifications()
        assert len(unfiltered) == 2

    # ── pagination ───────────────────────────────────────────────────────

    def test_pagination(self, storage_with_scan):
        """get_all_verifications() con limit/offset debe paginar correctamente."""
        mgr, scan_id = storage_with_scan
        for freq in [5480, 5500, 5520, 5540, 5560]:
            mgr.create_verification(scan_id=scan_id, recommended_freq=freq)

        page1 = mgr.get_all_verifications(limit=3, offset=0)
        page2 = mgr.get_all_verifications(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2
        ids_p1 = {v["id"] for v in page1}
        ids_p2 = {v["id"] for v in page2}
        assert ids_p1.isdisjoint(ids_p2)

    # ── update ───────────────────────────────────────────────────────────

    def test_update_verification(self, storage_with_scan):
        """update_verification() debe modificar los campos indicados."""
        mgr, scan_id = storage_with_scan
        vid = mgr.create_verification(scan_id=scan_id, recommended_freq=5745)

        result = mgr.update_verification(
            vid, applied_freq=5760, notes="Aplicado OK", channel_width=40
        )
        assert result is True

        v = mgr.get_verification(vid)
        assert v["applied_freq"] == 5760
        assert v["notes"] == "Aplicado OK"
        assert v["channel_width"] == 40

    def test_update_returns_false_for_unknown(self, storage):
        """update_verification() con id inexistente debe retornar False."""
        result = storage.update_verification(99999, applied_freq=5760)
        assert result is False

    # ── delete ───────────────────────────────────────────────────────────

    def test_delete_verification(self, storage_with_scan):
        """delete_verification() debe eliminar el registro y retornar True."""
        mgr, scan_id = storage_with_scan
        vid = mgr.create_verification(scan_id=scan_id, recommended_freq=5745)

        result = mgr.delete_verification(vid)
        assert result is True
        assert mgr.get_verification(vid) is None

    def test_delete_returns_false_for_unknown(self, storage):
        """delete_verification() con id inexistente debe retornar False."""
        result = storage.delete_verification(99999)
        assert result is False


# ── T40 — Tests de rutas HTTP ─────────────────────────────────────────────

# Fixtures HTTP


@pytest.fixture
def client_with_scan(authenticated_client, tmp_path):
    """authenticated_client con un scan válido insertado en la DB activa."""
    from app.web_app import app

    with app.app_context():
        db_manager = app.config["db_manager"]
        _insert_scan(db_manager, SCAN_ID)
    yield authenticated_client


@pytest.fixture
def client_operator(tmp_path, monkeypatch):
    """Cliente autenticado como operador (no admin) para tests de permisos."""
    from app.audit_manager import AuditManager
    from app.db_manager import DatabaseManager
    from app.web_app import app, auth_manager

    db_path = str(tmp_path / "op_test.db")
    monkeypatch.setenv("AUTH_DB_PATH", db_path)
    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)

    dm = DatabaseManager(db_path)
    auth_manager.__init__(db_manager=dm)
    app.config["auth_manager"] = auth_manager

    # Create an operator user (must_change=False so login doesn't redirect)
    auth_manager.create_user("operator1", "pass123", role="operator", must_change=False)

    app.config["TESTING"] = True
    with app.test_client() as c:
        c.post(
            "/login",
            data={"username": "operator1", "password": "pass123"},
        )
        yield c


class TestConfigRoutes:
    """Tests HTTP para los endpoints de verificaciones de configuración."""

    # ── POST /api/config-verifications ───────────────────────────────────

    def test_create_verification_success(self, client_with_scan):
        """POST /api/config-verifications debe crear la verificación y retornar 201."""
        resp = client_with_scan.post(
            "/api/config-verifications",
            json={
                "scan_id": SCAN_ID,
                "recommended_freq": 5745,
                "ap_ip": "10.0.0.1",
                "applied_freq": 5760,
                "notes": "Test OK",
            },
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert "id" in data
        assert isinstance(data["id"], int)
        assert "message" in data

    def test_create_missing_scan_id(self, authenticated_client):
        """POST sin scan_id debe retornar 400."""
        resp = authenticated_client.post(
            "/api/config-verifications",
            json={"recommended_freq": 5745},
        )
        assert resp.status_code == 400
        assert "scan_id" in resp.get_json().get("error", "")

    def test_create_missing_recommended_freq(self, authenticated_client):
        """POST sin recommended_freq debe retornar 400."""
        resp = authenticated_client.post(
            "/api/config-verifications",
            json={"scan_id": "some-scan"},
        )
        assert resp.status_code == 400
        assert "recommended_freq" in resp.get_json().get("error", "")

    def test_create_invalid_scan_id_returns_422(self, authenticated_client):
        """POST con scan_id que no existe en DB debe retornar 422."""
        resp = authenticated_client.post(
            "/api/config-verifications",
            json={"scan_id": "nonexistent-scan-xyz", "recommended_freq": 5745},
        )
        assert resp.status_code == 422

    def test_create_requires_login(self, client):
        """POST sin sesión debe retornar 401."""
        resp = client.post(
            "/api/config-verifications",
            json={"scan_id": SCAN_ID, "recommended_freq": 5745},
        )
        assert resp.status_code == 401

    # ── GET /api/config-verifications ────────────────────────────────────

    def test_list_verifications(self, client_with_scan):
        """GET /api/config-verifications debe retornar lista y total."""
        # Create one first
        client_with_scan.post(
            "/api/config-verifications",
            json={"scan_id": SCAN_ID, "recommended_freq": 5745},
        )
        resp = client_with_scan.get("/api/config-verifications")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "verifications" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_list_verifications_empty(self, authenticated_client):
        """GET /api/config-verifications debe retornar estructura correcta (verifications + total)."""
        resp = authenticated_client.get("/api/config-verifications")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "verifications" in data
        assert "total" in data
        assert isinstance(data["verifications"], list)
        assert data["total"] == len(data["verifications"])

    def test_list_requires_login(self, client):
        """GET /api/config-verifications sin sesión debe retornar 401."""
        resp = client.get(
            "/api/config-verifications",
            content_type="application/json",
        )
        assert resp.status_code == 401

    # ── GET /api/config-verifications/<id> ───────────────────────────────

    def test_get_by_id(self, client_with_scan):
        """GET /api/config-verifications/<id> debe retornar la verificación."""
        create_resp = client_with_scan.post(
            "/api/config-verifications",
            json={"scan_id": SCAN_ID, "recommended_freq": 5480},
        )
        vid = create_resp.get_json()["id"]

        resp = client_with_scan.get(f"/api/config-verifications/{vid}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == vid
        assert data["recommended_freq"] == 5480

    def test_get_not_found(self, authenticated_client):
        """GET /api/config-verifications/99999 debe retornar 404."""
        resp = authenticated_client.get("/api/config-verifications/99999")
        assert resp.status_code == 404

    # ── GET /api/scans/<scan_id>/verifications ────────────────────────────

    def test_get_scan_verifications(self, client_with_scan):
        """GET /api/scans/<scan_id>/verifications debe retornar las del scan."""
        client_with_scan.post(
            "/api/config-verifications",
            json={"scan_id": SCAN_ID, "recommended_freq": 5745},
        )
        resp = client_with_scan.get(f"/api/scans/{SCAN_ID}/verifications")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] >= 1
        assert all(v["scan_id"] == SCAN_ID for v in data["verifications"])

    # ── PUT /api/config-verifications/<id> ───────────────────────────────

    def test_update_verification_success(self, client_with_scan):
        """PUT /api/config-verifications/<id> debe actualizar los campos."""
        create_resp = client_with_scan.post(
            "/api/config-verifications",
            json={"scan_id": SCAN_ID, "recommended_freq": 5745},
        )
        vid = create_resp.get_json()["id"]

        resp = client_with_scan.put(
            f"/api/config-verifications/{vid}",
            json={"applied_freq": 5760, "notes": "Actualizado"},
        )
        assert resp.status_code == 200
        assert "message" in resp.get_json()

        # Verify fields were updated
        get_resp = client_with_scan.get(f"/api/config-verifications/{vid}")
        updated = get_resp.get_json()
        assert updated["applied_freq"] == 5760
        assert updated["notes"] == "Actualizado"

    def test_update_not_found(self, authenticated_client):
        """PUT /api/config-verifications/99999 debe retornar 404."""
        resp = authenticated_client.put(
            "/api/config-verifications/99999",
            json={"applied_freq": 5760},
        )
        assert resp.status_code == 404

    # ── DELETE /api/config-verifications/<id> ────────────────────────────

    def test_delete_requires_admin(self, client_operator, tmp_path):
        """DELETE sin rol admin debe retornar 403."""
        resp = client_operator.delete(
            "/api/config-verifications/1",
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_delete_verification_success(self, client_with_scan):
        """DELETE /api/config-verifications/<id> debe eliminar el registro (admin)."""
        create_resp = client_with_scan.post(
            "/api/config-verifications",
            json={"scan_id": SCAN_ID, "recommended_freq": 5745},
        )
        vid = create_resp.get_json()["id"]

        resp = client_with_scan.delete(f"/api/config-verifications/{vid}")
        assert resp.status_code == 200

        # Should now be 404
        get_resp = client_with_scan.get(f"/api/config-verifications/{vid}")
        assert get_resp.status_code == 404

    def test_delete_not_found(self, authenticated_client):
        """DELETE /api/config-verifications/99999 debe retornar 404."""
        resp = authenticated_client.delete("/api/config-verifications/99999")
        assert resp.status_code == 404
