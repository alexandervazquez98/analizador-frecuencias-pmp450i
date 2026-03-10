"""
tests/test_cnmaestro_client.py — Unit Tests para CnMaestroClient (app/cnmaestro_client.py)

Metodologia: TDD (data-driven) para flujos REST/OAuth2.

Cobertura:
  - Constructor: URL normalization, state defaults
  - _get_token: OAuth2 token acquisition, caching, expiry, errors
  - _fetch_all_statistics: pagination, ap_mac fallback, error handling
  - get_full_inventory: hierarchy building, AP/SM classification, linking,
    orphaned SMs, cache, circular reference prevention

Mock strategy:
  - requests.post patched for OAuth2 token
  - requests.get patched for statistics and inventory
  - app.cnmaestro_client.time.time patched for cache TTL tests
"""

import pytest
import requests
from unittest.mock import patch, MagicMock, call

from app.cnmaestro_client import CnMaestroClient


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers / Factories
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def make_client(
    api_url="https://cnmaestro.example.com/api/v1",
    client_id="test-id",
    client_secret="test-secret",
):
    """Factory para CnMaestroClient con defaults de test."""
    return CnMaestroClient(api_url, client_id, client_secret)


def make_mock_response(json_data=None, status_code=200, raise_error=False):
    """
    Build a mock requests.Response.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if raise_error:
        resp.raise_for_status.side_effect = requests.HTTPError("HTTP Error")
    else:
        resp.raise_for_status.return_value = None
    return resp


def make_token_response(access_token="test-token-abc", expires_in=3600):
    """Build a mock OAuth2 token response."""
    return make_mock_response(
        json_data={"access_token": access_token, "expires_in": expires_in}
    )


def make_device(
    name,
    ip,
    mac,
    network="TestNet",
    tower="Tower1",
    product="PMP 450i",
    status="online",
    mode=None,
):
    """Build a device dict matching cnMaestro API format."""
    return {
        "name": name,
        "ip": ip,
        "mac": mac,
        "network": network,
        "tower": tower,
        "product": product,
        "status": status,
    }


def make_stat(mac, ap_mac=None, mode=""):
    """Build a statistics entry matching cnMaestro API format."""
    entry = {"mac": mac, "mode": mode, "radio": {}}
    if ap_mac:
        entry["ap_mac"] = ap_mac
    return entry


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Constructor
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestConstructor:
    """
    GIVEN CnMaestroClient is instantiated
    WHEN various URL formats and parameters are provided
    THEN internal state is initialized correctly.
    """

    def test_trailing_slash_stripped(self):
        """GIVEN URL with trailing slash WHEN constructed THEN slash stripped."""
        client = CnMaestroClient("https://api.example.com/v1/", "id", "secret")
        assert client.api_url == "https://api.example.com/v1"

    def test_state_defaults(self):
        """GIVEN fresh client WHEN constructed THEN token=None, cache=None."""
        client = make_client()
        assert client.token is None
        assert client.token_expires_at == 0
        assert client.inventory_cache is None
        assert client.last_cache_update == 0
        assert client.CACHE_DURATION == 300

    def test_credentials_stored(self):
        """GIVEN id and secret WHEN constructed THEN stored as attributes."""
        client = CnMaestroClient("https://api.example.com", "my-id", "my-secret")
        assert client.client_id == "my-id"
        assert client.client_secret == "my-secret"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _get_token
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestGetToken:
    """
    GIVEN a CnMaestroClient
    WHEN _get_token() is called
    THEN it acquires, caches, and refreshes OAuth2 tokens correctly.
    """

    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_fresh_token_acquired(self, mock_time, mock_post):
        """GIVEN no cached token WHEN _get_token THEN POST to /access/token."""
        mock_post.return_value = make_token_response("fresh-token", 3600)
        client = make_client()
        token = client._get_token()

        assert token == "fresh-token"
        assert client.token == "fresh-token"
        # expires_at = time() + expires_in - 60 = 1000 + 3600 - 60 = 4540
        assert client.token_expires_at == 4540.0
        mock_post.assert_called_once()

    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=2000.0)
    def test_cached_token_reused(self, mock_time, mock_post):
        """GIVEN valid cached token WHEN _get_token THEN no HTTP call."""
        client = make_client()
        client.token = "cached-token"
        client.token_expires_at = 5000.0  # Far in the future

        token = client._get_token()
        assert token == "cached-token"
        mock_post.assert_not_called()

    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=6000.0)
    def test_expired_token_refreshed(self, mock_time, mock_post):
        """GIVEN expired token WHEN _get_token THEN fetches new token."""
        client = make_client()
        client.token = "old-token"
        client.token_expires_at = 5000.0  # Expired (time=6000 > 5000)

        mock_post.return_value = make_token_response("new-token", 3600)
        token = client._get_token()

        assert token == "new-token"
        mock_post.assert_called_once()

    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_auth_failure_raises(self, mock_time, mock_post):
        """GIVEN OAuth2 returns error WHEN _get_token THEN raises exception."""
        mock_post.return_value = make_mock_response(status_code=401, raise_error=True)
        client = make_client()

        with pytest.raises(requests.HTTPError):
            client._get_token()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# _fetch_all_statistics
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestFetchAllStatistics:
    """
    GIVEN a CnMaestroClient with a valid token
    WHEN _fetch_all_statistics() is called
    THEN it paginates through all statistics and builds a MAC->info map.
    """

    @patch("requests.get")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_single_page(self, mock_time, mock_get):
        """GIVEN one page of stats WHEN fetched THEN returns all entries."""
        client = make_client()
        client.token = "tok"
        client.token_expires_at = 99999

        stats = [make_stat("AA:BB:CC:DD:EE:01", mode="ap")]
        mock_get.side_effect = [
            make_mock_response({"data": stats}),
            make_mock_response({"data": []}),  # Empty page terminates loop
        ]

        result = client._fetch_all_statistics()
        assert "AA:BB:CC:DD:EE:01" in result
        assert result["AA:BB:CC:DD:EE:01"]["mode"] == "ap"

    @patch("requests.get")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_multi_page_pagination(self, mock_time, mock_get):
        """GIVEN 2 pages of 2 entries WHEN fetched THEN returns all 4."""
        client = make_client()
        client.token = "tok"
        client.token_expires_at = 99999

        page1 = [
            make_stat("AA:01", mode="ap"),
            make_stat("AA:02", mode="sm"),
        ]
        page2 = [
            make_stat("AA:03", mode="ap"),
            make_stat("AA:04", mode="sm"),
        ]

        mock_get.side_effect = [
            make_mock_response({"data": page1}),
            make_mock_response({"data": page2}),
            make_mock_response({"data": []}),  # Empty page terminates
        ]

        result = client._fetch_all_statistics()
        assert len(result) == 4

    @patch("requests.get")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_ap_mac_from_direct_field(self, mock_time, mock_get):
        """GIVEN stat with ap_mac at top level WHEN fetched THEN ap_mac captured."""
        client = make_client()
        client.token = "tok"
        client.token_expires_at = 99999

        stats = [{"mac": "SM:01", "ap_mac": "AP:01", "mode": "sm", "radio": {}}]
        mock_get.side_effect = [
            make_mock_response({"data": stats}),
            make_mock_response({"data": []}),  # Terminates loop
        ]

        result = client._fetch_all_statistics()
        assert result["SM:01"]["ap_mac"] == "AP:01"

    @patch("requests.get")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_ap_mac_from_radio_parent_mac(self, mock_time, mock_get):
        """GIVEN stat with ap_mac only in radio.parent_mac WHEN fetched THEN captured."""
        client = make_client()
        client.token = "tok"
        client.token_expires_at = 99999

        stats = [{"mac": "SM:02", "mode": "sm", "radio": {"parent_mac": "AP:99"}}]
        mock_get.side_effect = [
            make_mock_response({"data": stats}),
            make_mock_response({"data": []}),  # Terminates loop
        ]

        result = client._fetch_all_statistics()
        assert result["SM:02"]["ap_mac"] == "AP:99"

    @patch("requests.get")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_non_200_breaks_loop(self, mock_time, mock_get):
        """GIVEN first page returns 500 WHEN fetched THEN returns empty dict."""
        client = make_client()
        client.token = "tok"
        client.token_expires_at = 99999

        mock_get.return_value = make_mock_response(status_code=500)

        result = client._fetch_all_statistics()
        assert result == {}

    @patch("requests.get")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_exception_breaks_loop(self, mock_time, mock_get):
        """GIVEN requests.get raises WHEN fetched THEN returns what was collected."""
        client = make_client()
        client.token = "tok"
        client.token_expires_at = 99999

        mock_get.side_effect = Exception("Connection reset")

        result = client._fetch_all_statistics()
        assert result == {}

    @patch("requests.get")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_entries_without_mac_skipped(self, mock_time, mock_get):
        """GIVEN stat entry with no mac field WHEN fetched THEN entry skipped."""
        client = make_client()
        client.token = "tok"
        client.token_expires_at = 99999

        stats = [
            {"mode": "ap", "radio": {}},  # No mac
            {"mac": "AA:01", "mode": "sm", "radio": {}},
        ]
        mock_get.side_effect = [
            make_mock_response({"data": stats}),
            make_mock_response({"data": []}),  # Terminates loop
        ]

        result = client._fetch_all_statistics()
        assert len(result) == 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Inventory Cache
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestInventoryCache:
    """
    GIVEN CnMaestroClient with get_full_inventory()
    WHEN cache is fresh, expired, or force_refresh is used
    THEN caching behavior matches TTL and force flag.
    """

    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_cache_hit_within_ttl(self, mock_time):
        """GIVEN cache set 100s ago (TTL=300) WHEN get_full_inventory THEN returns cache."""
        client = make_client()
        client.inventory_cache = {"cached": True}
        client.last_cache_update = 900.0  # 1000 - 900 = 100s < 300s

        result = client.get_full_inventory()
        assert result == {"cached": True}

    @patch("requests.get")
    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=2000.0)
    def test_cache_miss_after_ttl(self, mock_time, mock_post, mock_get):
        """GIVEN cache set 400s ago (TTL=300) WHEN get_full_inventory THEN refetches."""
        client = make_client()
        client.inventory_cache = {"old": True}
        client.last_cache_update = 1600.0  # 2000 - 1600 = 400s > 300s

        mock_post.return_value = make_token_response()
        mock_get.return_value = make_mock_response({"data": []})

        result = client.get_full_inventory()
        # Should have fetched fresh data (empty structure since no devices)
        assert result != {"old": True}

    @patch("requests.get")
    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_force_refresh_bypasses_cache(self, mock_time, mock_post, mock_get):
        """GIVEN valid cache WHEN force_refresh=True THEN refetches."""
        client = make_client()
        client.inventory_cache = {"cached": True}
        client.last_cache_update = 999.0  # Only 1s ago

        mock_post.return_value = make_token_response()
        mock_get.return_value = make_mock_response({"data": []})

        result = client.get_full_inventory(force_refresh=True)
        assert result != {"cached": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Hierarchy Building
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestHierarchyBuilding:
    """
    GIVEN device and stats data from cnMaestro API
    WHEN get_full_inventory() processes them
    THEN it builds the correct Network -> Tower -> AP -> SM hierarchy.
    """

    @patch("requests.get")
    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_ap_classified_by_mode(self, mock_time, mock_post, mock_get):
        """GIVEN device with mode='ap' in stats WHEN processed THEN classified as AP."""
        devices = [make_device("MyAP", "10.0.0.1", "AP:01")]
        stats = [make_stat("AP:01", mode="ap")]

        mock_post.return_value = make_token_response()
        # Inventory: 1 device (len=1 < limit=100 → breaks after page 1)
        # Stats: 1 stat page, then empty page terminates
        mock_get.side_effect = [
            make_mock_response({"data": devices}),  # Inventory page 1 (breaks: 1<100)
            make_mock_response({"data": stats}),  # Stats page 1
            make_mock_response({"data": []}),  # Stats end (empty batch → break)
        ]

        client = make_client()
        result = client.get_full_inventory()

        aps = result["TestNet"]["Tower1"]["aps"]
        assert len(aps) == 1
        assert aps[0]["name"] == "MyAP"

    @patch("requests.get")
    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_ap_classified_by_name_prefix(self, mock_time, mock_post, mock_get):
        """GIVEN device name starts with 'AP' and no mode in stats WHEN processed THEN AP."""
        devices = [make_device("AP-Tower1-Sector1", "10.0.0.1", "XX:01")]

        mock_post.return_value = make_token_response()
        mock_get.side_effect = [
            make_mock_response({"data": devices}),  # Inventory (1<100, breaks)
            make_mock_response({"data": []}),  # Stats (empty → break)
        ]

        client = make_client()
        result = client.get_full_inventory()

        aps = result["TestNet"]["Tower1"]["aps"]
        assert len(aps) == 1

    @patch("requests.get")
    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_network_tower_structure(self, mock_time, mock_post, mock_get):
        """GIVEN devices in 2 networks WHEN processed THEN correct structure."""
        devices = [
            make_device("AP1", "10.0.0.1", "AP:01", network="NetA", tower="T1"),
            make_device("AP2", "10.0.0.2", "AP:02", network="NetB", tower="T2"),
        ]
        stats = [
            make_stat("AP:01", mode="ap"),
            make_stat("AP:02", mode="ap"),
        ]

        mock_post.return_value = make_token_response()
        mock_get.side_effect = [
            make_mock_response({"data": devices}),  # Inventory (2<100, breaks)
            make_mock_response({"data": stats}),  # Stats page 1
            make_mock_response({"data": []}),  # Stats end
        ]

        client = make_client()
        result = client.get_full_inventory()

        assert "NetA" in result
        assert "NetB" in result
        assert "T1" in result["NetA"]
        assert "T2" in result["NetB"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SM to AP Linking
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSMToAPLinking:
    """
    GIVEN AP and SM devices with stats containing ap_mac
    WHEN get_full_inventory() processes them
    THEN SMs are linked to correct APs, or placed in orphaned_sms.
    """

    @patch("requests.get")
    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_sm_linked_to_ap(self, mock_time, mock_post, mock_get):
        """GIVEN SM with ap_mac matching an AP WHEN processed THEN SM in AP's sms list."""
        devices = [
            make_device("AP1", "10.0.0.1", "AP:01"),
            make_device("SM1", "10.0.1.1", "SM:01"),
        ]
        stats = [
            make_stat("AP:01", mode="ap"),
            make_stat("SM:01", ap_mac="AP:01", mode="sm"),
        ]

        mock_post.return_value = make_token_response()
        mock_get.side_effect = [
            make_mock_response({"data": devices}),  # Inventory (2<100, breaks)
            make_mock_response({"data": stats}),  # Stats page 1
            make_mock_response({"data": []}),  # Stats end
        ]

        client = make_client()
        result = client.get_full_inventory()

        ap = result["TestNet"]["Tower1"]["aps"][0]
        assert len(ap["sms"]) == 1
        assert ap["sms"][0]["name"] == "SM1"

    @patch("requests.get")
    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_orphaned_sm(self, mock_time, mock_post, mock_get):
        """GIVEN SM with no matching AP mac WHEN processed THEN SM in orphaned_sms."""
        devices = [
            make_device("AP1", "10.0.0.1", "AP:01"),
            make_device("SM-Orphan", "10.0.1.2", "SM:02"),
        ]
        stats = [
            make_stat("AP:01", mode="ap"),
            make_stat("SM:02", ap_mac="UNKNOWN:MAC", mode="sm"),
        ]

        mock_post.return_value = make_token_response()
        mock_get.side_effect = [
            make_mock_response({"data": devices}),  # Inventory (2<100, breaks)
            make_mock_response({"data": stats}),  # Stats page 1
            make_mock_response({"data": []}),  # Stats end
        ]

        client = make_client()
        result = client.get_full_inventory()

        orphans = result["TestNet"]["Tower1"]["orphaned_sms"]
        assert len(orphans) == 1
        assert orphans[0]["name"] == "SM-Orphan"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Circular Reference Prevention
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCircularRefPrevention:
    """
    GIVEN SM entries with tower_ref during processing
    WHEN hierarchy building completes
    THEN tower_ref is removed to prevent circular JSON serialization.
    """

    @patch("requests.get")
    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_tower_ref_removed_from_sm(self, mock_time, mock_post, mock_get):
        """GIVEN SM linked to AP WHEN processed THEN no 'tower_ref' key in SM entry."""
        devices = [
            make_device("AP1", "10.0.0.1", "AP:01"),
            make_device("SM1", "10.0.1.1", "SM:01"),
        ]
        stats = [
            make_stat("AP:01", mode="ap"),
            make_stat("SM:01", ap_mac="AP:01", mode="sm"),
        ]

        mock_post.return_value = make_token_response()
        mock_get.side_effect = [
            make_mock_response({"data": devices}),  # Inventory (2<100, breaks)
            make_mock_response({"data": stats}),  # Stats page 1
            make_mock_response({"data": []}),  # Stats end
        ]

        client = make_client()
        result = client.get_full_inventory()

        sm = result["TestNet"]["Tower1"]["aps"][0]["sms"][0]
        assert "tower_ref" not in sm

    @patch("requests.get")
    @patch("requests.post")
    @patch("app.cnmaestro_client.time.time", return_value=1000.0)
    def test_tower_ref_removed_from_orphan(self, mock_time, mock_post, mock_get):
        """GIVEN orphaned SM WHEN processed THEN no 'tower_ref' key."""
        devices = [
            make_device("AP1", "10.0.0.1", "AP:01"),
            make_device("SM-Orphan", "10.0.1.2", "SM:02"),
        ]
        stats = [
            make_stat("AP:01", mode="ap"),
            make_stat("SM:02", ap_mac="UNKNOWN:MAC", mode="sm"),
        ]

        mock_post.return_value = make_token_response()
        mock_get.side_effect = [
            make_mock_response({"data": devices}),  # Inventory (2<100, breaks)
            make_mock_response({"data": stats}),  # Stats page 1
            make_mock_response({"data": []}),  # Stats end
        ]

        client = make_client()
        result = client.get_full_inventory()

        orphan = result["TestNet"]["Tower1"]["orphaned_sms"][0]
        assert "tower_ref" not in orphan
