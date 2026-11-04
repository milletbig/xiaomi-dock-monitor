"""System-level samplers used by the dock snapshot.

Everything here is best-effort: if a probe fails, the UI simply shows
"unknown" instead of crashing.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field

import psutil

_CREATE_NO_WINDOW = 0x08000000


# ---------------------------------------------------------------------------
# one-shot probes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SystemProbe:
    adapters: list[dict] = field(default_factory=list)
    sound: list[dict] = field(default_factory=list)
    disks: list[dict] = field(default_factory=list)

    @property
    def usb_sound_devices(self) -> list[dict]:
        return [
            item
            for item in self.sound
            if "usb" in str(item.get("Name", "")).lower()
        ]


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def probe_system() -> SystemProbe:
    """Query adapters, sound devices and disks in a single PowerShell run."""
    if sys.platform != "win32":
        return SystemProbe()

    script = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$o=[ordered]@{"
        "adapters=@(Get-NetAdapter -ErrorAction SilentlyContinue | "
        "Select-Object Name,InterfaceDescription,Status,LinkSpeed);"
        "sound=@(Get-CimInstance -ClassName Win32_SoundDevice -ErrorAction SilentlyContinue | "
        "Select-Object Name,Status,Manufacturer);"
        "disks=@(Get-Disk -ErrorAction SilentlyContinue | "
        "Select-Object Number,FriendlyName,BusType,Size)"
        "};"
        "$o|ConvertTo-Json -Depth 4 -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        payload = json.loads(completed.stdout.lstrip("\ufeff").strip() or "{}")
    except Exception:
        return SystemProbe()

    return SystemProbe(
        adapters=_as_list(payload.get("adapters")),
        sound=_as_list(payload.get("sound")),
        disks=_as_list(payload.get("disks")),
    )


# ---------------------------------------------------------------------------
# network
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NetworkMetrics:
    present: bool = False
    name: str = ""
    description: str = ""
    status: str = ""
    up: bool = False
    link_mbps: float = 0
    rx_bps: float = 0
    tx_bps: float = 0

    @property
    def total_bps(self) -> float:
        return self.rx_bps + self.tx_bps


def _parse_link_speed(text: str) -> float:
    match = re.search(r"([\d.]+)\s*(Gbps|Mbps|Kbps)", text or "", re.IGNORECASE)
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit == "gbps":
        return value * 1000
    if unit == "kbps":
        return value / 1000
    return value


class NetworkSampler:
    """Find the dock's USB NIC once, then sample counters with psutil."""

    def __init__(self, probe: SystemProbe) -> None:
        self.adapter: dict | None = None
        for item in probe.adapters:
            description = str(item.get("InterfaceDescription", "")).lower()
            if "usb" in description and any(
                token in description for token in ("gbe", "ethernet", "lan", "rtl8153", "network")
            ):
                self.adapter = item
                break
        self._last_counter = None
        self._last_time: float | None = None

    @property
    def found(self) -> bool:
        return self.adapter is not None

    def sample(self) -> NetworkMetrics:
        if not self.adapter:
            return NetworkMetrics()

        name = str(self.adapter.get("Name", ""))
        try:
            stats = psutil.net_if_stats().get(name)
            counters = psutil.net_io_counters(pernic=True).get(name)
        except Exception:
            stats, counters = None, None

        now = time.monotonic()
        rx_bps = tx_bps = 0.0
        if counters is not None and self._last_counter is not None and self._last_time is not None:
            elapsed = max(now - self._last_time, 1e-6)
            rx_bps = max(0.0, (counters.bytes_recv - self._last_counter.bytes_recv) / elapsed)
            tx_bps = max(0.0, (counters.bytes_sent - self._last_counter.bytes_sent) / elapsed)
        self._last_counter = counters
        self._last_time = now

        link_mbps = float(getattr(stats, "speed", 0) or 0)
        if not link_mbps:
            link_mbps = _parse_link_speed(str(self.adapter.get("LinkSpeed", "")))

        return NetworkMetrics(
            present=True,
            name=name,
            description=str(self.adapter.get("InterfaceDescription", "")),
            status=str(self.adapter.get("Status", "")),
            up=bool(getattr(stats, "isup", False)) if stats else False,
            link_mbps=link_mbps,
            rx_bps=rx_bps,
            tx_bps=tx_bps,
        )


# ---------------------------------------------------------------------------
# power
# ---------------------------------------------------------------------------


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


@dataclass(slots=True)
class PowerMetrics:
    available: bool = False
    ac_online: bool = False
    plugged: bool = False
    percent: int | None = None
    battery: bool = False


def sample_power() -> PowerMetrics:
    ac_online = False
    try:
        status = _SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            ac_online = status.ACLineStatus == 1
    except Exception:
        pass

    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery is None:
        return PowerMetrics(available=False, ac_online=ac_online, plugged=ac_online, battery=False)
    return PowerMetrics(
        available=True,
        ac_online=ac_online,
        plugged=bool(battery.power_plugged),
        percent=int(battery.percent) if battery.percent is not None else None,
        battery=True,
    )


# ---------------------------------------------------------------------------
# displays
# ---------------------------------------------------------------------------


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("DeviceName", wt.WCHAR * 32),
        ("DeviceString", wt.WCHAR * 128),
        ("StateFlags", wt.DWORD),
        ("DeviceID", wt.WCHAR * 128),
        ("DeviceKey", wt.WCHAR * 128),
    ]


_DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
_DISPLAY_DEVICE_PRIMARY_DEVICE = 0x4
_DISPLAY_DEVICE_MIRRORING_DRIVER = 0x8


@dataclass(slots=True)
class DisplayMetrics:
    monitors: list[str] = field(default_factory=list)
    primary_width: int = 0
    primary_height: int = 0

    @property
    def count(self) -> int:
        return len(self.monitors)


def probe_displays() -> DisplayMetrics:
    monitors: list[str] = []
    if sys.platform != "win32":
        return DisplayMetrics()
    user32 = ctypes.windll.user32
    adapter_index = 0
    while True:
        adapter = _DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(adapter)
        if not user32.EnumDisplayDevicesW(None, adapter_index, ctypes.byref(adapter), 0):
            break
        adapter_index += 1
        flags = int(adapter.StateFlags)
        if not (flags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP) or (flags & _DISPLAY_DEVICE_MIRRORING_DRIVER):
            continue
        monitor_index = 0
        while True:
            monitor = _DISPLAY_DEVICEW()
            monitor.cb = ctypes.sizeof(monitor)
            if not user32.EnumDisplayDevicesW(adapter.DeviceName, monitor_index, ctypes.byref(monitor), 0):
                break
            monitor_index += 1
            if int(monitor.StateFlags) & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
                label = monitor.DeviceString.strip() or monitor.DeviceID.strip() or "显示器"
                monitors.append(label)
        if not monitors and monitor_index == 0:
            monitors.append(adapter.DeviceString.strip() or "显示器")

    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))
    return DisplayMetrics(monitors=monitors, primary_width=width, primary_height=height)


# ---------------------------------------------------------------------------
# removable storage
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RemovableDrive:
    letter: str
    label: str


def probe_removable_drives() -> list[RemovableDrive]:
    drives: list[RemovableDrive] = []
    if sys.platform != "win32":
        return drives
    kernel32 = ctypes.windll.kernel32
    bitmask = kernel32.GetLogicalDrives()
    for index in range(26):
        if not bitmask & (1 << index):
            continue
        letter = chr(ord("A") + index)
        root = f"{letter}:\\"
        if kernel32.GetDriveTypeW(root) != 2:  # DRIVE_REMOVABLE
            continue
        label_buffer = ctypes.create_unicode_buffer(261)
        kernel32.GetVolumeInformationW(
            root, label_buffer, 261, None, None, None, None, 0
        )
        drives.append(RemovableDrive(letter=letter, label=label_buffer.value or "可移动磁盘"))
    return drives


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------


def format_bytes_rate(value: float) -> str:
    value = max(0.0, float(value))
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if value < 1024 or unit == "GB/s":
            return f"{value:.0f} {unit}" if unit == "B/s" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB/s"


def format_link_speed(mbps: float) -> str:
    if mbps <= 0:
        return "未连接"
    if mbps >= 1000:
        value = mbps / 1000
        return f"{value:.0f} Gbps" if value >= 10 or value == int(value) else f"{value:.1f} Gbps"
    if mbps >= 1:
        return f"{mbps:.0f} Mbps"
    return f"{mbps * 1000:.0f} Kbps"
