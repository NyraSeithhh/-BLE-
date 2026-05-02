"""
Jissbon "Master Remote Vibrator" BLE control library.

By Nyra (@NyraSeithhh) — github.com/NyraSeithhh/-BLE- — MIT.

Three independent motors (cumulative state — set_X(level) keeps the other two):
  - suction   (吮吸):   0..100   -> motor_A, byte[3] of frame
  - thrust    (拍打):   0..100   -> motor_B, byte[4] of frame
  - vibration (强震):   0..100   -> motor_C, byte[5] of frame

Wire protocol (write to FFE2, Write Request with response):
  Set intensities:   0x55 0x03 0x07 <suction> <thrust> <vibration>
  Heartbeat:         0x55 0x00

Notifications (from FFE1):
  Status frame:      0x55 0x80 0x07 0x02 0x00 <battery_pct_hex>
  pushed every ~2s while connected (under iOS official app at least).

DO NOT touch the AE00 service — it is the Telink OTA channel.
Writing to AE01 can brick the firmware.
"""
from __future__ import annotations
import asyncio
from typing import Optional
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice


DEVICE_NAME = "Master Remote Vibrator"
WRITE_UUID  = "0000ffe2-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"


def _clamp(v) -> int:
    return max(0, min(100, int(v)))


class JissbonToy:
    """Async controller for the Jissbon Master Remote Vibrator."""

    def __init__(self, scan_timeout: float = 8.0):
        self.scan_timeout = scan_timeout
        self.device: Optional[BLEDevice] = None
        self.client: Optional[BleakClient] = None
        self._suction = 0
        self._thrust = 0
        self._vibration = 0
        self._battery: Optional[int] = None

    @property
    def is_connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    @property
    def state(self) -> dict:
        return {
            "suction": self._suction,
            "thrust": self._thrust,
            "vibration": self._vibration,
            "battery": self._battery,
            "connected": self.is_connected,
        }

    async def find(self) -> Optional[BLEDevice]:
        for d in await BleakScanner.discover(timeout=self.scan_timeout):
            if d.name == DEVICE_NAME:
                self.device = d
                return d
        return None

    async def connect(self) -> None:
        if self.is_connected:
            return
        if self.device is None:
            await self.find()
        if self.device is None:
            raise RuntimeError(
                f"{DEVICE_NAME!r} not found. Powered on? In range? "
                f"Already connected to another client (LightBlue / iPhone app)?"
            )
        self.client = BleakClient(self.device.address)
        await self.client.connect()
        try:
            await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        except Exception:
            pass  # control still works without notify

    async def disconnect(self) -> None:
        if self.is_connected:
            try:
                await self.stop()
            except Exception:
                pass
            try:
                await self.client.stop_notify(NOTIFY_UUID)
            except Exception:
                pass
            await self.client.disconnect()
        self.client = None

    def _on_notify(self, _sender, data: bytearray) -> None:
        b = bytes(data)
        if len(b) == 6 and b[0] == 0x55 and b[1] == 0x80 and b[2] == 0x07:
            self._battery = b[5]

    async def _write(self, payload: bytes) -> None:
        if not self.is_connected:
            await self.connect()
        await self.client.write_gatt_char(WRITE_UUID, payload, response=True)

    async def _push_state(self) -> None:
        await self._write(bytes([0x55, 0x03, 0x07, self._suction, self._thrust, self._vibration]))

    async def set_motors(
        self,
        suction: Optional[int] = None,
        thrust: Optional[int] = None,
        vibration: Optional[int] = None,
    ) -> None:
        """Set one or more motors. Unspecified motors keep their current intensity."""
        if suction   is not None: self._suction   = _clamp(suction)
        if thrust    is not None: self._thrust    = _clamp(thrust)
        if vibration is not None: self._vibration = _clamp(vibration)
        await self._push_state()

    async def set_suction(self,   level: int) -> None: await self.set_motors(suction=level)
    async def set_thrust(self,    level: int) -> None: await self.set_motors(thrust=level)
    async def set_vibration(self, level: int) -> None: await self.set_motors(vibration=level)

    async def stop(self) -> None:
        """Stop all three motors."""
        self._suction = self._thrust = self._vibration = 0
        await self._push_state()

    async def heartbeat(self) -> None:
        """Send the 2-byte 0x55 0x00 heartbeat/keepalive packet."""
        await self._write(bytes([0x55, 0x00]))

    async def get_battery(self, wait_for_push: float = 2.5) -> Optional[int]:
        """Return last battery % from FFE1 notify stream. Wait briefly for a fresh push."""
        if self._battery is not None:
            return self._battery
        loop = asyncio.get_event_loop()
        deadline = loop.time() + wait_for_push
        while loop.time() < deadline:
            await asyncio.sleep(0.1)
            if self._battery is not None:
                return self._battery
        return None


# ---------- standalone smoke test (python3 toy.py) ----------

async def _demo():
    toy = JissbonToy()
    await toy.connect()
    print(f"connected: {toy.device.name} @ {toy.device.address}")
    for label, fn in [
        ("suction (吮吸) = 20",   lambda: toy.set_suction(20)),
        ("thrust  (拍打) = 20",   lambda: toy.set_thrust(20)),
        ("vibration (强震) = 20", lambda: toy.set_vibration(20)),
    ]:
        print(f"  >>> {label}, 2s")
        await fn()
        await asyncio.sleep(2.0)
        await toy.stop()
        await asyncio.sleep(0.5)
    print(f"battery: {await toy.get_battery()}")
    await toy.disconnect()
    print("disconnected")


if __name__ == "__main__":
    asyncio.run(_demo())
