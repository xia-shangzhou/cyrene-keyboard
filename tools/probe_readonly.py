"""
严格只读探测：只发 GET 查询命令，绝不改动键盘任何设置。

安全设计（三重）：
1. SafetyPolicy.strict_readonly = True  —— 非查询命令直接抛异常
2. Keyboard(readonly=True)              —— 底层 send() 再拦一道，帧不出本机
3. 本脚本只调用 query()，从不调用任何 SET 构造函数

查询命令本身也要发一个请求帧（HID 协议的机制），但该帧只含命令码，
不含任何配置数据，键盘收到只返回当前状态，不会修改任何东西。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import device as D          # noqa: E402
from kb import protocol as P        # noqa: E402
from kb.safety import SafetyPolicy, UnsafeCommandError  # noqa: E402

PROBES = [
    ("GET_USB_VERSION", P.Cmd.GET_USB_VERSION, False),
    ("GET_REV", P.Cmd.GET_REV, False),
    ("GET_SKU", P.Cmd.GET_SKU, False),
    ("GET_PROFILE", P.Cmd.GET_PROFILE, False),
    ("GET_LEDONOFF", P.Cmd.GET_LEDONOFF, False),
    ("GET_LEDPARAM(b7)", P.Cmd.GET_LEDPARAM, False),
    ("GET_LEDPARAM(b8)", P.Cmd.GET_LEDPARAM, True),
    ("GET_SLEDPARAM(b7)", P.Cmd.GET_SLEDPARAM, False),
    ("GET_SLEDPARAM(b8)", P.Cmd.GET_SLEDPARAM, True),
    ("GET_KBOPTION", P.Cmd.GET_KBOPTION, False),
    ("GET_SLEEPTIME", P.Cmd.GET_SLEEPTIME, False),
    ("GET_FEATURE_LIST", P.Cmd.GET_FEATURE_LIST, False),
    ("GET_BATTERY/0x83", P.Cmd.GET_REPORT_RATE, False),
    ("GET_DONGLE_STATUS", P.Cmd.GET_DONGLE_STATUS, False),
]


def hexline(data: bytes, n: int = 32) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


def main() -> int:
    pol = SafetyPolicy(strict_readonly=True)
    print("=" * 74)
    print("严格只读模式：只发查询命令，任何写命令都会被本地拦截")
    print("=" * 74)

    kb = D.Keyboard.open_any(readonly=True)
    print(f"已连接：{kb.path.decode(errors='replace')[:78]}\n")

    ok = 0
    for name, cmd, bit8 in PROBES:
        try:
            pol.assert_allowed(cmd)
            req = P.build(cmd, None, bit8=bit8)
            resp = kb.query(cmd, bit8=bit8)
            if resp is None:
                print(f"{name:<22} 0x{cmd:02X}  无响应")
                continue
            body = resp[2:]
            nonzero = any(body)
            if nonzero:
                ok += 1
            print(f"{name:<22} 0x{cmd:02X}  帧长={len(resp)}  {'有数据' if nonzero else '全零'}")
            print(f"    发: {hexline(req, 12)}")
            print(f"    收: {hexline(resp, 32)}")
        except UnsafeCommandError as e:
            print(f"{name:<22} 0x{cmd:02X}  已拦截：{e}")
        except Exception as e:  # noqa: BLE001
            print(f"{name:<22} 0x{cmd:02X}  异常：{type(e).__name__}: {e}")
        print()

    kb.close()
    print("=" * 74)
    print(f"完成：{ok}/{len(PROBES)} 条命令返回了非零数据")
    print(f"安全统计：{pol.summary()}")
    print("键盘设置未被修改（本次只执行了查询）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
