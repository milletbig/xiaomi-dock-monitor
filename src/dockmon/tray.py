"""System tray icon for the dock monitor.

The tray is optional: when ``pystray`` or Pillow is missing the monitor simply
keeps a normal window and ``--tray`` has no effect.
"""

from __future__ import annotations

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - optional dependency
    Image = None
    ImageDraw = None

try:
    import pystray
except Exception:  # pragma: no cover - optional dependency
    pystray = None

from . import autostart

AVAILABLE = pystray is not None and Image is not None


def build_icon_image(size: int = 64):
    """Draw the tray / exe icon: a dock body with three USB-A slots."""
    if Image is None:
        return None
    unit = size / 64.0
    stroke = max(1, int(round(2 * unit)))
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        [6 * unit, 24 * unit, 58 * unit, 44 * unit],
        radius=5 * unit,
        fill="#121a24",
        outline="#4ea1ff",
        width=stroke,
    )
    for index in range(3):
        left = 14 + index * 13
        draw.rectangle(
            [left * unit, 15 * unit, (left + 9) * unit, 24 * unit],
            fill="#3ddc97",
        )
    draw.line([(0, 34 * unit), (6 * unit, 34 * unit)], fill="#4ea1ff", width=stroke)
    draw.rectangle([57 * unit, 30 * unit, 63 * unit, 38 * unit], fill="#4ea1ff")
    return image


class TrayController:
    """Wraps a pystray icon and forwards menu actions to the Tk app."""

    def __init__(self, app) -> None:
        self.app = app
        self.icon = None

    def start(self) -> bool:
        if not AVAILABLE:
            return False
        image = build_icon_image(64)
        if image is None:
            return False
        menu = pystray.Menu(
            pystray.MenuItem("打开监控面板", self._show, default=True),
            pystray.MenuItem("隐藏窗口", self._hide),
            pystray.MenuItem("开机自启", self._toggle_autostart, checked=self._autostart_checked),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出监控", self._quit),
        )
        self.icon = pystray.Icon("xiaomi-dock-monitor", image, "小米十合一扩展坞监控", menu)
        try:
            self.icon.run_detached()
        except Exception:  # pragma: no cover - backend failure
            self.icon = None
            return False
        return True

    def stop(self) -> None:
        if self.icon is None:
            return
        try:
            self.icon.stop()
        except Exception:
            pass
        self.icon = None

    # ------------------------------------------------------------- actions
    def _show(self, *_args) -> None:
        self.app.post_command("show")

    def _hide(self, *_args) -> None:
        self.app.post_command("hide")

    def _quit(self, *_args) -> None:
        self.app.post_command("quit")

    def _toggle_autostart(self, *_args) -> None:
        enabled = autostart.toggle()
        if self.icon is None:
            return
        try:
            self.icon.update_menu()
            self.icon.notify("已开启开机自启" if enabled else "已关闭开机自启", "小米十合一扩展坞")
        except Exception:
            pass

    @staticmethod
    def _autostart_checked(_item) -> bool:
        return autostart.is_enabled()
