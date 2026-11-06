"""Tkinter UI for the dock monitor.

The device area is a top view of the dock (正面朝上):

* short edge left  -> upstream USB-C cable
* short edge right -> RJ45
* long edge top    -> PD Type-C, Type-C data, USB-A 1/2/3 (left to right)
* long edge bottom -> HDMI, SD/TF combo slot (TF above SD), 3.5mm audio

Ports live on a single Canvas so the physical arrangement stays exact;
clicking a port fills the detail panel below.
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
BODY = "#121a24"
BODY_EDGE = "#2b3a4e"
CARD_BORDER = "#263244"
CARD_BORDER_ACTIVE = "#2f6f57"
CARD_BORDER_WARN = "#7a5b23"
CARD_BORDER_HOVER = "#4a5f80"
TEXT = "#e9eff7"
MUTED = "#8290a4"
SUBTLE = "#5f6c80"
ACCENT = "#4ea1ff"
GREEN = "#3ddc97"
AMBER = "#f2b84b"
RED = "#ff6b6b"
BADGE_BG = "#1e2937"
SOCKET_EDGE = "#3a4a60"
SOCKET_FILL = "#0d141d"

STATUS_STYLE = {
    STATUS_CONNECTED: (GREEN, CARD_BORDER_ACTIVE, "● 已连接"),
    STATUS_WARNING: (AMBER, CARD_BORDER_WARN, "● 异常"),
    STATUS_IDLE: (SUBTLE, CARD_BORDER, "○ 空闲"),
    STATUS_UNKNOWN: (MUTED, CARD_BORDER, "○ 未知"),
}

TOP_PORTS = ("pd", "usb_c", "usb_a1", "usb_a2", "usb_a3")
BOTTOM_PORTS = ("hdmi", "audio")

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
    r = radius
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1 + r, x2, y2 - r,
        x2 - r, y2, x1 + r, y2, x1, y2 - r, x1, y1 + r,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


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
            "slot_title": (self.family, 9, "bold"),
            "slot_meta": (self.family, 8),
        }

        self.probe: SystemProbe | None = None
        self.sampler: NetworkSampler | None = None
        self.snapshot: Snapshot | None = None
        self.history: list[float] = []
        self.paused = False
        self.closed = False
        self.selected_id: str | None = None
        self.hover_id: str | None = None
        self._busy = threading.Lock()
        self._queue: queue.Queue[Snapshot] = queue.Queue()
        self._last_update = 0.0
        self._slot_items: dict[str, dict[str, int]] = {}
        self._ports_by_id: dict[str, object] = {}
        self._hovered_frame: int | None = None
        self._canvas_size = (0, 0)

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
        body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(4, 0))
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
            text="USB-C Dock Monitor · 俯视布局 / 设备 / 协商速率 / 网口吞吐 / 供电规格",
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
            sub_label.pack(anchor="w", padx=12, pady=(0, 3))
            self.stat_values[key] = value_label
            self.stat_subs[key] = sub_label

    def _build_device(self, parent: tk.Frame) -> None:
        shell = tk.Frame(parent, bg=BODY, highlightbackground=BODY_EDGE, highlightthickness=2)
        shell.pack(fill="both", expand=True)
        self.device_canvas = tk.Canvas(shell, bg=BODY, highlightthickness=0, cursor="arrow")
        self.device_canvas.pack(fill="both", expand=True)
        self.device_canvas.bind("<Configure>", self._on_canvas_resize)
        self.device_canvas.bind("<Button-1>", self._on_canvas_click)
        self.device_canvas.bind("<Motion>", self._on_canvas_motion)

    def _on_canvas_resize(self, event: tk.Event) -> None:
        size = (event.width, event.height)
        if abs(size[0] - self._canvas_size[0]) < 6 and abs(size[1] - self._canvas_size[1]) < 6:
            return
        self._canvas_size = size
        self._draw_device()

    # --------------------------------------------------------- device view
    def _draw_device(self) -> None:
        canvas = self.device_canvas
        canvas.delete("all")
        self._slot_items.clear()
        self._hovered_frame = None
        width = max(canvas.winfo_width(), 760)
        height = max(canvas.winfo_height(), 380)

        body_x1, body_x2 = 150, width - 200
        body_y1, body_y2 = int(height * 0.30), int(height * 0.70)

        canvas.create_text(
            18, 10, anchor="nw", text="俯视图 · 正面朝上", fill=SUBTLE, font=self.fonts["small"]
        )
        canvas.create_text(
            width - 18, 10, anchor="ne",
            text="长边上侧：供电 C → 数据 C → USB-A 1/2/3　|　长边下侧：HDMI → SD/TF → 音频",
            fill=SUBTLE, font=self.fonts["small"],
        )

        _round_rect(canvas, body_x1, body_y1, body_x2, body_y2, 18, fill=BODY, outline=BODY_EDGE, width=2)
        canvas.create_line(body_x1 + 26, body_y1 + 20, body_x2 - 26, body_y1 + 20, fill="#1b2534")
        canvas.create_text(
            (body_x1 + body_x2) // 2, body_y2 - 24,
            text="XIAOMI 10-in-1 USB-C HUB", fill=SUBTLE, font=self.fonts["small"],
        )

        # upstream cable on the left short edge
        cable_y = (body_y1 + body_y2) // 2
        canvas.create_line(16, cable_y, body_x1 - 12, cable_y, fill=SOCKET_EDGE, width=4)
        _round_rect(canvas, body_x1 - 12, cable_y - 9, body_x1 + 10, cable_y + 9, 6,
                    fill=SOCKET_FILL, outline=SOCKET_EDGE, width=1)
        canvas.create_text(16, cable_y - 44, anchor="nw", text="上行 Type-C\n连接电脑",
                           fill=MUTED, font=self.fonts["small"], justify="left")
        canvas.create_text(16, cable_y + 20, anchor="nw", text="短边左侧",
                           fill=SUBTLE, font=self.fonts["small"])

        # top long edge: five ports
        col_width = (body_x2 - body_x1) / len(TOP_PORTS)
        for index, port_id in enumerate(TOP_PORTS):
            cx = int(body_x1 + col_width * (index + 0.5))
            block_x1 = int(body_x1 + col_width * index) + 5
            block_x2 = int(body_x1 + col_width * (index + 1)) - 5
            block_y1, block_y2 = 26, body_y1 - 14
            self._draw_port_block(port_id, block_x1, block_y1, block_x2, block_y2, cx, body_y1, "top")

        # bottom long edge: HDMI, SD/TF combo, audio
        group_width = (body_x2 - body_x1) / 3
        block_y1, block_y2 = body_y2 + 22, height - 12
        hdmi_cx = int(body_x1 + group_width * 0.5)
        hdmi_x1 = int(body_x1 + group_width * 0) + 5
        hdmi_x2 = int(body_x1 + group_width * 1) - 5
        self._draw_port_block("hdmi", hdmi_x1, block_y1, hdmi_x2, block_y2, hdmi_cx, body_y2, "bottom")

        combo_cx = int(body_x1 + group_width * 1.5)
        combo_x1 = int(body_x1 + group_width * 1) + 5
        combo_x2 = int(body_x1 + group_width * 2) - 5
        self._draw_combo_block(combo_x1, block_y1, combo_x2, block_y2, combo_cx, body_y2)

        audio_cx = int(body_x1 + group_width * 2.5)
        audio_x1 = int(body_x1 + group_width * 2) + 5
        audio_x2 = int(body_x1 + group_width * 3) - 5
        self._draw_port_block("audio", audio_x1, block_y1, audio_x2, block_y2, audio_cx, body_y2, "bottom")

        # RJ45 on the right short edge
        lan_cx = body_x2
        lan_x1, lan_x2 = body_x2 + 26, width - 16
        lan_y1, lan_y2 = cable_y - 62, cable_y + 62
        self._draw_port_block("lan", lan_x1, lan_y1, lan_x2, lan_y2, lan_cx, cable_y, "right")

        self._refresh_device()

    def _draw_port_block(
        self, port_id: str, x1: int, y1: int, x2: int, y2: int, cx: int, edge_y: int, side: str
    ) -> None:
        canvas = self.device_canvas
        tag = f"port_{port_id}"
        canvas.create_rectangle(x1, y1, x2, y2, fill=BG, outline="", tags=(tag,))

        frame = _round_rect(canvas, x1, y1, x2, y2, 8, fill="", outline=CARD_BORDER, width=1, tags=(tag,))
        dot = canvas.create_oval(x1 + 12, y1 + 13, x1 + 20, y1 + 21, fill=SUBTLE, outline="", tags=(tag,))
        title = canvas.create_text(x1 + 26, y1 + 8, anchor="nw", text="", fill=TEXT,
                                   font=self.fonts["slot_title"], tags=(tag,))
        gap = 24 if (y2 - y1) >= 110 else 20
        device = canvas.create_text(x1 + 10, y1 + 32, anchor="nw", text="", fill=MUTED,
                                    font=self.fonts["slot_meta"], tags=(tag,))
        info = canvas.create_text(x1 + 10, y1 + 32 + gap, anchor="nw", text="", fill=SUBTLE,
                                  font=self.fonts["slot_meta"], tags=(tag,))
        note = canvas.create_text(x1 + 10, y1 + 32 + gap * 2, anchor="nw", text="", fill=SUBTLE,
                                  font=self.fonts["small"], tags=(tag,))

        socket = self._draw_socket(port_id, cx, edge_y, side, tag)
        self._slot_items[port_id] = {
            "frame": frame, "dot": dot, "title": title, "device": device,
            "info": info, "note": note, "socket": socket, "tag": tag,
            "title_max": (x2 - x1) - 32, "text_max": (x2 - x1) - 20,
            "compact": (y2 - y1) < 110,
        }

    def _draw_combo_block(self, x1: int, y1: int, x2: int, y2: int, cx: int, edge_y: int) -> None:
        canvas = self.device_canvas
        canvas.create_rectangle(x1, y1, x2, y2, fill=BG, outline="")
        canvas.create_text(x1 + 12, y1 + 6, anchor="nw", text="SD / TF 卡槽", fill=TEXT,
                           font=self.fonts["slot_title"])
        if y2 - y1 >= 165:
            canvas.create_text(x1 + 12, y2 - 24, anchor="nw", text="同一槽位：TF 在上 · SD 在下",
                               fill=SUBTLE, font=self.fonts["small"])

        row_height = 46
        for index, (port_id, label) in enumerate((("tf", "TF 卡槽（上层）"), ("sd", "SD 卡槽（下层）"))):
            row_y1 = y1 + 32 + index * (row_height + 8)
            row_y2 = row_y1 + row_height
            tag = f"port_{port_id}"
            canvas.create_rectangle(x1 + 2, row_y1, x2 - 2, row_y2, fill=BG, outline="")
            frame = _round_rect(canvas, x1 + 2, row_y1, x2 - 2, row_y2, 6, fill="",
                                outline=CARD_BORDER, width=1, tags=(tag,))
            dot = canvas.create_oval(x1 + 12, row_y1 + 14, x1 + 20, row_y1 + 22,
                                     fill=SUBTLE, outline="", tags=(tag,))
            title = canvas.create_text(x1 + 26, row_y1 + 5, anchor="nw", text=label,
                                       fill=TEXT, font=self.fonts["slot_meta"], tags=(tag,))
            info = canvas.create_text(x1 + 26, row_y1 + 23, anchor="nw", text="",
                                      fill=SUBTLE, font=self.fonts["small"], tags=(tag,))
            socket = self._draw_socket(port_id, cx, edge_y, "bottom", tag)
            self._slot_items[port_id] = {
                "frame": frame, "dot": dot, "title": title, "device": None,
                "info": info, "note": None, "socket": socket, "tag": tag,
                "title_max": (x2 - x1) - 32, "text_max": (x2 - x1) - 44,
            }

    def _draw_socket(self, port_id: str, cx: int, cy: int, side: str, tag: str) -> list[int]:
        canvas = self.device_canvas
        items: list[int] = []
        if port_id in ("pd", "usb_c"):
            items.append(_round_rect(canvas, cx - 15, cy - 8, cx + 15, cy + 8, 7,
                                     fill=SOCKET_FILL, outline=SOCKET_EDGE, width=2, tags=(tag,)))
        elif port_id.startswith("usb_a"):
            items.append(canvas.create_rectangle(cx - 15, cy - 8, cx + 15, cy + 8,
                                                 fill=SOCKET_FILL, outline=SOCKET_EDGE, width=2, tags=(tag,)))
            items.append(canvas.create_rectangle(cx - 10, cy - 3, cx + 10, cy + 3,
                                                 fill=SOCKET_EDGE, outline="", tags=(tag,)))
        elif port_id == "hdmi":
            items.append(canvas.create_polygon(cx - 16, cy - 7, cx + 16, cy - 7,
                                               cx + 13, cy + 8, cx - 13, cy + 8,
                                               fill=SOCKET_FILL, outline=SOCKET_EDGE, width=2, tags=(tag,)))
        elif port_id == "lan":
            items.append(canvas.create_rectangle(cx - 8, cy - 18, cx + 16, cy + 18,
                                                 fill=SOCKET_FILL, outline=SOCKET_EDGE, width=2, tags=(tag,)))
            for step in range(4):
                py = cy - 13 + step * 9
                items.append(canvas.create_line(cx - 4, py, cx + 10, py, fill=SOCKET_EDGE, tags=(tag,)))
        elif port_id in ("tf", "sd"):
            top = cy - 16 if port_id == "tf" else cy
            bottom = cy if port_id == "tf" else cy + 16
            items.append(canvas.create_polygon(cx - 14, top, cx + 10, top, cx + 14, top + 5,
                                               cx + 14, bottom, cx - 14, bottom,
                                               fill=SOCKET_FILL, outline=SOCKET_EDGE, width=2, tags=(tag,)))
        elif port_id == "audio":
            items.append(canvas.create_oval(cx - 9, cy - 9, cx + 9, cy + 9,
                                            fill=SOCKET_FILL, outline=SOCKET_EDGE, width=2, tags=(tag,)))
            items.append(canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                                            fill=SOCKET_EDGE, outline="", tags=(tag,)))
        else:
            items.append(canvas.create_rectangle(cx - 12, cy - 8, cx + 12, cy + 8,
                                                 fill=SOCKET_FILL, outline=SOCKET_EDGE, width=2, tags=(tag,)))
        return items

    def _refresh_device(self) -> None:
        for port_id, items in self._slot_items.items():
            port = self._ports_by_id.get(port_id)
            if port is None:
                continue
            connected = port.status == STATUS_CONNECTED
            color, border, _status_text = STATUS_STYLE.get(port.status, STATUS_STYLE[STATUS_UNKNOWN])
            selected = port_id == self.selected_id
            hovered = port_id == self.hover_id
            if selected:
                outline, outline_width = ACCENT, 2
            elif hovered:
                outline, outline_width = CARD_BORDER_HOVER, 2
            else:
                outline, outline_width = border, 1
            self.device_canvas.itemconfigure(items["frame"], outline=outline, width=outline_width)
            self.device_canvas.itemconfigure(items["dot"], fill=color)
            title_text = (
                f"#{port.number} {port.label.split(' · ', 1)[-1]}" if port.number else port.label
            )
            self._fit_text(items["title"], title_text, items["title_max"])
            if items["device"] is not None:
                self._fit_text(items["device"], port.headline, items["text_max"])
            self._fit_text(items["info"], self._info_line(port), items["text_max"])
            if items["note"] is not None:
                note_text = "" if items.get("compact") else (port.note or "")
                self._fit_text(items["note"], note_text, items["text_max"])
            socket_color = ACCENT if connected and port.status == STATUS_CONNECTED else SOCKET_EDGE
            if port.status == STATUS_WARNING:
                socket_color = AMBER
            for socket_item in items["socket"]:
                if self.device_canvas.type(socket_item) == "line":
                    self.device_canvas.itemconfigure(socket_item, fill=socket_color)
                else:
                    self.device_canvas.itemconfigure(
                        socket_item, outline=socket_color, width=2 if connected else 1
                    )

    def _fit_text(self, item: int, text: str, max_width: int) -> None:
        """Set canvas text, trimming with an ellipsis when wider than the card."""
        canvas = self.device_canvas
        text = (text or "").strip()
        canvas.itemconfigure(item, text=text)
        if not text:
            return
        bbox = canvas.bbox(item)
        while bbox and bbox[2] - bbox[0] > max_width:
            measured = bbox[2] - bbox[0]
            char_width = measured / max(len(text), 1)
            cut = max(1, int((measured - max_width) / char_width) + 1)
            text = text[: max(0, len(text) - cut)].rstrip()
            if not text:
                break
            canvas.itemconfigure(item, text=text + "…")
            bbox = canvas.bbox(item)
        if not text:
            canvas.itemconfigure(item, text="")

    @staticmethod
    def _info_line(port) -> str:
        if port.kind in ("sd", "tf") or not port.detail:
            return port.detail or "未插入"
        first = port.detail.split(" · ")[0]
        if port.speed and port.speed != "—":
            return f"{port.speed} · {first}"
        return port.detail

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
        except Exception as exc:
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
        self._ports_by_id = {port.id: port for port in snapshot.ports}
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

        if self.selected_id is None or self.selected_id not in self._ports_by_id:
            first = next((port.id for port in snapshot.ports if port.status == STATUS_CONNECTED), "usb_c")
            self.selected_id = first

        self._refresh_device()
        self._update_detail(self.selected_id)
        self._update_network_panel(snapshot)

    def _update_detail(self, port_id: str | None) -> None:
        if not self.snapshot or not port_id:
            return
        port = self._ports_by_id.get(port_id)
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
        self._refresh_device()
        self._update_detail(port_id)

    def _on_canvas_click(self, event: tk.Event) -> None:
        port_id = self._port_at(event.x, event.y)
        if port_id:
            self._select_port(port_id)

    def _on_canvas_motion(self, event: tk.Event) -> None:
        port_id = self._port_at(event.x, event.y)
        if port_id != self.hover_id:
            self.hover_id = port_id
            self.device_canvas.configure(cursor="hand2" if port_id else "arrow")
            self._refresh_device()

    def _port_at(self, x: int, y: int) -> str | None:
        item = self.device_canvas.find_withtag("current")
        if not item:
            current = self.device_canvas.find_closest(x, y)
            if not current:
                return None
            item = current
        for tag in self.device_canvas.gettags(item[0]):
            if tag.startswith("port_"):
                return tag[5:]
        return None

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
