# HYXi Cloud
![HYXi Cloud Logo](https://raw.githubusercontent.com/Veldkornet/ha-hyxi-cloud/main/custom_components/hyxi_cloud/brand/logo.png)

### [HYXiPower](https://www.hyxipower.com/) Cloud for Home Assistant
**Monitor your solar production, battery state-of-charge, and grid flow in real-time.**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=flat-square)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/Veldkornet/ha-hyxi-cloud?style=flat-square&color=blue)](https://github.com/Veldkornet/ha-hyxi-cloud/releases)
[![License](https://img.shields.io/github/license/Veldkornet/ha-hyxi-cloud?style=flat-square&color=lightgrey)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![GitHub Issues](https://img.shields.io/github/issues/Veldkornet/ha-hyxi-cloud?style=flat-square&color=red)](https://github.com/Veldkornet/ha-hyxi-cloud/issues)

[![CodeQL](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/codeql.yml/badge.svg)](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/codeql.yml)
[![HomeAssistant](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/validate.yml/badge.svg)](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/validate.yml)
[![Gitleaks](https://img.shields.io/badge/protected%20by-gitleaks-blue?style=flat-square)](https://github.com/gitleaks/gitleaks-action)
[![Security: Harden-Runner](https://img.shields.io/badge/Security-Harden--Runner-green?style=flat-square)](https://github.com/Veldkornet/ha-hyxi-cloud/actions)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-blue?style=flat-square&logo=dependabot)](https://github.com/Veldkornet/ha-hyxi-cloud/network/updates)

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-FFDD00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/veldkornet)

---

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

> [!TIP]
> **Dynamic Discovery:** This integration uses a proactive discovery model. Even if your device is listed as "Untested," it will automatically populate with at least basic diagnostic entities and known mapped entities. Full sensor mapping is applied once the device type is confirmed.

### 📡 Detailed Entity Support

| Device Type | Status | Key Entities Provided |
| :--- | :--- | :--- |
| **Hybrid & All-in-One** | ✅ Tested | **PV:** Power, Voltage (String 1/2), Current, Daily/Total Yield <br> **Battery:** SOC, Power (Charge/Discharge), Voltage, Current, SOH, Temp <br> **Grid:** Import/Export Power, Load Power, Voltage, Frequency <br> **System:** Internal Temp, Running State, Fault Codes |
| **Data Collector** | ✅ Tested | **Diagnostics:** Signal Intensity (RSSI), Heartbeat, Heartbeat Interval, Last Seen |
| **String Inverter** | ⚠️ Untested | **PV:** Power, String Volts/Amps <br> **AC:** Output Power, Daily/Total Yield, Bus Voltage, Temperature |
| **Micro Inverter** | ⚠️ Untested | **Module:** DC Input Power, AC Voltage, Frequency, Daily Energy, Internal Temp |
| **Smart Meter** | ⚠️ Untested | **Grid:** Active/Reactive Power, Voltage, Export Energy, Import Energy |

> [!IMPORTANT]
> ### 🤝 Call for Testers
> you own a **String Inverter, Micro Inverter, Standalone Batteryor Multiple Batteries**? Your data can help us move these to **✅ Tested**!
> 
> 1. Enable **Debug Logging** in Home Assistant for this integration.
> 2. Open a [GitHub Issue](https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Supported-Devices#-support-for-new-devices) and provide a sanitized (remove your ID/Serial) snippet of the API response.
> 3. We will verify the sensor mappings and update the integration!

### 🛡️ Reliability & Diagnostics

This integration includes a specialized diagnostic system to help you distinguish between local hardware issues and cloud service outages.

| Sensor | Purpose | Behavior |
| :--- | :--- | :--- |
| **Cloud Status** | Binary connectivity sensor. | Turns `Off` if the API is unreachable or authentication fails. |
| **Connection Quality** | API Stability tracking. | Reports "Excellent" or "Degraded" based on API retry attempts. |
| **Data Freshness** | Sync latency tracking. | Compares hardware `collectTime` to current time to detect "Stale" data. |
| **Integration Last Updated** | Local Sync timestamp. | The exact time Home Assistant last successfully processed a cloud update. |

### 🔋 Virtual Battery Management
For systems with multiple physical battery units, this integration automatically creates a **Virtual System Battery**. 

* **Aggregated View:** Combines SOC, SOH, and Power across all units into a single "System" entity for easy dashboarding.
* **Balanced Metrics:** Uses weighted averages for State of Charge (SOC) to ensure your "Full" or "Empty" readings are accurate across the entire bank.
* **Individual Monitoring:** You can still access individual battery telemetry for cell-level health checks.

## ⚙️ Setup & Configuration

1. Ensure you have a developer account and have created an **application** to obtain an **Access Key** and **Secret Key** from the [HYXiPOWER Developer Platform](https://open.hyxicloud.com/#/quickStart).
   
   > **Important:** 
   > Use the same email address that your devices are registered to in the HYXi app.
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
4. Open the file, replace any sensitive information (like your real serial numbers if you prefer), and attach it to your GitHub issue! _Note: If you sanitize the data, please keep it consistent as the serial number can be used to show how devices link to each other!_

## Disclaimer
This is a custom integration and is **not** an official product of HYXi Power. 

## Support
If you find this integration helpful and want to support its development:

[![Buy Me a Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=&slug=veldkornet&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff)](https://www.buymeacoffee.com/veldkornet)

---
