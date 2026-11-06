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
    partitions: list[dict] = field(default_factory=list)

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
    """Query adapters, sound devices, disks and partitions in one PowerShell run."""
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
        "Select-Object Number,FriendlyName,BusType,Size,SerialNumber,Path);"
        "partitions=@(Get-Partition -ErrorAction SilentlyContinue | "
        "Select-Object DriveLetter,DiskNumber,DriveType)"
        "};"
        "$o|ConvertTo-Json -Depth 4 -Compress"
    )
    payload = _powershell_json(script, timeout=12)
    return SystemProbe(
        adapters=_as_list(payload.get("adapters")),
        sound=_as_list(payload.get("sound")),
        disks=_as_list(payload.get("disks")),
        partitions=_as_list(payload.get("partitions")),
    )


def _powershell_json(script: str, timeout: float) -> dict:
    """Run a PowerShell snippet that prints one JSON object; return ``{}`` on failure."""
    if sys.platform != "win32":
        return {}
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        payload = json.loads(completed.stdout.lstrip("\ufeff").strip() or "{}")
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}



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

_ENUM_CURRENT_SETTINGS = -1


class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wt.WCHAR * 32),
        ("dmSpecVersion", wt.WORD),
        ("dmDriverVersion", wt.WORD),
        ("dmSize", wt.WORD),
        ("dmDriverExtra", wt.WORD),
        ("dmFields", wt.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wt.WCHAR * 32),
        ("dmLogPixels", wt.WORD),
        ("dmBitsPerPel", wt.DWORD),
        ("dmPelsWidth", wt.DWORD),
        ("dmPelsHeight", wt.DWORD),
        ("dmDisplayFlags", wt.DWORD),
        ("dmDisplayFrequency", wt.DWORD),
        ("dmICMMethod", wt.DWORD),
        ("dmICMIntent", wt.DWORD),
        ("dmMediaType", wt.DWORD),
        ("dmDitherType", wt.DWORD),
        ("dmReserved1", wt.DWORD),
        ("dmReserved2", wt.DWORD),
        ("dmPanningWidth", wt.DWORD),
        ("dmPanningHeight", wt.DWORD),
    ]


@dataclass(slots=True)
class DisplayMode:
    """Current mode of one attached display adapter."""

    device: str
    name: str
    device_id: str
    width: int = 0
    height: int = 0
    refresh: int = 0
    bpp: int = 0
    primary: bool = False


@dataclass(slots=True)
class DisplayMetrics:
    monitors: list[str] = field(default_factory=list)
    primary_width: int = 0
    primary_height: int = 0
    modes: list[DisplayMode] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.monitors)


def _current_mode(device: str) -> _DEVMODEW | None:
    if sys.platform != "win32":
        return None
    mode = _DEVMODEW()
    mode.dmSize = ctypes.sizeof(mode)
    try:
        if ctypes.windll.user32.EnumDisplaySettingsW(device, _ENUM_CURRENT_SETTINGS, ctypes.byref(mode)):
            return mode
    except Exception:
        pass
    return None


def probe_displays() -> DisplayMetrics:
    monitors: list[str] = []
    modes: list[DisplayMode] = []
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
        primary = bool(flags & _DISPLAY_DEVICE_PRIMARY_DEVICE)
        adapter_name = adapter.DeviceName.strip()
        devmode = _current_mode(adapter_name)
        adapter_monitors = 0
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
                adapter_monitors += 1
                modes.append(
                    DisplayMode(
                        device=adapter_name,
                        name=label,
                        device_id=monitor.DeviceID.strip(),
                        width=int(devmode.dmPelsWidth) if devmode else 0,
                        height=int(devmode.dmPelsHeight) if devmode else 0,
                        refresh=int(devmode.dmDisplayFrequency) if devmode else 0,
                        bpp=int(devmode.dmBitsPerPel) if devmode else 0,
                        primary=primary,
                    )
                )
        if adapter_monitors == 0:
            monitors.append(adapter.DeviceString.strip() or "显示器")
            modes.append(
                DisplayMode(
                    device=adapter_name,
                    name=adapter.DeviceString.strip() or "显示器",
                    device_id="",
                    width=int(devmode.dmPelsWidth) if devmode else 0,
                    height=int(devmode.dmPelsHeight) if devmode else 0,
                    refresh=int(devmode.dmDisplayFrequency) if devmode else 0,
                    bpp=int(devmode.dmBitsPerPel) if devmode else 0,
                    primary=primary,
                )
            )

    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))
    return DisplayMetrics(monitors=monitors, primary_width=width, primary_height=height, modes=modes)


# ---------------------------------------------------------------------------
# HDMI link capability (EDID + current mode)
# ---------------------------------------------------------------------------

_DISPLAY_ENUM_ROOT = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"

# Max_FRL_Rate nibble -> total FRL bandwidth in Gbps (0 means FRL unsupported).
_FRL_RATES = {0: 0.0, 1: 9.0, 2: 18.0, 3: 24.0, 4: 32.0, 5: 40.0, 6: 48.0}

_HDMI_1_4_MAX_GBPS = 10.2
_HDMI_2_0_MAX_GBPS = 18.0


@dataclass(slots=True)
class EdidInfo:
    max_tmds_mhz: float = 0.0
    frl_gbps: float = 0.0
    has_hdmi: bool = False

    @property
    def max_gbps(self) -> float:
        tmds = self.max_tmds_mhz * 3 * 10 / 1000.0
        return max(self.frl_gbps, tmds)


@dataclass(slots=True)
class HdmiMetrics:
    connected: bool = False
    monitor: str = ""
    width: int = 0
    height: int = 0
    refresh: int = 0
    bpp: int = 0
    required_gbps: float = 0.0
    min_cable: str = ""
    sink_limit: str = ""
    edid_ok: bool = False


def read_display_edids() -> dict[str, bytes]:
    """Map ``UMC``-style monitor ids (e.g. ``DEL4098``) to their raw EDID."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return {}
    result: dict[str, bytes] = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_ENUM_ROOT)
    except OSError:
        return result
    with root:
        index = 0
        while True:
            try:
                model = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                model_key = winreg.OpenKey(root, model)
            except OSError:
                continue
            with model_key:
                sub_index = 0
                while True:
                    try:
                        instance = winreg.EnumKey(model_key, sub_index)
                    except OSError:
                        break
                    sub_index += 1
                    params = None
                    try:
                        params = winreg.OpenKey(model_key, instance + r"\Device Parameters")
                        data, _kind = winreg.QueryValueEx(params, "EDID")
                    except OSError:
                        continue
                    finally:
                        if params is not None:
                            params.Close()
                    if isinstance(data, (bytes, bytearray)) and len(data) >= 128:
                        result.setdefault(model.upper(), bytes(data))
                    break
    return result


def parse_edid(data: bytes) -> EdidInfo:
    """Read the HDMI VSDB / HDMI Forum VSDB capability from a raw EDID blob."""
    info = EdidInfo()
    if not data or len(data) < 128:
        return info
    offset = 128
    while offset + 128 <= len(data):
        block = data[offset : offset + 128]
        if block[0] != 0x02:  # CEA-861 extension
            break
        end = block[2] if 4 < block[2] < 128 else 128
        position = 4
        while position < end:
            header = block[position]
            tag = header >> 5
            length = header & 0x1F
            payload = block[position + 1 : position + 1 + length]
            if tag == 0x03 and len(payload) >= 5 and payload[0:3] == b"\x03\x0c\x00":
                # HDMI 1.4 VSDB (OUI 00-0C-03, stored little-endian).
                info.has_hdmi = True
                if len(payload) >= 7:
                    # Byte 6 is Max_TMDS_Clock in 5 MHz units.
                    info.max_tmds_mhz = max(info.max_tmds_mhz, payload[6] * 5)
            elif tag == 0x07 and len(payload) >= 4 and payload[0] == 0x79:
                # HDMI Forum VSDB: OUI C4-5D-D8 (little-endian), then version, rate, flags.
                if payload[1:4] == b"\xd8\x5d\xc4":
                    info.has_hdmi = True
                    if len(payload) >= 6:
                        info.max_tmds_mhz = max(info.max_tmds_mhz, payload[5] * 5)
                    if len(payload) >= 7:
                        rate = (payload[6] >> 4) & 0x0F
                        info.frl_gbps = max(info.frl_gbps, _FRL_RATES.get(rate, 0.0))
            position += 1 + length
        offset += 128
    return info


def required_bandwidth_gbps(width: int, height: int, refresh: int, bpp: int) -> float:
    """Approximate the link bandwidth a mode needs, including blanking overhead."""
    if not (width and height and refresh):
        return 0.0
    color_bits = bpp if 0 < bpp <= 24 else 24
    return width * height * refresh * color_bits * 1.06 / 1e9


def cable_for(gbps: float) -> str:
    if gbps <= 0:
        return ""
    if gbps <= _HDMI_1_4_MAX_GBPS:
        return "HDMI 1.4"
    if gbps <= _HDMI_2_0_MAX_GBPS:
        return "HDMI 2.0"
    return "HDMI 2.1"


def _sink_limit(info: EdidInfo) -> str:
    if info.frl_gbps >= 48:
        return "HDMI 2.1（FRL 48 Gbps）"
    if info.frl_gbps > 0:
        return f"HDMI 2.1（FRL {info.frl_gbps:.0f} Gbps）"
    if info.max_tmds_mhz >= 600:
        return "HDMI 2.0（18 Gbps）"
    if info.max_tmds_mhz > 0:
        return f"HDMI 1.4（{info.max_gbps:.1f} Gbps）"
    if info.has_hdmi:
        return "HDMI 设备（EDID 未标注带宽上限）"
    return ""


def probe_hdmi(displays: DisplayMetrics | None = None) -> HdmiMetrics:
    """Describe the external display's current mode and EDID capability."""
    metrics = displays if displays is not None else probe_displays()
    external = [mode for mode in metrics.modes if not mode.primary]
    if not external:
        return HdmiMetrics()
    mode = external[0]
    required = required_bandwidth_gbps(mode.width, mode.height, mode.refresh, mode.bpp)
    edid_key = mode.device_id.split("\\")[1].upper() if "\\" in mode.device_id else ""
    info = parse_edid(read_display_edids().get(edid_key, b""))
    return HdmiMetrics(
        connected=True,
        monitor=mode.name,
        width=mode.width,
        height=mode.height,
        refresh=mode.refresh,
        bpp=mode.bpp,
        required_gbps=required,
        min_cable=cable_for(required),
        sink_limit=_sink_limit(info),
        edid_ok=info.has_hdmi,
    )


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
# disk throughput
# ---------------------------------------------------------------------------

_DISK_SCRIPT = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$o=[ordered]@{"
    "disks=@(Get-Disk -ErrorAction SilentlyContinue | Select-Object Number,SerialNumber,Path);"
    "partitions=@(Get-Partition -ErrorAction SilentlyContinue | Select-Object DriveLetter,DiskNumber)"
    "};"
    "$o|ConvertTo-Json -Depth 4 -Compress"
)

_DISK_REFRESH_SECONDS = 10.0


class DiskRateSampler:
    """Per-physical-disk read/write throughput, matched to dock ports.

    USB mass-storage devices are mapped to ``PhysicalDriveN`` by the serial
    number in their PnP instance id, which also appears in ``Get-Disk``'s
    ``Path``.  Non-storage USB devices have no counter source on Windows
    short of USB ETW (which needs administrator rights).
    """

    def __init__(self, probe: SystemProbe) -> None:
        self._disks: list[dict] = list(probe.disks)
        self._partitions: list[dict] = list(probe.partitions)
        self._last: dict[str, tuple[int, int]] = {}
        self._last_time: float | None = None
        self._queried_at = time.monotonic()
        self._letter_map = self._build_letter_map()

    def _build_letter_map(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for partition in self._partitions:
            letter = str(partition.get("DriveLetter") or "").strip().upper()
            number = partition.get("DiskNumber")
            if letter and isinstance(number, int):
                mapping[letter] = f"PhysicalDrive{number}"
        return mapping

    def disk_for_letter(self, letter: str) -> str | None:
        return self._letter_map.get((letter or "").strip().upper())

    def disk_for_device(self, device) -> str | None:
        """Return ``PhysicalDriveN`` for a USB storage device instance."""
        serial = str(getattr(device, "instance_id", "") or "").rsplit("\\", 1)[-1].strip().lower()
        if not serial:
            return None
        disk = self._match_serial(serial)
        if disk is None and self._looks_like_storage(device):
            self._refresh()
            disk = self._match_serial(serial)
        return disk

    @staticmethod
    def _looks_like_storage(device) -> bool:
        """Avoid re-querying disks for HID/audio/network devices."""
        service = str(getattr(device, "service", "") or "").lower()
        name = str(getattr(device, "friendly_name", "") or getattr(device, "name", "") or "").lower()
        return "usbstor" in service or "disk" in name or "storage" in name

    def _match_serial(self, serial: str) -> str | None:
        for disk in self._disks:
            number = disk.get("Number")
            if not isinstance(number, int):
                continue
            path = str(disk.get("Path") or "").lower()
            disk_serial = str(disk.get("SerialNumber") or "").strip().lower()
            if (path and serial in path) or (disk_serial and disk_serial.split("&")[0] == serial):
                return f"PhysicalDrive{number}"
        return None

    def _refresh(self) -> None:
        """Re-read the disk table so a drive plugged after start-up is found."""
        now = time.monotonic()
        if now - self._queried_at < _DISK_REFRESH_SECONDS:
            return
        self._queried_at = now
        payload = _powershell_json(_DISK_SCRIPT, timeout=8)
        if not payload:
            return
        self._disks = _as_list(payload.get("disks"))
        self._partitions = _as_list(payload.get("partitions"))
        self._letter_map = self._build_letter_map()

    def sample(self) -> dict[str, tuple[float, float]]:
        """Return ``{disk: (read_bps, write_bps)}`` from the counter delta."""
        try:
            counters = psutil.disk_io_counters(perdisk=True)
        except Exception:
            return {}
        now = time.monotonic()
        rates: dict[str, tuple[float, float]] = {}
        if self._last_time is not None:
            elapsed = max(now - self._last_time, 1e-6)
            for name, counter in counters.items():
                previous = self._last.get(name)
                if previous is None:
                    continue
                rates[name] = (
                    max(0.0, (counter.read_bytes - previous[0]) / elapsed),
                    max(0.0, (counter.write_bytes - previous[1]) / elapsed),
                )
        self._last = {
            name: (counter.read_bytes, counter.write_bytes) for name, counter in counters.items()
        }
        self._last_time = now
        return rates


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
