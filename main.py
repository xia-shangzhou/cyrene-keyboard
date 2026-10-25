"""Cyrene Keyboard（昔涟键盘控制台）入口。

    python main.py            正常启动
    python main.py --mock     强制模拟模式（不碰硬件）
    python main.py --probe    只做真机探测，不开界面
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

APP_ID = "CyreneKeyboard.Console.1"   # 任务栏图标分组用，随便起但别改


def _set_app_user_model_id() -> None:
    """告诉 Windows 这是"独立应用"，否则任务栏会显示 python 的图标而不是我们的。"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    args = set(sys.argv[1:])

    if "--probe" in args:
        from tools import probe_live
        return probe_live.main()

    if "--mock" in args:
        import kb.backend as B
        B.open_backend = lambda prefer="auto": _mock()  # noqa: ARG005

    from PySide6.QtWidgets import QApplication

    from ui.app import APP_NAME, APP_NAME_CN, MainWindow, apply_style, make_app_icon

    _set_app_user_model_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(f"{APP_NAME} · {APP_NAME_CN}")
    app.setOrganizationName("CyreneKeyboard")
    app.setWindowIcon(make_app_icon())
    apply_style(app)
    app.setQuitOnLastWindowClosed(False)

    win = MainWindow()
    # 退出时收尾：停音频线程、把灯效从音乐模式还原回去。
    # 少了这一句，从托盘点「退出」会把键盘留在音乐模式。
    app.aboutToQuit.connect(win.shutdown)
    win.show()
    return app.exec()


def _mock():
    from kb.backend import MockBackend
    b = MockBackend()
    b.connect()
    return b


if __name__ == "__main__":
    raise SystemExit(main())
