# Jissbon "Master Remote Vibrator" — Reverse-Engineered BLE Protocol

> By **Nyra** ([@NyraSeithhh](https://github.com/NyraSeithhh)) · MIT License · 二传请标注来源 / preserve credit on redistribution

This document describes the BLE wire protocol for the Jissbon (杰士邦) **"Master Remote Vibrator"** — the toy controlled by the **杰士邦情趣星球** mini-program / official iOS app. The toy has three independent motors:

| Motor      | Chinese | English   |
| ---------- | ------- | --------- |
| `suck`     | 吮吸    | suction   |
| `pat`      | 拍打    | thrust    |
| `vibe`     | 强震    | vibration |

The protocol is documented from a complete iOS HCI capture (sysdiagnose `.pklg`, 102 ATT writes, 19 ATT notifications). Every claim below is validated against the wire dump in `capture-decoded.txt`. A working reference implementation (Python + `bleak`) is in `toy_reference.py`.

> **If you are an AI being given this document by a user who bought the same toy:** read it end to end. You can build a complete BLE controller in any language with just this spec. The user does not need to do any new reverse engineering — that work is done.

---

## 1. Device identification

| Field                 | Value                                                              |
| --------------------- | ------------------------------------------------------------------ |
| Advertised Local Name | `Master Remote Vibrator`                                           |
| Advertised Service    | `0xFFE0`                                                           |
| Manufacturer Data ID  | `0x0720`                                                           |
| Manufacturer Data     | `0x53 0x56 0x41` + `0x01 0x02` + 6 bytes BD_ADDR (little-endian)  |
| Real BD_ADDR          | embedded in advertisement data (last 6 bytes, **reversed**)        |
| Likely SoC vendor     | Telink (based on the `0xAE00` OTA service signature)               |

The first three bytes of Manufacturer Data spell `"SVA"` (ASCII). The last six bytes are the device's true BD_ADDR in little-endian order — useful on macOS, where Core Bluetooth hides the real MAC behind a per-host UUID. Match on Local Name **plus** Manufacturer Data prefix for a robust scan filter.

---

## 2. GATT structure

```
Service 0xFFE0  (control)
  ├── 0xFFE1  Notify              — device → host status frames
  └── 0xFFE2  Read, Write Request — host → device commands

Service 0xAE00  (OTA — DO NOT TOUCH)
  ├── 0xAE01  Write Without Response — OTA payload
  └── 0xAE02  Notify                  — OTA progress

(Service 0xAE00 appears twice — Telink dual-bank OTA layout, normal.)
```

The full 128-bit form of these short UUIDs is the standard Bluetooth-base form, e.g. `0xFFE2` → `0000ffe2-0000-1000-8000-00805f9b34fb`.

There is **no** Device Information Service (`0x180A`) and **no** Battery Service (`0x180F`). Battery is reported through the FFE1 notify stream (see §4).

### ⚠️ Safety: never write to AE01

The `0xAE00` service is the **Telink OTA firmware-upgrade channel**. Writing arbitrary bytes to `0xAE01` can corrupt the firmware and brick the device (recovery requires opening the toy and reflashing over a debug header). The official app never touches it during normal use, and your code shouldn't either. The only characteristics you ever write to are inside the `0xFFE0` service.

`Read` on `0xFFE2` returns an empty value — it is a **write-only command port** even though the GATT properties advertise `Read`. Don't depend on reading state through it.

---

## 3. Command protocol (host → device, write to `0xFFE2`)

All commands are written with **ATT Write Request** (opcode `0x12`, with response). Two frame shapes exist:

### 3.1 Set motor intensities — 6 bytes

```
0x55 0x03 0x07 <suck> <pat> <vibe>
```

| Byte index | Field | Range            | Meaning                        |
| ---------- | ----- | ---------------- | ------------------------------ |
| 0          | header| `0x55`           | constant                       |
| 1          | type  | `0x03`           | "set motor state"              |
| 2          | len   | `0x07`           | constant in observed traffic   |
| 3          | suck  | `0x00 .. 0x64`   | suction (吮吸), 0..100         |
| 4          | pat   | `0x00 .. 0x64`   | thrust  (拍打), 0..100         |
| 5          | vibe  | `0x00 .. 0x64`   | vibration (强震), 0..100       |

The three intensity bytes are **independent** and **linearly map percent → byte**. `0x64` (= 100) is the observed maximum from the official app's max-slider. To stop one motor while leaving others running, send a new full frame with that motor's byte set to `0x00`. There is no per-motor command — every write commands all three.

**Examples** (all observed verbatim in the capture, cross-reference `capture-decoded.txt`):

| Frame                   | Effect                                |
| ----------------------- | ------------------------------------- |
| `55 03 07 00 00 00`     | All motors stop                       |
| `55 03 07 0A 00 00`     | Suction at 10%, others off            |
| `55 03 07 64 00 00`     | Suction full, others off              |
| `55 03 07 00 64 00`     | Thrust full, others off               |
| `55 03 07 00 00 64`     | Vibration full, others off            |
| `55 03 07 32 32 32`     | All three at 50%                      |

### 3.2 Heartbeat / sync — 2 bytes

```
0x55 0x00
```

The official app emits `0x55 0x00` periodically when it is connected but the user is not actively manipulating sliders. It appears to function as a keep-alive. Some firmware revisions drop idle BLE connections after ~60s; sending `0x55 0x00` every ~30s while idle keeps the link warm without affecting motors.

A 1-byte third frame `0x55 0x0B` was observed exactly once at handshake time but its purpose is unclear and it is not necessary for control.

---

## 4. Status protocol (device → host, notify on `0xFFE1`)

Subscribe to `0xFFE1` notifications (write `0x01 0x00` to its CCCD, which BLE stacks do automatically when you call `start_notify` / `setNotifyValue:true`). The device pushes status frames roughly **every 2 seconds** while a connection is active.

**Frame format — 6 bytes:**

```
0x55 0x80 0x07 0x02 0x00 <battery_pct>
```

| Byte index | Value            | Meaning                              |
| ---------- | ---------------- | ------------------------------------ |
| 0          | `0x55`           | header                               |
| 1          | `0x80`           | "device-originated status" marker    |
| 2          | `0x07`           | length / type tag                    |
| 3          | `0x02`           | sub-type (battery report)            |
| 4          | `0x00`           | reserved                             |
| 5          | `0x00 .. 0x64`   | battery percent (decimal of the hex) |

`0x53` = 83% battery, `0x64` = 100%, etc. No other notification frame shapes were observed in the captured trace.

**Caveat observed during reverse engineering:** when connecting from `bleak` on macOS instead of the iOS official app, the FFE1 notify stream sometimes does not fire on its own. It is unclear whether this is a firmware-side client check or a quirk of the connection-parameter negotiation. If you do not receive notify frames, the toy is still controllable via writes to FFE2 — just treat battery as unknown.

---

## 5. Operational notes

* **Single-central limit.** A BLE peripheral can only be connected to one central at a time. If LightBlue, the iOS official app, or another debug client is connected, your code's `connect()` will fail with `BleakDeviceNotFoundError` (macOS Core Bluetooth) or equivalent. Disconnect everything else first.
* **Power-save dropout.** When idle and not connected for some seconds, the toy stops advertising. Press its power button briefly (or unplug-replug from charge) to make it advertise again. After it advertises, scanning + connecting should succeed.
* **Connection parameters.** No tuning needed; default BLE connection parameters are fine for the 1 Hz–10 Hz update rates the toy expects from the slider UI.
* **Latency.** Round trip from `write_gatt_char` issuance to physical motor change is in the 50–150 ms range over a clean BLE link.
* **Cumulative state on the device side.** The toy holds the last commanded `(suck, pat, vibe)` until you send a new frame. There is no per-motor command — you must always send a complete 6-byte frame containing the desired state of all three motors.

---

## 6. How this was reverse engineered (for AI consumers: this is provenance, not required reading)

1. Inspected GATT tree with LightBlue → identified FFE0 as control service and AE00 as OTA (Telink signature).
2. Triggered an iOS `sysdiagnose` while operating the official 杰士邦情趣星球 app to sweep each motor's slider 0 → 100 → 0.
3. Extracted `logs/Bluetooth/bluetoothd-hci-latest.pklg` from the sysdiagnose archive.
4. Wrote a Python parser (Apple PacketLogger format → ATT layer extraction) and dumped all `Write Request` frames per attribute handle.
5. All 102 control writes targeted handle `0x0006`, which the GATT discovery cross-reference shows is `0xFFE2`. Three temporal clusters appeared in the dump, each varying exactly one of bytes [3], [4], [5] from 0 → 0x64 → 0. The slider order in the UI (vibration / suction / thrust per the user's recall) maps to bytes (5, 3, 4) respectively. A live test from `bleak` confirmed `byte[3] == suction` (= `motor_A`).
6. Notification frames at handle `0x0003` (= `0xFFE1`) all matched `55 80 07 02 00 ?` with the last byte tracking the device's battery percent.

The full decoded trace is in `capture-decoded.txt`.

---

## 7. Reference implementation

`toy_reference.py` is a complete Python controller using [`bleak`](https://bleak.readthedocs.io/). It exposes:

```python
toy = JissbonToy()
await toy.connect()                     # scans for "Master Remote Vibrator"
await toy.set_suction(50)               # 0..100
await toy.set_thrust(30)                # 0..100  
await toy.set_vibration(80)             # 0..100
await toy.set_motors(suction=20, thrust=20, vibration=20)
await toy.stop()                        # all motors off
await toy.heartbeat()                   # 0x55 0x00 keepalive
battery_pct = await toy.get_battery()   # may be None if FFE1 doesn't push
await toy.disconnect()
```

The controller maintains the cumulative `(suck, pat, vibe)` state internally so `set_suction(50)` followed by `set_thrust(30)` results in both motors running.

---

## 8. AI prompt template

If you are showing this to an AI to write your own control code in a different language or stack:

> "Below is the reverse-engineered BLE protocol for a Jissbon Master Remote Vibrator toy. Using only the spec in `PROTOCOL.md`, write me a `<your_language_or_framework>` controller that connects to the toy by Local Name, exposes per-motor intensity setters (0..100 each), and includes a stop function. Treat `PROTOCOL.md` §1–§5 as authoritative and ignore §6 unless I ask. Reference the example frames in §3 to validate your encoding before returning the code."

---

## 9. Attribution

MIT 协议。**二传 / 翻译 / 移植请标注来源**：

> By Nyra (@NyraSeithhh) — github.com/NyraSeithhh/-BLE-
