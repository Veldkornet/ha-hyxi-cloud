# Where the Modbus register facts come from

Every register value in this integration traces to one of three kinds of
source, and they are **not** equally trustworthy. This file records which is
which, so a later change does not quietly promote a guess to a fact.

The short version: **hardware observation outranks a vendor document, and a
vendor document outranks anything inferred from the cloud API.** Where two
sources disagree, that disagreement is evidence and must be preserved, not
resolved by making one match the other.

## Sources

| Source | Covers | Standing |
| :--- | :--- | :--- |
| HYXIPower *Micro Storage RS485 MODBUS* protocol, V1.0, 2026-02-10 | HALO / HYX-MS3000AC, registers 4002–5023 | **Vendor claim.** Shared on [issue #662](https://github.com/Veldkornet/ha-hyxi-cloud/issues/662) by @Ton123, who confirmed with HYXI that it may be published. Not yet checked against a device — no HALO has been on a bus. |
| The same document's §5.1–5.6 worked examples | HYX-H hybrid *holding* registers 1009, 1099–1105, 1108 | **Vendor claim, internally corroborated.** These examples do not match the HALO table they sit in; they decode cleanly and coherently against the hybrid map (see below). |
| The same document's §5.7 TOU example | HALO holding registers 4178–4184 | **Vendor claim, internally corroborated.** Wire address `0x1052` = 4178 decimal, matching the table exactly. |
| HYXIPower *HYX-H(5~12)K-HT User Manual*, V1.2, 2024-07 | Port 14 is "Reserved Communication"; alarm code table | **Vendor, published.** Does not publish a pinout for port 14. |
| [Eniris device documentation](https://docs.eniris.be/nl/Controller/Devices/PV-hybrid-and-battery-inverters/HYXiPOWER/Hybrid%20Inverters) | RS485 is on port 14; supported hybrid models; RS485 only, no native Modbus TCP | **Third party.** No register map. |
| `hyxi_cloud_api.VPP_ACTIVE_MODES` | Cloud `workMode` values 13 and 14 | **Inference, unconfirmed.** Derived by reverse-engineering the HYXI phone app's APK. Never observed on a device. See the warning below. |

## The hybrid examples inside the HALO document

§5.1–5.6 read addresses that appear nowhere in the HALO's own register table.
They are not errors — they decode against the HYX-H hybrid map, which lives
around 1000–1300 rather than 4000–5023:

| Example | Address | Response | Reads as |
| :--- | :--- | :--- | :--- |
| §5.3 #1 | `0x03F1` = 1009 | `0x7080` = 28800 | Time zone offset: 28800 s = UTC+8 |
| §5.3 #2 | `0x0454` = 1108 | `0x007F` = 127 | TOU weekday bitmask: all seven days |
| §5.4 #1 | `0x044B` = 1099, 7 registers | 1, 0, 1, 10, 10, 60, 10 | Export control on, feed-in 0, anti-starvation on, then four SOC setpoints |

Two conclusions follow, and both are load-bearing:

1. The HALO's 4xxx table is **decimal and directly addressable**. §5.7 already
   implied it; this confirms it from a second direction.
2. The **hybrid series speaks a different map entirely**. A HYX-H10K-HT cannot
   be used to verify the HALO table, and vice versa.

## Rules for anyone changing this code

### 1. Never reconcile the Modbus work mode with the cloud's `VPP_ACTIVE_MODES`

They disagree, and that is the point.

| | 13 | 14 | 15 | 16 |
| :--- | :--- | :--- | :--- | :--- |
| Micro Storage document, register 4102 | VPP idle | VPP charge | VPP discharge | VPP self-use |
| `hyxi_cloud_api.VPP_ACTIVE_MODES` | charge | discharge | — | enrolled / standby |

The cloud set came from reading the phone app, not from watching a device. The
document is a vendor statement about the same field. Neither has been checked
against hardware, so they are two independent pieces of evidence — and editing
either to agree with the other throws away the only means of telling which is
right.

Resolve it by **observation**: command a charge, then a discharge, through the
cloud API on a device that accepts cloud control, snapshotting the bus around
each with `tools/modbus_probe.py`. Whichever register tracks the change is the
work mode, and its values are then fact. Record the result here and *then*
change the code.

Note that this is not hypothetical housekeeping: `binary_sensor.py` reads
`metrics["vppMode"]`, a key the cloud client never populates, so the VPP
dispatch sensor has never worked. The same experiment settles that too.

### 2. Do not encode an unconfirmed enumeration as an enum

`registers.py` deliberately declares `work_state`, `work_mode`, `grid_state`
and `bms_state` as plain integers rather than `enum(...)` fields. An `IntEnum`
would bake one reading of an unverified table into the type system and raise on
any value outside it. Raw integers pass the device's own answer through, which
is what a later investigation needs.

### 3. Do not decode fault bits into names yet

The document contradicts itself about where the BMS fault words sit: the
register table says 5000–5002, the alarm table in §6 says 5001–5003. One
address cell in §6 is also visibly corrupt (it prints `42`). `HaloFaults` and
the BMS alarm fields therefore expose raw words. Induce a known fault on
hardware and read the block wide before naming any bit.

### 4. Never write registers 4000–4005

Clock, timezone, RS485 address and baud rate. Writing the last two can take the
device off the bus, and nothing here has a reason to touch any of them. They are
deliberately absent from `HaloSettings`.

## Still unverified

Everything below is a vendor claim or an inference. Check against hardware and
update this file with the result.

| Question | Why it matters |
| :--- | :--- |
| Work mode enumeration at 4102 | See rule 1. Also fixes a live cloud bug. |
| Sign convention of `gridP` (4152) and battery power (4985) | Decides whether import reads as export and charge as discharge. The cloud path already carries a note that `batP` has an inverted sign on all-in-one units, so this is not a safe guess. |
| Battery capacity unit at 5020 | Documented in **Ah**; the cloud's `batCap` is kWh. Converting needs a nominal pack voltage. Currently not mapped at all rather than mapped wrongly. |
| BMS fault word addresses | See rule 3. |
| Whether 4146 must enable dispatch before 4147 takes effect | `_write_vpp` writes the enable every time on the assumption it does. Harmless if unnecessary. |
| Whether a VPP dispatch survives a power cycle, or a watchdog reverts it | Decides whether the integration needs a heartbeat write to hold a mode. |
| Whether a Modbus write is overwritten by a cloud settings sync | The DCS module stays connected. The two control paths are independent and may fight. |
| Whether pooled block reads may span undefined addresses | `Component.max_gap` defaults to 16, so fields up to 16 registers apart are read as one block. If real hardware rejects a read crossing an undefined address, `max_gap` must come down. `tools/fake_hyxi.py` fills gaps with zeros to model the common behaviour, which is an assumption, not an observation. |
| The entire HYX-H hybrid register map | Only 1009, 1099–1105 and 1108 are known, from the examples above. Derive the rest with `tools/modbus_probe.py`, or ask HYXI for the HYX-H document the way @Ton123 obtained the HALO one. |
| Port 14 pinout on the hybrids | HYXI confirmed pins 7 (A) and 8 (B) for the HALO. The equivalent for HYX-H is unpublished; ports 9 (BAT) and 10 (METER) are occupied in a normal install. |
