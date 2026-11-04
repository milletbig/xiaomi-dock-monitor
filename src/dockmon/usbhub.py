"""Windows USB hub access via registry enumeration + user-mode USB IOCTLs.

Only the pieces needed by the dock monitor are implemented:

* enumerate external hubs (``Enum\\USB`` + ``Device Parameters\\SymbolicName``)
* read per-port connection status, device descriptor, negotiated speed
* read the driver key name of the device attached to a port

The IOCTL numbers and structures mirror the packed layout used by ``usbioctl.h``
(user-mode section is wrapped in ``pshpack1.h``).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import re
import winreg
from dataclasses import dataclass, field

GUID_DEVINTERFACE_USB_HUB = "{f18a0e88-c30c-11d0-8815-00a0c906bed8}"

_FILE_DEVICE_USB = 0x22


def _ctl_code(function: int) -> int:
    # METHOD_BUFFERED (0), FILE_ANY_ACCESS (0)
    return (_FILE_DEVICE_USB << 16) | (function << 2)


IOCTL_USB_GET_NODE_INFORMATION = _ctl_code(258)
IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX = _ctl_code(274)
IOCTL_USB_GET_NODE_CONNECTION_DRIVERKEY_NAME = _ctl_code(264)
IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2 = _ctl_code(279)

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_KEY_READ = 0x20019
_KEY_WOW64_64KEY = 0x0100

_USB_ROOT = r"SYSTEM\CurrentControlSet\Enum\USB"

# Connection status values (USB_CONNECTION_STATUS)
_CONNECTION_STATUS = {
    0: "未连接",
    1: "已连接",
    2: "枚举失败",
    3: "设备故障",
    4: "端口过流",
    5: "供电不足",
    6: "带宽不足",
    7: "Hub 嵌套过深",
    8: "旧版 Hub",
    9: "枚举中",
    10: "复位中",
}

_USB_VERSION = {
    0x0100: "1.1",
    0x0110: "1.1",
    0x0200: "2.0",
    0x0210: "2.1",
    0x0300: "3.0",
    0x0310: "3.1",
    0x0320: "3.2",
}


class _USB_HUB_DESCRIPTOR(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("bDescriptorLength", ctypes.c_ubyte),
        ("bDescriptorType", ctypes.c_ubyte),
        ("bNumberOfPorts", ctypes.c_ubyte),
        ("wHubCharacteristics", ctypes.c_ushort),
        ("bPowerOnToPowerGood", ctypes.c_ubyte),
        ("bHubControlCurrent", ctypes.c_ubyte),
        ("bRemoveAndPowerMask", ctypes.c_ubyte * 64),
    ]


class _USB_HUB_INFORMATION(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("HubDescriptor", _USB_HUB_DESCRIPTOR), ("HubIsBusPowered", ctypes.c_ubyte)]


class _USB_NODE_INFORMATION(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("NodeType", ctypes.c_ulong), ("HubInformation", _USB_HUB_INFORMATION)]


class _USB_DEVICE_DESCRIPTOR(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("bLength", ctypes.c_ubyte),
        ("bDescriptorType", ctypes.c_ubyte),
        ("bcdUSB", ctypes.c_ushort),
        ("bDeviceClass", ctypes.c_ubyte),
        ("bDeviceSubClass", ctypes.c_ubyte),
        ("bDeviceProtocol", ctypes.c_ubyte),
        ("bMaxPacketSize0", ctypes.c_ubyte),
        ("idVendor", ctypes.c_ushort),
        ("idProduct", ctypes.c_ushort),
        ("bcdDevice", ctypes.c_ushort),
        ("iManufacturer", ctypes.c_ubyte),
        ("iProduct", ctypes.c_ubyte),
        ("iSerialNumber", ctypes.c_ubyte),
        ("bNumConfigurations", ctypes.c_ubyte),
    ]


class _USB_PIPE_INFO(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("EndpointDescriptor", ctypes.c_ubyte * 7), ("ScheduleOffset", ctypes.c_ulong)]


class _USB_NODE_CONNECTION_INFORMATION_EX(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("ConnectionIndex", ctypes.c_ulong),
        ("DeviceDescriptor", _USB_DEVICE_DESCRIPTOR),
        ("CurrentConfigurationValue", ctypes.c_ubyte),
        ("Speed", ctypes.c_ubyte),
        ("DeviceIsHub", ctypes.c_ubyte),
        ("DeviceAddress", ctypes.c_ushort),
        ("NumberOfOpenPipes", ctypes.c_ulong),
        ("ConnectionStatus", ctypes.c_ulong),
        ("PipeList", _USB_PIPE_INFO * 32),
    ]


class _USB_NODE_CONNECTION_INFORMATION_EX_V2(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("ConnectionIndex", ctypes.c_ulong),
        ("Length", ctypes.c_ulong),
        ("SupportedUsbProtocols", ctypes.c_ulong),
        ("Flags", ctypes.c_ulong),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateFileW.restype = wt.HANDLE
_kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
_kernel32.DeviceIoControl.restype = wt.BOOL
_kernel32.DeviceIoControl.argtypes = [
    wt.HANDLE,
    wt.DWORD,
    wt.LPVOID,
    wt.DWORD,
    wt.LPVOID,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    wt.LPVOID,
]
_kernel32.CloseHandle.argtypes = [wt.HANDLE]


@dataclass(slots=True)
class PortInfo:
    number: int
    connected: bool
    status_code: int
    status_label: str
    vid: int = 0
    pid: int = 0
    usb_version: str = ""
    speed: str = ""
    speed_mbps: int = 0
    is_hub: bool = False
    driver_key: str = ""
    instance_id: str = ""

    @property
    def speed_group(self) -> str:
        if self.speed_mbps >= 5000:
            return "superspeed"
        if self.speed_mbps >= 480:
            return "highspeed"
        return "slow"


@dataclass(slots=True)
class HubInfo:
    instance_id: str
    description: str
    vid: int
    pid: int
    symbolic_name: str
    is_xiaomi: bool
    ports: list[PortInfo] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.description or self.instance_id

    @property
    def vid_pid(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"


def _read_value(key: winreg.HKEYType, name: str) -> str | None:
    try:
        return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def _device_desc(raw: str | None) -> str:
    if not raw:
        return ""
    return raw.split(";")[-1].strip()


def _parse_vid_pid(instance_id: str) -> tuple[int, int]:
    match = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", instance_id)
    if not match:
        return 0, 0
    return int(match.group(1), 16), int(match.group(2), 16)


def _open_hub(symbolic_name: str) -> int:
    path = symbolic_name
    if path.startswith("\\??\\"):
        path = "\\\\?\\" + path[4:]
    handle = _kernel32.CreateFileW(
        path,
        _GENERIC_READ | _GENERIC_WRITE,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        None,
        _OPEN_EXISTING,
        0,
        None,
    )
    if not handle or handle == wt.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _close_hub(handle: int) -> None:
    if handle:
        _kernel32.CloseHandle(handle)


def _ioctl(handle: int, code: int, input_buffer, input_size: int, output_buffer, output_size: int) -> bool:
    returned = wt.DWORD()
    ok = _kernel32.DeviceIoControl(
        handle,
        code,
        input_buffer,
        input_size,
        output_buffer,
        output_size,
        ctypes.byref(returned),
        None,
    )
    return bool(ok)


def enumerate_hubs() -> list[HubInfo]:
    """Return external (non-root) hubs currently known to Windows."""
    hubs: list[HubInfo] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _USB_ROOT, 0, _KEY_READ | _KEY_WOW64_64KEY)
    except OSError:
        return hubs

    with root:
        index = 0
        while True:
            try:
                vendor_key_name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            if not vendor_key_name.startswith("VID_"):
                continue
            try:
                vendor_key = winreg.OpenKey(root, vendor_key_name, 0, _KEY_READ | _KEY_WOW64_64KEY)
            except OSError:
                continue
            with vendor_key:
                sub_index = 0
                while True:
                    try:
                        instance = winreg.EnumKey(vendor_key, sub_index)
                    except OSError:
                        break
                    sub_index += 1
                    symbolic = None
                    try:
                        params = winreg.OpenKey(
                            vendor_key, instance + r"\Device Parameters", 0, _KEY_READ | _KEY_WOW64_64KEY
                        )
                    except OSError:
                        continue
                    with params:
                        symbolic = _read_value(params, "SymbolicName")
                    if not symbolic:
                        continue
                    try:
                        device_key = winreg.OpenKey(vendor_key, instance, 0, _KEY_READ | _KEY_WOW64_64KEY)
                    except OSError:
                        continue
                    with device_key:
                        service = _read_value(device_key, "Service") or ""
                        description = _device_desc(_read_value(device_key, "DeviceDesc"))
                    if not service.upper().startswith("USBHUB"):
                        continue
                    instance_id = f"USB\\{vendor_key_name}\\{instance}"
                    vid, pid = _parse_vid_pid(instance_id)
                    hubs.append(
                        HubInfo(
                            instance_id=instance_id,
                            description=description,
                            vid=vid,
                            pid=pid,
                            symbolic_name=symbolic,
                            is_xiaomi=vid == 0x2717,
                        )
                    )
    return hubs


def _speed_from_flags(speed_field: int, flags: int) -> tuple[str, int]:
    # Flags.IsOperatingAtSuperSpeedOrHigher / IsOperatingAtSuperSpeedPlusOrHigher
    if flags & 0x1:
        if flags & 0x4:
            return "10 Gbps+", 10000
        return "5 Gbps", 5000
    # EX.Speed values follow USB 2.0 speed ids
    return {
        0: ("1.5 Mbps", 1),
        1: ("12 Mbps", 12),
        2: ("480 Mbps", 480),
    }.get(speed_field, ("未知", 0))


def _query_port(handle: int, number: int) -> PortInfo:
    connection_index = ctypes.c_ulong(number)
    buffer = ctypes.create_string_buffer(4096)
    ok = _ioctl(
        handle,
        IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX,
        ctypes.byref(connection_index),
        ctypes.sizeof(connection_index),
        buffer,
        ctypes.sizeof(buffer),
    )
    if not ok:
        return PortInfo(number, False, -1, "读取失败")

    info = _USB_NODE_CONNECTION_INFORMATION_EX.from_buffer_copy(
        buffer.raw[: ctypes.sizeof(_USB_NODE_CONNECTION_INFORMATION_EX)]
    )
    status = int(info.ConnectionStatus)
    label = _CONNECTION_STATUS.get(status, f"状态 {status}")
    if status != 1:
        return PortInfo(number, False, status, label)

    descriptor = info.DeviceDescriptor
    flags = 0
    v2 = _USB_NODE_CONNECTION_INFORMATION_EX_V2()
    v2.ConnectionIndex = number
    v2.Length = ctypes.sizeof(v2)
    v2.SupportedUsbProtocols = 0x7  # Usb110 | Usb200 | Usb300
    if _ioctl(
        handle,
        IOCTL_USB_GET_NODE_CONNECTION_INFORMATION_EX_V2,
        ctypes.byref(v2),
        ctypes.sizeof(v2),
        ctypes.byref(v2),
        ctypes.sizeof(v2),
    ):
        flags = int(v2.Flags)

    speed_label, speed_mbps = _speed_from_flags(int(info.Speed), flags)
    return PortInfo(
        number=number,
        connected=True,
        status_code=status,
        status_label=label,
        vid=int(descriptor.idVendor),
        pid=int(descriptor.idProduct),
        usb_version=_USB_VERSION.get(int(descriptor.bcdUSB), f"0x{int(descriptor.bcdUSB):04X}"),
        speed=speed_label,
        speed_mbps=speed_mbps,
        is_hub=bool(info.DeviceIsHub),
    )


def _query_driver_key(handle: int, number: int) -> str:
    connection_index = ctypes.c_ulong(number)
    buffer = ctypes.create_string_buffer(2048)
    if not _ioctl(
        handle,
        IOCTL_USB_GET_NODE_CONNECTION_DRIVERKEY_NAME,
        ctypes.byref(connection_index),
        ctypes.sizeof(connection_index),
        buffer,
        ctypes.sizeof(buffer),
    ):
        return ""
    actual_length = ctypes.c_ulong.from_buffer(buffer, 4).value
    if actual_length <= 8:
        return ""
    raw = buffer.raw[8:actual_length]
    return raw.decode("utf-16-le", errors="ignore").strip("\x00")


def read_hub_ports(hub: HubInfo) -> list[PortInfo]:
    """Populate ``hub.ports`` from the live hub. Returns an empty list on failure."""
    hub.ports = []
    try:
        handle = _open_hub(hub.symbolic_name)
    except OSError:
        return []
    try:
        node = ctypes.create_string_buffer(1024)
        if not _ioctl(handle, IOCTL_USB_GET_NODE_INFORMATION, None, 0, node, ctypes.sizeof(node)):
            return []
        node_info = _USB_NODE_INFORMATION.from_buffer_copy(
            node.raw[: ctypes.sizeof(_USB_NODE_INFORMATION)]
        )
        port_count = int(node_info.HubInformation.HubDescriptor.bNumberOfPorts)
        ports: list[PortInfo] = []
        for number in range(1, port_count + 1):
            port = _query_port(handle, number)
            if port.connected:
                port.driver_key = _query_driver_key(handle, number)
            ports.append(port)
        hub.ports = ports
        return ports
    finally:
        _close_hub(handle)


def sample_hubs() -> list[HubInfo]:
    hubs = enumerate_hubs()
    for hub in hubs:
        read_hub_ports(hub)
    return hubs
