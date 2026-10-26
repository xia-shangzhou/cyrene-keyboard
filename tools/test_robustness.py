"""健壮性回归测试 —— 只覆盖 2026-10-26 修的边界问题，不碰真实硬件。

    python tools/test_robustness.py          # 全部
    python tools/test_robustness.py --list   # 看有哪几组

为什么单独一个文件：smoke_test 只走一次成功路径，边界（空数组、打开失败、
除零）根本走不到。这里的每条断言都对着一个真实缺陷，改坏了会立刻红。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

import kb.device as D  # noqa: E402
import kb.protocol as P  # noqa: E402
from kb.audioviz import AudioVizEngine, VizSettings  # noqa: E402

_fail = 0
_pass = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _fail, _pass
    if cond:
        _pass += 1
        print(f"  [OK]   {name}" + (f"  {detail}" if detail else ""))
    else:
        _fail += 1
        print(f"  [FAIL] {name}  {detail}")


# ---------------------------------------------------------------- ① 蓝牙帧长度
def t_ble_frame_lengths() -> None:
    print("\n① build_audio_viz_ble 任意长度输入都不该崩")
    cases = [
        ("空列表", []),
        ("1 个值", [3]),
        ("8 个值（UI 的真实输入）", [i % 8 for i in range(8)]),
        ("15 个值", [7] * 15),
        ("16 个值（标准）", [15] * 16),
        ("40 个值（超长）", [15] * 40),
    ]
    for label, bands in cases:
        try:
            frame = P.build_audio_viz_ble(bands)
            ok = len(frame) == P.FRAME_LEN and frame[1] == P.Cmd.SET_AUDIO_VIZ
            check(f"{label} 不抛异常且帧长正确", ok, f"{len(frame)} 字节")
        except Exception as e:  # noqa: BLE001
            check(f"{label} 不抛异常且帧长正确", False, f"{type(e).__name__}: {e}")

    # 8 个值必须展开成"相邻两段同值"，不能变成左 8 右 0。
    # 取值必须 <= AUDIO_BLE_MAX(5)，否则会被正常截断，看不出展开效果。
    src = [1, 2, 3, 4, 5, 1, 2, 3]
    f8 = P.build_audio_viz_ble(src)
    nibbles = []
    for j in range(P.AUDIO_BANDS // 2):
        hi, lo = f8[2 + j] >> 4, f8[2 + j] & 0x0F
        nibbles += [lo, hi]                    # high_first=False → 低 nibble 在前
    expect = [v for v in src for _ in range(2)]
    check("8 个值按相邻两段展开", nibbles == expect, f"{nibbles}")

    # 超出 AUDIO_BLE_MAX 的必须截断到上限，不能被 0x0F 掩码搞成回绕
    # （注意比的是**半字节**：9 截断成 5 后打包出来是 0x55 = 85）
    fbig = P.build_audio_viz_ble([9] * 16)
    nibs = []
    for b in fbig[2:10]:
        nibs += [b & 0x0F, b >> 4]
    check("超上限值截断到 AUDIO_BLE_MAX",
          all(v == P.AUDIO_BLE_MAX for v in nibs), f"半字节 {nibs}")

    # 补齐的段必须是 0，不能是垃圾值
    f3 = P.build_audio_viz_ble([9, 9, 9])
    tail = [f3[2 + j] for j in range(2, P.AUDIO_BANDS // 2)]
    check("不足 16 个值时后段补 0", all(b == 0 for b in tail), f"后段 {tail}")


# ---------------------------------------------------------------- ② 打开失败不中断
def t_open_any_failure() -> None:
    print("\n② open_any 打不开设备时要给出可读错误，而不是抛底层 OSError")
    real_find = D.find_keyboard
    real_vendor = D.is_vendor_iface
    fake_path = b"\\\\?\\hid#vid_38a9&pid_0017#8&0000&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"
    try:
        D.find_keyboard = lambda vid, pid: [{
            "path": fake_path, "vendor_id": vid, "product_id": pid,
            "usage_page": 0xFFFF, "usage": 0x0002, "product_string": "假键盘",
        }]
        D.is_vendor_iface = lambda d: True
        try:
            D.Keyboard.open_any(readonly=True, discover=False)
            check("设备打不开时抛出 RuntimeError", False, "居然没抛异常")
        except RuntimeError as e:
            msg = str(e)
            check("设备打不开时抛出 RuntimeError", True)
            check("错误信息能指导用户", "打不开" in msg and "占用" in msg, msg[:60])
            check("不是裸的 OSError", "WinError" not in msg.split("：")[0])
        except OSError as e:
            check("设备打不开时抛出 RuntimeError", False,
                  f"穿透了 OSError（try/except 没生效）：{e}")
    finally:
        D.find_keyboard = real_find
        D.is_vendor_iface = real_vendor


# ---------------------------------------------------------------- ③ 除零 NaN
def t_gain_zero() -> None:
    print("\n③ gain=0 / 负数时不能把 NaN 污染进电平")
    for gain in (0.0, -1.0, 1e-9):
        eng = AudioVizEngine(VizSettings(gain=gain, auto_gain=False))
        eng._last_t = 0.0
        frame = np.full(eng.CHUNK if hasattr(eng, "CHUNK") else 1024, 0.3, dtype=np.float32)
        try:
            for _ in range(30):           # 多喂几帧，让指数平滑有机会扩散 NaN
                eng._process(frame)
            lv = np.array(eng.levels(), dtype=np.float64)
            check(f"gain={gain} 电平无 NaN/Inf",
                  bool(np.all(np.isfinite(lv))), f"levels={lv[:3]}")
        except Exception as e:  # noqa: BLE001
            check(f"gain={gain} 电平无 NaN/Inf", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- ④ 单实例互斥
def t_single_instance() -> None:
    print("\n④ 单实例互斥体")
    import main as M

    first = M._acquire_single_instance()
    second = M._acquire_single_instance()
    check("第一次能拿到锁", first is True)
    check("同进程再次获取被拒（模拟多开）", second is False)
    check("互斥体名字带 Local\\ 前缀（仅本会话）", M.MUTEX_NAME.startswith("Local\\"),
          M.MUTEX_NAME)
    check("找不存在的窗口返回 False 而不是抛异常",
          M._focus_existing_window() is False)


def t_version_parse() -> None:
    print("\n⑤ 版本号解析（构建元数据不能参与比较）")
    from kb.update import parse_version as parse

    cases = [
        ("0.4.2", (0, 4, 2)),
        ("v0.4.2", (0, 4, 2)),
        ("0.4.2+build5", (0, 4, 2)),       # 曾经解析成 (0,4,25) → 误报有新版本
        ("0.4.2+20261026", (0, 4, 2)),
        ("0.5", (0, 5)),
    ]
    for text, want in cases:
        got = parse(text)
        check(f"{text!r} → {want}", got == want, f"实际 {got}")
    check("带构建元数据的版本不比纯版本新", not (parse("0.4.2+build5") > parse("0.4.2")),
          f"{parse('0.4.2+build5')} vs {parse('0.4.2')}")


def t_read_exception_counts() -> None:
    print("\n⑥ HID 层抛异常要计入配置失败（阈值放宽到 5 次）")
    import kb.backend as B

    class Boom:
        """读就抛异常的假设备（模拟拔线/句柄失效）。"""

        readonly = False

        def query(self, *a, **k):
            raise OSError("device unplugged")

        def send(self, _f):
            pass

        def config_alive(self, *a, **k):
            return True

        def close(self):
            pass

    hb = B.HidBackend(readonly=False)
    hb.kb = Boom()
    hb.config_ok = True
    hb.policy.read_interval = 0        # 关掉 50ms 读频控，否则会被 QueryThrottled 跳过

    for _ in range(4):
        got = hb.get_led()
        check("异常时返回 None 而不是抛出去", got is None, f"第 {hb._config_exc} 次")
    check("4 次异常后仍算可用（瞬态不该一票否决）", hb.config_ok is True,
          f"exc={hb._config_exc}")
    hb.get_led()
    check("第 5 次异常后判定不可用", hb.config_ok is False, f"exc={hb._config_exc}")


def main() -> int:
    if "--list" in sys.argv[1:]:
        print("① 蓝牙帧长度  ② 打开失败容错  ③ 增益除零  ④ 单实例互斥  "
              "⑤ 版本号解析  ⑥ HID 异常计数")
        return 0
    t_ble_frame_lengths()
    t_open_any_failure()
    t_gain_zero()
    t_single_instance()
    t_version_parse()
    t_read_exception_counts()
    print("\n" + "=" * 62)
    print(f"通过 {_pass} 项，失败 {_fail} 项")
    print("=" * 62)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
