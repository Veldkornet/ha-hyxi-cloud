import time
import hashlib
import hmac
import base64
import requests
import logging
from datetime import datetime, timezone

_LOGGER = logging.getLogger(__name__)

class HyxiApiClient:
    def __init__(self, access_key, secret_key, base_url):
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip('/')
        self.token = None
        self.token_expires_at = 0

    def _generate_headers(self, path, method, is_token_request=False):
        """Generates headers matching HYXi's official Java SDK implementation."""
        now_ms = int(time.time() * 1000)
        timestamp = str(now_ms)
        nonce = format(now_ms, "x")[-8:]
        
        # Mimicking Java's buildContent + deleteWhitespace
        content_str = "grantType:1" if is_token_request else ""
        hex_hash = hashlib.sha512(content_str.encode("utf-8")).hexdigest()
        
        string_to_sign = f"{path}\n{method.upper()}\n{hex_hash}\n"
        token_str = self.token if self.token else ""
        
        sign_string = f"{self.access_key}{token_str}{timestamp}{nonce}{string_to_sign}"
        hmac_bytes = hmac.new(self.secret_key.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha512).digest()
        signature = base64.b64encode(hmac_bytes).decode("utf-8")
        
        # EXACT Header casing from the Java document
        headers = {
            "accessKey": self.access_key,
            "timestamp": timestamp,
            "nonce": nonce,
            "sign": signature,
            "Content-Type": "application/json"
        }
        
        if is_token_request:
            headers["sign-headers"] = "grantType"
        elif token_str:
            headers["Authorization"] = token_str
            
        return headers

    def _refresh_token(self):
        if self.token and time.time() < self.token_expires_at:
            return True
            
        path = "/api/authorization/v1/token"
        
        try:
            r = requests.post(
                f"{self.base_url}{path}", 
                json={"grantType": 1}, 
                headers=self._generate_headers(path, "POST", is_token_request=True), 
                timeout=15
            )
            res = r.json()
            
            if not res.get("success"):
                _LOGGER.error("HYXi API Token Rejected: %s", res)
                return False
                
            token_val = res.get("data", {}).get("token") or res.get("data", {}).get("access_token")
            if token_val:
                self.token = f"Bearer {token_val}"
                self.token_expires_at = time.time() + 7000
                return True
        except Exception as e:
            _LOGGER.error("HYXi Token Request Failed: %s", e)
        return False

    def get_all_device_data(self):
        if not self._refresh_token():
            _LOGGER.error("HYXi API: Setup aborted due to token failure.")
            return None
            
        results = {}
        now = datetime.now(timezone.utc).isoformat()
        
        try:
            # 1. Get Plants
            p_path = "/api/plant/v1/page"
            r_p = requests.post(
                f"{self.base_url}{p_path}", 
                json={"pageSize": 10, "currentPage": 1}, 
                headers=self._generate_headers(p_path, "POST", is_token_request=False), 
                timeout=15
            )
            res_p = r_p.json()
            
            if not res_p.get("success"):
                _LOGGER.error("HYXi API Plant Fetch Rejected: %s", res_p)
                return None
                
            plants = res_p.get("data", {}).get("list", []) if isinstance(res_p.get("data"), dict) else []

            for p in plants:
                plant_id = p.get("plantId")
                if not plant_id: 
                    continue

                # 2. Get Devices
                d_path = "/api/plant/v1/devicePage"
                r_d = requests.post(
                    f"{self.base_url}{d_path}", 
                    json={"plantId": plant_id, "pageSize": 50, "currentPage": 1}, 
                    headers=self._generate_headers(d_path, "POST", is_token_request=False), 
                    timeout=15
                )
                res_d = r_d.json()
                
                if not res_d.get("success"):
                    _LOGGER.error("HYXi API Device Fetch Rejected: %s", res_d)
                    continue

                data_val = res_d.get("data")
                devices = data_val if isinstance(data_val, list) else data_val.get("deviceList", []) if isinstance(data_val, dict) else []

                for d in devices:
                    sn = d.get("deviceSn")
                    if not sn: 
                        continue
                    
                    is_inverter = d.get("deviceType") == "HYBRID_INVERTER"
                    
                    entry = {
                        "sn": sn,
                        "device_name": d.get("deviceName") or (f"Inverter {sn}" if is_inverter else f"Collector {sn}"),
                        "model": "Hybrid Inverter" if is_inverter else "Data Collector",
                        "sw_version": d.get("swVer"),
                        "hw_version": d.get("hwVer"),
                        "metrics": {"last_seen": now}
                    }

                    if is_inverter:
                        q_path = "/api/device/v1/queryDeviceData"
                        qr = requests.get(
                            f"{self.base_url}{q_path}?deviceSn={sn}", 
                            headers=self._generate_headers(q_path, "GET", is_token_request=False), 
                            timeout=15
                        )
                        
                        if qr.json().get("success"):
                            data_list = qr.json().get("data", [])
                            m_raw = {item.get("dataKey"): item.get("dataValue") for item in data_list if isinstance(item, dict) and item.get("dataKey")}
                            entry["metrics"].update(m_raw)
                            
                            def get_f(key, mult=1.0):
                                try: 
                                    return round(float(m_raw.get(key, 0)) * mult, 2)
                                except (ValueError, TypeError, AttributeError): 
                                    return 0.0

                            grid = get_f("gridP", 1000.0)
                            pbat = get_f("pbat")
                            
                            entry["metrics"].update({
                                "home_load": get_f("ph1Loadp") + get_f("ph2Loadp") + get_f("ph3Loadp"),
                                "grid_import": abs(grid) if grid < 0 else 0,
                                "grid_export": grid if grid > 0 else 0,
                                "bat_charging": abs(pbat) if pbat < 0 else 0,
                                "bat_discharging": pbat if pbat > 0 else 0,
                                "bat_charge_total": get_f("batCharge"),
                                "bat_discharge_total": get_f("batDisCharge"),
                            })
                        else:
                            _LOGGER.error("HYXi API Inverter Data Rejected: %s", qr.json())

                    results[sn] = entry
            return results
        except Exception as e:
            _LOGGER.error("HYXi Code Crash: %s", e, exc_info=True)
            return None