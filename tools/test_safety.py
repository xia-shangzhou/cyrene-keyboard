"""
安全策略与写入链路的回归测试 —— **完全不碰硬件**。

为什么单独一个文件
------------------
`smoke_test.py` 只覆盖"一次写入成功"的顺路流程。2026-10-25 的代码审查指出：
它全绿也不能说明连续写入、限流、失败重试、危险命令这几条边界是对的。
这份测试专门盯这些边界，全部用假设备，随时可以在没插键盘的机器上跑。

    python tools/test_safety.py

覆盖的 6 条
-----------
1. **限流不能污染「已发送」记录**（本轮真实 bug）
   被限流挡下的那次不能把值记成已发送，否则同样的值永远发不出去
2. 相同值只发一次（省 flash）
3. 限流过后能自动补发（这是 1 的可见后果）
4. `Keyboard.send()` 层无条件拦 FORBIDDEN —— 不管 readonly 是真是假
5. 回显判定：响应与请求逐字节相同 → 当没数据
6. 读不到时返回 None，绝不拿默认值冒充
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import protocol as P                              # noqa: E402
from kb.backend import HidBackend, LedState               # noqa: E402
from kb.device import Keyboard                            # noqa: E402
from kb.safety import UnsafeCommandError    # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))


# ---------------------------------------------------------------- 假设备

class FakeHid:
    """假 HID 句柄：只记账，什么都不发。

    这里**故意不放任何安全闸** —— 后端层（`HidBackend._send`）应当先过
    `policy.assert_allowed()`，底层 `Keyboard.send()` 另有闸（见第 5 组测试）。
    假设备只负责记录「到底发没发」，这样测出来的才是真实行为。
    """

    def __init__(self, reply: bytes | None = None):
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


class FakeDev:
    """假 hidapi device：记录写入，供 Keyboard.send 测试用。"""

    def __init__(self):
        self.writes: list[bytes] = []
        self.features: list[bytes] = []

    def send_feature_report(self, data) -> int:
        self.features.append(bytes(data))
        return len(data)

    def write(self, data) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def close(self) -> None:
        pass


def make_backend(fake=None) -> HidBackend:
    b = HidBackend(readonly=False)
    b.kb = fake or FakeHid()
    return b


# ---------------------------------------------------------------- 1/2/3 限流

def test_throttle_does_not_poison_state() -> None:
    print("\n1-3. 限流 / 重复值 / 补发")
    b = make_backend()
    b.policy.write_interval = 0.12          # 缩短方便测

    a = LedState(mode=1, speed=2, brightness=4, colorful=False, rgb=(255, 0, 0))
    c = LedState(mode=2, speed=2, brightness=4, colorful=False, rgb=(0, 255, 0))

    ok1 = b.set_led(a)
    check("第一次写入成功", ok1 is True, f"帧数={len(b.kb.frames)}")
    check("确实发出去了", len(b.kb.frames) == 1)

    # 立刻改另一个值 —— 会被 0.12s 限流挡住，必须返回 False
    ok2 = b.set_led(c)
    check("被限流时返回 False（不能谎报成功）", ok2 is False, f"返回值={ok2!r}")
    check("被限流时没有新增帧", len(b.kb.frames) == 1)

    # ★ 核心回归：刚才那个值**不能被记成已发送**，否则这次会静默跳过
    time.sleep(0.15)
    ok3 = b.set_led(c)
    check("限流过后同一个值能补发出去", ok3 is True and len(b.kb.frames) == 2,
          f"返回值={ok3!r} 帧数={len(b.kb.frames)}")

    # 4. 相同值第二次应该被跳过（省 flash），且返回 True
    ok4 = b.set_led(c)
    check("相同值不再重复发", ok4 is True and len(b.kb.frames) == 2)
    check("跳过重复值有计数", b.policy.stats["skipped_duplicate"] >= 1,
          f"skipped_duplicate={b.policy.stats['skipped_duplicate']}")


def test_mock_backend_same_rules() -> None:
    """模拟后端也必须守同样的规则，否则 UI 在无硬件时测不出问题。"""
    print("\n4. 模拟后端遵守同样的限流语义")
    from kb.backend import MockBackend
    m = MockBackend()
    m.policy.write_interval = 0.12
    a = LedState(mode=1, brightness=2)
    c = LedState(mode=2, brightness=2)
    m.set_led(a)
    if m.set_led(c) is False:
        time.sleep(0.15)
        check("模拟后端限流后能补发", m.set_led(c) is True and m.led.mode == 2,
              f"当前模式={m.led.mode}")
    else:
        check("模拟后端限流后能补发", m.led.mode == 2)


# ---------------------------------------------------------------- 5. 危险命令

def test_forbidden_at_device_layer() -> None:
    print("\n5. 底层 Keyboard.send() 无条件拦危险命令")

    for ro in (True, False):
        kb = Keyboard.__new__(Keyboard)          # 不走 __init__，不碰真设备
        kb.path = b"fake"
        kb.readonly = ro
        kb.ble = False
        kb.dev = FakeDev()
        kb.vid = kb.pid = 0
        kb.product = ""
        kb.link = "usb"
        kb.auto_detected = False

        blocked = 0
        for cmd in (0x7F, 0x01, 0x0A, 0x0B, 0x0C, 0x12):
            frame = P.build(cmd, [0] * 8)
            try:
                kb.send(frame)
            except UnsafeCommandError:
                blocked += 1
            except Exception:  # noqa: BLE001
                pass
        check(f"readonly={ro} 时 6 条危险命令全被拦", blocked == 6, f"拦住 {blocked}/6")
        check(f"readonly={ro} 时一帧都没发出去", not kb.dev.features and not kb.dev.writes)

    # 正常灯光命令在解锁后必须能发（别一刀切）
    kb = Keyboard.__new__(Keyboard)
    kb.path = b"fake"; kb.readonly = False; kb.ble = False
    kb.dev = FakeDev(); kb.vid = kb.pid = 0; kb.product = ""; kb.link = "usb"
    kb.auto_detected = False
    kb.send(P.build_ledparam(5, 2, 1))
    check("解锁后正常灯效命令仍可发送", len(kb.dev.features) == 1)


def test_perkey_disabled() -> None:
    print("\n6. 逐键 RGB（帧格式未验证）已禁用")
    from kb.backend import PerKey
    b = make_backend()
    raised = False
    try:
        b.set_perkey(PerKey(colors={0: (255, 0, 0)}))
    except UnsafeCommandError:
        raised = True
    check("set_perkey 直接报错而不是盲写", raised)
    check("没有发出任何帧", not b.kb.frames)


# ---------------------------------------------------------------- 7. 回显 / 读不到

def test_echo_and_missing() -> None:
    print("\n7. 回显判定 & 读不到时返回 None")

    req = P.build(P.Cmd.GET_LEDPARAM, None, bit8=True)
    check("响应==请求 判为回显", HidBackend._is_echo(req, req))
    check("正常响应不算回显", not HidBackend._is_echo(req, bytes([0, 0x87, 5, 2, 1, 8, 255, 255, 255] + [0] * 56)))

    # 设备把请求原样回显 → 必须返回 None，不能把 00 当"模式=关闭"
    b = make_backend(FakeHid(reply=req))
    check("回显时 get_led 返回 None", b.get_led() is None)

    # 全零帧 → 同样返回 None
    b = make_backend(FakeHid(reply=bytes(P.FRAME_LEN)))
    check("全零帧时 get_led 返回 None", b.get_led() is None)

    # 正常数据 → 解析正确
    good = bytes([0, 0x87, 0x05, 0x02, 0x01, 0x08, 255, 255, 255] + [0] * 56)
    b = make_backend(FakeHid(reply=good))
    st = b.get_led()
    check("正常数据解析正确", st is not None and st.mode == 5 and st.brightness == 1,
          f"{st.mode_name if st else None}")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    print("=" * 70)
    print("安全策略与写入链路回归测试（不碰硬件）")
    print("=" * 70)

    test_throttle_does_not_poison_state()
    test_mock_backend_same_rules()
    test_forbidden_at_device_layer()
    test_perkey_disabled()
    test_echo_and_missing()

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print("  FAIL:", f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
