import base64
import hashlib
import hmac
import logging
import time
from datetime import UTC
from datetime import datetime

import aiohttp

_LOGGER = logging.getLogger(__name__)


class HyxiApiClient:
    def __init__(
        self, access_key, secret_key, base_url, session: aiohttp.ClientSession
    ):
        self.access_key = access_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.token = None
        self.token_expires_at = 0

    def _generate_headers(self, path, method, is_token_request=False):
        """Generates headers matching HYXi's official Java SDK implementation."""
        now_ms = int(time.time() * 1000)
        timestamp = str(now_ms)
        nonce = format(now_ms, "x")[-8:]

        content_str = "grantType:1" if is_token_request else ""
        hex_hash = hashlib.sha512(content_str.encode("utf-8")).hexdigest()

        string_to_sign = f"{path}\n{method.upper()}\n{hex_hash}\n"
        token_str = self.token if self.token else ""

        sign_string = f"{self.access_key}{token_str}{timestamp}{nonce}{string_to_sign}"
        hmac_bytes = hmac.new(
            self.secret_key.encode("utf-8"), sign_string.encode("utf-8"), hashlib.sha512
        ).digest()
        signature = base64.b64encode(hmac_bytes).decode("utf-8")

        headers = {
            "accessKey": self.access_key,
            "timestamp": timestamp,
            "nonce": nonce,
            "sign": signature,
            "Content-Type": "application/json",
        }

        if is_token_request:
            headers["sign-headers"] = "grantType"
        elif token_str:
            headers["Authorization"] = token_str

        return headers

    async def _refresh_token(self):
        """Async version of token refresh."""
        if self.token and time.time() < self.token_expires_at:
            return True

        path = "/api/authorization/v1/token"

        try:
            async with self.session.post(
                f"{self.base_url}{path}",
                json={"grantType": 1},
                headers=self._generate_headers(path, "POST", is_token_request=True),
                timeout=15,
            ) as response:
                # NEW: Catch Auth failures immediately
                if response.status in [401, 403]:
                    _LOGGER.error("HYXi API: Token request unauthorized (401/403)")
                    return "auth_failed"

                res = await response.json()

                if not res.get("success"):
                    _LOGGER.error("HYXi API Token Rejected: %s", res)
                    # Some APIs return 200 OK but success: false for bad keys
                    if res.get("code") in [401, 403, "401", "403"]:
                        return "auth_failed"
                    return False

                data = res.get("data", {})
                token_val = data.get("token") or data.get("access_token")
                if token_val:
                    self.token = f"Bearer {token_val}"
                    self.token_expires_at = time.time() + 7000
                    return True
        except Exception as e:
            _LOGGER.error("HYXi Token Request Failed: %s", e)
        return False

    async def get_all_device_data(self):
        """Fetches all plant and device data asynchronously."""

        token_status = await self._refresh_token()

        if token_status == "auth_failed":
            return "auth_failed"
        if not token_status:
            _LOGGER.error("HYXi API: Setup aborted due to token failure.")
            return None

        results = {}
        now = datetime.now(UTC).isoformat()

        try:
            # 1. Get Plants
            p_path = "/api/plant/v1/page"
            async with self.session.post(
                f"{self.base_url}{p_path}",
                json={"pageSize": 10, "currentPage": 1},
                headers=self._generate_headers(p_path, "POST"),
                timeout=15,
            ) as resp_p:
                res_p = await resp_p.json()

            if not res_p.get("success"):
                _LOGGER.error("HYXi API Plant Fetch Rejected: %s", res_p)
                return None

            data_p = res_p.get("data", {})
            plants = data_p.get("list", []) if isinstance(data_p, dict) else []

            for p in plants:
                plant_id = p.get("plantId")
                if not plant_id:
                    continue

                # 2. Get Devices
                d_path = "/api/plant/v1/devicePage"
                async with self.session.post(
                    f"{self.base_url}{d_path}",
                    json={"plantId": plant_id, "pageSize": 50, "currentPage": 1},
                    headers=self._generate_headers(d_path, "POST"),
                    timeout=15,
                ) as resp_d:
                    res_d = await resp_d.json()

                if not res_d.get("success"):
                    _LOGGER.error("HYXi API Device Fetch Rejected: %s", res_d)
                    continue

                data_val = res_d.get("data", {})
                devices = (
                    data_val
                    if isinstance(data_val, list)
                    else data_val.get("deviceList", [])
                    if isinstance(data_val, dict)
                    else []
                )

                for d in devices:
                    sn = d.get("deviceSn")
                    if not sn:
                        continue

                    # 1. Dynamically capture the true device type
                    dev_type = d.get("deviceType") or "UNKNOWN"

                    # Create a friendly fallback name for the model
                    friendly_name = dev_type.replace("_", " ").title()

                    # 2. Add 'device_type_code' to the entry so sensor.py can read it!
                    entry = {
                        "sn": sn,
                        "device_name": d.get("deviceName") or f"{friendly_name} {sn}",
                        "model": friendly_name,
                        "device_type_code": dev_type,
                        "sw_version": d.get("swVer"),
                        "hw_version": d.get("hwVer"),
                        "metrics": {"last_seen": now},
                    }

                    # 3. Fetch detailed metrics for EVERYTHING except the basic Collector stick
                    if dev_type != "COLLECTOR":
                        q_path = "/api/device/v1/queryDeviceData"
                        async with self.session.get(
                            f"{self.base_url}{q_path}?deviceSn={sn}",
                            headers=self._generate_headers(q_path, "GET"),
                            timeout=15,
                        ) as resp_q:
                            res_q = await resp_q.json()

                        if res_q.get("success"):
                            data_list = res_q.get("data", [])
                            m_raw = {
                                item.get("dataKey"): item.get("dataValue")
                                for item in data_list
                                if isinstance(item, dict) and item.get("dataKey")
                            }
                            entry["metrics"].update(m_raw)

                            # Helper function for safe math
                            def get_f(key, data_map, mult=1.0):
                                try:
                                    return round(float(data_map.get(key, 0)) * mult, 2)
                                except (ValueError, TypeError, AttributeError):
                                    return 0.0

                            # 4. Only do Hybrid math if the keys actually exist in the data
                            if "gridP" in m_raw or "pbat" in m_raw:
                                grid = get_f("gridP", m_raw, 1000.0)
                                pbat = get_f("pbat", m_raw)

                                entry["metrics"].update(
                                    {
                                        "home_load": get_f("ph1Loadp", m_raw)
                                        + get_f("ph2Loadp", m_raw)
                                        + get_f("ph3Loadp", m_raw),
                                        "grid_import": abs(grid) if grid < 0 else 0,
                                        "grid_export": grid if grid > 0 else 0,
                                        "bat_charging": abs(pbat) if pbat < 0 else 0,
                                        "bat_discharging": pbat if pbat > 0 else 0,
                                        "bat_charge_total": get_f("batCharge", m_raw),
                                        "bat_discharge_total": get_f(
                                            "batDisCharge", m_raw
                                        ),
                                    }
                                )
                        else:
                            _LOGGER.error(
                                f"HYXi API Data Rejected for {sn} ({dev_type}): {res_q}"
                            )

                    results[sn] = entry
            return results
        except Exception as e:
            _LOGGER.error("HYXi Async Code Crash: %s", e, exc_info=True)
            return None
