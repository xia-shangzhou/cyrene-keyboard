"""
2.4G 转发时序诊断：到底等多久才拿得到键盘的响应。

为什么要单独测
--------------
2.4G 是「PC → dongle → 键盘 → dongle → PC」两跳，文档给的往返约 220ms。
但 dongle 只有一个 64 字节缓存槽，时机不对就只能读到空帧（全 0）——
看起来像"固件不支持"，其实只是**问得太早或太晚**。

本工具用不同的等待时间反复问同一条 GET_LEDPARAM，找出能拿到真数据的时间窗。
如果所有等待都只返回全 0，再考虑"这个通道不开放配置"。

    python tools/probe_24g_timing.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

from kb import protocol as P   # noqa: E402


def hexline(b, n: int = 20) -> str:
    return " ".join(f"{x:02X}" for x in list(b)[:n])


def find_config_iface():
    for d in hid.enumerate():
        if (d["vendor_id"], d["product_id"]) != (0x3151, 0x5052):
            continue
        if d.get("usage_page") == 0xFFFF and d.get("usage") == 0x0002:
            return d
    return None


def main() -> int:
    d = find_config_iface()
    if not d:
        print("没找到 3151:5052 的配置接口（usagePage=0xFFFF usage=0x0002）")
        return 2

    print("=" * 70)
    print("2.4G 转发时序诊断")
    print("=" * 70)
    print(f"接口：itf={d.get('interface_number')} "
          f"usagePage=0x{d.get('usage_page', 0):04X} usage=0x{d.get('usage', 0):04X}")
    print(f"路径：{d['path'][:70]}\n")

    dev = hid.device()
    dev.open_path(d["path"])

    req = P.build(P.Cmd.GET_LEDPARAM, None, bit8=True)
    print("不同等待时间下的响应（同一条 GET_LEDPARAM）：")
    print(f"  {'等待':>7}  {'耗时':>7}  响应前 12 字节")
    for wait in (0.0, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2):
        t0 = time.monotonic()
        try:
            dev.send_feature_report(req)
            if wait:
                time.sleep(wait)
            resp = dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
            dt = (time.monotonic() - t0) * 1000
            body = hexline(resp, 12) if resp else "（空）"
            allzero = resp and all(v == 0 for v in resp)
            mark = "  ← 全零" if allzero else ("  ← 有数据!" if resp else "")
            print(f"  {wait * 1000:6.0f}ms  {dt:6.0f}ms  {body}{mark}")
        except Exception as e:  # noqa: BLE001
            print(f"  {wait * 1000:6.0f}ms        异常 {type(e).__name__}: {e}")
        time.sleep(0.3)

    # 连续两次读：第一读可能拿旧缓存，第二读才拿到真响应
    print("\n连续两次读取（不发新命令）：")
    try:
        dev.send_feature_report(req)
        time.sleep(0.45)
        for i in range(3):
            resp = dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
            print(f"  第 {i + 1} 次: {hexline(resp, 14) if resp else '（空）'}")
            time.sleep(0.1)
    except Exception as e:  # noqa: BLE001
        print(f"  异常 {type(e).__name__}: {e}")

    dev.close()

    print("\n" + "=" * 70)
    print("判读：")
    print("  · 某个等待值能拿到非全零数据 → 那就是 dongle 的响应窗口，")
    print("    把这套延时写进 Keyboard.query 的无线模式即可")
    print("  · 全部全零 → 这条通道不开放配置读取（写也可能不通）")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
