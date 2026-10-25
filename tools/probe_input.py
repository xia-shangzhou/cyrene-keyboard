"""
监听厂商接口的 input report（纯读，不发送任何数据）。

用途：找电量通知。很多三模键盘不开查询命令，而是在有事件时主动推一包
`05 [type] [value] ...` 到厂商接口的中断端点。

本脚本只调用 hid.read()，一次都不写。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0


def main() -> int:
    devs = [d for d in hid.enumerate() if d["vendor_id"] == 0x38A9 and d["product_id"] == 0x0017]
    vendor = [d for d in devs if d.get("usage_page", 0) in (0xFFFF, 0xFF55)]
    print("=" * 70)
    print("监听 input report（只读，不发送任何数据）")
    print("=" * 70)
    for d in vendor:
        print(f"  接口 itf={d.get('interface_number')} "
              f"usagePage=0x{d.get('usage_page', 0):04X} usage=0x{d.get('usage', 0):04X}")

    if not vendor:
        print("没找到厂商接口")
        return 1

    handles = []
    for d in vendor:
        try:
            dev = hid.device()
            dev.open_path(d["path"])
            dev.set_nonblocking(1)
            handles.append((d, dev))
            print(f"  已打开 itf={d.get('interface_number')} usage=0x{d.get('usage', 0):04X}")
        except Exception as e:  # noqa: BLE001
            print(f"  打开失败 itf={d.get('interface_number')}: {e}")

    if not handles:
        return 2

    print(f"\n开始监听 {DURATION:.0f} 秒。如果键盘没有主动上报，这里是安静的。")
    print("提示：可以在这期间按一下 Fn + 右Shift（查电量），看是否触发上报。\n")

    t0 = time.monotonic()
    hits = 0
    seen: dict[tuple, int] = {}
    while time.monotonic() - t0 < DURATION:
        got = False
        for d, dev in handles:
            try:
                data = dev.read(64, 40)
            except Exception:  # noqa: BLE001
                continue
            if data:
                got = True
                key = tuple(data)
                seen[key] = seen.get(key, 0) + 1
                if seen[key] <= 3:          # 同一包最多打 3 次，避免刷屏
                    tag = f"itf={d.get('interface_number')}/usage=0x{d.get('usage', 0):04X}"
                    body = " ".join(f"{b:02X}" for b in data[:24])
                    print(f"  [{time.monotonic() - t0:6.2f}s] {tag}  len={len(data)}  {body}")
                hits += 1
        if not got:
            time.sleep(0.01)

    print(f"\n共收到 {hits} 个输入包，去重后 {len(seen)} 种：")
    for k, n in sorted(seen.items(), key=lambda x: -x[1])[:12]:
        body = " ".join(f"{b:02X}" for b in k[:20])
        print(f"  ×{n:<4} {body}")

    for _d, dev in handles:
        try:
            dev.close()
        except Exception:  # noqa: BLE001
            pass
    print("\n监听结束（全程只读）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
