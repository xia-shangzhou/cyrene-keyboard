"""
音乐律动页的集成测试：不碰真机，全程 offscreen + MockBackend。

验的是 Qt 那一层的逻辑，这些是单元测试最容易漏、手动点又最容易忘的地方：
  · 启动时把主灯切到模式 22
  · 定时器真的在推帧（不是只启动了引擎但忘了 tick）
  · 停止时把灯效恢复成进入前的样子
  · 停止后不再推帧
  · 只读模式下拒绝启动

    python tools/test_music_page.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from kb.backend import MockBackend  # noqa: E402
from ui.app import MusicPage, apply_style  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


# 弹窗在 offscreen 下会卡死，全部改成打印
_popups: list[str] = []
QMessageBox.warning = staticmethod(lambda *a, **k: _popups.append(str(a[2] if len(a) > 2 else a)))
QMessageBox.critical = staticmethod(lambda *a, **k: _popups.append(str(a[2] if len(a) > 2 else a)))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)


def pump(app: QApplication, seconds: float) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication([])
    apply_style(app)

    from test_audioviz import make_sweep  # 复用扫频生成
    import tempfile

    wav = Path(tempfile.gettempdir()) / "_xilian_music_page.wav"
    make_sweep(wav)

    b = MockBackend()
    b.connect()
    page = MusicPage()
    page.backend = b
    page.resize(900, 420)
    page.show()

    print("1. 只读模式（真机后端）")

    class FakeHid(MockBackend):
        """冒充真实设备：只读时页面必须拒绝启动，一次帧都不能发。"""

        name = "hid"
        readonly = True

    fh = FakeHid()
    fh.connect()
    ro_page = MusicPage()
    ro_page.backend = fh
    ro_page.set_readonly(True)
    ro_page.start()
    check("只读时拒绝启动", not ro_page.running,
          f"弹窗={_popups[-1][:30] if _popups else '无'}")
    check("只读时灯效未被改动", fh.led.mode == 4, f"mode={fh.led.mode}")

    print("\n2. 启动")
    page.set_readonly(False)
    page.start()
    page.start()          # 故意重复点击
    check("引擎已启动", page.running)
    check("主灯切到音乐模式", b.led.mode == 22, f"mode={b.led.mode}")
    check("亮度取界面值", b.led.brightness == page.s_bright.value(), f"{b.led.brightness}")

    print("\n3. 推送（播放 2 秒扫频）")
    import winsound
    winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
    pump(app, 2.0)
    winsound.PlaySound(None, winsound.SND_PURGE)
    n_frames = page._frames
    check("定时器在推帧", n_frames >= 40, f"{n_frames} 帧 / 2s")
    check("频段数据非全零", any(v > 0 for v in b.last_bands), str(b.last_bands))
    check("界面柱子有高度", max(page.bars._levels) > 0.1,
          f"max={max(page.bars._levels):.2f}")
    # 关键回归：流式帧绝不能计进「本次写入」，否则顶栏每秒涨 50 次，
    # 会让人以为在疯狂擦写 flash（实际一次都没写）
    w = b.policy.stats["write"]
    check("流式帧不计入写入次数", w <= 1, f"写入={w} 流式={b.policy.stats['stream']}")
    check("流式帧有自己的计数", b.policy.stats["stream"] >= 40,
          f"{b.policy.stats['stream']} 帧")

    print("\n4. 停止")
    page.stop()
    check("引擎已停", not page.running)
    check("灯效已恢复", b.led.mode == 4 and b.led.brightness == 4,
          f"mode={b.led.mode} 亮度={b.led.brightness}")
    check("柱子归零", max(page.bars._levels) == 0.0)

    n_after = page._frames
    pump(app, 0.4)
    check("停止后不再推帧", page._frames == n_after, f"{page._frames}")

    print("\n5. 参数联动")
    page.style = 0
    page.cb_style.setCurrentIndex(1)
    check("样式写入 direction", page._music_state().direction == 1,
          f"direction={page._music_state().direction}")

    # 采集线程死掉（拔耳机/切默认设备/驱动崩）时，界面不能一直显示"运行中"
    print("\n6. 采集中断感知")
    page.set_readonly(False)
    page.start()
    pump(app, 0.3)
    eng = page.engine
    check("已启动，可注入错误", page.running and eng is not None)
    if eng is not None:
        eng.error = "设备已断开（模拟）"
        page._tick()
        check("检测到采集错误后自动停止", not page.running)
        check("状态栏显示具体原因", "设备已断开" in page.state_lbl.text(),
              page.state_lbl.text())
        check("灯效已从音乐模式还原", b.led.mode == 4, f"mode={b.led.mode}")

    try:
        wav.unlink()
    except OSError:
        pass

    print()
    if fails:
        print(f"失败 {len(fails)} 项: {fails}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
