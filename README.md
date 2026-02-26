# HYXi Cloud Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
![GitHub release (latest by date)](https://img.shields.io/github/v/release/Veldkornet/ha-hyxi-cloud)
[![License](https://img.shields.io/github/license/Veldkornet/ha-hyxi-cloud?style=flat-square)](https://github.com/Veldkornet/ha-hyxi-cloud/blob/main/LICENSE)
[![Open Issues](https://img.shields.io/github/issues/Veldkornet/ha-hyxi-cloud?style=flat-square)](https://github.com/Veldkornet/ha-hyxi-cloud/issues)
![Dependabot](https://img.shields.io/badge/dependabot-enabled-blue?logo=dependabot&logoColor=white)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Validate](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/validate.yml/badge.svg)](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/validate.yml)
![Lint Status](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/lint.yml/badge.svg)
[![CodeQL](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/codeql.yml/badge.svg)](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/codeql.yml)

[![Buy Me a Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/veldkornet)

A Home Assistant integration to monitor [HYXiPower](https://www.hyxipower.com/) Inverters and Energy Storage Systems via the HYXi Cloud API. This integration provides near real-time data for solar production, battery status, and home energy usage.

## ✨ Features

- **⚡ Energy Dashboard Ready:** Native support for Home Assistant's built-in Energy Dashboard. Track daily solar yield, grid dependency, and battery cycles.
- **🛡️ Smart Caching:** If the cloud goes offline, sensors retain their last known values instead of becoming "Unavailable," ensuring your dashboards stay readable.
- **📊 Advanced Diagnostics:** Track cloud connectivity, API success rates, and data sync latency with dedicated diagnostic sensors.
- **🕥 Adjustable Polling:** Fine-tune your data refresh rate between 1 and 60 minutes via the integration options.
- **🔋 Virtual Battery System:** Automatically aggregates multiple physical battery units into a single system view.
- **Clean UI:** Precision-tuned data with multi-language support (English, French, German, Dutch).

## 📥 Installation

[![Open your Home Assistant instance and open the repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Veldkornet&repository=ha-hyxi-cloud&category=Integration)

### Method 1: HACS (Recommended)
1. Open **HACS** in Home Assistant.
2. Go to **Integrations** > **Custom repositories** (three dots menu).
3. Paste: `https://github.com/Veldkornet/ha-hyxi-cloud`
4. Select **Integration** and click **Add**.
5. Restart Home Assistant.

### Method 2: Manual
1. Copy the `hyxi_cloud` folder to your `/config/custom_components/` directory.
2. Restart Home Assistant.

## 🔌 Supported Devices

The integration dynamically adapts to your hardware based on the device types reported by the HYXi Cloud and creates sensors based on what your specific hardware reports to the cloud. If you are missing sensors / devices, please see [New Devices](https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Supported-Devices#-support-for-new-devices)

| Device Type | HYXi API Code | Status | Supported Sensors |
| :--- | :--- | :--- | :--- |
| **Hybrid Inverter** | `HYBRID_INVERTER`, `ALL_IN_ONE` | ✅ **Tested** | Solar, Battery, Grid, Diagnostics |
| **Data Collector** | `COLLECTOR`, `DMU` | ✅ **Tested** | Heartbeat (`last_seen`) |
| **String Inverter** | `STRING_INVERTER` | ⚠️ *Untested* | Solar, Diagnostics |
| **Micro Inverter** | `MICRO_INVERTER` | ⚠️ *Untested* | Solar, Diagnostics |
| **Standalone Battery** | `ENERGY_STORAGE_BATTERY`, `AC_BATTERY`, `EMS` | ⚠️ *Untested* | Battery SOC, Power, Health |
| **Smart Meter** | `METER` | ⚠️ *Untested* | Grid Import/Export, Home Load |

## 🛡️ Reliability & Diagnostics

This integration includes a specialized diagnostic system to help you distinguish between local hardware issues and cloud service outages.

| Sensor | Purpose | Behavior |
| :--- | :--- | :--- |
| **Cloud Status** | Binary connectivity sensor. | Turns `Off` if the API is unreachable or authentication fails. |
| **Connection Quality** | API Stability tracking. | Reports "Excellent" or "Degraded" based on API retry attempts. |
| **Data Freshness** | Sync latency tracking. | Compares hardware `collectTime` to current time to detect "Stale" data. |
| **Integration Last Updated** | Local Sync timestamp. | The exact time Home Assistant last successfully processed a cloud update. |

### Smart Caching Logic
When the **Cloud Status** becomes `Offline`, the integration will:
1. Keep your sensor values at their **last known state** (preventing "Unavailable" icons).
2. Log the connection attempt and use **exponential backoff** to retry.
3. Automatically resume updates once the cloud is back online.

## 📊 Supported Sensors

### Core Monitoring
| Category | Sensor Name | ID (Key) | Unit |
| :--- | :--- | :--- | :--- |
| **Solar** | Solar Power | `ppv` | W |
| **Solar** | Total Energy Yield | `totale` | kWh |
| **Battery** | State of Charge | `batsoc` | % |
| **Battery** | State of Health | `batsoh` | % |
| **Battery** | Battery Power | `pbat` | W |
| **Battery** | Battery Charging | `bat_charging` | W |
| **Battery** | Battery Discharging | `bat_discharging` | W |
| **Grid/Load** | Home Load | `home_load` | W |
| **Grid/Load** | Grid Import | `grid_import` | W |
| **Grid/Load** | Grid Export | `grid_export` | W |

### Virtual Battery System (Aggregated)
*Enabled via Options if 2+ batteries are detected.*
| Sensor Name | ID (Key) | Unit |
| :--- | :--- | :--- |
| System Battery SoC Average | `battery_system_avg_soc` | % |
| System Battery Power | `battery_system_total_pbat` | W |
| System Battery Charging | `battery_system_bat_charging` | W |
| System Battery Discharging | `battery_system_bat_discharging` | W |
| System Total Battery Charge | `battery_system_bat_charge_total` | kWh |
| System Total Battery Discharge | `battery_system_bat_discharge_total` | kWh |

## ⚙️ Setup & Configuration

1. Ensure you have a developer account and have created an **application** to obtain an **Access Key** and **Secret Key** from the [HYXiPOWER Developer Platform](https://open.hyxicloud.com/#/quickStart).
   > **Important:** Use the same email address that your devices are registered to in the HYXi app.
2. Go to **Settings > Devices & Services** > **Add Integration** > **HYXi Cloud**.
Or alternatively, add the integration with the following:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hyxi_cloud)

## Configuration

1. Enter your **Access Key** and **Secret Key** from the HYXi Open API portal.

### Optional Features (Options Flow)
Click the **Configure** button on the HYXi integration card to access:
* **Polling Interval:** Adjust frequency between 1–60 minutes (Default: 5).
* **Enable Aggregated Virtual Battery:** Combine data from 2+ batteries into single "System" sensors.

## 🐛 Troubleshooting

If you are opening a bug report, please include **Debug Logs**:
**How to enable and download debug logs:**
1. Go to **Settings > Devices & Services** > **HYXi Cloud**.
2. Click the three dots (⋮) and select **Enable debug logging**.
3. Wait 5-10 minutes, then click **Disable debug logging** to download the file.
4. Open the file, remove any sensitive information (like your real serial numbers if you prefer), and attach it to your GitHub issue!

## Disclaimer
This is a custom integration and is **not** an official product of HYXi Power. 

## Support
If you find this integration helpful and want to support its development:
[![Buy Me a Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=&slug=veldkornet&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff)](https://www.buymeacoffee.com/veldkornet)

---
