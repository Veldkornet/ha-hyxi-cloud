![HYXI Integration for Home Assistant](https://raw.githubusercontent.com/Veldkornet/ha-hyxi-cloud/main/assets/readme-header.png)

# HYXI Integration for Home Assistant

**Monitor your [HYXIPower](https://www.hyxipower.com/) solar production, battery, and grid flow in real-time.**

[![Release][release-shield]][releases]
[![HACS][hacs-shield]][hacs]
[![Home Assistant][ha-version-shield]][hacs]
[![Docs][wiki-shield]][wiki]

[![Tests][tests-shield]][tests]
[![Coverage][coverage-shield]][tests]
[![CodeQL][codeql-shield]][codeql]
[![Downloads][downloads-shield]][releases]
[![License][license-shield]](LICENSE)
[![Open in Dev Containers][devcontainer-shield]][devcontainer]

---

Bring your HYXIPower inverter, battery, and meter data into Home Assistant — over the **HYXI Cloud** (your account, any supported device) or a direct **Local Modbus (RS485)** connection to one inverter (no account, no internet dependency).

> 📖 **Full documentation is in the [Wiki][wiki].** This page is the quick start.

## ✨ Features

- **☁️ Cloud or 📟 local** — pull data through the HYXI Cloud API, or talk straight to one inverter over RS485 Modbus for faster polling and no internet dependency ([Local Modbus][wiki-modbus]).
- **⚡ Energy Dashboard ready** — native support for Home Assistant's Energy Dashboard ([setup][wiki-energy]).
- **🔄 Real-time push** — optional webhook subscriptions for instant telemetry and alarm updates, bypassing the poll interval ([details][wiki-push]).
- **🔧 Device control** — opt-in inverter mode buttons, charge/discharge power, peak shaving, battery protection, and microinverter controls ([reference][wiki-control]).
- **🔋 Energy Manager (Beta)** — an automated battery engine that manages charge/discharge from your P1 meter, solar, SOC, and forecast ([guide][wiki-em]).
- **📊 Diagnostics** — dedicated sensors for cloud connectivity, data freshness, and sync latency.
- **🛡️ Resilient** — 100% test coverage, and glitch-filtering that rejects impossible energy spikes and dips from cloud reporting delays.
- **🕥 Adjustable polling** — 1 to 60 minutes.
- **🌍 20+ languages** — English, German, French, Dutch, Afrikaans, Portuguese, Spanish, Italian, and more.

## 📥 Installation

[![Open your Home Assistant instance and open the repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)][hacs]

1. Open **HACS** in Home Assistant and search for **HYXI**.
2. Click **Download**, then restart Home Assistant.

<details>
<summary>Can't find it in HACS?</summary>

If **HYXI** doesn't appear in search, add it as a custom repository first:

1. In HACS, open the **⋮** menu → **Custom repositories**.
2. Repository `https://github.com/Veldkornet/ha-hyxi-cloud`, category **Integration** → **Add**.
3. Search for and download **HYXI** as above.

</details>

Manual installation and the **beta channel** (pre-release features) are in the [Installation Guide][wiki-install].

## ⚙️ Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hyxi_cloud)

**Settings → Devices & Services → Add Integration → HYXI**, then pick a transport:

- **HYXI Cloud** — needs an **Access Key** + **Secret Key** from the [HYXIPOWER Developer Platform](https://open.hyxicloud.com/#/quickStart). Log in with the **same email** your devices are registered to in the HYXI app — this is *not* your app password.
- **Local Modbus (RS485)** — needs a wired RS485 link to one inverter ([Local Modbus][wiki-modbus]).

Optional features live under **Configure** on the integration card: polling interval, alarm-based discovery, [device control][wiki-control], [real-time push][wiki-push], and the [Energy Manager][wiki-em]. Full walkthrough in the [Installation Guide][wiki-install].

## 🔌 Supported Devices

The integration adapts to whatever HYXI Cloud reports (or what the Modbus probe detects). Even an "Untested" device populates with basic diagnostics and known entities.

| Device | Cloud | Local Modbus |
| :--- | :--- | :--- |
| Hybrid Inverter / All-in-One | ✅ Tested | ⚠️ Reads only |
| Micro ESS (HALO) | ✅ Tested | ⚠️ Untested |
| Data Collector | ✅ Tested | — |
| Micro Inverter | ✅ Tested | — |
| String Inverter / Smart Meter | ⚠️ Untested | — |

Per-device entity lists and the full status matrix: [Supported Devices][wiki-devices] and [Available Sensors][wiki-sensors].

> **🤝 Own a String Inverter, Smart Meter, or multiple batteries behind one inverter?** Debug logs from your setup can help move these to ✅ Tested — see [Supported Devices][wiki-devices].

## 🔧 Device Control

Control entities (mode buttons, power targets, switches) are **hidden by default** so the integration never fights an external controller or grid schedule. Enable **Device Control & Protection** under **Configure** to reveal them.

What appears depends on the device — three-phase mode buttons, single-phase peak shaving and frequency control, microinverter power limits, battery protection thresholds, and the automated **Energy Manager (Beta)**.

The full reference — controlId maps, phase detection, the Energy Manager decision logic and every tunable — is in the wiki: **[Device Control][wiki-control]** and **[Energy Manager][wiki-em]**.

## 📟 Local Modbus (RS485)

An alternative to the cloud: the integration talks directly to one inverter over RS485 — no account, no internet, far faster polling, and control on devices the cloud API refuses. Trade-off: one device only, a wired connection, and no multi-device discovery or remote access.

Setup probes the bus and picks the register map automatically (Hybrid or HALO). Reads are reliable on both; some writes are still being validated against hardware. See **[Local Modbus (RS485)][wiki-modbus]** for wiring and setup, and [`docs/modbus-provenance.md`](docs/modbus-provenance.md) for per-register provenance.

## 🎨 Community Examples

- **[HYXi Ultra Dashboard](https://github.com/Robinbraakman/HYXi-Ultra-Dashboard)** — a Lovelace card for the HYXI Halo: SOC, charge/discharge power, cumulative energy, efficiency, cycles, and estimated payback.

## 🐛 Troubleshooting

Opening a bug report? Attach **debug logs**: **Settings → Devices & Services → HYXI → ⋮ → Enable debug logging**, wait 5–10 minutes, then **Disable debug logging** to download the file. Serial numbers, plant IDs, and your home address are masked automatically.

Common issues — credentials, stale data, Modbus detection, push-subscription lockouts, Energy Manager status — are covered in the [Troubleshooting][wiki-troubleshooting] wiki.

## Disclaimer

This is a custom integration and is **not** an official product of HYXI Power.

## Support

If this integration is useful to you and you'd like to support its development:

[![Buy Me a Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=&slug=veldkornet&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff)](https://www.buymeacoffee.com/veldkornet)

---

<!-- Badges -->
[release-shield]: https://img.shields.io/github/v/release/Veldkornet/ha-hyxi-cloud?include_prereleases&sort=semver&style=for-the-badge&logo=github&logoColor=white&label=Release&color=41BDF5
[releases]: https://github.com/Veldkornet/ha-hyxi-cloud/releases
[hacs-shield]: https://img.shields.io/badge/HACS-Default-41BDF5?style=for-the-badge&logo=homeassistant&logoColor=white
[hacs]: https://my.home-assistant.io/redirect/hacs_repository/?owner=Veldkornet&repository=ha-hyxi-cloud&category=Integration
[ha-version-shield]: https://img.shields.io/badge/dynamic/json?url=https://raw.githubusercontent.com/Veldkornet/ha-hyxi-cloud/main/hacs.json&query=$.homeassistant&style=for-the-badge&logo=homeassistant&logoColor=white&label=Home%20Assistant&color=41BDF5&prefix=%E2%89%A5%20
[wiki-shield]: https://img.shields.io/badge/Docs-Wiki-41BDF5?style=for-the-badge&logo=readthedocs&logoColor=white
[wiki]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki
[tests-shield]: https://img.shields.io/github/actions/workflow/status/Veldkornet/ha-hyxi-cloud/tests.yml?branch=main&style=for-the-badge&logo=github&logoColor=white&label=Tests
[tests]: https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/tests.yml
[coverage-shield]: https://img.shields.io/badge/Coverage-100%25-31C653?style=for-the-badge&logo=pytest&logoColor=white
[codeql-shield]: https://img.shields.io/github/actions/workflow/status/Veldkornet/ha-hyxi-cloud/codeql.yml?branch=main&style=for-the-badge&logo=github&logoColor=white&label=CodeQL
[codeql]: https://github.com/Veldkornet/ha-hyxi-cloud/actions/workflows/codeql.yml
[downloads-shield]: https://img.shields.io/github/downloads/Veldkornet/ha-hyxi-cloud/total?style=for-the-badge&logo=github&logoColor=white&label=Downloads
[license-shield]: https://img.shields.io/github/license/Veldkornet/ha-hyxi-cloud?style=for-the-badge&color=6E7681
[devcontainer-shield]: https://img.shields.io/badge/Dev%20Container-Open-41BDF5?style=for-the-badge&logo=devcontainers&logoColor=white
[devcontainer]: https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/Veldkornet/ha-hyxi-cloud

<!-- Wiki -->
[wiki-install]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Installation-Guide
[wiki-devices]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Supported-Devices
[wiki-sensors]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Available-Sensors
[wiki-modbus]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Local-Modbus-RS485
[wiki-em]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Local-Energy-Manager
[wiki-control]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Device-Control
[wiki-push]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Real-Time-Push
[wiki-energy]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Energy-Dashboard-Setup
[wiki-troubleshooting]: https://github.com/Veldkornet/ha-hyxi-cloud/wiki/Troubleshooting
