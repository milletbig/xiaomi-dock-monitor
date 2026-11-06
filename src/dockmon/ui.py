"""Tkinter UI for the dock monitor.

The layout mirrors the physical dock instead of using a flat card grid:
rear connectors (PD / Type-C data / HDMI) on the top row, the three
USB-A ports on the front row, and the side connectors (LAN / SD / TF /
audio) on the bottom row.  Clicking any port fills the detail panel.
"""

from __future__ import annotations

import ctypes
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

from . import __version__
from .dock import STATUS_CONNECTED, STATUS_IDLE, STATUS_UNKNOWN, STATUS_WARNING, Snapshot, build_snapshot
from .metrics import NetworkSampler, SystemProbe, format_bytes_rate, format_link_speed, probe_system

# palette
BG = "#0b0f14"
PANEL = "#111823"
CARD = "#151d29"
CARD_HOVER = "#1a2434"
BODY = "#121a24"
BODY_EDGE = "#2b3a4e"
CARD_BORDER = "#263244"
CARD_BORDER_ACTIVE = "#2f6f57"
CARD_BORDER_WARN = "#7a5b23"
CARD_BORDER_HOVER = "#3c4d66"
TEXT = "#e9eff7"
MUTED = "#8290a4"
SUBTLE = "#5f6c80"
ACCENT = "#4ea1ff"
GREEN = "#3ddc97"
AMBER = "#f2b84b"
RED = "#ff6b6b"
BADGE_BG = "#1e2937"

STATUS_STYLE = {
    STATUS_CONNECTED: (GREEN, CARD_BORDER_ACTIVE, "● 已连接"),
    STATUS_WARNING: (AMBER, CARD_BORDER_WARN, "● 异常"),
    STATUS_IDLE: (SUBTLE, CARD_BORDER, "○ 空闲"),
    STATUS_UNKNOWN: (MUTED, CARD_BORDER, "○ 未知"),
}

# (zone title, port ids, tile height)
ZONES = (
    ("后侧接口 · REAR", ("pd", "usb_c", "hdmi"), 142),
    ("正面接口 · FRONT", ("usb_a1", "usb_a2", "usb_a3"), 142),
    ("侧边接口 · SIDE", ("lan", "sd", "tf", "audio"), 152),
)

HEADER_HEIGHT = 64
STATS_HEIGHT = 68
BOTTOM_HEIGHT = 204


def _set_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _font_family(root: tk.Tk) -> str:
    families = set(tkfont.families(root))
    for name in ("Microsoft YaHei UI", "微软雅黑", "Segoe UI", "Arial"):
        if name in families:
            return name
    return "TkDefaultFont"


def _round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs) -> int:
    """Draw a rounded rectangle on a Canvas (Tk has no native one)."""
    r = radius
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1 + r, x2, y2 - r,
        x2 - r, y2, x1 + r, y2, x1, y2 - r, x1, y1 + r,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def _draw_port_icon(canvas: tk.Canvas, kind: str, color: str) -> None:
    """Draw a small connector glyph inside the tile icon canvas."""
    canvas.delete("all")
    width, height = 46, 34
    cx, cy = width / 2, height / 2

    if kind in ("usb_c", "pd"):
        _round_rect(canvas, cx - 17, cy - 7, cx + 17, cy + 7, 7, outline=color, width=2)
        _round_rect(canvas, cx - 11, cy - 3, cx + 11, cy + 3, 3, fill=color, outline="")
    elif kind == "usb_a":
        canvas.create_rectangle(cx - 15, cy - 7, cx + 15, cy + 7, outline=color, width=2)
        canvas.create_rectangle(cx - 10, cy - 2, cx + 10, cy + 2, fill=color, outline="")
        canvas.create_line(cx - 12, cy - 4, cx - 12, cy + 4, fill=color)
        canvas.create_line(cx + 12, cy - 4, cx + 12, cy + 4, fill=color)
    elif kind == "hdmi":
        canvas.create_polygon(cx - 15, cy - 6, cx + 15, cy - 6, cx + 12, cy + 7, cx - 12, cy + 7, outline=color, width=2)
        canvas.create_line(cx - 9, cy - 2, cx + 9, cy - 2, fill=color)
    elif kind == "lan":
        canvas.create_rectangle(cx - 13, cy - 8, cx + 13, cy + 8, outline=color, width=2)
        for step in range(4):
            x = cx - 9 + step * 6
            canvas.create_line(x, cy - 8, x, cy - 3, fill=color)
            canvas.create_line(x, cy + 3, x, cy + 8, fill=color)
    elif kind in ("sd", "tf"):
        inset = 13 if kind == "sd" else 10
        canvas.create_polygon(
            cx - inset, cy - 10, cx + inset - 4, cy - 10, cx + inset, cy - 6,
            cx + inset, cy + 10, cx - inset, cy + 10, outline=color, width=2, fill="",
        )
        canvas.create_line(cx - inset + 3, cy - 6, cx - inset + 3, cy + 6, fill=color)
    elif kind == "audio":
        canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline=color, width=2)
        canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=color, outline="")
        canvas.create_arc(cx - 14, cy - 14, cx + 14, cy + 14, start=200, extent=140, style="arc", outline=color)
    else:
        canvas.create_oval(cx - 10, cy - 10, cx + 10, cy + 10, outline=color, width=2)


class PortTile:
    """One clickable physical connector with hover and selection states."""

    def __init__(self, parent: tk.Frame, port_id: str, height: int, wrap: int, on_select, fonts) -> None:
        self.port_id = port_id
        self.on_select = on_select
        self.port = None
        self.selected = False
        self._hover = 0
        self._base_border = CARD_BORDER

        self.frame = tk.Frame(parent, bg=CARD, highlightbackground=CARD_BORDER, highlightthickness=1)
        self.frame.configure(height=height)
        self.frame.pack_propagate(False)

        header = tk.Frame(self.frame, bg=CARD)
        header.pack(fill="x", padx=10, pady=(5, 0))
        self.badge = tk.Label(header, text="", bg=BADGE_BG, fg=ACCENT, font=fonts["badge"], padx=5, pady=1)
        self.badge.pack(side="left")
        self.name = tk.Label(header, text="", bg=CARD, fg=TEXT, font=fonts["label"], anchor="w")
        self.name.pack(side="left", padx=(5, 0))
        self.status = tk.Label(header, text="○ 空闲", bg=CARD, fg=SUBTLE, font=fonts["badge"], anchor="e")
        self.status.pack(side="right")

        main = tk.Frame(self.frame, bg=CARD)
        main.pack(fill="x", padx=10, pady=(2, 0))
        self.icon = tk.Canvas(main, width=46, height=34, bg=CARD, highlightthickness=0)
        self.icon.pack(side="left", padx=(0, 6))
        text_col = tk.Frame(main, bg=CARD)
        text_col.pack(side="left", fill="x", expand=True)
        self.headline = tk.Label(
            text_col, text="空闲", bg=CARD, fg=TEXT, font=fonts["device"], anchor="w",
            justify="left", wraplength=wrap,
        )
        self.headline.pack(fill="x")
        self.detail = tk.Label(text_col, text="未插入设备", bg=CARD, fg=MUTED, font=fonts["detail"], anchor="w")
        self.detail.pack(fill="x")

        footer = tk.Frame(self.frame, bg=CARD)
        footer.pack(fill="x", padx=10, pady=(2, 0))
        self.speed = tk.Label(footer, text="—", bg=BADGE_BG, fg=ACCENT, font=fonts["badge"], padx=5, pady=1)
        self.speed.pack(side="left")
        self.power = tk.Label(footer, text="—", bg=BADGE_BG, fg=MUTED, font=fonts["badge"], padx=5, pady=1)
        self.power.pack(side="left", padx=(5, 0))

        self._bind_all(self.frame)

    def _bind_all(self, widget: tk.Widget) -> None:
        widget.bind("<Enter>", self._enter)
        widget.bind("<Leave>", self._leave)
        widget.bind("<Button-1>", lambda _event: self.on_select(self.port_id))
        for child in widget.winfo_children():
            self._bind_all(child)

    def _enter(self, _event=None) -> None:
        self._hover += 1
        self._paint_border()

    def _leave(self, _event=None) -> None:
        self._hover = max(0, self._hover - 1)
        self._paint_border()

    def _paint_border(self) -> None:
        if self.selected:
            border = ACCENT
        elif self._hover:
            border = CARD_BORDER_HOVER
        else:
            border = self._base_border
        self.frame.configure(highlightbackground=border)

    def update(self, port, connected: bool, selected: bool) -> None:
        self.port = port
        self.selected = selected
        color, border, status_text = STATUS_STYLE.get(port.status, STATUS_STYLE[STATUS_UNKNOWN])
        self._base_border = border
        self.badge.configure(
            text=str(port.number) if port.number else "",
            bg=BADGE_BG if port.number else CARD,
        )
        self.name.configure(text=port.label.split(" · ", 1)[-1] if port.number else port.label)
        self.status.configure(text=status_text, fg=color)
        self.headline.configure(text=port.headline)
        self.detail.configure(text=port.detail or " ")
        self.speed.configure(text=port.speed)
        self.power.configure(text=port.power)
        _draw_port_icon(self.icon, port.kind, ACCENT if connected else SUBTLE)
        self._paint_border()


class DockMonitorApp:
    def __init__(self) -> None:
        _set_dpi_awareness()
        self.root = tk.Tk()
        self.root.title("小米十合一扩展坞 · 实时监控")
        self.root.configure(bg=BG)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1240, screen_w - 40)
        height = min(1000, screen_h - 80)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(1080, width), min(680, height))

        self.family = _font_family(self.root)
        self.fonts = {
            "title": (self.family, 16, "bold"),
            "sub": (self.family, 9),
            "section": (self.family, 10, "bold"),
            "label": (self.family, 9),
            "device": (self.family, 10, "bold"),
            "detail": (self.family, 8),
            "badge": (self.family, 8),
            "value": (self.family, 16, "bold"),
            "small": (self.family, 8),
            "detail_title": (self.family, 12, "bold"),
        }

        self.probe: SystemProbe | None = None
        self.sampler: NetworkSampler | None = None
        self.snapshot: Snapshot | None = None
        self.history: list[float] = []
        self.paused = False
        self.closed = False
        self.selected_id: str | None = None
        self._busy = threading.Lock()
        self._queue: queue.Queue[Snapshot] = queue.Queue()
        self._last_update = 0.0

        self.tiles: dict[str, PortTile] = {}
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._bootstrap)

    # ------------------------------------------------------------------ UI
    def _build_layout(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_stats()

        body = tk.Frame(self.root, bg=BG)
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(4, 2))
        self._build_device(body)

        toolbar = tk.Frame(self.root, bg=BG, height=30)
        toolbar.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 2))
        toolbar.pack_propagate(False)

        self.unmapped_note = tk.Label(
            toolbar, text="· 未标定 USB 端口：无", bg=BG, fg=SUBTLE,
            font=self.fonts["small"], anchor="w",
        )
        self.unmapped_note.pack(side="left")
        self.refresh_button = tk.Button(
            toolbar, text="立即刷新", command=self._request_refresh, bg=BADGE_BG, fg=TEXT,
            font=self.fonts["badge"], relief="flat", bd=0, padx=10, pady=2,
            activebackground=CARD_BORDER, activeforeground=TEXT, cursor="hand2",
        )
        self.refresh_button.pack(side="right")
        self.pause_button = tk.Button(
            toolbar, text="暂停", command=self._toggle_pause, bg=BADGE_BG, fg=TEXT,
            font=self.fonts["badge"], relief="flat", bd=0, padx=10, pady=2,
            activebackground=CARD_BORDER, activeforeground=TEXT, cursor="hand2",
        )
        self.pause_button.pack(side="right", padx=(0, 6))
        tk.Label(
            toolbar, text=f"v{__version__}", bg=BG, fg=SUBTLE, font=self.fonts["small"]
        ).pack(side="right", padx=(0, 10))

        bottom = tk.Frame(self.root, bg=BG, height=BOTTOM_HEIGHT)
        bottom.grid(row=4, column=0, sticky="nsew", padx=16, pady=(0, 12))
        bottom.grid_propagate(False)
        bottom.grid_columnconfigure(0, weight=3, uniform="bottom")
        bottom.grid_columnconfigure(1, weight=2, uniform="bottom")
        bottom.grid_rowconfigure(0, weight=1)
        self._build_detail_panel(bottom)
        self._build_network_panel(bottom)

    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=PANEL, height=HEADER_HEIGHT)
        header.grid(row=0, column=0, sticky="nsew")
        header.grid_propagate(False)

        left = tk.Frame(header, bg=PANEL)
        left.pack(side="left", padx=20, pady=8)
        tk.Label(left, text="小米十合一扩展坞", bg=PANEL, fg=TEXT, font=self.fonts["title"], anchor="w").pack(anchor="w")
        tk.Label(
            left,
            text="USB-C Dock Monitor · 物理接口 1–5 已标定 / 设备 / 协商速率 / 网口吞吐 / 供电规格",
            bg=PANEL, fg=MUTED, font=self.fonts["sub"], anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=PANEL)
        right.pack(side="right", padx=20, pady=8)
        self.dock_state = tk.Label(right, text="正在检测扩展坞…", bg=PANEL, fg=AMBER, font=(self.family, 10))
        self.dock_state.pack(anchor="e")
        self.updated = tk.Label(right, text="", bg=PANEL, fg=SUBTLE, font=self.fonts["small"])
        self.updated.pack(anchor="e", pady=(3, 0))

    def _build_stats(self) -> None:
        stats = tk.Frame(self.root, bg=BG, height=STATS_HEIGHT)
        stats.grid(row=1, column=0, sticky="nsew", padx=16, pady=(8, 2))
        stats.grid_propagate(False)
        for column in range(4):
            stats.grid_columnconfigure(column, weight=1, uniform="stat")
        self.stat_values: dict[str, tk.Label] = {}
        self.stat_subs: dict[str, tk.Label] = {}
        tiles = [
            ("bus", "USB 上行总线", "5 Gbps", "3×USB-A + 1×USB-C 共享"),
            ("devices", "已连接 USB 设备", "0", "端口状态实时刷新"),
            ("power", "PD 供电规格", "100W", "输出至电脑最高 90W"),
            ("network", "扩展坞网口", "—", "等待检测"),
        ]
        for column, (key, title, value, sub) in enumerate(tiles):
            tile = tk.Frame(stats, bg=CARD, highlightbackground=CARD_BORDER, highlightthickness=1)
            tile.grid(row=0, column=column, sticky="nsew", padx=5, pady=2)
            tk.Label(tile, text=title, bg=CARD, fg=MUTED, font=self.fonts["label"], anchor="w").pack(
                anchor="w", padx=12, pady=(4, 0)
            )
            value_label = tk.Label(tile, text=value, bg=CARD, fg=TEXT, font=self.fonts["value"], anchor="w")
            value_label.pack(anchor="w", padx=12)
            sub_label = tk.Label(tile, text=sub, bg=CARD, fg=SUBTLE, font=self.fonts["small"], anchor="w")
            sub_label.pack(anchor="w", padx=12, pady=(0, 4))
            self.stat_values[key] = value_label
            self.stat_subs[key] = sub_label

    def _build_device(self, parent: tk.Frame) -> None:
        shell = tk.Frame(parent, bg=BODY, highlightbackground=BODY_EDGE, highlightthickness=2)
        shell.pack(fill="both", expand=True)

        cable = tk.Frame(shell, bg=BODY)
        cable.pack(fill="x", padx=18, pady=(6, 0))
        tk.Label(
            cable, text="◄ 上行 Type-C 连接电脑", bg=BODY, fg=MUTED, font=self.fonts["label"], anchor="w"
        ).pack(side="left")
        tk.Label(
            cable, text="10-in-1 · 4 个 USB 数据口共享 5 Gbps", bg=BODY, fg=SUBTLE,
            font=self.fonts["small"], anchor="e",
        ).pack(side="right")

        for title, port_ids, height in ZONES:
            self._build_zone(shell, title, port_ids, height)

    def _build_zone(self, parent: tk.Frame, title: str, port_ids: tuple[str, ...], height: int) -> None:
        zone = tk.Frame(parent, bg=BODY)
        zone.pack(fill="x", padx=18, pady=(6, 0))

        heading = tk.Frame(zone, bg=BODY)
        heading.pack(fill="x")
        tk.Label(heading, text=title, bg=BODY, fg=SUBTLE, font=self.fonts["small"], anchor="w").pack(side="left")
        line = tk.Frame(heading, bg=BODY_EDGE, height=1)
        line.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=(6, 0))

        row = tk.Frame(zone, bg=BODY)
        row.pack(fill="x", pady=(4, 0))
        for column, port_id in enumerate(port_ids):
            row.grid_columnconfigure(column, weight=1, uniform=f"zone-{title}")
            wrap = 250 if len(port_ids) == 3 else 190
            tile = PortTile(row, port_id, height, wrap, self._select_port, self.fonts)
            tile.frame.grid(row=0, column=column, sticky="nsew", padx=4)
            self.tiles[port_id] = tile

    def _build_detail_panel(self, parent: tk.Frame) -> None:
        panel = tk.Frame(parent, bg=CARD, highlightbackground=CARD_BORDER, highlightthickness=1)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=2)

        top = tk.Frame(panel, bg=CARD)
        top.pack(fill="x", padx=14, pady=(7, 0))
        tk.Label(top, text="接口详情", bg=CARD, fg=TEXT, font=self.fonts["section"]).pack(side="left")
        self.detail_status = tk.Label(top, text="点击上方接口", bg=CARD, fg=SUBTLE, font=self.fonts["badge"])
        self.detail_status.pack(side="right")

        self.detail_name = tk.Label(
            panel, text="—", bg=CARD, fg=TEXT, font=self.fonts["detail_title"], anchor="w"
        )
        self.detail_name.pack(fill="x", padx=14, pady=(3, 0))

        self.detail_device = tk.Label(
            panel, text="点击上方任一接口查看详细信息", bg=CARD, fg=TEXT,
            font=(self.family, 11, "bold"), anchor="w", justify="left", wraplength=560,
        )
        self.detail_device.pack(fill="x", padx=14, pady=(2, 0))

        self.detail_meta = tk.Label(
            panel, text=" ", bg=CARD, fg=MUTED, font=self.fonts["detail"], anchor="w"
        )
        self.detail_meta.pack(fill="x", padx=14, pady=(1, 0))

        badges = tk.Frame(panel, bg=CARD)
        badges.pack(fill="x", padx=14, pady=(4, 0))
        self.detail_speed = tk.Label(badges, text="—", bg=BADGE_BG, fg=ACCENT, font=self.fonts["badge"], padx=6, pady=2)
        self.detail_speed.pack(side="left")
        self.detail_power = tk.Label(badges, text="—", bg=BADGE_BG, fg=MUTED, font=self.fonts["badge"], padx=6, pady=2)
        self.detail_power.pack(side="left", padx=(6, 0))

        self.detail_map = tk.Label(
            panel, text="", bg=CARD, fg=SUBTLE, font=self.fonts["small"], anchor="w",
            justify="left", wraplength=600,
        )
        self.detail_map.pack(fill="x", padx=14, pady=(4, 0))

    def _build_network_panel(self, parent: tk.Frame) -> None:
        panel = tk.Frame(parent, bg=CARD, highlightbackground=CARD_BORDER, highlightthickness=1)
        panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=2)

        top = tk.Frame(panel, bg=CARD)
        top.pack(fill="x", padx=14, pady=(9, 0))
        tk.Label(top, text="扩展坞网口实时流量", bg=CARD, fg=TEXT, font=self.fonts["section"]).pack(side="left")
        self.net_status = tk.Label(top, text="等待采样…", bg=CARD, fg=SUBTLE, font=self.fonts["badge"])
        self.net_status.pack(side="right")

        values = tk.Frame(panel, bg=CARD)
        values.pack(fill="x", padx=14, pady=(6, 0))
        self.net_rx = tk.Label(values, text="↓ 0 B/s", bg=CARD, fg=GREEN, font=(self.family, 14, "bold"))
        self.net_rx.pack(side="left")
        self.net_tx = tk.Label(values, text="↑ 0 B/s", bg=CARD, fg=ACCENT, font=(self.family, 14, "bold"))
        self.net_tx.pack(side="left", padx=(20, 0))
        self.net_link = tk.Label(values, text="—", bg=CARD, fg=MUTED, font=self.fonts["label"])
        self.net_link.pack(side="right")

        self.spark = tk.Canvas(panel, bg=CARD, highlightthickness=0, height=40)
        self.spark.pack(fill="x", padx=14, pady=(6, 4))

        self.net_detail = tk.Label(panel, text="", bg=CARD, fg=SUBTLE, font=self.fonts["small"], anchor="w")
        self.net_detail.pack(fill="x", padx=14, pady=(0, 8))

    # ------------------------------------------------------------- sampling
    def _bootstrap(self) -> None:
        threading.Thread(target=self._initial_sample, daemon=True).start()
        self.root.after(150, self._poll_queue)
        self.root.after(1000, self._tick)

    def _initial_sample(self) -> None:
        try:
            probe = probe_system()
            sampler = NetworkSampler(probe)
            snapshot = build_snapshot(probe, sampler)
            self.probe = probe
            self.sampler = sampler
            self._queue.put(snapshot)
        except Exception as exc:  # keep the window usable even if a probe fails
            self._queue.put(Snapshot(dock_present=False, dock_name=f"初始化失败：{exc}"))

    def _request_refresh(self) -> None:
        if not self.probe or not self.sampler:
            return
        if not self._busy.acquire(blocking=False):
            return
        threading.Thread(target=self._sample_once, daemon=True).start()

    def _sample_once(self) -> None:
        try:
            snapshot = build_snapshot(self.probe, self.sampler)
            self._queue.put(snapshot)
        except Exception:
            pass
        finally:
            self._busy.release()

    def _poll_queue(self) -> None:
        try:
            while True:
                snapshot = self._queue.get_nowait()
                self._apply(snapshot)
        except queue.Empty:
            pass
        if not self.closed:
            self.root.after(150, self._poll_queue)

    def _tick(self) -> None:
        if self.closed:
            return
        if not self.paused:
            self._request_refresh()
        self.root.after(900, self._tick)

    # ------------------------------------------------------------------ UI
    def _apply(self, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        self._last_update = time.time()
        dock_color = GREEN if snapshot.dock_present else AMBER
        self.dock_state.configure(
            text=("● 已检测到扩展坞" if snapshot.dock_present else "● 未检测到小米扩展坞"), fg=dock_color
        )
        self.updated.configure(text=f"更新于 {time.strftime('%H:%M:%S')} · 自动刷新 0.9s")

        connected_usb = sum(
            1 for port in snapshot.ports if port.kind in ("usb_a", "usb_c") and port.status == STATUS_CONNECTED
        )
        self.stat_values["devices"].configure(text=str(connected_usb + (snapshot.extra_usb or 0)))
        if not snapshot.dock_present:
            device_hint = "未检测到扩展坞"
        elif snapshot.unmapped_usb:
            device_hint = "未标定端口会在下方提示"
        else:
            device_hint = "按物理接口 1–5 映射"
        self.stat_subs["devices"].configure(text=device_hint)
        self._update_unmapped_note(snapshot)

        network = snapshot.network
        if network.present:
            value = format_link_speed(network.link_mbps) if network.up else "未连接"
            self.stat_values["network"].configure(text=value)
            self.stat_subs["network"].configure(text=network.description or network.name)
        else:
            self.stat_values["network"].configure(text="—")
            self.stat_subs["network"].configure(text="未检测到 USB 网卡")

        power = snapshot.power
        self.stat_values["power"].configure(text="100W" if power.plugged else "90W")
        if power.plugged:
            self.stat_subs["power"].configure(text="规格 100W 输入 / 90W 输出")
        else:
            self.stat_subs["power"].configure(text="未接通电源 · 规格 100W")

        for port in snapshot.ports:
            tile = self.tiles.get(port.id)
            if not tile:
                continue
            tile.update(port, port.status == STATUS_CONNECTED, port.id == self.selected_id)

        if self.selected_id is None or not any(port.id == self.selected_id for port in snapshot.ports):
            first = next((port.id for port in snapshot.ports if port.status == STATUS_CONNECTED), "usb_c")
            self.selected_id = first
            for port in snapshot.ports:
                tile = self.tiles.get(port.id)
                if tile:
                    tile.update(port, port.status == STATUS_CONNECTED, port.id == self.selected_id)
        self._update_detail(self.selected_id)

        self._update_network_panel(snapshot)

    def _update_detail(self, port_id: str | None) -> None:
        if not self.snapshot or not port_id:
            return
        port = next((item for item in self.snapshot.ports if item.id == port_id), None)
        if not port:
            return
        color, _border, status_text = STATUS_STYLE.get(port.status, STATUS_STYLE[STATUS_UNKNOWN])
        self.detail_name.configure(text=port.label)
        self.detail_status.configure(text=status_text, fg=color)
        self.detail_device.configure(text=port.headline)
        self.detail_meta.configure(text=port.detail or "—")
        self.detail_speed.configure(text=port.speed)
        self.detail_power.configure(text=port.power)
        if port.note:
            prefix = "Hub 映射" if ("Hub" in port.note or "已标定" in port.note) else "说明"
            self.detail_map.configure(text=f"{prefix}：{port.note}")
        else:
            self.detail_map.configure(text="Hub 映射：—")

    def _select_port(self, port_id: str) -> None:
        self.selected_id = port_id
        if self.snapshot:
            for port in self.snapshot.ports:
                tile = self.tiles.get(port.id)
                if tile:
                    tile.update(port, port.status == STATUS_CONNECTED, port.id == port_id)
        self._update_detail(port_id)

    def _update_unmapped_note(self, snapshot: Snapshot) -> None:
        parts = [
            (
                f"{item.hub_role} {item.hub_level} #{item.hub_port} · {item.device}"
                f"（{item.vid_pid}，{item.speed}）"
            )
            for item in snapshot.unmapped_usb
        ]
        parts.extend(snapshot.port_map_warnings)
        if parts:
            self.unmapped_note.configure(text="· 未标定 / 配置警告：" + "；".join(parts), fg=AMBER)
        else:
            self.unmapped_note.configure(text="· 未标定 USB 端口：无", fg=SUBTLE)

    def _update_network_panel(self, snapshot: Snapshot) -> None:
        network = snapshot.network
        if network.present:
            self.net_status.configure(
                text=("● 链路已连接" if network.up else "● 网线未连接"),
                fg=GREEN if network.up else AMBER,
            )
            self.net_rx.configure(text=f"↓ {format_bytes_rate(network.rx_bps)}")
            self.net_tx.configure(text=f"↑ {format_bytes_rate(network.tx_bps)}")
            self.net_link.configure(text=format_link_speed(network.link_mbps) if network.up else "—")
            self.net_detail.configure(
                text=f"{network.name} · {network.description} · 合计 {format_bytes_rate(network.total_bps)}"
            )
        else:
            self.net_status.configure(text="● 未检测到扩展坞网卡", fg=SUBTLE)
            self.net_rx.configure(text="↓ 0 B/s")
            self.net_tx.configure(text="↑ 0 B/s")
            self.net_link.configure(text="—")
            self.net_detail.configure(text="扩展坞未接入，或网卡驱动未就绪")

        self.history.append(network.total_bps)
        self.history = self.history[-60:]
        self._draw_sparkline()

    def _draw_sparkline(self) -> None:
        canvas = self.spark
        canvas.delete("all")
        width = max(canvas.winfo_width(), 200)
        height = max(canvas.winfo_height(), 40)
        canvas.create_line(0, height - 1, width, height - 1, fill="#243044")
        values = self.history[-60:]
        if len(values) < 2:
            canvas.create_text(width // 2, height // 2, text="等待流量样本…", fill=SUBTLE, font=self.fonts["small"])
            return
        peak = max(values + [1.0])
        step = width / max(len(values) - 1, 1)
        points: list[float] = []
        for index, value in enumerate(values):
            x = index * step
            y = height - 4 - (value / peak) * (height - 10)
            points.extend((x, y))
        canvas.create_line(*points, fill=ACCENT, width=2, smooth=True)
        canvas.create_text(
            6, 6, anchor="nw", text=f"峰值 {format_bytes_rate(peak)}", fill=SUBTLE, font=self.fonts["small"]
        )

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_button.configure(text="继续" if self.paused else "暂停")
        if not self.paused:
            self._request_refresh()

    def _on_close(self) -> None:
        self.closed = True
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def run_ui() -> int:
    DockMonitorApp().run()
    return 0
