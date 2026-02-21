# HYXi Cloud for Home Assistant

Monitor your HYXi Hybrid Inverter and Battery Storage directly in Home Assistant using the HYXi Cloud Open API.

This custom integration provides real-time data for solar production, battery status, and home energy usage, perfectly formatted for the Home Assistant Energy Dashboard.

## 🌟 Key Features

* **Real-time Power Monitoring:** Track your Solar Power, Battery SOC, Home Load, and Grid flow.
* **Energy Dashboard Ready:** Includes strictly formatted `kWh` sensors for Lifetime Yield and Battery totals.
* **Smart Device Grouping:** Automatically separates your Inverter and Battery into distinct, logically linked devices in Home Assistant.
* **Auto-Naming:** Automatically prefixes sensors with your unique Plant/Device Name.
* **Clean UI:** Precision-tuned sensors (e.g., Battery SOH at 1 decimal point, SOC as whole numbers).

## 🛠️ Prerequisites

To use this integration, you must have a HYXi Developer Account. 
1. Go to the [HYXi Open API Portal](https://open.hyxicloud.com/#/quickStart).
2. Register for an account (use the exact same email address your inverter is registered to).
3. Generate an **Access Key** and **Secret Key**.

## 🚀 Setup Instructions

1. Go to **Settings > Devices & Services** in Home Assistant.
2. Click **Add Integration** and search for **HYXi Cloud**.
3. Enter your generated **Access Key** and **Secret Key**.
4. Your Inverter and Battery devices will automatically populate!

---

*Disclaimer: This is a custom community integration and is not an official product of HYXi Power. Currently, only Hybrid Inverter and Battery systems have been fully verified for compatibility.*
