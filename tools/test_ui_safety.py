"""界面层回归测试：托盘快捷切换 + 只读切换顺序 + 写入代次（offscreen，不碰硬件）。

对应 2026-10-25 审查意见里的 P1-3 与 P2-2，以及 2026-10-26 第二轮外部审查
（Kimi）指出的三条高优先级：

    1. 旧限流重试不能覆盖用户后来的选择（A → B → A）
    2. 恢复失败不能丢掉原灯效（否则键盘永久停在音乐模式 22）
    3. 写入成功后要更新 _last_led 镜像（托盘兜底不能拿旧参数全量写回）

    python tools/test_ui_safety.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

import kb.backend as B                                    # noqa: E402
from kb import protocol as P                              # noqa: E402
from kb.backend import HidBackend, LedState               # noqa: E402

PASS, FAIL = [], []
APP = None      # main() 里赋值，pump() 要用


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


def pump(app, seconds: float) -> None:
    """让 QTimer.singleShot 真的有机会触发（offscreen 下也要转事件循环）。"""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


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
    before_mode = getattr(win.kbview.state, "mode", None)
    before = len(fake.frames)
    win._quick(1)
    check("零写入", len(fake.frames) == before, f"写帧={len(fake.frames) - before}")
    # 原来是 `xxxx or True`，恒真，等于没断言。改成"界面状态没被改动"。
    check("界面没伪报成功",
          getattr(win.kbview.state, "mode", None) == before_mode,
          f"mode={getattr(win.kbview.state, 'mode', None)}")


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
    hb.policy.write_interval = 0.15
    # ★ 先用掉当前写入额度：否则第一次就写成功，"重试分支"根本没被走到
    #   （原来的版本就是这样，测了个寂寞）
    hb.set_led(LedState(mode=2))
    base = len(fake.frames)
    throttled0 = hb.policy.stats["skipped_throttle"]

    win._quick(4)
    check("第一次确实被限流", hb.policy.stats["skipped_throttle"] > throttled0,
          f"throttle={hb.policy.stats['skipped_throttle'] - throttled0}")
    check("补发后只多一帧", len(fake.frames) - base == 1, f"写帧={len(fake.frames) - base}")
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


def test_stale_retry_does_not_override() -> None:
    print("\n6. 旧限流重试不能覆盖用户后来的选择（A → B → A）")
    fake = FakeHid(reply=None)
    win, hb = new_window(fake)
    hb.policy.write_interval = 0.2

    writes: list[int] = []
    orig = hb.set_led

    def spy(st):
        ok = orig(st)
        if ok:
            writes.append(st.mode)
        return ok

    hb.set_led = spy

    win._apply_main(LedState(mode=3))          # A：立刻写成功
    pump(APP, 0.02)
    t0 = hb.policy.stats["skipped_throttle"]
    win._apply_main(LedState(mode=5))          # B：被限流 → 排 400ms 重试
    pump(APP, 0.36)
    win._apply_main(LedState(mode=7))          # C：用户又改，写成功
    check("B 确实被限流并排了重试", hb.policy.stats["skipped_throttle"] > t0,
          f"throttle={hb.policy.stats['skipped_throttle'] - t0}")

    pump(APP, 1.2)                             # 给 B 的重试充分时间落地
    check("B 的旧重试没有写出去", 5 not in writes, f"实际写入序列={writes}")
    check("最终停在 C", writes and writes[-1] == 7, f"实际写入序列={writes}")
    check("预览也停在 C", win.kbview.state.mode == 7, f"mode={win.kbview.state.mode}")


def test_failed_restore_keeps_original() -> None:
    print("\n7. 恢复失败时不能丢掉原灯效（否则永久停在音乐模式）")
    fake = FakeHid(reply=None)
    win, hb = new_window(fake)
    _fake_running(win, LedState(mode=3, brightness=2))

    hb.set_led = lambda st: False              # 模拟"通道不可用，恢复失败"
    win.page_music.stop(restore=True)
    kept = win.page_music.original
    check("原灯效仍然保留", kept is not None,
          f"original={None if kept is None else kept.mode}")

    writes: list[int] = []
    hb.set_led = lambda st: (writes.append(st.mode), True)[1]   # 通道恢复
    win.shutdown()                             # 退出前补一次
    check("退出前补了一次恢复", writes == [3], f"写入={writes}")
    check("恢复成功后原值才清空", win.page_music.original is None)


def test_last_led_updated_after_write() -> None:
    print("\n8. 写入成功后要更新 _last_led 镜像（托盘兜底不能用旧参数）")
    fake = FakeHid(reply=None)
    win, hb = new_window(fake)
    win._last_led = LedState(mode=1, speed=2, brightness=4, rgb=(10, 20, 30))

    win._apply_main(LedState(mode=4, speed=2, brightness=1, rgb=(10, 20, 30)))
    check("镜像亮度已更新", win._last_led.brightness == 1,
          f"brightness={win._last_led.brightness}")

    seen: list[int] = []
    orig = hb.set_led
    hb.set_led = lambda st: (seen.append(st.brightness), orig(st))[1]
    hb.policy.write_interval = 0
    win._quick(7)                              # 读不到状态 → 走镜像
    check("托盘沿用新亮度而不是旧值", bool(seen) and seen[-1] == 1, f"写入亮度={seen}")


MSGS: list[str] = []


def _record_msgs(on: bool) -> None:
    """把提示框正文录下来（offscreen 下没有托盘，_notify 会走提示框那条路）。"""
    if on:
        MSGS.clear()
        QMessageBox.warning = staticmethod(
            lambda *a, **k: MSGS.append(str(a[2] if len(a) > 2 else a)) or None)
    else:
        QMessageBox.warning = staticmethod(lambda *a, **k: None)


def test_quick_readonly_hint() -> None:
    print("\n9. 只读模式下托盘切换：一条都不写，且提示指向只读")
    fake = FakeHid(reply=None)
    win, hb = new_window(fake)
    win.tray = None                    # 没托盘 → _notify 走提示框，好录制
    hb.readonly = True
    hb.policy.strict_readonly = True
    win._last_led = LedState(mode=1)
    before = len(fake.frames)
    _record_msgs(True)
    win._quick(4)
    _record_msgs(False)
    check("零写入", len(fake.frames) == before, f"写帧={len(fake.frames) - before}")
    check("提示指向只读模式", any("只读" in m for m in MSGS), str(MSGS[:1]))
    # 老文案是"写入被限流或失败，请稍后再试一次"——只读状态下等多久都没用
    check("不再误导成「稍后再试」", not any("稍后再试一次" in m for m in MSGS))


def test_write_blocked_when_channel_dead() -> None:
    print("\n10. 配置通道不可用时，写灯效要拦住并说明原因")
    fake = FakeHid(reply=None)
    win, hb = new_window(fake)
    win.tray = None
    hb.config_ok = False               # 拔线 / 断连 / 读失败累积后的状态
    before = len(fake.frames)
    _record_msgs(True)
    win._apply_main(LedState(mode=3))
    _record_msgs(False)
    check("没有向设备发帧", len(fake.frames) == before, f"写帧={len(fake.frames) - before}")
    check("提示说明读不到键盘状态", any("读不到" in m for m in MSGS), str(MSGS[:1]))


def test_do_exhausted_notifies() -> None:
    print("\n11. 重试 3 次仍写不进去：必须给出可见提示，不能只 print")
    fake = FakeHid(reply=None)
    win, hb = new_window(fake)
    win.tray = None
    calls: list[int] = []

    def fn(_st):
        calls.append(1)
        return False                   # 模拟一直被限流

    _record_msgs(True)
    win._do(fn, LedState(mode=2), lambda s: None, which="led")
    pump(APP, 1.6)                     # 让两次 400ms 重试有机会触发
    _record_msgs(False)
    check("确实重试了 3 次", len(calls) == 3, f"次数={len(calls)}")
    check("失败有可见提示", any("没有写进去" in m for m in MSGS), str(MSGS[:1]))


def main() -> int:
    global APP
    print("=" * 70)
    print("界面层回归测试（offscreen，不碰硬件）")
    print("=" * 70)

    APP = QApplication.instance() or QApplication(sys.argv)
    # 这些路径会弹窗，统一按"是"处理，避免测试被阻塞
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: None)

    for fn in (test_quick_without_state, test_quick_preserves_params,
               test_quick_retries_throttle, test_quick_stops_music,
               test_lock_restores_before_readonly,
               test_stale_retry_does_not_override,
               test_failed_restore_keeps_original,
               test_last_led_updated_after_write,
               test_quick_readonly_hint,
               test_write_blocked_when_channel_dead,
               test_do_exhausted_notifies):
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
