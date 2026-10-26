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
WINDOW_TITLE = "Cyrene Keyboard · 昔涟键盘控制台"

# 单实例互斥体。Local\ 前缀 = 仅当前登录会话，同一台机器多用户各开一份互不干扰。
MUTEX_NAME = r"Local\CyreneKeyboard.Console.SingleInstance"
_ERROR_ALREADY_EXISTS = 183
_mutex_handle = None      # 必须留引用：进程活着锁才在，被 GC 掉就等于没加锁


def _acquire_single_instance() -> bool:
    """拿单实例锁。返回 False 表示已有实例在跑。

    多开的真实危害不是"多一个窗口"：两个进程会同时打开键盘的配置接口、
    同时读写 Feature Report，读响应互相抢；叠加音乐律动就是两路流式帧交错
    轰同一个通道，表现为灯效乱闪甚至管道卡死。所以这里必须挡住。
    """
    global _mutex_handle
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        h = k32.CreateMutexW(None, False, MUTEX_NAME)
        if not h:
            return True                      # 建不出锁就别把用户拦在门外
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            k32.CloseHandle(h)
            return False
        _mutex_handle = h
        return True
    except Exception:  # noqa: BLE001
        return True                          # 任何异常都放行，互斥失败不该变成启动失败


def _focus_existing_window() -> bool:
    """把已在跑的那个窗口抬到前台。找不到就返回 False（由调用方提示）。"""
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.WinDLL("user32", use_last_error=True)
        u32.FindWindowW.restype = wintypes.HWND
        u32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        hwnd = u32.FindWindowW(None, WINDOW_TITLE)
        if not hwnd:
            return False
        u32.ShowWindow(hwnd, 9)              # SW_RESTORE：从最小化/托盘态恢复
        u32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False



def _set_app_user_model_id() -> None:
    """告诉 Windows 这是"独立应用"，否则任务栏会显示 python 的图标而不是我们的。"""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:  # noqa: BLE001
        pass


def _message_box(text: str) -> None:
    """不依赖 Qt 的提示框：此时 QApplication 还没建。"""
    try:
        import ctypes
        from ctypes import wintypes

        u32 = ctypes.WinDLL("user32", use_last_error=True)
        u32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR,
                                    wintypes.LPCWSTR, wintypes.UINT)
        u32.MessageBoxW(None, text, "昔涟键盘控制台", 0x40)   # MB_ICONINFORMATION
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

    if not _acquire_single_instance():
        # 已有一个实例：优先把它抬到前台（缩在托盘里也能被 FindWindow 找到）
        if not _focus_existing_window():
            _message_box(
                "昔涟键盘控制台已经在运行了。\n\n"
                "如果没看到窗口，请看屏幕右下角托盘区的小图标，双击它即可打开。\n"
                "（不要同时开两个：两个实例会抢同一个键盘通道，灯效会错乱）"
            )
        return 0

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
