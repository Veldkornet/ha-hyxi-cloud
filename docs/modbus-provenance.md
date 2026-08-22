# Where the Modbus register facts come from

Every register value in this integration traces to one of three kinds of
source, and they are **not** equally trustworthy. This file records which is
which, so a later change does not quietly promote a guess to a fact.

The short version: **hardware observation outranks a vendor document, and a
vendor document outranks anything inferred from the cloud API.** Where two
sources disagree, that disagreement is evidence and must be preserved, not
resolved by making one match the other. And within "vendor document": a
document written for the exact hardware family in use outranks one written
for a different family whose examples happened to decode against it.

## Sources

| Source | Covers | Standing |
| :--- | :--- | :--- |
| HYXIPower *RS485_MODBUS RTU Hybrid Inverter Protocol*, V4.1, 2025-06-13 | HYX-H(5~12)K-HT (incl. the H10K-HT on the bench), HYX-H(15~25)K-HT, HYX-H(6~15)K-HTA/HTAC — registers 0–1351 and 973–3121 | **Vendor claim, current document, exact hardware family.** Supplied directly for this project. The strongest source held for any device here — not yet checked against a device, but not borrowed from a different product's document either. |
| HYXIPower *Micro Storage RS485 MODBUS* protocol, V1.0, 2026-02-10 | HALO / HYX-MS3000AC, registers 4002–5023 | **Vendor claim.** Shared on [issue #662](https://github.com/Veldkornet/ha-hyxi-cloud/issues/662) by @Ton123, who confirmed with HYXI that it may be published. Not yet checked against a device — no HALO has been on a bus. |
| HYXIPower *HYX-H(5~12)K-HT User Manual*, V1.2, 2024-07 | Alarm code table; lists port 14 as "Reserved Communication" with no pinout | **Vendor, published.** Superseded on the pinout question by the protocol document above — see below. |
| [Eniris device documentation](https://docs.eniris.be/nl/Controller/Devices/PV-hybrid-and-battery-inverters/HYXiPOWER/Hybrid%20Inverters) | Claimed RS485 is on port 14; supported hybrid models | **Third party, and contradicted.** The protocol document says PIN5/6 of the inverter's COM port (RJ45), not port 14. Kept in this table only as a record of what was checked, not as a source to trust. |
| `hyxi_cloud_api.VPP_ACTIVE_MODES` | Cloud `workMode` values 13 and 14 | **Inference, unconfirmed.** Derived by reverse-engineering the HYXI phone app's APK. Never observed on a device. See rule 1. |

## Resolved: the RS485 wiring for the hybrid series

The protocol document states it plainly: **PIN5 = RS485 A, PIN6 = RS485 B, on
the inverter's COM port (RJ45)**. 115200bps, no parity, 8 data bits, 1 stop
bit — same serial parameters as the HALO. This settles the question that was
open after the HYX-H12K user manual turned out not to publish a pinout, and
after Eniris's port-14 claim turned out not to match the vendor's own
protocol document.

Two things the hybrid protocol states that the HALO's does not:

- **Function code 0x06** (write single register) is available, alongside
  0x03/0x04/0x10. The HALO document only offers 0x03/0x04/0x10.
- **Frame spacing is >500ms**, not the HALO's >200ms. `HybridSettings` and
  its siblings don't encode this themselves — it belongs on the connection
  (`message_spacing`) once a hybrid client is wired into setup, and it must
  not be copied from the HALO client's 200ms constant.

## A worked example that does not work

Both documents — the HALO one and this hybrid one — contain an *identical*
example in their "acquire multiple settings" section: reading two registers
at address 1007 (0x03EF) and being told the response `18 6A 66 3A` represents
"the initial input data 1715081225".

It does not. Decoded low-word-first, exactly as both documents' own prose
describes U32 encoding, register 0 (`0x186A`) is the low word and register 1
(`0x663A`) is the high word, giving `0x663A186A` = **1715083370** — not
1715081225. The two numbers share the high word (`0x663A`) but differ in the
low word (`0x186A` vs the `0x1009` that 1715081225 actually requires), a gap
of 2145 that has no obvious explanation.

**An earlier version of this document asserted these numbers matched. They
don't — that was an arithmetic error, not a verified fact, and it has been
removed as supporting evidence for the word-order rule.**

The word-order rule itself still stands, on better evidence than this one
broken example: both documents independently state it in prose ("the high
word is behind and the low word is in front" / "the high word is behind and
the low word is in the front" — same rule, worded almost identically two
years apart), and the HALO document's §5.7 TOU example decodes correctly
against its own register table by address (`0x1052` = 4178 decimal, matching
exactly). What this broken example actually demonstrates is the same thing
the HALO document's misplaced §5.1–5.6 examples demonstrated: **HYXI reuses
worked examples across documents for different hardware, and they are not
reliably specific to the device the surrounding table describes.** Treat
every worked example as illustrative, not as a byte-exact fixture, until
checked against real hardware.

## Rules for anyone changing this code

### 1. Never reconcile a device's work-mode field with the cloud's `VPP_ACTIVE_MODES`

There are now *three* independent, mutually inconsistent sources for "what
mode is the device in":

| | 13 | 14 | 15 | 16 |
| :--- | :--- | :--- | :--- | :--- |
| HALO document, register 4102 | VPP idle | VPP charge | VPP discharge | VPP self-use |
| `hyxi_cloud_api.VPP_ACTIVE_MODES` | charge | discharge | — | enrolled / standby |

...and separately, the hybrid document's register 1265 ("current operating
mode") uses a **third, unrelated enumeration** — `1 self-use, 2 backup(green),
3 backup(grid), 4 feed-in, 5 off-grid, 6 battery SOC calibration, 7 battery
forced charging` — which doesn't resolve the HALO/cloud conflict (it's a
different register on a different device family) but is worth keeping
straight: three devices, three numbering schemes, none of them interchangeable.

The cloud set came from reading the phone app, not from watching a device.
The HALO document is a vendor statement about the same field on a *different*
device family. Neither has been checked against hardware, so editing either
to agree with the other throws away the only means of telling which is right.

Resolve the HALO/cloud conflict by **observation**: command a charge, then a
discharge, through the cloud API on a device that accepts cloud control,
snapshotting the bus around each with `tools/modbus_probe.py`. Whichever
register tracks the change is the work mode, and its values are then fact.
Record the result here and *then* change the code.

Note that this is not hypothetical housekeeping: `binary_sensor.py` reads
`metrics["vppMode"]`, a key the cloud client never populates, so the VPP
dispatch sensor has never worked. The same experiment settles that too.

### 2. Do not encode an unconfirmed enumeration as an enum

Every status/mode field in `registers.py` and `registers_hybrid.py` — work
state, work mode, grid state, BMS state, `HybridStatus.operation_status`,
`HybridBattery.operating_status`, `HybridStatus.current_operating_mode` — is
declared as a plain integer rather than `enum(...)`, even where the hybrid
document is a strong, current, hardware-specific source. An `IntEnum` would
raise on any value outside the documented range, and the hybrid document
covers four separate model variants (HT/HTA/HTAC across several power
ratings) that have not individually been checked. Raw integers pass the
device's own answer through, which is what a later investigation needs, and
raising on an unexpected value would break polling instead of just looking odd.

### 3. Do not decode fault bits into names yet

The HALO document contradicts itself about where the BMS fault words sit: the
register table says 5000–5002, the alarm table in §6 says 5001–5003, and one
address cell in §6 is visibly corrupt (it prints `42`). `HaloFaults` and the
BMS alarm fields therefore expose raw words. The hybrid document's fault
words (`HybridFaults`, registers 38–45 and 1041–1043) are internally
consistent with no such contradiction, but are still raw for the same reason
everything else on this list is: unconfirmed against hardware. Induce a known
fault and read the block wide before naming any bit, on either device family.

### 4. Never write the clock, address or baud-rate registers

HALO: 4000–4005 (clock, timezone, RS485 address, baud rate) — absent from
`HaloSettings`. Hybrid: 1007/1009 (absolute time, time zone), 3120 (baud
rate), 3121 (Modbus address) — absent from `HybridSettings`. Writing the
address or baud rate can take the device off the bus permanently until
someone re-wires it with the old settings; nothing here has a reason to
touch any of them.

### 5. The hybrid's control mapping is a single signed register, not a mode block

This is worth stating because it's a genuinely different shape from the
HALO's VPP block, and it would be easy to "simplify" one to match the other
incorrectly:

- **HALO**: enable (4146) + mode (4147, 0/1/2/3) + separate charge-power
  (4148) and discharge-power (4150) registers.
- **Hybrid**: scheduling enable (3000) + control mode (3004, must be 0 for
  this client) + **one** signed watts register (3015) — positive discharges,
  negative charges. `set_mode_self_consume` on the hybrid client disables
  register 3000 entirely rather than writing a zero setpoint, handing control
  back to the inverter's own self-use logic instead of pinning it at idle
  under external control — deliberately different from the HALO client's
  `set_mode_self_consume`, which writes VPP mode 3.

Two polarity/semantic differences between the device families, both
documented explicitly rather than assumed to match:

- **Anti-starvation protection**: HALO's register (4121) reads `0 not
  enabled, 1 enabled`. The hybrid's (1101) reads `0 open, 1 close` —
  apparently the opposite sense. Not reconciled; each client's register
  comment states its own device's polarity as the document gives it.
- **Feed-in / export switch**: HALO's `feed_in_enable` (4162) is a literal
  export gate — `0 off, 1 on`. The hybrid's `feed_in_enable` (1099) is an
  *export-control enable* — `0 disable export control, 1 enable export
  control`, with the actual limit in a separate register (1100). The two
  `set_peak_shaving` implementations therefore write opposite values for
  "on": the HALO client closes the gate (writes 0), the hybrid client enables
  the limit (writes 1). This is not a bug in either — they are different
  registers on different hardware with different documented meanings.

## Still unverified

Everything below is a vendor claim or an inference. Check against hardware
and update this file with the result.

| Question | Device | Why it matters |
| :--- | :--- | :--- |
| Work mode enumeration at 4102 | HALO | See rule 1. Also fixes a live cloud bug. |
| Sign convention of `gridP` (4152) and battery power (4985) | HALO | Decides whether import reads as export and charge as discharge. The cloud path already carries a note that `batP` has an inverted sign on all-in-one units. |
| Battery capacity unit at 5020 | HALO | Documented in **Ah**; the cloud's `batCap` is kWh. Needs nominal pack voltage to convert — currently not mapped at all rather than mapped wrongly. |
| BMS fault word addresses | HALO | See rule 3. |
| Whether 4146 must enable dispatch before 4147 takes effect | HALO | `_write_vpp` writes the enable every time on the assumption it does. Harmless if unnecessary. |
| Whether a VPP dispatch survives a power cycle, or a watchdog reverts it | HALO | Decides whether the integration needs a heartbeat write to hold a mode. |
| **Unit of the grid/inverter power registers** (316–318, 333, 370–372, 507, 520–522, PV powers) | Hybrid | The document gives 0 decimal places and no unit label. Treated as Watts by convention (0dp is too coarse for kW at this precision), then `gridP` alone is converted to kW to satisfy `compute_derived_metrics`'s general contract. If the true native unit is something else, every power metric on the hybrid client is wrong by a constant factor. |
| **Sign convention of battery real-time power** (register 1065) | Hybrid | Unlike register 3015 (explicitly "positive discharge, negative charge"), 1065's sign is not stated. `batP`/`pbat` currently pass it through unconverted; if the read-side convention differs from the write-side one, the sensor and the control write would disagree about which sign means what. |
| Whether control_mode (3004) must be written before scheduling_enabled (3000), or vice versa, or either order works | Hybrid | `_prepare_scheduling` writes enable then control_mode every call. Untested ordering. |
| Whether a Modbus scheduling write survives a power cycle, or reverts when the master goes quiet | Hybrid + HALO | Decides whether either integration needs a heartbeat write. |
| Whether a Modbus write is overwritten by a cloud settings sync | Hybrid + HALO | The DCS/WiFi module stays connected on both. The two control paths are independent and may fight. |
| Whether pooled block reads may span undefined addresses | Hybrid + HALO | `Component.max_gap` defaults to 16, so fields up to 16 registers apart are read as one block. `tools/fake_hyxi.py` fills gaps with zeros to model likely real behaviour, which is an assumption, not an observation, and currently only exists for the HALO profile. |
| The entire register map, on real hardware | Hybrid + HALO | Nothing above has been checked against a device yet. Phase 1 of bringing either transport up is a `tools/modbus_probe.py` sweep compared against the app/cloud, not trusting this file's tables blind. |
