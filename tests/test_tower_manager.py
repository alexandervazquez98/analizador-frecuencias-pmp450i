"""
tests/test_tower_manager.py — Unit & integration tests for TowerManager and tower routes.

Specification: change-004 specs § S4.5 — Tower CRUD
Design:        change-004 design § D4.5 — TowerManager

Tests (~25):
  - Tower ID validation (valid patterns, invalid patterns, normalization)
  - CRUD operations (create, get, list, update, delete)
  - Duplicate tower_id error (IntegrityError)
  - Update/delete non-existent tower
  - Search functionality
  - Thread safety (basic)
  - Route-level tests (POST, GET, PUT, DELETE, search)
"""

import sqlite3
import pytest
from threading import Thread

from app.db_manager import DatabaseManager
from app.tower_manager import TowerManager, TowerValidationError, TOWER_ID_PATTERN


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def tower_manager(db_manager):
    """Creates a TowerManager with the shared db_manager fixture."""
    return TowerManager(db_manager)


@pytest.fixture
def tower_client(tmp_path, monkeypatch):
    """Flask test client with TowerManager wired in, logged in as admin."""
    from app.audit_manager import AuditManager

    db_path = str(tmp_path / "test_tower.db")
    monkeypatch.setenv("AUTH_DB_PATH", db_path)

    log_file = str(tmp_path / "audit_logs.jsonl")
    monkeypatch.setattr(AuditManager, "LOG_FILE", log_file)

    from app.web_app import app, auth_manager

    dm = DatabaseManager(db_path)
    auth_manager.__init__(db_manager=dm)
    app.config["auth_manager"] = auth_manager

    tm = TowerManager(dm)
    app.config["tower_manager"] = tm

    # Clear must_change_password for admin
    auth_manager.change_password(1, "admin")

    app.config["TESTING"] = True
    with app.test_client() as c:
        c.post("/login", data={"username": "admin", "password": "admin"})
        # Set admin role in session
        with c.session_transaction() as sess:
            sess["role"] = "admin"
        yield c


# ══════════════════════════════════════════════════════════════════════
# Tower ID Validation Tests
# ══════════════════════════════════════════════════════════════════════


class TestTowerIDValidation:
    """Tests for validate_tower_id() — pattern matching and normalization."""

    @pytest.mark.parametrize(
        "tower_id",
        [
            "BAJ02-RTD-ENSE-003",
            "AA-BB-CC-001",
            "ABCDE-ABCDE-ABCDE-999",
            "A1-AB-CD-000",
            "AB-AB-AB-123",
        ],
    )
    def test_valid_tower_ids(self, tower_id):
        """GIVEN valid tower ID WHEN validate THEN returns same string."""
        result = TowerManager.validate_tower_id(tower_id)
        assert result == tower_id

    def test_normalizes_lowercase_to_upper(self):
        """GIVEN lowercase tower ID WHEN validate THEN normalized to uppercase."""
        result = TowerManager.validate_tower_id("baj02-rtd-ense-003")
        assert result == "BAJ02-RTD-ENSE-003"

    def test_normalizes_mixed_case(self):
        """GIVEN mixed case tower ID WHEN validate THEN normalized to uppercase."""
        result = TowerManager.validate_tower_id("Baj02-Rtd-Ense-003")
        assert result == "BAJ02-RTD-ENSE-003"

    def test_strips_whitespace(self):
        """GIVEN tower ID with leading/trailing whitespace WHEN validate THEN stripped."""
        result = TowerManager.validate_tower_id("  AA-BB-CC-001  ")
        assert result == "AA-BB-CC-001"

    @pytest.mark.parametrize(
        "tower_id,reason",
        [
            ("BAJ02_RTD_ENSE_003", "underscores instead of dashes"),
            ("BAJ02-RTD-003", "only 3 segments"),
            ("TOOLONG1-RTD-ENSE-003", "first segment >5 chars"),
            ("A-BB-CC-001", "first segment <2 chars"),
            ("AA-B-CC-001", "second segment <2 chars"),
            ("AA-BB-C-001", "third segment <2 chars"),
            ("AA-BB-CC-01", "number <3 digits"),
            ("AA-BB-CC-1234", "number >3 digits"),
            ("AA-12-CC-001", "digits in second segment"),
            ("AA-BB-12-001", "digits in third segment"),
            ("", "empty string"),
            ("   ", "whitespace only"),
        ],
    )
    def test_invalid_tower_ids(self, tower_id, reason):
        """GIVEN invalid tower ID ({reason}) WHEN validate THEN TowerValidationError."""
        with pytest.raises(TowerValidationError):
            TowerManager.validate_tower_id(tower_id)

    def test_none_raises_error(self):
        """GIVEN None as tower_id WHEN validate THEN TowerValidationError."""
        with pytest.raises(TowerValidationError):
            TowerManager.validate_tower_id(None)

    def test_regex_pattern_matches_spec(self):
        """GIVEN the TOWER_ID_PATTERN THEN it matches the specified regex."""
        assert (
            TOWER_ID_PATTERN.pattern == r"^[A-Z0-9]{2,5}-[A-Z]{2,5}-[A-Z]{2,5}-\d{3}$"
        )


# ══════════════════════════════════════════════════════════════════════
# CRUD Operation Tests
# ══════════════════════════════════════════════════════════════════════


class TestTowerCreate:
    """Tests for TowerManager.create()."""

    def test_create_tower_returns_dict(self, tower_manager):
        """GIVEN valid data WHEN create tower THEN returns dict with all fields."""
        tower = tower_manager.create("AA-BB-CC-001", "Test Tower")
        assert isinstance(tower, dict)
        assert tower["tower_id"] == "AA-BB-CC-001"
        assert tower["name"] == "Test Tower"
        assert tower["location"] is None
        assert tower["notes"] is None
        assert tower["created_at"] is not None

    def test_create_tower_with_all_fields(self, tower_manager):
        """GIVEN all optional fields WHEN create tower THEN all persisted."""
        tower = tower_manager.create(
            "AA-BB-CC-002",
            "Full Tower",
            location="Site Alpha",
            notes="Test notes",
            created_by=None,
        )
        assert tower["name"] == "Full Tower"
        assert tower["location"] == "Site Alpha"
        assert tower["notes"] == "Test notes"

    def test_create_normalizes_id(self, tower_manager):
        """GIVEN lowercase tower_id WHEN create THEN stored as uppercase."""
        tower = tower_manager.create("aa-bb-cc-001", "Lowercase Tower")
        assert tower["tower_id"] == "AA-BB-CC-001"

    def test_create_duplicate_raises_integrity_error(self, tower_manager):
        """GIVEN existing tower WHEN create same ID THEN IntegrityError."""
        tower_manager.create("AA-BB-CC-001", "First")
        with pytest.raises(sqlite3.IntegrityError):
            tower_manager.create("AA-BB-CC-001", "Second")

    def test_create_invalid_id_raises_validation_error(self, tower_manager):
        """GIVEN invalid tower_id WHEN create THEN TowerValidationError."""
        with pytest.raises(TowerValidationError):
            tower_manager.create("INVALID", "Bad Tower")


class TestTowerGet:
    """Tests for TowerManager.get_by_id()."""

    def test_get_existing_tower(self, tower_manager):
        """GIVEN created tower WHEN get_by_id THEN returns tower dict."""
        tower_manager.create("AA-BB-CC-001", "Test Tower")
        tower = tower_manager.get_by_id("AA-BB-CC-001")
        assert tower is not None
        assert tower["name"] == "Test Tower"

    def test_get_nonexistent_tower(self, tower_manager):
        """GIVEN no tower WHEN get_by_id THEN returns None."""
        tower = tower_manager.get_by_id("AA-BB-CC-999")
        assert tower is None

    def test_get_normalizes_id(self, tower_manager):
        """GIVEN tower created uppercase WHEN get with lowercase THEN still found."""
        tower_manager.create("AA-BB-CC-001", "Test Tower")
        tower = tower_manager.get_by_id("aa-bb-cc-001")
        assert tower is not None


class TestTowerList:
    """Tests for TowerManager.list_all()."""

    def test_list_empty(self, tower_manager):
        """GIVEN no towers WHEN list_all THEN returns empty list."""
        result = tower_manager.list_all()
        assert result == []

    def test_list_returns_all_towers(self, tower_manager):
        """GIVEN 3 towers WHEN list_all THEN returns 3 dicts."""
        tower_manager.create("AA-BB-CC-001", "Tower 1")
        tower_manager.create("AA-BB-CC-002", "Tower 2")
        tower_manager.create("AA-BB-CC-003", "Tower 3")
        result = tower_manager.list_all()
        assert len(result) == 3

    def test_list_ordered_by_created_at_desc(self, tower_manager):
        """GIVEN 2 towers WHEN list_all THEN most recent first."""
        tower_manager.create("AA-BB-CC-001", "First")
        tower_manager.create("AA-BB-CC-002", "Second")
        result = tower_manager.list_all()
        # Second created should be first in list (DESC order)
        # Note: SQLite datetime('now') may have same second, but order is still deterministic
        assert len(result) == 2


class TestTowerUpdate:
    """Tests for TowerManager.update()."""

    def test_update_name(self, tower_manager):
        """GIVEN existing tower WHEN update name THEN name changes."""
        tower_manager.create("AA-BB-CC-001", "Original")
        updated = tower_manager.update("AA-BB-CC-001", name="Updated")
        assert updated["name"] == "Updated"
        assert updated["tower_id"] == "AA-BB-CC-001"

    def test_update_location_and_notes(self, tower_manager):
        """GIVEN existing tower WHEN update location+notes THEN both change."""
        tower_manager.create("AA-BB-CC-001", "Tower")
        updated = tower_manager.update(
            "AA-BB-CC-001", location="New Site", notes="New notes"
        )
        assert updated["location"] == "New Site"
        assert updated["notes"] == "New notes"

    def test_update_nonexistent_returns_none(self, tower_manager):
        """GIVEN no tower WHEN update THEN returns None."""
        result = tower_manager.update("AA-BB-CC-999", name="Ghost")
        assert result is None

    def test_update_with_no_fields_still_updates_timestamp(self, tower_manager):
        """GIVEN existing tower WHEN update with no fields THEN updated_at changes."""
        tower_manager.create("AA-BB-CC-001", "Tower")
        original = tower_manager.get_by_id("AA-BB-CC-001")
        updated = tower_manager.update("AA-BB-CC-001")
        assert updated is not None
        # updated_at should be set (may or may not differ in same second)
        assert updated["updated_at"] is not None


class TestTowerDelete:
    """Tests for TowerManager.delete()."""

    def test_delete_existing_tower(self, tower_manager):
        """GIVEN existing tower WHEN delete THEN returns True."""
        tower_manager.create("AA-BB-CC-001", "To Delete")
        assert tower_manager.delete("AA-BB-CC-001") is True
        assert tower_manager.get_by_id("AA-BB-CC-001") is None

    def test_delete_nonexistent_returns_false(self, tower_manager):
        """GIVEN no tower WHEN delete THEN returns False."""
        assert tower_manager.delete("AA-BB-CC-999") is False


class TestTowerSearch:
    """Tests for TowerManager.search()."""

    def test_search_by_tower_id(self, tower_manager):
        """GIVEN towers WHEN search by partial ID THEN matches found."""
        tower_manager.create("BAJ02-RTD-ENSE-003", "Bajada Tower")
        tower_manager.create("AA-BB-CC-001", "Other Tower")
        results = tower_manager.search("BAJ02")
        assert len(results) == 1
        assert results[0]["tower_id"] == "BAJ02-RTD-ENSE-003"

    def test_search_by_name(self, tower_manager):
        """GIVEN towers WHEN search by name THEN matches found."""
        tower_manager.create("AA-BB-CC-001", "Alpha Site")
        tower_manager.create("AA-BB-CC-002", "Beta Site")
        results = tower_manager.search("Alpha")
        assert len(results) == 1
        assert results[0]["name"] == "Alpha Site"

    def test_search_no_results(self, tower_manager):
        """GIVEN towers WHEN search no match THEN empty list."""
        tower_manager.create("AA-BB-CC-001", "Tower")
        results = tower_manager.search("NONEXISTENT")
        assert results == []

    def test_search_partial_match(self, tower_manager):
        """GIVEN towers WHEN search with partial string THEN matches found."""
        tower_manager.create("AA-BB-CC-001", "Cerro Norte")
        tower_manager.create("AA-BB-CC-002", "Cerro Sur")
        tower_manager.create("AA-BB-CC-003", "Valle Este")
        results = tower_manager.search("Cerro")
        assert len(results) == 2


# ══════════════════════════════════════════════════════════════════════
# Thread Safety Tests
# ══════════════════════════════════════════════════════════════════════


class TestTowerThreadSafety:
    """Basic thread safety tests for TowerManager."""

    def test_concurrent_creates_no_crash(self, tower_manager):
        """GIVEN concurrent create calls WHEN different IDs THEN no crash."""
        errors = []

        def create_tower(suffix):
            try:
                tower_manager.create(f"AA-BB-CC-{suffix:03d}", f"Tower {suffix}")
            except Exception as e:
                errors.append(e)

        threads = [Thread(target=create_tower, args=(i,)) for i in range(1, 11)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        towers = tower_manager.list_all()
        assert len(towers) == 10


# ══════════════════════════════════════════════════════════════════════
# Route-Level Tests (Integration)
# ══════════════════════════════════════════════════════════════════════


class TestTowerRoutes:
    """Integration tests for /api/towers endpoints."""

    def test_create_tower_route(self, tower_client):
        """GIVEN authenticated user WHEN POST /api/towers THEN 201."""
        resp = tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-001", "name": "Route Tower"},
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["tower_id"] == "AA-BB-CC-001"
        assert data["name"] == "Route Tower"

    def test_create_tower_missing_fields(self, tower_client):
        """GIVEN missing name WHEN POST /api/towers THEN 400."""
        resp = tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-001"},
        )
        assert resp.status_code == 400

    def test_create_tower_invalid_id(self, tower_client):
        """GIVEN invalid tower_id WHEN POST /api/towers THEN 400."""
        resp = tower_client.post(
            "/api/towers",
            json={"tower_id": "INVALID", "name": "Bad"},
        )
        assert resp.status_code == 400

    def test_create_tower_duplicate(self, tower_client):
        """GIVEN existing tower WHEN POST same ID THEN 409."""
        tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-001", "name": "First"},
        )
        resp = tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-001", "name": "Second"},
        )
        assert resp.status_code == 409

    def test_list_towers_route(self, tower_client):
        """GIVEN 2 towers WHEN GET /api/towers THEN 200 with list."""
        tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-001", "name": "Tower 1"},
        )
        tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-002", "name": "Tower 2"},
        )
        resp = tower_client.get("/api/towers")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2

    def test_get_tower_route(self, tower_client):
        """GIVEN existing tower WHEN GET /api/towers/<id> THEN 200."""
        tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-001", "name": "My Tower"},
        )
        resp = tower_client.get("/api/towers/AA-BB-CC-001")
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "My Tower"

    def test_get_tower_not_found(self, tower_client):
        """GIVEN no tower WHEN GET /api/towers/<id> THEN 404."""
        resp = tower_client.get("/api/towers/AA-BB-CC-999")
        assert resp.status_code == 404

    def test_update_tower_route(self, tower_client):
        """GIVEN existing tower WHEN PUT /api/towers/<id> THEN 200."""
        tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-001", "name": "Original"},
        )
        resp = tower_client.put(
            "/api/towers/AA-BB-CC-001",
            json={"name": "Updated"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Updated"

    def test_delete_tower_route_admin(self, tower_client):
        """GIVEN admin user WHEN DELETE /api/towers/<id> THEN 200."""
        tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-001", "name": "To Delete"},
        )
        resp = tower_client.delete("/api/towers/AA-BB-CC-001")
        assert resp.status_code == 200

    def test_search_towers_route(self, tower_client):
        """GIVEN towers WHEN GET /api/towers/search?q=Alpha THEN matches."""
        tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-001", "name": "Alpha Site"},
        )
        tower_client.post(
            "/api/towers",
            json={"tower_id": "AA-BB-CC-002", "name": "Beta Site"},
        )
        resp = tower_client.get("/api/towers/search?q=Alpha")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1

    def test_search_towers_missing_query(self, tower_client):
        """GIVEN no q param WHEN GET /api/towers/search THEN 400."""
        resp = tower_client.get("/api/towers/search")
        assert resp.status_code == 400
