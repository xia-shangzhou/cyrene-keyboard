"""无界面冒烟测试：验证模块导入、后端切换、灯效读写链路。"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import protocol as P                      # noqa: E402
from kb.audioviz import AudioVizEngine            # noqa: E402
from kb.backend import LedState, MockBackend      # noqa: E402
from kb.safety import FORBIDDEN, SafetyPolicy, UnsafeCommandError  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        fails.append(name)


print("1. 协议层")
f = P.build(P.Cmd.GET_SKU)
check("帧长 65", len(f) == 65, f"len={len(f)}")
check("ReportID=0", f[0] == 0)
check("命令码就位", f[1] == 0xD0)
check("Bit7 校验和自洽", (sum(f[0:7]) + f[7]) & 0xFF == 0xFF, f"chk=0x{f[7]:02X}")

led = P.build_ledparam(4, 2, 4, 0x70, (255, 128, 0))
check("LED帧 SET校验自洽", (sum(led[1:9]) + led[9]) & 0xFF == 0xFF, f"chk=0x{led[9]:02X}")
check("灯效模式表非空", len(P.LED_MODES) >= 20, f"{len(P.LED_MODES)} 种")

# 音乐律动帧：校验和在 frame[8]（同 GET），数据从 frame[9] 起 —— 与 LED 命令完全不同
av = P.build_audio_viz([6, 5, 4, 3, 2, 1, 0] + [0] * 9)
check("0x0D 命令码", av[1] == 0x0D)
check("0x0D 校验和在 frame[8]", (sum(av[0:8]) + av[8]) & 0xFF == 0xFF, f"chk=0x{av[8]:02X}")
check("0x0D padding 为 0", all(av[2 + i] == 0 for i in range(6)))
check("0x0D 频段落在 frame[9..24]", list(av[9:16]) == [6, 5, 4, 3, 2, 1, 0], str(list(av[9:16])))
check("0x0D 频段上限截断到 6", P.build_audio_viz([200] * 16)[9] == 6)

print("\n2. 安全策略")
pol = SafetyPolicy()
for cmd, label in [(0x7F, "bootloader"), (0x0A, "改键"), (0x0B, "宏"),
                   (0x01, "恢复出厂"), (0xF8, "配对")]:
    try:
        pol.assert_allowed(cmd)
        check(f"应拦截 {label}", False)
    except UnsafeCommandError:
        check(f"已拦截 {label} (0x{cmd:02X})", True)

try:
    pol.assert_allowed(0x99)
    check("应拦截未知命令", False)
except UnsafeCommandError:
    check("已拦截未知命令 0x99", True)

check("放行 GET_LEDPARAM", pol.assert_allowed(P.Cmd.GET_LEDPARAM) is None)
check("放行 SET_LEDPARAM", pol.assert_allowed(P.Cmd.SET_LEDPARAM) is None)
check("放行 SET_AUDIO_VIZ", pol.assert_allowed(P.Cmd.SET_AUDIO_VIZ) is None)
check("0x0E 屏幕取色仍拦截", P.Cmd.SET_SCREEN_COLOR in FORBIDDEN)
check("重复值跳过", not pol.values_unchanged("k", (1, 2)))
pol.remember_values("k", (1, 2))
check("重复值跳过", pol.values_unchanged("k", (1, 2)))

# 流式命令不能被 350ms 写限流卡住，否则音乐律动只能跑到 3Hz
pol2 = SafetyPolicy()
first = pol2.may_send(P.Cmd.SET_AUDIO_VIZ, now=100.0)
second = pol2.may_send(P.Cmd.SET_AUDIO_VIZ, now=100.03)
check("0x0D 不受 350ms 写限流", first and second, "30ms 间隔应放行")
third = pol2.may_send(P.Cmd.SET_AUDIO_VIZ, now=100.034)
check("0x0D 仍有兜底限流", not third, "4ms 间隔应被档")

print("\n3. 后端（模拟）")
b = MockBackend()
b.connect()
st = b.get_led()
check("读到灯效", isinstance(st, LedState), st.mode_name)
st.mode, st.brightness = 2, 3
b.set_led(st)
check("写入生效", b.get_led().mode == 2 and b.get_led().brightness == 3)
batt = b.get_battery()
check("读到电量", batt.percent is not None, batt.label)
check("模拟后端接受律动帧", b.set_audio_viz([1] * 16) and len(b.last_bands) == 16)

print("\n4. 界面构建")
try:
    from PySide6.QtWidgets import QApplication
    from ui.app import MainWindow, apply_style
    app = QApplication([])
    apply_style(app)
    w = MainWindow()
    check("主窗口构造", w is not None, f"后端={w.backend.name}")
    check("页面数量", w.stack.count() == 7, f"{w.stack.count()} 个")
    check("音乐律动页就位", w.page_music is not None
          and w.page_music.btn_run.text() == "开始律动")
    check("音频引擎可用", AudioVizEngine.available())
    w._refresh()
    check("状态条刷新", bool(w.topbar.sub.text()))
    w.backend.disconnect()
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("主窗口构造", False, str(e))

print()
if fails:
    print(f"失败 {len(fails)} 项: {fails}")
    raise SystemExit(1)
print("全部通过")
