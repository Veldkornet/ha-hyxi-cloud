import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import UTC
from datetime import datetime

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 2  # Seconds to wait between retries (multiplied by attempt number)


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

        # 🚀 Generate a truly unique Nonce for concurrent requests
        nonce = os.urandom(4).hex()

        content_str = "grantType:1" if is_token_request else ""
        hex_hash = hashlib.sha512(content_str.encode("utf-8")).hexdigest()

        string_to_sign = f"{path}\n{method.upper()}\n{hex_hash}\n"

        # 🚀 Do not poison the signature with an expired token!
        if is_token_request:
            token_str = ""
        else:
            token_str = self.token if self.token else ""

        # Build the final string
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
                if response.status in [401, 403]:
                    _LOGGER.error("HYXi API: Token request unauthorized (401/403)")
                    return "auth_failed"

                res = await response.json()

                if not res.get("success"):
                    _LOGGER.error("HYXi API Token Rejected: %s", res)
                    if res.get("code") in [401, 403, "401", "403"]:
                        return "auth_failed"
                    return False

                data = res.get("data", {})
                token_val = data.get("token") or data.get("access_token")
                if token_val:
                    self.token = f"Bearer {token_val}"
                    self.token_expires_at = time.time() + 6600
                    return True
        except Exception as e:
            _LOGGER.error("HYXi Token Request Failed: %s", e)
        return False

    async def _fetch_device_metrics(self, sn, entry):
        """Helper to fetch detailed metrics for a single device."""
        q_path = "/api/device/v1/queryDeviceData"
        try:
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
                _LOGGER.debug(
                    "HYXi Raw Metrics for %s (%s): %s",
                    sn,
                    entry.get("device_type_code"),
                    m_raw,
                )
                entry["metrics"].update(m_raw)

                def get_f(key, data_map, mult=1.0):
                    try:
                        val = data_map.get(key)
                        if val is None or val == "":
                            return 0.0
                        return round(float(val) * mult, 2)
                    except (ValueError, TypeError):
                        return 0.0

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
                            "bat_discharge_total": get_f("batDisCharge", m_raw),
                        }
                    )
            else:
                _LOGGER.warning(
                    "HYXi API metrics rejected for %s: %s", sn, res_q.get("message")
                )
        except Exception as e:
            _LOGGER.error("Error fetching metrics for %s: %s", sn, e)

    async def _fetch_device_info(self, sn, entry):
        """Helper to fetch static device info (firmware, capacity, limits)."""
        i_path = "/api/device/v1/queryDeviceInfo"
        try:
            async with self.session.get(
                f"{self.base_url}{i_path}?deviceSn={sn}",
                headers=self._generate_headers(i_path, "GET"),
                timeout=15,
            ) as resp_i:
                res_i = await resp_i.json()

            if res_i.get("success"):
                data_list = res_i.get("data", [])
                i_raw = {
                    item.get("dataKey"): item.get("dataValue")
                    for item in data_list
                    if isinstance(item, dict) and item.get("dataKey")
                }

                # 👇 This will dump the EXACT info the cloud sends back
                _LOGGER.debug("HYXi Raw INFO for %s: %s", sn, i_raw)

                # Smart Firmware Finder
                sw_ver = (
                    i_raw.get("swVerSys")
                    or i_raw.get("swVerMaster")
                    or i_raw.get("swVer")
                )
                if sw_ver:
                    entry["sw_version"] = sw_ver

                # Merge static info into metrics
                entry["metrics"].update(
                    {
                        "signalIntensity": i_raw.get("signalIntensity"),
                        "signalVal": i_raw.get("signalVal"),
                        "wifiVer": i_raw.get("wifiVer"),
                        "comMode": i_raw.get("comMode"),
                        "batCap": i_raw.get("batCap"),
                        "maxChargePower": i_raw.get("maxChargePower")
                        or i_raw.get("maxChargingDischargingPower"),
                        "maxDischargePower": i_raw.get("maxDischargePower")
                        or i_raw.get("maxChargingDischargingPower"),
                    }
                )
            else:
                _LOGGER.warning(
                    "HYXi INFO API Rejected for %s: %s", sn, res_i.get("message")
                )

        except Exception as e:
            _LOGGER.error("Error fetching device info for %s: %s", sn, e)

    async def _fetch_all_for_device(self, sn, entry, dev_type):
        """Fires off concurrent requests for Data and Info, merging the results."""
        tasks = [self._fetch_device_info(sn, entry)]

        # Only fetch metrics for devices that actually generate live power data
        if dev_type != "COLLECTOR":
            tasks.append(self._fetch_device_metrics(sn, entry))

        await asyncio.gather(*tasks)
        return sn, entry

    async def get_all_device_data(self):
        """Fetches data with built-in retry logic and returns attempt count."""

        # Exponential Backoff Loop
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                data = await self._execute_fetch_all()
                if data:
                    # ✅ Success: Return a package containing data AND the attempt number
                    return {"data": data, "attempts": attempt}
                return None
            except (aiohttp.ClientError, TimeoutError) as err:
                if attempt < MAX_RETRIES:
                    wait_time = attempt * RETRY_DELAY
                    _LOGGER.warning(
                        "HYXi Connection attempt %s/%s failed. Retrying in %ss... (Error: %s)",
                        attempt,
                        MAX_RETRIES,
                        wait_time,
                        err,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    _LOGGER.error(
                        "HYXi Cloud connection failed after %s attempts: %s",
                        MAX_RETRIES,
                        err,
                    )
            except Exception:
                _LOGGER.exception("HYXi Unexpected Code Crash:")
                break  # Don't retry code bugs

        return None

    async def _execute_fetch_all(self):
        """The actual fetching logic moved to a private method for the retry loop."""

        # 🧪 MOCK OVERRIDE START
        mock_file = os.path.join(os.path.dirname(__file__), "mock_data.json")

        # Helper function to read the file synchronously
        def load_mock():
            if os.path.exists(mock_file):
                with open(mock_file, encoding="utf-8") as f:
                    return json.load(f)
            return "NOT_FOUND"

        try:
            mock_data = await asyncio.to_thread(load_mock)
            if mock_data != "NOT_FOUND":
                _LOGGER.warning(
                    "HYXi API 🧪: MOCK MODE ACTIVE - Successfully loaded %s", mock_file
                )
                return mock_data
        except json.JSONDecodeError as e:
            _LOGGER.error(
                "HYXi API 🧪: MOCK FILE FOUND, BUT JSON IS INVALID! Error: %s", e
            )
            return None
        except Exception as e:
            _LOGGER.error("HYXi API 🧪: Unexpected error reading mock file: %s", e)
            return None
        # 🧪 MOCK OVERRIDE END

        token_status = await self._refresh_token()

        if token_status == "auth_failed":
            return "auth_failed"
        if not token_status:
            return None

        results = {}
        now = datetime.now(UTC).isoformat()

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
        metric_tasks = []

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

                dev_type = d.get("deviceType") or "UNKNOWN"
                friendly_name = dev_type.replace("_", " ").title()

                entry = {
                    "sn": sn,
                    "device_name": d.get("deviceName") or f"{friendly_name} {sn}",
                    "model": friendly_name,
                    "device_type_code": dev_type,
                    "sw_version": d.get("swVer"),
                    "hw_version": d.get("hwVer"),
                    "metrics": {"last_seen": now},
                }

                metric_tasks.append(self._fetch_all_for_device(sn, entry, dev_type))

        # 3. Concurrent Metrics
        if metric_tasks:
            updated_entries = await asyncio.gather(*metric_tasks)
            for sn, entry in updated_entries:
                if sn:
                    results[sn] = entry

        return results
