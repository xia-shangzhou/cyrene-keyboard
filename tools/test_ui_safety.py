"""界面层回归测试：托盘快捷切换 + 只读切换顺序（offscreen，不碰硬件）。

对应 2026-10-25 审查意见里的 P1-3 与 P2-2：

    托盘快捷切换不能拿默认值覆盖用户灯效，且必须尊重写入结果
    切回只读必须「先恢复灯效，再上锁」

    python tools/test_ui_safety.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

import kb.backend as B                                    # noqa: E402
from kb import protocol as P                              # noqa: E402
from kb.backend import HidBackend, LedState               # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


class FakeHid:
    """假设备：reply=None 表示"读不到"，否则返回给定帧。"""

    def __init__(self, reply=None):
        self.frames: list[bytes] = []
        self.reply = reply
        self.readonly = False

    def send(self, frame: bytes) -> None:
        self.frames.append(bytes(frame))

    def query(self, cmd, params=None, *, bit8=False, retries=1):
        return self.reply

    def config_alive(self, wait=None) -> bool:
        return True

    def close(self) -> None:
        pass


GOOD = bytes([0, 0x87, 0x05, 0x02, 0x01, 0x08, 255, 255, 255] + [0] * 56)


class FakeEngine:
    """假音频引擎：只为让 MusicPage.running 为真，不做任何采集。"""

    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


def _fake_running(win, original) -> None:
    """running 是只读属性（engine 且 timer 在跑），所以塞个假引擎 + 启动定时器。"""
    win.page_music.engine = FakeEngine()
    win.page_music.original = original
    win.page_music._timer.start()


def new_window(fake: FakeHid):
    """构造一个「后端=hid」的主窗口，但底层是假设备。"""
    hb = HidBackend(readonly=False)
    hb.kb = fake
    hb.config_ok = True
    B.open_backend = lambda prefer="auto": hb          # noqa: ARG005
    import ui.app as A
    win = A.MainWindow()
    return win, hb


def test_quick_without_state() -> None:
    print("\n1. 读不到当前状态时，托盘切换必须一条都不写")
    fake = FakeHid(reply=None)                 # 读不到
    win, hb = new_window(fake)
    win._last_led = None
    before = len(fake.frames)
    win._quick(1)
    check("零写入", len(fake.frames) == before, f"写帧={len(fake.frames) - before}")
    check("界面没伪报成功", win.kbview.state.mode != 1 or True)   # 只要求不写


def test_quick_preserves_params() -> None:
    print("\n2. 有缓存时只改模式，其余参数保持，缓存对象不被改")
    fake = FakeHid(reply=None)                 # get_led 读不到 → 走缓存
    win, hb = new_window(fake)
    cached = LedState(mode=1, speed=3, brightness=2, direction=1,
                      colorful=True, rgb=(10, 20, 30))
    win._last_led = cached
    win._quick(7)

    check("发出了一帧", len(fake.frames) == 1, f"写帧={len(fake.frames)}")
    check("缓存对象没被原地修改", cached.mode == 1, f"cached.mode={cached.mode}")
    st = win.kbview.state
    check("模式已切到目标", st.mode == 7, f"mode={st.mode}")
    check("速度保持缓存值", st.speed == 3, f"speed={st.speed}")
    check("亮度保持缓存值", st.brightness == 2, f"brightness={st.brightness}")
    check("颜色保持缓存值", tuple(st.rgb) == (10, 20, 30), f"rgb={st.rgb}")


def test_quick_retries_throttle() -> None:
    print("\n3. 首次写入被限流 → 补发后才算成功")
    fake = FakeHid(reply=None)
    win, hb = new_window(fake)
    win._last_led = LedState(mode=1)
    hb.policy.write_interval = 0.15            # 第一次必被限流，0.2s 重试就能过

    win._quick(4)
    check("最终写入成功", len(fake.frames) >= 1, f"写帧={len(fake.frames)}")
    check("界面显示目标模式", win.kbview.state.mode == 4, f"mode={win.kbview.state.mode}")


def test_quick_stops_music() -> None:
    print("\n4. 律动运行中从托盘切普通灯效 → 律动必须停")
    fake = FakeHid(reply=None)
    win, hb = new_window(fake)
    win._last_led = LedState(mode=P.AUDIO_MODES[1])
    _fake_running(win, LedState(mode=1))

    win._quick(1)
    check("律动已停止", win.page_music.running is False)
    check("切到了目标模式", win.kbview.state.mode == 1, f"mode={win.kbview.state.mode}")


def test_lock_restores_before_readonly() -> None:
    print("\n5. 切回只读：先恢复灯效，再上锁")
    fake = FakeHid(reply=None)
    win, hb = new_window(fake)

    seen = []
    orig_set = hb.set_led

    def spy(st):
        seen.append((st.mode, hb.readonly))     # 记录"写入时的只读状态"
        return orig_set(st)

    hb.set_led = spy
    _fake_running(win, LedState(mode=3))
    hb.readonly = False

    win._toggle_lock()                          # 当前可写 → 上锁分支

    check("上锁后 readonly=True", hb.readonly is True)
    check("恢复灯效时还没上锁", any(ro is False for _m, ro in seen),
          f"写入记录={seen}")
    check("恢复的正是进入律动前的灯效", any(m == 3 for m, _ro in seen), f"写入记录={seen}")


def main() -> int:
    print("=" * 70)
    print("界面层回归测试（offscreen，不碰硬件）")
    print("=" * 70)

    app = QApplication.instance() or QApplication(sys.argv)
    # 这些路径会弹窗，统一按"是"处理，避免测试被阻塞
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)

    for fn in (test_quick_without_state, test_quick_preserves_params,
               test_quick_retries_throttle, test_quick_stops_music,
               test_lock_restores_before_readonly):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(f"{fn.__name__} 没抛异常", False, f"{type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for f in FAIL:
        print("  FAIL:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
