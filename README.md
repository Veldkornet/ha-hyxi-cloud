# HYXI
![HYXI Logo](https://raw.githubusercontent.com/Veldkornet/ha-hyxi-cloud/main/custom_components/hyxi_cloud/brand/logo.png)

### [HYXIPower](https://www.hyxipower.com/) Cloud Integration for Home Assistant
**Monitor your solar production, battery state-of-charge, and grid flow in real-time.**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/Veldkornet/ha-hyxi-cloud?style=flat-square&color=blue)](https://github.com/Veldkornet/ha-hyxi-cloud/releases)
[![License](https://img.shields.io/github/license/Veldkornet/ha-hyxi-cloud?style=flat-square&color=lightgrey)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json&style=flat-square)](https://github.com/astral-sh/ruff)
[![GitHub Issues](https://img.shields.io/github/issues/Veldkornet/ha-hyxi-cloud?style=flat-square&color=blue)](https://github.com/Veldkornet/ha-hyxi-cloud/issues)

[![CodeQL](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/codeql.yml/badge.svg)](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/codeql.yml)
[![HomeAssistant](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/validate.yml/badge.svg)](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/validate.yml)
[![Gitleaks](https://img.shields.io/badge/protected%20by-gitleaks-blue?style=flat-square)](https://github.com/gitleaks/gitleaks-action)
[![Security: Harden-Runner](https://img.shields.io/badge/Security-Harden--Runner-green?style=flat-square)](https://github.com/Veldkornet/ha-hyxi-cloud/actions)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-blue?style=flat-square&logo=dependabot)](https://github.com/Veldkornet/ha-hyxi-cloud/network/updates)
[![OpenSSF Baseline](https://www.bestpractices.dev/projects/12051/baseline)](https://www.bestpractices.dev/projects/12051)

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-FFDD00?style=flat-square&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/veldkornet)

---

## ✨ Features

- **⚡ Energy Dashboard Ready:** Native support for Home Assistant's built-in Energy Dashboard. Track daily solar yield, grid dependency, and battery cycles.
- **🔄 Real-Time Push Subscriptions:** Optional support for receiving instantaneous updates for telemetry data and active alarms directly from HYXI Cloud via Home Assistant's webhook endpoints, bypassing polling delays.
- **🔧 Device Control:** Send supported HYXI Cloud control commands from Home Assistant, including inverter mode buttons, peak shaving buttons, frequency control, and microinverter power controls.
- **📊 Advanced Diagnostics:** Track cloud connectivity, API success rates, and data sync latency with dedicated diagnostic sensors.
- **🕥 Adjustable Polling:** Fine-tune your data refresh rate between 1 and 60 minutes via the integration options.
- **🛡️ Reliable Quality Assurance:** Built with **99%+ automated test coverage** and robust numeric safety nets to ensure your energy data is accurate and resilient.
- **🧼 Clean UI:** Precision-tuned data with support for **20+ languages** (English, German, French, Dutch, Afrikaans, Portuguese, Spanish, Italian, and more).

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
| **Hybrid & All-in-One** | ✅ Tested | **PV:** Power, Voltage (String 1/2), Current, Daily/Total Yield <br> **Battery:** SOC, Power (Charge/Discharge), Voltage, Current, SOH, **Capacity (kWh)**, **Max Chg/Disch Power**, Temp <br> **Grid:** Import/Export Power, Load Power, Voltage, Frequency, **Phase(1/2/3) Volts/Amps/Power**, **Bus Voltage** <br> **System:** Internal Temp, Running State, Fault Codes |
| **Data Collector** | ✅ Tested | **Diagnostics:** Signal Intensity (RSSI/%), Heartbeat, Heartbeat Interval, Last Seen, **WiFi Version**, **Comm Mode**, **App Version** |
| **String Inverter** | ⚠️ Untested | **PV:** Power, String Volts/Amps <br> **AC:** Output Power, Daily/Total Yield, Bus Voltage, Temperature |
| **Micro Inverter** | ⚠️ Untested | **Module:** DC Input Power, AC Voltage, Frequency, Daily Energy, Internal Temp |
| **Smart Meter** | ⚠️ Untested | **Grid:** Active/Reactive Power, Voltage, Export Energy, Import Energy |

> [!IMPORTANT]
> ### 🤝 Call for Testers
> Do you own a **String Inverter, Micro Inverter, Standalone Battery or Multiple Batteries**? Your data can help us move these to **✅ Tested**!
> Specifically multiple batteries behind an inverter would be a great addition to confirm working!
>
> 1. Enable **Debug Logging** in Home Assistant for this integration.
> 2. Open a [GitHub Issue](https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Supported-Devices#-support-for-new-devices) and attach a snippet of the debug log output.
> 3. We will verify the sensor mappings and update the integration!

### 🔧 Device Control

> [!IMPORTANT]
> **Device Controls are Opt-In:** To prevent Home Assistant from interfering with external device management or grid curtailment schedules by default, all control entities (such as Operating Mode buttons, power sliders, and switches) are hidden initially.
>
> **How to enable them:**
> 1. Go to **Settings > Devices & Services** > **HYXI Cloud**.
> 2. Click the **Configure** button on the integration card.
> 3. Check the box for **"Enable Device Control & Protection"** and save. The integration will reload and all control entities will appear.
>
> **VPP Lockout Safety:** If a Virtual Power Plant (VPP) dispatch is active (i.e. your energy provider is actively controlling your device), controls will be automatically disabled/locked out in Home Assistant to prevent command conflicts.
> - *Bypassing the Lockout:* You can bypass this lockout by checking the **"Override VPP Lock (force-enable controls during VPP)"** option in the Configure card. **Warning:** Enabling this could interfere with grid management commands sent by your energy provider.


This integration supports writing control commands to compatible inverters via the HYXI Cloud API.

Controls are scoped per device type — each device only gets the controls it supports:

#### Hybrid Inverter / All-in-One

The HYXI API defines controls by **phase type** (Single Phase / Three-Phase). The integration auto-detects phase type and exposes matching controls:

| Phase | Controls | controlId |
| :--- | :--- | :--- |
| **Three-Phase** | Operating Mode buttons (Mode: Idle / Mode: Charge / Mode: Discharge / Mode: Self-Consumption) | 1062–1065 |
| **Three-Phase** | Target Charge / Discharge Power | — |
| **Single Phase** | Peak Shaving buttons (Peak Shaving: Turn Off / Peak Shaving: Force Charge / Peak Shaving: Force Discharge / Peak Shaving: Stop / Peak Shaving: Hold) | 1021 |
| **Single Phase** | Frequency Control (Enable / Disable) | 1020 |

**Phase Detection** — determined in priority order:

1. **Model name suffix:** `-HT` / `-HTA` / `-ET` → Three-Phase, `-HS` / `-LS` → Single Phase
2. **Runtime metrics:** Phase power keys (`ph2Loadp` / `ph3Loadp` / `ph2p` / `ph3p`) or non-zero phase 2/3 voltage (`ph2v` / `ph3v`) → Three-Phase

> [!IMPORTANT]
> If the phase type cannot be determined from either the model name or runtime metrics, **no control entities are created**. This is a safety measure to prevent sending unsupported commands to your inverter. If you believe your device should have controls, please open a [GitHub Issue](https://github.com/Veldkornet/ha-hyxi-cloud/issues) with your device model and we will add support.

#### Energy Manager Standalone (Beta)

The Energy Manager Standalone is an automated battery control engine that runs a 15-second decision loop inside Home Assistant. It reads your P1 smart meter, solar production, battery SOC, and optional solar forecast to automatically manage your inverter's operating mode (charge, discharge, self-consume, idle).

> [!NOTE]
> This is the **Standalone** energy manager — it makes all decisions locally based on real-time sensor data and configurable rules. A future **Day Ahead** energy manager (optimizing against dynamic energy prices) is planned for a separate release.

**Important:** The Energy Manager builds on top of the existing Battery Protection — it does **not** replace it. SOC minimum/maximum limits are read from the existing protection number entities and are always respected.

##### Prerequisites

- **Enable Device Control & Protection** must be turned on first (Options → Configure)
- A **P1 smart meter** entity (power sensor) configured in Home Assistant

##### Enabling

1. Go to **Settings > Devices & Services** > **HYXI Cloud** > **Configure**.
2. Enable **Device Control & Protection** and save.
3. Re-open **Configure** — the **Enable Energy Manager Standalone (Beta)** toggle is now visible.
4. Enable it and configure:
   - **P1 Smart Meter** — your grid power sensor (required, e.g. `sensor.p1_meter_power`)
   - **Solar Forecast Remaining Today** — remaining solar energy for today in kWh (optional)
   - **Solar Forecast Current Power** — current predicted solar power in W (optional)
   - **Inverter to Control** — which inverter the engine manages
   - **Override Battery Capacity** — check this to manually set battery capacity (see below)
   - **Battery Capacity (Wh)** — manual override value (only used when override is checked)

The Energy Manager is **disabled by default** even after enabling it in options. You must also turn on the **Energy Manager** switch entity. Each sub-feature (Night Mode, High Load Assist) must be individually enabled via its own switch entity.

##### Solar Forecast Integration

The engine can use solar forecast data to make smarter decisions about battery preservation and charge timing. Two optional forecast entities can be configured:

- **Solar Forecast Remaining Today (kWh):** Used for night preservation decisions — the engine checks whether remaining solar can recharge the battery to the night target before sunset. Compatible with:
  - [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) — use the `energy_production_today_remaining` sensor
  - [Solcast](https://github.com/BJReplay/ha-solcast-solar) — use the `forecast_remaining_today` sensor
  - Any sensor providing remaining solar energy for today in kWh

- **Solar Forecast Current Power (W):** Currently reserved for future use. Compatible with:
  - [Forecast.Solar](https://www.home-assistant.io/integrations/forecast_solar/) — use the `power_production_now` sensor
  - [Solcast](https://github.com/BJReplay/ha-solcast-solar) — use the `forecast_this_hour` sensor

If no forecast entities are configured, the engine estimates solar availability from current production and time to sunset.

##### Battery Capacity

The engine needs to know your battery's total capacity (in Wh) for SOC calculations, night reserve estimation, and high-load cost analysis.

**How it's determined (in priority order):**

1. **Manual override** — If you check *Override Battery Capacity* in the energy manager options and set a value, that value is always used.
2. **API auto-detection** — The `batCap` metric from your inverter (reported in kWh, converted to Wh). Most hybrid inverters report this automatically.
3. **Fallback** — 2000 Wh if neither of the above is available.

> [!TIP]
> If your inverter reports `batCap` correctly (visible as the "Battery Capacity" sensor on your inverter device), you don't need to configure anything. The override is for situations where the API value is missing, incorrect, or you have modified your battery setup.

##### How It Works — Decision Priorities

Every 15 seconds the engine evaluates these priorities in order. The first matching priority wins:

| Priority | Condition | Action | Details |
| :--- | :--- | :--- | :--- |
| **1. Emergency Low SOC** | SOC < SOC Minimum | Charge from solar or grid | If solar is producing, charges from solar. If no solar and *Grid Charge Allowed* is on, charges from grid at up to 2000W. Otherwise goes idle to prevent further drain. |
| **2. Over-Max SOC** | SOC > SOC Maximum | Forced discharge | Discharges at the higher of current grid import or 1000W, capped at max discharge power. Prevents overcharging. |
| **2b. Export Limiting** | Grid export > max limit | Charge or curtail PV | Single-phase only. Uses peak shaving control. See details below. |
| **3. High Load Assist** | Home load > threshold | Battery assist or grid-only | Only active when the *High Load Battery Assist* switch is ON. See details below. |
| **4. Night Mode** | Nighttime (sun below horizon) | Self-consume or idle | Only active when the *Night Mode* switch is ON. See details below. |
| **5. Solar Optimization** | Solar producing + SOC < max | Smart charge from solar | Waits for sustained grid export before entering charge mode. Continuously tunes charge power to minimize grid import/export. |
| **Default** | None of the above | Self-consume | Safe fallback. If currently charging or discharging, switches to self-consume. |

##### Night Mode (Priority 4)

**Requires:** *Night Mode* switch entity → ON (default: OFF)

Night Mode manages battery usage during nighttime and preserves battery for overnight consumption:

- **At night (sun below horizon, no solar):**
  - If SOC is above SOC Minimum → **self-consume** (battery powers the house)
  - If SOC is at or below SOC Minimum → **idle** (stop discharging, protect the reserve)

- **During daytime — night preservation:**
  - If SOC has dropped to or below the calculated *night SOC target* and the house is importing from grid and solar forecast cannot cover the gap → **idle** (preserve remaining battery for tonight)

The **night SOC target** is automatically calculated based on:
- `Average Night Consumption` (W) — configurable, also auto-updated hourly from real P1 data between 21:00–06:00
- `Night Buffer %` — extra safety margin (default 5%)
- Battery capacity (from options or API)
- Hours until sunrise

Formula: `night_target = soc_min + ((avg_consumption × hours_remaining × (1 + buffer%)) / capacity) × 100`

**Example:** With 400W average consumption, 14.8 kWh battery, 5% buffer, 20% SOC minimum, 12 hours until sunrise:
Night target ≈ 20% + 34% = **54%**. The engine will preserve battery above 54% during daytime if it calculates that solar won't be enough to recharge before sunset.

##### High Load Assist (Priority 3)

**Requires:** *High Load Battery Assist* switch entity → ON (default: OFF)

High Load Assist detects when your home consumption exceeds a configurable threshold and decides whether the battery should help:

- **Home load > High Load Threshold** and assist is enabled:
  - Calculates the SOC cost of running battery assist for 30 minutes at 50% max discharge power
  - If the battery can afford it (remaining SOC after assist would still exceed the night SOC target) → **self-consume** (battery helps power the high load)
  - If the battery cannot afford it (would drain below night target) → **idle** (let the grid handle it, preserve battery for night)

- **Home load below threshold:** No action, falls through to next priority.

**Use case:** Running an oven, EV charger, or heat pump. The engine prevents the battery from draining itself to cover a temporary spike that would leave you with insufficient reserve for the night.

##### Export Limiting (Priority 2b)

**Requires:** *Export Limiting* switch entity → ON (default: OFF)

**Single-phase devices only** — uses peak shaving control (controlId 1021) which is not available on three-phase inverters.

Export Limiting caps how much power is fed back to the grid:

- **Grid export > Max Grid Export** and battery has room (SOC < SOC Maximum):
  - Charges battery to absorb excess (minimum 300W, capped at max charge power)
  - Continuously adjusts charge power as export fluctuates

- **Grid export > Max Grid Export** and battery is full:
  - Sends peak shaving `stop` to curtail PV production entirely
  - 30-second cooldown between stop/hold toggles prevents oscillation

- **Grid export drops below limit:**
  - Sends peak shaving `hold` to resume PV production
  - Returns to self-consume

**Use case:** Feed-in tariff limits, grid connection limits, or reducing grid export to maximize self-consumption.

##### Solar Charge Logic (Priority 5)

When solar is producing and the battery isn't full, the engine optimizes charging:

1. **Entry gate:** Solar must exceed `Min Solar for Charge` (default: 1000W).
2. **Export confirmation:** Grid export must exceed `Charge Entry Threshold` (default: 500W) for several consecutive readings before entering charge mode. This prevents charge/discharge oscillation on cloudy days.
3. **Power tuning:** Once charging, the engine continuously adjusts charge power to keep P1 close to zero (not importing, not exporting).
4. **Bottomout exit:** If charge power drops to minimum (100W) for 3 consecutive ticks due to insufficient solar, exits back to self-consume.
5. **Sunset urgency:** Within 4 hours of sunset, if SOC is below the night target and solar forecast won't cover it, entry thresholds are relaxed to capture remaining solar.

##### Grid Charge Allowed

The *Grid Charge Allowed* switch (on the inverter device, not the Energy Manager device) controls whether the engine may charge the battery from the grid during emergencies. This is only used when SOC drops below minimum and there is no solar available. Default: OFF.

##### Entity Reference

All Energy Manager entities appear on a virtual **Energy Manager** device linked to your inverter.

**Switches (all default OFF):**

| Entity | Purpose |
| :--- | :--- |
| Energy Manager | Master on/off for the decision loop |
| Night Mode | Enable night self-consume and battery preservation |
| High Load Battery Assist | Enable battery assist during high home loads |
| Export Limiting | Cap grid export and charge battery with excess (single-phase only) |
| Grid Charge Allowed | Allow grid charging in low-SOC emergencies (on inverter device) |

**Number parameters:**

| Entity | Unit | Default | Range | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| High Load Threshold | W | 6500 | 1000–20000 | Home load above this triggers high-load logic |
| Max Charge Power | W | 5000 | 500–15000 | Maximum charge power sent to inverter |
| Max Discharge Power | W | 5000 | 500–15000 | Maximum discharge power |
| Min Solar for Charge | W | 1000 | 200–3000 | Solar must exceed this to consider charging |
| Mode Switch Cooldown | s | 60 | 10–300 | Minimum seconds between mode changes |
| Power Change Threshold | W | 100 | 10–500 | Minimum power change before resending command |
| Power Adjust Cooldown | s | 30 | 5–120 | Minimum seconds between power adjustments |
| Night Buffer | % | 5 | 0–20 | Extra safety margin for night SOC calculation |
| Avg Night Consumption | W | 400 | 100–2000 | Baseline night power draw (auto-updated hourly) |
| Charge Margin | W | 150 | 0–500 | Buffer between solar charge and grid balance point |
| Charge Entry Threshold | W | 500 | 100–2000 | Grid export required before entering charge mode |
| Charge Re-entry Delay | s | 300 | 30–600 | Cooldown before re-entering charge after exit |
| Bottomout Cooldown | s | 300 | 60–900 | Extended cooldown after charge bottomout exit |
| P1 Smoothing Period | s | 60 | 1–300 | Rolling average window for P1 meter readings |
| Max Grid Export | W | 0 | 0–10000 | Maximum allowed grid export before charging kicks in (single-phase only) |

**Options flow parameters** (set once in Configure, not entities):

| Setting | Default | Purpose |
| :--- | :--- | :--- |
| Override Battery Capacity | OFF | Enable manual battery capacity override |
| Battery Capacity (Wh) | 2000 | Manual capacity value (only used when override is checked) |
| Dry-Run Mode | OFF | Engine logs decisions and fires HA events but skips all API calls |

**Sensors (read-only):**

| Entity | Purpose |
| :--- | :--- |
| EM Decision | The active decision label (e.g., `solar_charge`, `night_self_consume`) |
| EM Last Action | Last mode command sent (e.g., `charge @ 2500W`, or `[dry-run] charge @ 2500W`) |
| EM Status | Engine state: `running`, `stopped`, `disabled`, `cooldown`, `dry_run`, or `error` |
| Battery Energy Available | Usable energy above SOC minimum (Wh) |
| Hours Until Sunrise | Calculated from `sun.sun` entity |
| Hours Until Sunset | Calculated from `sun.sun` entity |
| P1 Average Power | Rolling average of P1 meter readings, configurable window (W) |

**Binary sensors:**

| Entity | Purpose |
| :--- | :--- |
| Night Mode Active | Whether it's currently nighttime (sun below horizon, no solar) |
| High Load Detected | Whether home load exceeds the high load threshold |

**HA Events:**

The engine fires a `hyxi_em_mode_changed` event on every mode change, usable in automations:

| Field | Description |
| :--- | :--- |
| `sn` | Inverter serial number |
| `mode` | New mode (`charge`, `discharge`, `self_consume`, `idle`) |
| `power` | Target power in watts (null for idle/self_consume) |
| `previous_mode` | Mode before the change |
| `decision` | Decision label that triggered the change |
| `dry_run` | `true` if in dry-run mode (field absent when not dry-run) |

#### Microinverter

| Controls | controlId |
| :--- | :--- |
| Power On/Off | 3011 |
| Power Limit (0–100%) | 3012 |
| Restart | 3013 |

#### Unsupported Device Types

> [!WARNING]
> **Micro ESS (EMS):** Control entities are **not** enabled for EMS devices. The HYXI API documentation does not list any control endpoints for EMS — the EMS API section only provides read-only data queries.

#### Real-Time Webhook Push Subscriptions

The integration supports subscribing to real-time telemetry and alarm push notifications from the HYXI Cloud. Instead of waiting for the polling interval, HYXI Cloud will push data and alarm changes directly to Home Assistant as soon as they are received.

##### Configuration
1. Go to **Settings > Devices & Services** > **HYXI Cloud** > **Configure**.
2. Toggle **Enable Real-Time Telemetry & Alarm Push**.
3. Configure the following optional parameters if needed:
   - **Real-Time Push Rate (s):** Telemetry push rate in seconds (default: 10).
   - **Real-Time Push Custom Callback URL:** By default, the integration uses the public external URL registered with Home Assistant to configure the webhook. If your Home Assistant's default public URL is not directly accessible by HYXI Cloud (e.g. if you are behind CGNAT, using custom Nginx proxies, or using an ngrok / Cloudflare tunnel), you can provide a custom callback URL here. The webhook path must be appended manually.
4. Saving the options will register a new webhook callback endpoint on your Home Assistant instance and automatically subscribe to HYXI Cloud.

##### Diagnostics & Monitoring
The integration provides a **Subscription Status** sensor on your inverter's device page:
- **State:** Reports `active`, `inactive`, or `error` depending on subscription health.
- **Attributes:** Displays URLs, subscriber codes, rates, errors, and the timestamp of the last received push frame.
- **Renewal Button:** A stateless button entity **Renew Subscription** is provided to manually trigger unregistration and re-registration of the webhook if needed.

##### Troubleshooting Subscription Lockouts
If registration fails due to a lockout (e.g. API error `B004002` indicating that a device serial number has been subscribed to repeatedly), it usually means an active or orphaned subscription remains on the HYXI Cloud server:
1. Retrieve the active or orphaned subscription code(s) from the **known_subscription_codes** attribute of your inverter's **Subscription Status** sensor (this list is persistent and survives integration reinstalls).
2. Go to **Developer Tools > Actions** (or **Services** in older Home Assistant versions) in the Home Assistant UI.
3. Call the **HYXI Cloud: Cancel Subscription** (`hyxi_cloud.cancel_subscription`) service, providing the subscription code. The integration will call the API to manually tear down that subscription from the server.

##### Interaction with Polling (Coexistence Loop)
You do not need to disable or modify the standard polling interval when enabling push subscriptions:
- **Coexistence and Syncing:** When a real-time push update is received, it immediately updates your sensors in Home Assistant. However, the standard background polling loop (default: 5 minutes) still runs periodically in the background to fetch and synchronize metrics that are only available via pull queries (such as grid power) and to act as a heartbeat fallback.
- **Polling Fallback:** If push updates stop arriving (e.g., due to a network disruption, proxy failure, or cloud outage), standard REST API polling will continue as normal to keep your sensors updated.

### 🛡️ Reliability & Diagnostics

This integration includes a specialized diagnostic system to help you distinguish between local hardware issues and cloud service outages.

| Sensor | Purpose | Behavior |
| :--- | :--- | :--- |
| **Cloud Status** | Binary connectivity sensor. | Indicates Cloud connectivity. Includes **Connection Quality** and **Data Freshness** as attributes. |
| **Device Alarm** | Hardware fault tracking. | Binary sensor that turns `On` if the hardware reports active alarms. |
| **Integration Last Updated** | Local Sync timestamp. | The exact time Home Assistant last successfully processed a cloud update. |

## 🎨 Community Examples

* **[HYXi Ultra Dashboard](https://github.com/Robinbraakman/HYXi-Ultra-Dashboard)**: A custom Lovelace card for the HYXi Halo battery. Visualizes SOC, charge/discharge power, cumulative energy, efficiency, cycles, and estimated payback details.

## ⚙️ Setup & Configuration

1. Ensure you have a developer account and have created an **application** to obtain an **Access Key** and **Secret Key** from the [HYXIPOWER Developer Platform](https://open.hyxicloud.com/#/quickStart).

   > **Important:**
   > Use the same email address that your devices are registered to in the HYXI app.
2. Go to **Settings > Devices & Services** > **Add Integration** > **HYXI Cloud**.
Or alternatively, add the integration with the following:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hyxi_cloud)

## Configuration

1. Enter your **Access Key** and **Secret Key** from the HYXI Open API portal.

### Optional Features (Options Flow)
Click the **Configure** button on the HYXI integration card to access:
* **Polling Interval:** Adjust frequency between 1–60 minutes (Default: 5).
* **Enable Discovery via Alarms:** Proactively discover child devices reporting active alarms (Advanced).
* **Enable Device Control & Protection:** Opt-in to enable inverter mode buttons, charge/discharge power settings, automatic battery protection thresholds, and micro-inverter power limits or switches. By default, this is disabled to prevent conflicts with external control systems (e.g. energy providers or grid constraints).
* **Enable Real-Time Telemetry & Alarm Push:** Registers a webhook endpoint in Home Assistant and subscribes to HYXI Cloud push notifications to receive real-time updates and active alarms.
* **Enable Energy Manager Standalone (Beta):** Automated battery management engine. Only visible after enabling Device Control & Protection. See [Energy Manager Standalone](#energy-manager-standalone-beta) above.

## 🛡️ Quality Assurance

This integration prioritizes data integrity and system stability above all else:
- **99% Test Coverage**: Every line of core logic is validated against dozens of simulated hardware scenarios.
- **Glitch Filtering**: Built-in protection against impossible energy "spikes" and "dips" often caused by cloud reporting delays.
- **Continuous Integration**: Every change is automatically scanned for security vulnerabilities (CodeQL) and code quality (Ruff).

## 🐛 Troubleshooting

If you are opening a bug report, please include **Debug Logs**:
**How to enable and download debug logs:**
1. Go to **Settings > Devices & Services** > **HYXI Cloud**.
2. Click the three dots (⋮) and select **Enable debug logging**.
3. Wait 5-10 minutes, then click **Disable debug logging** to download the file.
4. Attach the downloaded log file to your GitHub issue — **no manual editing needed**, serial numbers, plant IDs, and your home address are automatically masked in the logs.

## Disclaimer
This is a custom integration and is **not** an official product of HYXI Power.

## Support
If you find this integration helpful and want to support its development:

[![Buy Me a Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=&slug=veldkornet&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff)](https://www.buymeacoffee.com/veldkornet)

---
