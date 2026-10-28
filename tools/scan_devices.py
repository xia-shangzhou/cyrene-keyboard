"""
扫描本机所有可能是「键盘配置口」的 HID 接口。

用途：切换连接模式（有线 / 2.4G / 蓝牙）后跑一次，对比 VID/PID 是否变化。
键盘的厂商配置通道通常是 usagePage >= 0xFF00 的自定义接口。

    python tools/scan_devices.py
    python tools/scan_devices.py --save 基线.json     # 存一份基线
    python tools/scan_devices.py --diff 基线.json     # 和基线对比
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

# 已知的昔涟/腹灵平台标识
KNOWN = {
    (0x38A9, 0x0017): "昔涟键盘（USB 有线，已实测可用）",
    (0x38A9, 0x0018): "昔涟键盘（蓝牙，已实测可读可写）",
    (0x3151, 0x5052): "昔涟键盘 2.4G 接收器（需先唤醒才转发配置）",
}

# 常见键盘主控 VID
VENDOR_NAMES = {
    0x38A9: "BY Technology / 容源（本键盘有线模式的 VID）",
    0x3151: "BY Technology（同平台另一组 VID）",
    0x1CA2: "BY Technology",
    0x1CA3: "BY Technology",
    0x1CA5: "BY Technology",
    0x1C4F: "SinoWealth 中颖",
    0x258A: "SinoWealth 中颖",
    0x05AC: "Apple",
    0x046D: "Logitech",
    0x1532: "Razer",
    0x24AE: "Rapoo 雷柏",
}


def key_of(d: dict) -> str:
    return f"{d['vendor_id']:04X}:{d['product_id']:04X}"


def collect() -> list[dict]:
    rows = []
    for d in hid.enumerate():
        up = d.get("usage_page", 0)
        rows.append({
            "vid": d["vendor_id"],
            "pid": d["product_id"],
            "key": key_of(d),
            "itf": d.get("interface_number"),
            "usage_page": up,
            "usage": d.get("usage", 0),
            "product": d.get("product_string") or "",
            "mfr": d.get("manufacturer_string") or "",
            "vendor_iface": up >= 0xFF00,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", help="把本次结果存为基线 JSON")
    ap.add_argument("--diff", help="与基线 JSON 对比")
    args = ap.parse_args()

    rows = collect()
    print("=" * 76)
    print("HID 设备扫描")
    print("=" * 76)

    # 只看带厂商自定义接口、或有产品名的
    cands = [r for r in rows if r["vendor_iface"] or r["product"]]
    seen = set()
    for r in sorted(cands, key=lambda x: (x["vid"], x["pid"], x["usage_page"])):
        k = (r["key"], r["usage_page"], r["usage"])
        if k in seen:
            continue
        seen.add(k)
        flag = "★配置口" if r["vendor_iface"] else "        "
        known = KNOWN.get((r["vid"], r["pid"]), "")
        vendor = VENDOR_NAMES.get(r["vid"], "")
        print(f"{flag} {r['key']}  itf={r['itf']:<3} "
              f"up=0x{r['usage_page']:04X} u=0x{r['usage']:04X}  {r['product']}")
        if known:
            print(f"           └─ 已知：{known}")
        elif vendor:
            print(f"           └─ 厂商：{vendor}")

    # 昔涟相关
    print("\n--- 是否识别到昔涟键盘 ---")
    hits = [r for r in rows if (r["vid"], r["pid"]) in KNOWN and r["vendor_iface"]]
    if hits:
        for r in hits:
            print(f"  ✅ {r['key']}  {r['product']}  "
                  f"({KNOWN[(r['vid'], r['pid'])]})")
    else:
        print("  ❌ 没找到已知的配置接口。")
        print("     如果你刚切换了连接模式，把上面 ★ 标记的 VID/PID 报给我，我来适配。")

    if args.save:
        Path(args.save).write_text(
            json.dumps([{k: r[k] for k in ("key", "usage_page", "usage", "product")}
                        for r in rows], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"\n基线已保存：{args.save}")

    if args.diff:
        base = json.loads(Path(args.diff).read_text(encoding="utf-8"))
        bkey = {(b["key"], b["usage_page"], b["usage"]) for b in base}
        nkey = {(r["key"], r["usage_page"], r["usage"]) for r in rows}
        added, removed = nkey - bkey, bkey - nkey
        print("\n--- 与基线对比 ---")
        if not added and not removed:
            print("  完全一致，连接方式没变。")
        for a in sorted(added):
            print(f"  ＋新增 {a[0]}  up=0x{a[1]:04X} u=0x{a[2]:04X}")
        for r in sorted(removed):
            print(f"  －消失 {r[0]}  up=0x{r[1]:04X} u=0x{r[2]:04X}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
