import time
import hashlib
import hmac
import base64
import requests
import logging

_LOGGER = logging.getLogger(__name__)

class HyxiApiClient:
    def __init__(self, access_key, secret_key, base_url):
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url
        self.token = None
        self.token_expires_at = 0

    def _generate_headers(self, path, method, use_body_hash=False):
        """Generates the required HMAC signature headers for the HYXi API."""
        timestamp = str(int(time.time() * 1000))
        nonce = format(int(time.time() * 1000), "x")[-8:]
        
        # Only use body hash for the token request
        content_str = "grantType:1" if (use_body_hash and path == "/api/authorization/v1/token") else ""
        hex_hash = hashlib.sha512(content_str.encode("utf-8")).hexdigest()
        
        string_to_sign = f"{path}\n{method.upper()}\n{hex_hash}\n"
        token_str = self.token if self.token else ""
        
        # HMAC-SHA512 Signature
        sign_string = f"{self.access_key}{token_str}{timestamp}{nonce}{string_to_sign}"
        hmac_bytes = hmac.new(
            self.secret_key.encode("utf-8"), 
            sign_string.encode("utf-8"), 
            hashlib.sha512
        ).digest()
        
        return {
            "AccessKey": self.access_key,
            "Timestamp": timestamp,
            "Nonce": nonce,
            "Sign": base64.b64encode(hmac_bytes).decode("utf-8"),
            "Authorization": token_str,
            "Content-Type": "application/json;charset=utf-8"
        }

    def _refresh_token(self):
        """Fetches a new Bearer token if current one is expired or missing."""
        if self.token and time.time() < self.token_expires_at:
            return True

        path = "/api/authorization/v1/token"
        headers = self._generate_headers(path, "POST", use_body_hash=True)
        try:
            r = requests.post(
                f"{self.base_url}{path}", 
                json={"grantType": 1}, 
                headers=headers, 
                timeout=15
            )
            res = r.json()
            if res.get("success") and "data" in res:
                # API sometimes returns 'token', sometimes 'access_token'
                token_val = res["data"].get("token") or res["data"].get("access_token")
                if token_val:
                    self.token = f"Bearer {token_val}"
                    self.token_expires_at = time.time() + 7000 # Buffer before 2hr expiry
                    return True
        except Exception as e:
            _LOGGER.error("HYXi Token Refresh Error: %s", e)
        return False

    def get_all_device_data(self):
        """Main entry point: Discovers plants/devices and processes metrics."""
        if not self._refresh_token():
            return None
        
        results = {}
        try:
            # 1. Get List of Plants
            p_path = "/api/plant/v1/page"
            r_plants = requests.post(
                f"{self.base_url}{p_path}", 
                json={"pageSize": 10, "currentPage": 1}, 
                headers=self._generate_headers(p_path, "POST")
            )
            plants = r_plants.json().get("data", {}).get("list", [])
            
            for p in plants:
                plant_id = p["plantId"]
                plant_name = p["plantName"]
                plant_slug = plant_name.replace(" ", "_").lower()
                
                # 2. Get Devices for this Plant
                d_path = "/api/plant/v1/devicePage"
                r_devices = requests.post(
                    f"{self.base_url}{d_path}", 
                    json={"plantId": plant_id, "pageSize": 50, "currentPage": 1}, 
                    headers=self._generate_headers(d_path, "POST")
                )
                
                for d in r_devices.json().get("data", {}).get("deviceList", []):
                    if d["deviceType"] == "HYBRID_INVERTER":
                        sn = d["deviceSn"]
                        
                        # 3. Get Real-time Metrics
                        q_path = "/api/device/v1/queryDeviceData"
                        qr = requests.get(
                            f"{self.base_url}{q_path}?deviceSn={sn}", 
                            headers=self._generate_headers(q_path, "GET"), 
                            timeout=15
                        )
                        
                        data_list = qr.json().get("data", [])
                        m_raw = {item["dataKey"]: item["dataValue"] for item in data_list}
                        
                        # Helper for safe float conversion
                        def get_f(key, mult=1.0):
                            try: return round(float(m_raw.get(key, 0)) * mult, 2)
                            except (ValueError, TypeError): return 0.0

                        # Perform calculations
                        pbat = get_f("pbat")
                        grid_w = get_f("gridP", 1000.0) # Convert kW to W
                        # Load is the sum of three phases
                        load = get_f("ph1Loadp") + get_f("ph2Loadp") + get_f("ph3Loadp")

                        # Generate a ISO 8601 timestamp for "Now" (Cloud Sync Time)
                        sync_time = datetime.now(timezone.utc).isoformat()

                        # Build the metrics dictionary
                        results[sn] = {
                            "sn": sn,
                            "device_name": d.get("deviceName", f"Inverter {sn}"),
                            "plant_name": plant_name,
                            "plant_slug": plant_slug,
                            "model": "Hybrid Inverter",
                            "metrics": {
                                **m_raw, # This automatically includes collectTime, batSn, deviceSn
                                "home_load": load,
                                "grid_import": abs(grid_w) if grid_w < 0 else 0,
                                "grid_export": grid_w if grid_w > 0 else 0,
                                "bat_charging": abs(pbat) if pbat < 0 else 0,
                                "bat_discharging": pbat if pbat > 0 else 0,
                                "bat_charge_total": get_f("batCharge"),
                                "bat_discharge_total": get_f("batDisCharge"),
                                "last_seen": sync_time # Full ISO timestamp for HA
                            }
                        }
            
            _LOGGER.debug("HYXi API Processed Data for: %s", list(results.keys()))
            return results

        except Exception as e:
            _LOGGER.error("HYXi API Error: %s", e)
            return None