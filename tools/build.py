"""
打包成单个 exe。

    python tools/build.py

产物：dist/昔涟键盘控制台.exe
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "XilianKeyboard"

# 排除用不到的 Qt 模块，能把体积压下来一大截
EXCLUDES = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "tkinter", "matplotlib", "numpy", "PIL", "pytest",
]


def main() -> int:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--hidden-import", "hid",
        "--paths", str(ROOT),
    ]
    for m in EXCLUDES:
        args += ["--exclude-module", m]

    icon = ROOT / "docs" / "app.ico"
    if icon.exists():
        args += ["--icon", str(icon)]

    args.append(str(ROOT / "main.py"))

    print("执行:", " ".join(args[:8]), "...")
    return subprocess.call(args, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
