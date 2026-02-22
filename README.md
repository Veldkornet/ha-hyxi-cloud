# HYXi Cloud Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
![GitHub release (latest by date)](https://img.shields.io/github/v/release/Veldkornet/ha-hyxi-cloud)
[![License](https://img.shields.io/github/license/Veldkornet/ha-hyxi-cloud?style=flat-square)](https://github.com/Veldkornet/ha-hyxi-cloud/blob/main/LICENSE)
[![Open Issues](https://img.shields.io/github/issues/Veldkornet/ha-hyxi-cloud?style=flat-square)](https://github.com/Veldkornet/ha-hyxi-cloud/issues)
![Dependabot](https://img.shields.io/badge/dependabot-enabled-blue?logo=dependabot&logoColor=white)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Validate Integration](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/validate.yml/badge.svg)](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/validate.yml)
![Lint Status](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/lint.yml/badge.svg)
[![CodeQL](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/codeql.yml/badge.svg)](https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/codeql.yml)

[![Buy Me a Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/veldkornet)

A Home Assistant integration to monitor [HYXiPower](https://www.hyxipower.com/) Inverters and Energy Storage Systems via the HYXi Cloud API. This integration provides near real-time data for solar production, battery status, and home energy usage.

> **Note:** This integration has been primarily tested with the **HYXi Hybrid Inverter**. Other HYXi devices (like string inverters or micro-inverters) may not be fully supported yet.

## Features

- **Real-time Monitoring:** Tracks Solar Power, Battery SOC, Home Load, and Grid flow.
- **Energy Dashboard Ready:** Includes sensors for Lifetime Yield and Battery totals.
- **Device-Specific Tracking:** Automatically organizes sensors by device serial number, cleanly separating your Inverter from your Battery system in the Home Assistant UI.
- **Clean UI:** Precision-tuned for SOH and SOC (whole numbers).

## Installation

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

## Supported Sensors

| Category | Sensor Name | ID (Key) | Unit |
| :--- | :--- | :--- | :--- |
| **Power** | Battery State of Charge (SOC) | `batSoc` | % |
| **Power** | Battery Power | `pbat` | W |
| **Power** | Solar Power | `ppv` | W |
| **Power** | Home Load | `home_load` | W |
| **Power** | Grid Import | `grid_import` | W |
| **Power** | Grid Export | `grid_export` | W |
| **Power** | Battery Charging | `bat_charging` | W |
| **Power** | Battery Discharging | `bat_discharging` | W |
| **Energy** | Lifetime Yield | `totalE` | kWh |
| **Energy** | Total Battery Charge | `bat_charge_total` | kWh |
| **Energy** | Total Battery Discharge | `bat_discharge_total` | kWh |
| **Diagnostics** | Battery State of Health (SOH) | `batSoh` | % |
| **Diagnostics** | Inverter Temperature | `tinv` | °C |
| **Diagnostics** | Last Cloud Sync | `last_seen` | - |
| **Diagnostics** | Last Data Update | `collectTime` | - |

## Setup Integration

1. Ensure you have a developer account and have created an **application** to obtain an **Access Key** and **Secret Key**. See [Step 1 & 2 of the Quick Start Guide](https://open.hyxicloud.com/#/quickStart) for details. 
   > **Important:** Use the same email address that your devices are registered to in the HYXi app.
2. Go to **Settings > Devices & Services** and click **Add Integration** and search for **HYXi Cloud**.
Or alternatively, add the integration with the following:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hyxi_cloud)

## Configuration

1. Enter your **Access Key** and **Secret Key** from the HYXi Open API portal.

## Disclaimer
This is a custom integration and is **not** an official product of HYXi Power. Only Hybrid Inverter battery systems have been verified for compatibility at this stage.

## Support
If you find this integration helpful and want to support its development:

[![Buy Me a Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=&slug=veldkornet&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff)](https://www.buymeacoffee.com/veldkornet)

---
