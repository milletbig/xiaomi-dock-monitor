"""Assemble a snapshot of the dock for the CLI and the UI."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from . import pnp, usbhub
from .metrics import (
    DisplayMetrics,
    NetworkMetrics,
    NetworkSampler,
    PowerMetrics,
    RemovableDrive,
    SystemProbe,
    format_bytes_rate,
    format_link_speed,
    probe_displays,
    probe_removable_drives,
    probe_system,
    sample_power,
)

STATUS_CONNECTED = "connected"
STATUS_IDLE = "idle"
STATUS_UNKNOWN = "unknown"
STATUS_WARNING = "warning"

XIAOMI_VID = 0x2717


@dataclass(slots=True)
class PortView:
    id: str
    label: str
    kind: str
    status: str
    headline: str
    detail: str = ""
    speed: str = "—"
    power: str = "—"
    note: str = ""


@dataclass(slots=True)
class Snapshot:
    dock_present: bool
    dock_name: str
    hubs: list[usbhub.HubInfo] = field(default_factory=list)
    ports: list[PortView] = field(default_factory=list)
    network: NetworkMetrics = field(default_factory=NetworkMetrics)
    power: PowerMetrics = field(default_factory=PowerMetrics)
    displays: DisplayMetrics = field(default_factory=DisplayMetrics)
    removable: list[RemovableDrive] = field(default_factory=list)
    usb_devices: list[str] = field(default_factory=list)
    extra_usb: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


def _fallback_name(port: usbhub.PortInfo) -> str:
    return f"USB 设备 {port.vid:04X}:{port.pid:04X}"


def _external_devices(
    hubs: list[usbhub.HubInfo],
    devices: dict[str, pnp.UsbDevice],
    key_index: dict[str, pnp.UsbDevice],
) -> tuple[list[tuple[usbhub.HubInfo, usbhub.PortInfo, pnp.UsbDevice | None]], list[pnp.UsbDevice], list[pnp.UsbDevice], list[pnp.UsbDevice]]:
    external: list[tuple[usbhub.HubInfo, usbhub.PortInfo, pnp.UsbDevice | None]] = []
    lan: list[pnp.UsbDevice] = []
    storage: list[pnp.UsbDevice] = []
    audio: list[pnp.UsbDevice] = []

    for hub in hubs:
        for port in hub.ports:
            if not port.connected:
                continue
            dev = key_index.get(port.driver_key.lower()) if port.driver_key else None
            if dev is not None:
                port.instance_id = dev.instance_id
            if dev is None:
                port.instance_id = ""
            if port.is_hub:
                continue
            if dev is not None and dev.is_network:
                lan.append(dev)
                continue
            if dev is not None and dev.is_storage:
                storage.append(dev)
                continue
            if dev is not None and dev.is_audio:
                audio.append(dev)
                continue
            external.append((hub, port, dev))
    return external, lan, storage, audio


def _usb_views(
    external: list[tuple[usbhub.HubInfo, usbhub.PortInfo, pnp.UsbDevice | None]],
) -> tuple[list[PortView], int]:
    slots = [
        ("usb_a1", "USB-A 1"),
        ("usb_a2", "USB-A 2"),
        ("usb_a3", "USB-A 3"),
        ("usb_c", "USB-C 数据"),
    ]
    ordered = sorted(external, key=lambda item: (item[0].vid != XIAOMI_VID, item[0].instance_id, item[1].number))
    views: list[PortView] = []
    extra = max(0, len(ordered) - len(slots))
    for index, (slot_id, label) in enumerate(slots):
        if index >= len(ordered):
            views.append(
                PortView(
                    id=slot_id,
                    label=label,
                    kind="usb_c" if slot_id == "usb_c" else "usb_a",
                    status=STATUS_IDLE,
                    headline="空闲",
                    detail="未插入设备",
                    speed="—",
                    power="≤7.5W",
                )
            )
            continue
        _hub, port, dev = ordered[index]
        views.append(
            PortView(
                id=slot_id,
                label=label,
                kind="usb_c" if slot_id == "usb_c" else "usb_a",
                status=STATUS_CONNECTED,
                headline=dev.name if dev else _fallback_name(port),
                detail=f"{port.vid:04X}:{port.pid:04X} · {dev.class_label if dev else 'USB 设备'}",
                speed=port.speed or "未知",
                power="≤7.5W",
                note=f"USB {port.usb_version}" if port.usb_version else "",
            )
        )
    return views, extra


def _lan_view(network: NetworkMetrics, lan_devices: list[pnp.UsbDevice]) -> PortView:
    if network.present:
        if network.up:
            status = STATUS_CONNECTED
            headline = network.description or "USB 千兆网卡"
            detail = f"{network.status} · {format_link_speed(network.link_mbps)}"
            speed = format_link_speed(network.link_mbps)
            note = f"↓ {format_bytes_rate(network.rx_bps)}   ↑ {format_bytes_rate(network.tx_bps)}"
        else:
            status = STATUS_WARNING
            headline = network.description or "USB 千兆网卡"
            detail = "网卡已识别，网线未连接"
            speed = "—"
            note = "链路断开"
        return PortView("lan", "RJ45 网口", "lan", status, headline, detail, speed, "—", note)
    if lan_devices:
        return PortView(
            "lan", "RJ45 网口", "lan", STATUS_CONNECTED, lan_devices[0].name,
            "已识别 USB 网卡", "—", "—", "未获取到链路状态"
        )
    return PortView("lan", "RJ45 网口", "lan", STATUS_IDLE, "空闲", "未检测到 USB 网卡")


def _storage_views(
    removable: list[RemovableDrive], storage_devices: list[pnp.UsbDevice]
) -> list[PortView]:
    slots = [("sd", "SD 卡槽"), ("tf", "TF 卡槽")]
    views: list[PortView] = []
    for index, (slot_id, label) in enumerate(slots):
        if index < len(removable):
            drive = removable[index]
            views.append(
                PortView(
                    slot_id, label, slot_id, STATUS_CONNECTED,
                    f"{drive.letter}: {drive.label}", "可移动存储", "—", "—",
                    "由可移动盘推断（P3 增加读写速率）",
                )
            )
        else:
            detail = "读卡器已识别，未插卡" if storage_devices else "未检测到读卡器"
            views.append(PortView(slot_id, label, slot_id, STATUS_IDLE, "空闲", detail))
    return views


def _audio_view(audio_devices: list[pnp.UsbDevice], system_probe: SystemProbe) -> PortView:
    usb_sound = system_probe.usb_sound_devices
    if usb_sound:
        name = str(usb_sound[0].get("Name", "USB 音频设备"))
        return PortView("audio", "3.5mm 音频", "audio", STATUS_CONNECTED, name, "USB 音频设备", "—", "—")
    if audio_devices:
        return PortView(
            "audio", "3.5mm 音频", "audio", STATUS_CONNECTED,
            audio_devices[0].name, "已识别音频设备", "—", "—"
        )
    return PortView("audio", "3.5mm 音频", "audio", STATUS_IDLE, "空闲", "未检测到 USB 音频")


def _hdmi_view(displays: DisplayMetrics) -> PortView:
    if displays.count >= 2:
        return PortView(
            "hdmi", "HDMI 输出", "hdmi", STATUS_CONNECTED,
            f"外接显示 {displays.count - 1} 台",
            f"主屏 {displays.primary_width}×{displays.primary_height}",
            "—", "—", "DP Alt Mode，非 USB 设备",
        )
    detail = (
        f"主屏 {displays.primary_width}×{displays.primary_height}"
        if displays.primary_width
        else "未获取显示模式"
    )
    return PortView(
        "hdmi", "HDMI 输出", "hdmi", STATUS_IDLE,
        "未检测到外接显示", detail, "—", "—", "系统级推断，仅作参考",
    )


def _pd_view(power: PowerMetrics) -> PortView:
    if power.plugged:
        return PortView(
            "pd", "PD 供电", "pd", STATUS_CONNECTED,
            "电脑已接通电源",
            "规格 100W 输入 / 90W 输出",
            "100W", "—", "实际功率不可测，仅显示规格",
        )
    return PortView(
        "pd", "PD 供电", "pd", STATUS_IDLE,
        "未接通电源",
        "PD 口仅供电，不传数据",
        "100W", "—", "实际功率不可测，仅显示规格",
    )


def build_snapshot(
    system_probe: SystemProbe | None = None,
    network_sampler: NetworkSampler | None = None,
) -> Snapshot:
    probe = system_probe if system_probe is not None else probe_system()
    sampler = network_sampler if network_sampler is not None else NetworkSampler(probe)

    hubs = usbhub.sample_hubs()
    devices = pnp.enumerate_usb_devices()
    key_index = pnp.driver_key_index(devices)

    dock_hubs = [hub for hub in hubs if hub.is_xiaomi]
    dock_present = bool(dock_hubs)
    if dock_present:
        dock_name = f"小米 USB-C 扩展坞（VID {XIAOMI_VID:04X}，{len(dock_hubs)} 个 Hub）"
    else:
        dock_name = f"未检测到小米扩展坞（VID {XIAOMI_VID:04X}）"
        dock_hubs = []

    external, lan_devices, storage_devices, audio_devices = _external_devices(
        dock_hubs, devices, key_index
    )
    usb_views, extra = _usb_views(external)

    network = sampler.sample()
    power = sample_power()
    displays = probe_displays()
    removable = probe_removable_drives()

    ports = [
        *usb_views,
        _pd_view(power),
        _hdmi_view(displays),
        _lan_view(network, lan_devices),
        *_storage_views(removable, storage_devices),
        _audio_view(audio_devices, probe),
    ]

    return Snapshot(
        dock_present=dock_present,
        dock_name=dock_name,
        hubs=dock_hubs,
        ports=ports,
        network=network,
        power=power,
        displays=displays,
        removable=removable,
        usb_devices=[(dev.name if dev else _fallback_name(port)) for _hub, port, dev in external],
        extra_usb=extra,
    )
