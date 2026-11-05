"""Tkinter UI for the dock monitor."""

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
CARD_BORDER = "#263244"
CARD_BORDER_ACTIVE = "#2f6f57"
CARD_BORDER_WARN = "#7a5b23"
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

PORT_ORDER = ["pd", "usb_c", "usb_a1", "usb_a2", "usb_a3", "hdmi", "lan", "sd", "tf", "audio"]

HEADER_HEIGHT = 86
STATS_HEIGHT = 80
BOTTOM_HEIGHT = 232
CARD_HEIGHT = 172


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


class DockMonitorApp:
    def __init__(self) -> None:
        _set_dpi_awareness()
        self.root = tk.Tk()
        self.root.title("小米十合一扩展坞 · 实时监控")
        self.root.configure(bg=BG)
        self.root.geometry("1180x820")
        self.root.minsize(1100, 780)

        self.family = _font_family(self.root)
        self.f_title = (self.family, 18, "bold")
        self.f_sub = (self.family, 9)
        self.f_section = (self.family, 11, "bold")
        self.f_label = (self.family, 10)
        self.f_device = (self.family, 11, "bold")
        self.f_detail = (self.family, 8)
        self.f_badge = (self.family, 8)
        self.f_value = (self.family, 19, "bold")
        self.f_small = (self.family, 8)

        self.probe: SystemProbe | None = None
        self.sampler: NetworkSampler | None = None
        self.snapshot: Snapshot | None = None
        self.history: list[float] = []
        self.paused = False
        self.closed = False
        self._busy = threading.Lock()
        self._queue: queue.Queue[Snapshot] = queue.Queue()
        self._last_update = 0.0

        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(80, self._bootstrap)

    # ------------------------------------------------------------------ UI
    def _build_layout(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(2, weight=1)

        # header
        header = tk.Frame(self.root, bg=PANEL, height=HEADER_HEIGHT)
        header.grid(row=0, column=0, sticky="nsew")
        header.grid_propagate(False)

        left = tk.Frame(header, bg=PANEL)
        left.pack(side="left", padx=20, pady=8)
        tk.Label(left, text="小米十合一扩展坞", bg=PANEL, fg=TEXT, font=self.f_title, anchor="w").pack(
            anchor="w"
        )
        tk.Label(
            left,
            text="USB-C Dock Monitor · 接口 1–5 物理编号 / 协商速率 / 网口吞吐 / 供电规格",
            bg=PANEL,
            fg=MUTED,
            font=self.f_sub,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        right = tk.Frame(header, bg=PANEL)
        right.pack(side="right", padx=20, pady=8)
        self.dock_state = tk.Label(right, text="正在检测扩展坞…", bg=PANEL, fg=AMBER, font=(self.family, 10))
        self.dock_state.pack(anchor="e")
        self.updated = tk.Label(right, text="", bg=PANEL, fg=SUBTLE, font=self.f_small)
        self.updated.pack(anchor="e", pady=(3, 0))

        # stats
        stats = tk.Frame(self.root, bg=BG, height=STATS_HEIGHT)
        stats.grid(row=1, column=0, sticky="nsew", padx=14, pady=(10, 2))
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
            tile.grid(row=0, column=column, sticky="nsew", padx=6, pady=4)
            tk.Label(tile, text=title, bg=CARD, fg=MUTED, font=self.f_label, anchor="w").pack(
                anchor="w", padx=12, pady=(7, 0)
            )
            value_label = tk.Label(tile, text=value, bg=CARD, fg=TEXT, font=self.f_value, anchor="w")
            value_label.pack(anchor="w", padx=12, pady=(0, 0))
            sub_label = tk.Label(tile, text=sub, bg=CARD, fg=SUBTLE, font=self.f_small, anchor="w")
            sub_label.pack(anchor="w", padx=12, pady=(0, 6))
            self.stat_values[key] = value_label
            self.stat_subs[key] = sub_label

        # port cards
        grid = tk.Frame(self.root, bg=BG)
        grid.grid(row=2, column=0, sticky="nsew", padx=8, pady=2)
        for column in range(5):
            grid.grid_columnconfigure(column, weight=1, uniform="port")
        for row in range(2):
            grid.grid_rowconfigure(row, weight=1, uniform="portrow")
        self.cards: dict[str, dict[str, tk.Widget]] = {}
        for index, port_id in enumerate(PORT_ORDER):
            row, column = divmod(index, 5)
            self._build_card(grid, row, column, port_id)

        # bottom
        bottom = tk.Frame(self.root, bg=BG, height=BOTTOM_HEIGHT)
        bottom.grid(row=3, column=0, sticky="nsew", padx=14, pady=(2, 12))
        bottom.grid_propagate(False)
        bottom.grid_columnconfigure(0, weight=3, uniform="bottom")
        bottom.grid_columnconfigure(1, weight=2, uniform="bottom")
        bottom.grid_rowconfigure(0, weight=1)
        self._build_network_panel(bottom)
        self._build_notes_panel(bottom)

    def _build_card(self, parent: tk.Frame, row: int, column: int, port_id: str) -> None:
        card = tk.Frame(parent, bg=CARD, highlightbackground=CARD_BORDER, highlightthickness=1)
        card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
        card.grid_propagate(False)
        card.configure(height=CARD_HEIGHT)

        top = tk.Frame(card, bg=CARD)
        top.pack(fill="x", padx=10, pady=(8, 0))
        label = tk.Label(top, text="", bg=CARD, fg=TEXT, font=self.f_label, anchor="w")
        label.pack(side="left")
        status = tk.Label(top, text="○ 空闲", bg=CARD, fg=SUBTLE, font=self.f_badge, anchor="e")
        status.pack(side="right")

        headline = tk.Label(
            card,
            text="空闲",
            bg=CARD,
            fg=TEXT,
            font=self.f_device,
            anchor="w",
            justify="left",
            wraplength=200,
        )
        headline.pack(fill="x", padx=10, pady=(5, 0))
        detail = tk.Label(card, text="", bg=CARD, fg=MUTED, font=self.f_detail, anchor="w")
        detail.pack(fill="x", padx=10, pady=(2, 0))

        badges = tk.Frame(card, bg=CARD)
        badges.pack(fill="x", padx=10, pady=(6, 0))
        speed = tk.Label(badges, text="—", bg=BADGE_BG, fg=ACCENT, font=self.f_badge, padx=6, pady=2)
        speed.pack(side="left")
        power = tk.Label(badges, text="—", bg=BADGE_BG, fg=MUTED, font=self.f_badge, padx=6, pady=2)
        power.pack(side="left", padx=(6, 0))

        note = tk.Label(card, text="", bg=CARD, fg=SUBTLE, font=self.f_small, anchor="w")
        note.pack(fill="x", padx=10, pady=(5, 0))

        for widget in (card, top, label, status, headline, detail, badges, speed, power, note):
            widget.bind("<Button-1>", lambda _event, pid=port_id: self._show_port_detail(pid))

        self.cards[port_id] = {
            "card": card,
            "label": label,
            "status": status,
            "headline": headline,
            "detail": detail,
            "speed": speed,
            "power": power,
            "note": note,
        }

    def _build_network_panel(self, parent: tk.Frame) -> None:
        panel = tk.Frame(parent, bg=CARD, highlightbackground=CARD_BORDER, highlightthickness=1)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=4)

        top = tk.Frame(panel, bg=CARD)
        top.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(top, text="扩展坞网口实时流量", bg=CARD, fg=TEXT, font=self.f_section).pack(side="left")
        self.net_status = tk.Label(top, text="等待采样…", bg=CARD, fg=SUBTLE, font=self.f_badge)
        self.net_status.pack(side="right")

        values = tk.Frame(panel, bg=CARD)
        values.pack(fill="x", padx=14, pady=(8, 0))
        self.net_rx = tk.Label(values, text="↓ 0 B/s", bg=CARD, fg=GREEN, font=(self.family, 15, "bold"))
        self.net_rx.pack(side="left")
        self.net_tx = tk.Label(values, text="↑ 0 B/s", bg=CARD, fg=ACCENT, font=(self.family, 15, "bold"))
        self.net_tx.pack(side="left", padx=(22, 0))
        self.net_link = tk.Label(values, text="—", bg=CARD, fg=MUTED, font=self.f_label)
        self.net_link.pack(side="right")

        self.spark = tk.Canvas(panel, bg=CARD, highlightthickness=0, height=62)
        self.spark.pack(fill="x", padx=14, pady=(8, 6))

        self.net_detail = tk.Label(panel, text="", bg=CARD, fg=SUBTLE, font=self.f_small, anchor="w")
        self.net_detail.pack(fill="x", padx=14, pady=(0, 8))

    def _build_notes_panel(self, parent: tk.Frame) -> None:
        panel = tk.Frame(parent, bg=CARD, highlightbackground=CARD_BORDER, highlightthickness=1)
        panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=4)

        tk.Label(panel, text="数据说明", bg=CARD, fg=TEXT, font=self.f_section, anchor="w").pack(
            fill="x", padx=14, pady=(10, 4)
        )
        notes = [
            "物理编号：1 供电 Type-C、2 Type-C 数据、3–5 USB-A。",
            "2/3 已按实测标定；4/5 待插拔标定，未标定设备会在下方提示。",
            "功率为规格上限；HDMI / 音频 / 存储为系统级推断。",
        ]
        for text in notes:
            tk.Label(
                panel, text=f"· {text}", bg=CARD, fg=MUTED, font=self.f_small,
                anchor="w", justify="left", wraplength=370
            ).pack(fill="x", padx=14, pady=1)

        self.unmapped_note = tk.Label(
            panel, text="· 未标定 USB 端口：—", bg=CARD, fg=SUBTLE, font=self.f_small,
            anchor="w", justify="left", wraplength=370,
        )
        self.unmapped_note.pack(fill="x", padx=14, pady=(3, 0))

        footer = tk.Frame(panel, bg=CARD)
        footer.pack(side="bottom", fill="x", padx=14, pady=(4, 8))
        tk.Label(footer, text=f"v{__version__}", bg=CARD, fg=SUBTLE, font=self.f_small).pack(side="left")
        self.pause_button = tk.Button(
            footer, text="暂停", command=self._toggle_pause, bg=BADGE_BG, fg=TEXT, font=self.f_badge,
            relief="flat", bd=0, padx=10, pady=3, activebackground=CARD_BORDER, activeforeground=TEXT,
            cursor="hand2",
        )
        self.pause_button.pack(side="right")
        self.refresh_button = tk.Button(
            footer, text="立即刷新", command=self._request_refresh, bg=BADGE_BG, fg=TEXT, font=self.f_badge,
            relief="flat", bd=0, padx=10, pady=3, activebackground=CARD_BORDER, activeforeground=TEXT,
            cursor="hand2",
        )
        self.refresh_button.pack(side="right", padx=(0, 6))

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
        self.dock_state.configure(text=("● 已检测到扩展坞" if snapshot.dock_present else "● 未检测到小米扩展坞"), fg=dock_color)
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
            card = self.cards.get(port.id)
            if not card:
                continue
            color, border, status_text = STATUS_STYLE.get(port.status, STATUS_STYLE[STATUS_UNKNOWN])
            card["card"].configure(highlightbackground=border)
            card["label"].configure(text=port.label)
            card["status"].configure(text=status_text, fg=color)
            card["headline"].configure(text=port.headline)
            card["detail"].configure(text=port.detail or " ")
            card["speed"].configure(text=port.speed)
            card["power"].configure(text=port.power)
            card["note"].configure(text=port.note)

        self._update_network_panel(snapshot)

    def _update_unmapped_note(self, snapshot: Snapshot) -> None:
        parts = [
            (
                f"{item.hub_role} Hub #{item.hub_port} · {item.device}"
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
            canvas.create_text(width // 2, height // 2, text="等待流量样本…", fill=SUBTLE, font=self.f_small)
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
            6, 6, anchor="nw", text=f"峰值 {format_bytes_rate(peak)}", fill=SUBTLE, font=self.f_small
        )

    def _show_port_detail(self, port_id: str) -> None:
        if not self.snapshot:
            return
        port = next((item for item in self.snapshot.ports if item.id == port_id), None)
        if not port:
            return
        from tkinter import messagebox

        messagebox.showinfo(
            port.label,
            f"物理接口：{port.number if port.number else '—'}\n"
            f"状态：{port.status}\n设备：{port.headline}\n详情：{port.detail}\n"
            f"速率：{port.speed}\n功率：{port.power}\n备注：{port.note or '—'}",
            parent=self.root,
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
