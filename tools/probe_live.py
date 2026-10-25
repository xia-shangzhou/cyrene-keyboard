"""
真机探测：确认昔涟键盘的厂商接口与可用命令。

用法：
    python tools/probe_live.py
键盘需用 USB 线连接并处于有线模式。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import device as D  # noqa: E402
from kb import protocol as P  # noqa: E402


def step(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def main() -> int:
    step("1. 枚举 0x3151 设备")
    targets = []
    for vid, uid, label in D.KNOWN_IDS:
        found = D.find_keyboard(vid, uid)
        if found:
            targets += found
            print(f"\n[{label}] {vid:04X}:{uid:04X}  共 {len(found)} 个接口")
            for d in found:
                print("   ", D.describe_iface(d))

    if not targets:
        print("\n[!] 没找到键盘。请检查：")
        print("    - USB 线是否插好")
        print("    - 键盘底部开关是否拨到「有线/USB」而不是 2.4G/蓝牙")
        print("\n当前在线 HID 设备（参考）：")
        for d in D.list_devices():
            print(f"    {d['vendor_id']:04X}:{d['product_id']:04X}  {D.describe_iface(d)}")
        return 1

    step("2. 打开厂商接口")
    try:
        kb = D.Keyboard.open_vendor()
    except Exception as e:  # noqa: BLE001
        print("[!] 打开失败:", e)
        return 2
    print("已打开:", kb.path)
    time.sleep(0.2)

    step("3. 逐个 GET 命令试探")
    probes = [
        ("GET_USB_VERSION", P.Cmd.GET_USB_VERSION, True),
        ("GET_REV", P.Cmd.GET_REV, False),
        ("GET_SKU", P.Cmd.GET_SKU, False),
        ("GET_PROFILE", P.Cmd.GET_PROFILE, False),
        ("GET_LEDONOFF", P.Cmd.GET_LEDONOFF, False),
        ("GET_LEDPARAM", P.Cmd.GET_LEDPARAM, True),
        ("GET_SLEDPARAM", P.Cmd.GET_SLEDPARAM, True),
        ("GET_KBOPTION", P.Cmd.GET_KBOPTION, False),
        ("GET_SLEEPTIME", P.Cmd.GET_SLEEPTIME, False),
        ("GET_FEATURE_LIST", P.Cmd.GET_FEATURE_LIST, False),
        ("GET_BATTERY/REPORT_RATE", P.Cmd.GET_REPORT_RATE, False),
    ]
    ok_count = 0
    for name, cmd, bit8 in probes:
        try:
            resp = kb.query(cmd, bit8=bit8)
            if resp is None:
                print(f"  {name:<24} 0x{cmd:02X}  无响应")
                continue
            nonzero = any(resp[2:])
            flag = "OK" if nonzero else "空响应"
            if nonzero:
                ok_count += 1
            print(f"  {name:<24} 0x{cmd:02X}  [{flag}] {P.describe(resp)}")
        except Exception as e:  # noqa: BLE001
            print(f"  {name:<24} 0x{cmd:02X}  异常: {e}")
        time.sleep(0.05)

    step("4. 解析灯光参数")
    try:
        resp = kb.query(P.Cmd.GET_LEDPARAM, bit8=True)
        if resp:
            info = P.parse_ledparam(resp)
            if info:
                for k, v in info.items():
                    print(f"  {k:<12}: {v}")
    except Exception as e:  # noqa: BLE001
        print("  解析失败:", e)

    kb.close()
    step(f"完成：{ok_count}/{len(probes)} 条命令有数据返回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
