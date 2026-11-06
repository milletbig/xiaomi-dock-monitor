"""Build a single-file Windows executable with PyInstaller.

    python scripts/build_exe.py

The result is ``dist/dockmon.exe``. The icon and the version resource are
generated from the package version, so nothing has to be kept in sync by
hand.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BUILD = ROOT / "build"
DIST = ROOT / "dist"

sys.path.insert(0, str(SRC))

from dockmon import __version__  # noqa: E402

VERSION_TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers},
    prodvers={numbers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'xiaomi-dock-monitor'),
        StringStruct('FileDescription', '小米十合一扩展坞监控'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'dockmon'),
        StringStruct('OriginalFilename', 'dockmon.exe'),
        StringStruct('ProductName', '小米十合一扩展坞监控'),
        StringStruct('ProductVersion', '{version}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def _version_numbers(version: str) -> tuple[int, int, int, int]:
    parts = [int(part) for part in version.split(".") if part.isdigit()]
    parts += [0] * (4 - len(parts))
    return tuple(parts[:4])


def _write_version_file(path: Path) -> None:
    numbers = _version_numbers(__version__)
    path.write_text(
        VERSION_TEMPLATE.format(numbers=numbers, version=__version__), encoding="utf-8"
    )


def _write_icon(path: Path) -> bool:
    try:
        from dockmon.tray import build_icon_image
    except Exception:
        return False
    image = build_icon_image(256)
    if image is None:
        return False
    image.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return True


def build() -> int:
    BUILD.mkdir(parents=True, exist_ok=True)
    version_file = BUILD / "version_info.txt"
    _write_version_file(version_file)
    icon_file = BUILD / "dockmon.ico"
    has_icon = _write_icon(icon_file)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "dockmon",
        "--paths",
        str(SRC),
        "--collect-submodules",
        "pystray",
        "--version-file",
        str(version_file),
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD / "pyinstaller"),
        "--specpath",
        str(BUILD),
    ]
    if has_icon:
        command += ["--icon", str(icon_file)]
    command.append(str(ROOT / "packaging" / "entry.py"))

    print("building dockmon.exe …", flush=True)
    result = subprocess.call(command)
    if result == 0:
        exe = DIST / "dockmon.exe"
        print(f"built {exe} ({exe.stat().st_size / 1048576:.1f} MB)", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(build())
