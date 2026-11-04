"""Read USB device metadata from the Windows PnP registry (``Enum\\USB``).

The driver key returned by the hub IOCTL is matched against the ``Driver``
value stored for each USB device instance. That gives us a reliable
port -> device-instance mapping, even for devices that carry a serial number.
"""

from __future__ import annotations

import re
import winreg
from dataclasses import dataclass

_USB_ROOT = r"SYSTEM\CurrentControlSet\Enum\USB"
_KEY_READ = 0x20019
_KEY_WOW64_64KEY = 0x0100

CLASS_GUID = {
    "usb": "{36fc9e60-c465-11cf-8056-444553540000}",
    "net": "{4d36e972-e325-11ce-bfc1-08002be10318}",
    "disk": "{4d36e967-e325-11ce-bfc1-08002be10318}",
    "media": "{4d36e96c-e325-11ce-bfc1-08002be10318}",
    "hid": "{745a17a0-74d3-11d0-b6fe-00a0c90f57da}",
    "image": "{6bdd1fc6-810f-11d0-bec7-08002be2092f}",
    "printer": "{4d36e979-e325-11ce-bfc1-08002be10318}",
    "ports": "{4d36e978-e325-11ce-bfc1-08002be10318}",
    "audio_endpoint": "{c166523c-fe0c-4a94-a586-f1a80cfbbf3e}",
    "camera": "{ca3e7ab9-b4c3-4ae6-8251-579ef933890f}",
}

CLASS_LABEL = {
    "usb": "USB",
    "net": "网络",
    "disk": "存储",
    "media": "音频",
    "hid": "HID",
    "image": "图像",
    "printer": "打印",
    "ports": "串并口",
    "audio_endpoint": "音频端点",
    "camera": "摄像头",
}


@dataclass(slots=True)
class UsbDevice:
    instance_id: str
    vid: int
    pid: int
    name: str
    friendly_name: str
    driver_key: str
    class_guid: str
    service: str
    location: str
    container_id: str = ""

    @property
    def vid_pid(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"

    @property
    def class_kind(self) -> str:
        guid = self.class_guid.lower()
        for kind, known in CLASS_GUID.items():
            if guid == known.lower():
                return kind
        return "usb"

    @property
    def class_label(self) -> str:
        return CLASS_LABEL.get(self.class_kind, self.class_kind.upper())

    @property
    def is_network(self) -> bool:
        return self.class_kind == "net"

    @property
    def is_storage(self) -> bool:
        return self.class_kind == "disk"

    @property
    def is_audio(self) -> bool:
        return self.class_kind in ("media", "audio_endpoint")

    @property
    def is_hid(self) -> bool:
        return self.class_kind == "hid"


def _read_string(key, name: str) -> str:
    try:
        return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def _device_desc(raw: str) -> str:
    return raw.split(";")[-1].strip() if raw else ""


def _parse_vid_pid(instance_id: str) -> tuple[int, int]:
    match = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", instance_id)
    if not match:
        return 0, 0
    return int(match.group(1), 16), int(match.group(2), 16)


def enumerate_usb_devices() -> dict[str, UsbDevice]:
    devices: dict[str, UsbDevice] = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _USB_ROOT, 0, _KEY_READ | _KEY_WOW64_64KEY)
    except OSError:
        return devices

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
                    try:
                        device_key = winreg.OpenKey(
                            vendor_key, instance, 0, _KEY_READ | _KEY_WOW64_64KEY
                        )
                    except OSError:
                        continue
                    with device_key:
                        instance_id = f"USB\\{vendor_key_name}\\{instance}"
                        vid, pid = _parse_vid_pid(instance_id)
                        desc = _device_desc(_read_string(device_key, "DeviceDesc"))
                        friendly = _read_string(device_key, "FriendlyName").strip()
                        driver_key = _read_string(device_key, "Driver").strip()
                        class_guid = _read_string(device_key, "ClassGUID").strip()
                        service = _read_string(device_key, "Service").strip()
                        location = _read_string(device_key, "LocationInformation").strip()
                        container = _read_string(device_key, "ContainerID").strip()
                    devices[instance_id] = UsbDevice(
                        instance_id=instance_id,
                        vid=vid,
                        pid=pid,
                        name=friendly or desc or f"{vid:04X}:{pid:04X}",
                        friendly_name=friendly,
                        driver_key=driver_key,
                        class_guid=class_guid,
                        service=service,
                        location=location,
                        container_id=container,
                    )
    return devices


def driver_key_index(devices: dict[str, UsbDevice]) -> dict[str, UsbDevice]:
    """Map lower-cased driver key (e.g. ``{...}\\0010``) to the device."""
    index: dict[str, UsbDevice] = {}
    for device in devices.values():
        if device.driver_key:
            index[device.driver_key.lower()] = device
    return index
