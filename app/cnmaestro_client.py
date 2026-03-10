import requests
import logging
import time
from typing import Dict
import requests.packages.urllib3

# Suppress InsecureRequestWarning
requests.packages.urllib3.disable_warnings(
    requests.packages.urllib3.exceptions.InsecureRequestWarning
)

logger = logging.getLogger(__name__)


class CnMaestroClient:
    def __init__(self, api_url: str, client_id: str, client_secret: str):
        self.api_url = api_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = None
        self.token_expires_at = 0
        self.inventory_cache = None
        self.last_cache_update = 0
        self.CACHE_DURATION = 300  # 5 minutes

    def _get_token(self) -> str:
        if self.token and time.time() < self.token_expires_at:
            return self.token

        url = f"{self.api_url}/access/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }

        try:
            response = requests.post(url, data=data, verify=False, timeout=10)
            response.raise_for_status()
            js = response.json()
            self.token = js["access_token"]
            # Expire slightly before actual time
            self.token_expires_at = time.time() + int(js.get("expires_in", 3600)) - 60
            return self.token
        except Exception as e:
            logger.error(f"Error getting cnMaestro token: {e}")
            raise

    def _fetch_all_statistics(self) -> Dict[str, Dict]:
        """
        Fetch statistics for ALL devices to get Link info (parent match).
        Returns dict: { device_mac: { 'ap_mac': '...', 'mode': '...' } }
        """
        stats_map = {}
        offset = 0
        limit = 100

        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}"}

        logger.info("Fetching cnMaestro statistics for AP-SM matching...")

        while True:
            try:
                url = f"{self.api_url}/devices/statistics?offset={offset}&limit={limit}"
                resp = requests.get(url, headers=headers, verify=False, timeout=30)

                if resp.status_code != 200:
                    logger.warning(
                        f"Stats fetch failed at offset {offset}: {resp.status_code}"
                    )
                    break

                data = resp.json()
                batch = data.get("data", [])
                if not batch:
                    break

                for d in batch:
                    mac = d.get("mac")
                    if not mac:
                        continue

                    # Extract Link Info
                    radio = d.get("radio", {})
                    ap_mac = (
                        d.get("ap_mac")
                        or radio.get("ap_mac")
                        or radio.get("parent_mac")
                    )

                    stats_map[mac] = {
                        "ap_mac": ap_mac,
                        "mode": d.get("mode", radio.get("mode", "")),
                    }

                offset += len(batch)

            except Exception as e:
                logger.error(f"Error fetching stats: {e}")
                break

        return stats_map

    def get_full_inventory(self, force_refresh: bool = False) -> Dict:
        """
        Fetches all devices + stats matches.
        Returns hierarchy: Network -> Tower -> { aps: [ {..., sms: []} ] }
        """
        if (
            self.inventory_cache
            and not force_refresh
            and (time.time() - self.last_cache_update < self.CACHE_DURATION)
        ):
            return self.inventory_cache

        # 1. Fetch Basic Inventory (Names, IPs, Towers)
        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        all_devices = []
        offset = 0
        limit = 100

        logger.info("Fetching cnMaestro inventory...")
        while True:
            try:
                url = f"{self.api_url}/devices?offset={offset}&limit={limit}"
                resp = requests.get(url, headers=headers, verify=False, timeout=20)
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("data", [])
                all_devices.extend(batch)
                if len(batch) < limit:
                    break
                offset += len(batch)
            except Exception as e:
                logger.error(f"Error fetching devices: {e}")
                break

        # 2. Fetch Statistics to link SM -> AP
        stats_map = self._fetch_all_statistics()

        # 3. Process & Build Hierarchy
        structure = {}
        ap_lookup = {}  # map ap_mac -> ap_object ref

        sms_buffer = []  # Store SMs temporarily to link later

        # Pass 1: Create APs and Structure
        for d in all_devices:
            network = d.get("network", "Unknown")
            tower = d.get("tower", "Unknown")
            name = d.get("name", "")
            ip = d.get("ip", "")
            mac = d.get("mac", "")

            # Determine type using Stats if available (more reliable), else Heuristic
            stat = stats_map.get(mac, {})
            mode = stat.get("mode", "").lower()

            is_ap = (
                mode in ["ap", "master"]
                or name.upper().startswith("AP")
                or name.upper().startswith("BHS")
            )

            if network not in structure:
                structure[network] = {}
            if tower not in structure[network]:
                structure[network][tower] = {"aps": [], "orphaned_sms": []}

            device_entry = {
                "name": name,
                "ip": ip,
                "mac": mac,
                "type": d.get("product", "Unknown"),
                "status": d.get("status", "offline"),
                "sms": [],  # Only used if AP
            }

            if is_ap:
                structure[network][tower]["aps"].append(device_entry)
                if mac:
                    ap_lookup[mac] = device_entry
            else:
                # Is SM
                # Store extra info for linking
                device_entry["ap_mac"] = stat.get("ap_mac")
                device_entry["tower_ref"] = structure[network][
                    tower
                ]  # Ref to tower obj
                sms_buffer.append(device_entry)

        # Pass 2: Link SMs to APs
        for sm in sms_buffer:
            parent_mac = sm.get("ap_mac")
            linked = False

            # Retrieve and remove usage refs to prevent circular json
            tower_obj = sm.pop("tower_ref", None)
            # Note: keeping ap_mac might be useful for debug, but 'tower_ref' IS the killer.
            # user might want ap_mac visible? Let's keep ap_mac string, but definitely kill the object ref.

            if parent_mac and parent_mac in ap_lookup:
                # Direct Link Match!
                ap_lookup[parent_mac]["sms"].append(sm)
                linked = True

            # If not linked by MAC, put in tower orphans (fallback)
            if not linked:
                if tower_obj:
                    tower_obj["orphaned_sms"].append(sm)

        self.inventory_cache = structure
        self.last_cache_update = time.time()
        logger.info(f"Inventory updated with Topology: {len(all_devices)} devices.")
        return structure
