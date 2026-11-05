"""Physical port catalogue and hub-port mapping for the Xiaomi dock.

The dock exposes two top-level hubs (a SuperSpeed hub and its USB2
companion), and each top-level hub has a nested four-port hub.  A physical
connector is not necessarily wired to the same hub or port number: USB-A 1
uses top-level SS port 2, USB-A 3 uses nested SS port 3, and Type-C uses
top-level USB2 port 4.  Enumerating the tree in order is therefore not
enough to label the four data ports.

Physical numbering used by this project:

=====  ==============================  ====================================
number physical connector              current evidence
=====  ==============================  ====================================
1      供电 Type-C (PD, power only)      PD state, not in the USB tree
2      Type-C 数据                     watch charger on top USB2 port 4
3      USB-A 1                         HDD on top SS port 2
4      USB-A 2                         HDD on nested SS port 4; 16GB drive on nested USB2 port 4
5      USB-A 3                         HDD on nested SS port 3; 16GB drive on nested USB2 port 3
=====  ==============================  ====================================

The table is loaded from ``config/port_map.json`` when available.  The
built-in defaults mirror the file so the tool still works from a source
archive without the config directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from .usbhub import HubInfo

ROLE_SS = "ss"
ROLE_USB2 = "usb2"

LEVEL_TOP = "top"
LEVEL_NESTED = "nested"

ROLE_LABEL = {ROLE_SS: "SS", ROLE_USB2: "USB2"}
ROLE_LONG_LABEL = {ROLE_SS: "SuperSpeed", ROLE_USB2: "USB2"}
LEVEL_LABEL = {LEVEL_TOP: "Hub", LEVEL_NESTED: "子Hub"}

_XIAOMI_SS_PIDS = {0x50BA}
_XIAOMI_USB2_PIDS = {0x50BB}
_EXPECTED_IDS = ("pd", "usb_c", "usb_a1", "usb_a2", "usb_a3")


@dataclass(frozen=True, slots=True)
class HubPortRef:
    """A downstream port on a top-level or nested dock hub."""

    role: str
    port: int
    level: str = LEVEL_TOP

    @property
    def label(self) -> str:
        role = ROLE_LABEL.get(self.role, self.role.upper())
        level = LEVEL_LABEL.get(self.level, "Hub")
        return f"{role} {level} #{self.port}"


@dataclass(frozen=True, slots=True)
class PhysicalPort:
    number: int
    id: str
    label: str
    connector: str
    hub_ports: tuple[HubPortRef, ...] = ()
    evidence: str = ""

    @property
    def is_data(self) -> bool:
        return self.id != "pd"

    @property
    def display_label(self) -> str:
        return f"{self.number} · {self.label}"

    @property
    def is_calibrated(self) -> bool:
        return bool(self.hub_ports)


DEFAULT_PORTS: tuple[PhysicalPort, ...] = (
    PhysicalPort(
        number=1,
        id="pd",
        label="供电 Type-C",
        connector="usb_c",
        hub_ports=(),
        evidence="PD 100W 输入，仅供电，不经过 USB Hub",
    ),
    PhysicalPort(
        number=2,
        id="usb_c",
        label="Type-C 数据",
        connector="usb_c",
        hub_ports=(HubPortRef(ROLE_USB2, 4),),
        evidence="实测：手表充电器（05AC:0503）与中兴手机（19D2:0307）接此口均出现在顶层 USB2 Hub #4；USB3 侧待标定",
    ),
    PhysicalPort(
        number=3,
        id="usb_a1",
        label="USB-A 1",
        connector="usb_a",
        hub_ports=(HubPortRef(ROLE_SS, 2),),
        evidence="实测：移动硬盘（152D:0583）接此口，出现在 SuperSpeed Hub 端口 2；USB2 侧待标定",
    ),
    PhysicalPort(
        number=4,
        id="usb_a2",
        label="USB-A 2",
        connector="usb_a",
        hub_ports=(
            HubPortRef(ROLE_SS, 4, level=LEVEL_NESTED),
            HubPortRef(ROLE_USB2, 4, level=LEVEL_NESTED),
        ),
        evidence="USB3 侧：移动硬盘在 4 口时测得 SS 子Hub #4；USB2 侧：16GB U 盘（1609:3A04）测得 USB2 子Hub #4",
    ),
    PhysicalPort(
        number=5,
        id="usb_a3",
        label="USB-A 3",
        connector="usb_a",
        hub_ports=(
            HubPortRef(ROLE_SS, 3, level=LEVEL_NESTED),
            HubPortRef(ROLE_USB2, 3, level=LEVEL_NESTED),
        ),
        evidence="SS 侧：硬盘在 5 口时测得 SS 子Hub #3；USB2 侧：16GB U 盘（1609:3A04）测得 USB2 子Hub #3",
    ),
)


@dataclass(frozen=True, slots=True)
class PortMap:
    ports: tuple[PhysicalPort, ...]
    source: str = "built-in defaults"
    warnings: tuple[str, ...] = ()

    @property
    def by_id(self) -> dict[str, PhysicalPort]:
        return {port.id: port for port in self.ports}

    @property
    def data_ports(self) -> tuple[PhysicalPort, ...]:
        """Physical data ports in display order (Type-C, then USB-A 1..3)."""
        return tuple(port for port in self.ports if port.is_data)

    def lookup(self, hub: HubInfo, port_number: int) -> PhysicalPort | None:
        """Return the physical connector for a connected hub port, if known."""
        role = detect_hub_role(hub)
        level = hub_level(hub)
        for physical in self.ports:
            for ref in physical.hub_ports:
                if ref.role == role and ref.level == level and ref.port == port_number:
                    return physical
        return None

    def refs_for(self, physical_id: str) -> tuple[HubPortRef, ...]:
        physical = self.by_id.get(physical_id)
        return physical.hub_ports if physical else ()


DEFAULT_PORT_MAP = PortMap(DEFAULT_PORTS)


def detect_hub_role(hub: HubInfo) -> str:
    """Classify a Xiaomi hub as ``ss`` or ``usb2``.

    The product PIDs are the primary signal; the description is a fallback
    for future revisions of the same dock.
    """
    description = (hub.description or "").lower()
    if "superspeed" in description:
        return ROLE_SS
    if hub.pid in _XIAOMI_SS_PIDS:
        return ROLE_SS
    if hub.pid in _XIAOMI_USB2_PIDS:
        return ROLE_USB2
    if "usb 2" in description or "usb2" in description:
        return ROLE_USB2
    return ROLE_USB2


def hub_level(hub: HubInfo) -> str:
    """Classify a dock hub as ``top`` or ``nested``.

    Windows instance IDs use ``6&...`` for root-hub children and ``7&...``
    (or deeper) for hubs attached to the dock's top-level hubs.  Both
    levels can carry external connectors; the nested SS hub also hosts the
    internal LAN controller, so devices are filtered by class before mapping.
    """
    stem = (hub.instance_id or "").rsplit("\\", 1)[-1]
    return LEVEL_TOP if stem.startswith("6&") else LEVEL_NESTED


def is_top_level_hub(hub: HubInfo) -> bool:
    """True for hubs connected directly to a root hub."""
    return hub_level(hub) == LEVEL_TOP


def default_config_path() -> Path:
    candidates = (
        Path(__file__).resolve().parents[2] / "config" / "port_map.json",
        Path.cwd() / "config" / "port_map.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _parse_ports(payload: dict) -> tuple[PhysicalPort, ...]:
    rows = payload.get("ports")
    if not isinstance(rows, list):
        raise ValueError("ports 必须是数组")

    ports: list[PhysicalPort] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("ports 中的每一项必须是对象")
        number = int(row["number"])
        physical_id = str(row["id"])
        label = str(row["label"])
        connector = str(row.get("connector", "usb_a"))
        evidence = str(row.get("evidence", ""))

        refs: list[HubPortRef] = []
        for raw_ref in row.get("hub_ports", []) or []:
            if not isinstance(raw_ref, dict):
                raise ValueError(f"{physical_id}: hub_ports 中的每一项必须是对象")
            role = str(raw_ref["role"])
            port = int(raw_ref["port"])
            level = str(raw_ref.get("level", LEVEL_TOP))
            if role not in ROLE_LABEL:
                raise ValueError(f"{physical_id}: 未知 hub role {role!r}")
            if level not in LEVEL_LABEL:
                raise ValueError(f"{physical_id}: 未知 hub level {level!r}")
            if not 1 <= port <= 255:
                raise ValueError(f"{physical_id}: 端口号超出范围 {port}")
            refs.append(HubPortRef(role, port, level))

        ports.append(
            PhysicalPort(
                number=number,
                id=physical_id,
                label=label,
                connector=connector,
                hub_ports=tuple(refs),
                evidence=evidence,
            )
        )

    if len(ports) != len(_EXPECTED_IDS) or {port.id for port in ports} != set(_EXPECTED_IDS):
        raise ValueError(f"ports 必须恰好包含 {', '.join(_EXPECTED_IDS)}")
    if sorted(port.number for port in ports) != list(range(1, len(_EXPECTED_IDS) + 1)):
        raise ValueError("物理接口编号必须从 1 连续到 5")

    return tuple(sorted(ports, key=lambda port: port.number))


def _mapping_warnings(ports: tuple[PhysicalPort, ...]) -> tuple[str, ...]:
    seen: dict[tuple[str, str, int], str] = {}
    warnings: list[str] = []
    for physical in ports:
        for ref in physical.hub_ports:
            key = (ref.role, ref.level, ref.port)
            if key in seen:
                warnings.append(
                    f"{ref.label} 同时映射到 {seen[key]} 与 {physical.id}，请检查 port_map.json"
                )
            else:
                seen[key] = physical.id
    return tuple(warnings)


def load_port_map(path: str | Path | None = None) -> PortMap:
    """Load ``config/port_map.json``; fall back to the measured defaults."""
    target = Path(path) if path is not None else default_config_path()
    if not target.is_file():
        return DEFAULT_PORT_MAP

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("配置根节点必须是对象")
        ports = _parse_ports(payload)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return replace(
            DEFAULT_PORT_MAP,
            warnings=(f"端口映射配置读取失败，已使用内置默认值：{exc}",),
        )

    return PortMap(
        ports=ports,
        source=str(target),
        warnings=_mapping_warnings(ports),
    )
