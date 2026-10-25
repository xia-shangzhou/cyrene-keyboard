"""
2.4G 无线能不能配灯效 —— 深挖探测（只读，只发 GET 查询）。

为什么还要重测
--------------
日志 §6.2 的旧结论是「2.4G 只通电量，配置命令不转发」，依据是：
发 `GET_LEDPARAM` 读到全零、且 **6ms 就返回**（推断是 dongle 本地空缓存）。

但 2026-10-25 蓝牙那次证明「文档/推断说不行」并不可靠 —— 换传输层后就通了。
所以 2.4G 这条也要把所有可能性排一遍再下结论，旧测试可能漏了这些：

  1. **传输层不只是 Feature Report**：蓝牙就是 Output Report + Input 通知。
     2.4G 的 dongle 是另一个固件，完全可能也是 Output 通道。
  2. **帧可能也要 `0x55` 前导**（蓝牙需要，2.4G 未必）。
  3. **键盘可能在睡眠**：睡眠时 dongle 只能用缓存/空帧立刻应答（就是那个 6ms）。
     醒来时才会真正转发 —— 旧测试没排除这一点。
  4. **配置口可能不是 itf=2**：旧测试只看 usage=0x0002，把 itf=1(usage=0x0001) 跳过了。

本工具做的事
------------
对 3151:* / 38A9:* 的每个厂商接口，把「传输方式 × 帧格式」的组合全试一遍，
每种都打印原始收发字节、耗时和判定。

    python tools/probe_24g_deep.py
    python tools/probe_24g_deep.py --wait 0.8      # 等更久（排除"链路慢"）
    python tools/probe_24g_deep.py --all           # 连非厂商接口也扫（排查隐藏配置口）
    python tools/probe_24g_deep.py --loop          # 循环发，边发边按键盘唤醒

⚠ 本工具只发 GET 命令，不会改任何设置。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

from kb import protocol as P   # noqa: E402

WATCH_IDS = {0x3151: "2.4G 接收器 / 同平台无线", 0x38A9: "本键盘（USB/蓝牙）"}
MARKER = 0x55


def hexline(b, n: int = 24) -> str:
    return " ".join(f"{x:02X}" for x in list(b)[:n]) if b else "（空）"


def list_ifaces(show_all: bool) -> list[dict]:
    out = []
    for d in hid.enumerate():
        if d["vendor_id"] not in WATCH_IDS:
            continue
        if not show_all and d.get("usage_page", 0) < 0xFF00:
            continue
        out.append(d)
    return out


def variants(cmd: int) -> list[tuple[str, bytes]]:
    """候选帧。返回 (说明, 不含 Report ID 的载荷)。"""
    usb = P.build(cmd, None, bit8=True)      # 65B, [0]=ReportID 0
    body = usb[1:]                            # 64B, 从 cmd 开始
    return [
        ("原样USB(cmd起)", body),
        ("0x55前导", bytes([MARKER]) + body),
    ]


def probe_iface(d: dict, cmd: int, wait: float, loop: int) -> None:
    name = (f"itf={d.get('interface_number')} "
            f"page=0x{d.get('usage_page', 0):04X} usage=0x{d.get('usage', 0):04X}")
    print(f"\n  ── {name} ──")

    for label, payload in variants(cmd):
        for transport in ("feature", "output"):
            # ⚠ 两种传输层的**读取方式不一样**，不能混用：
            #   feature → get_feature_report（对只支持 Feature 的接口调 read() 会 OSError: read error）
            #   output  → read() 收 Input Report 通知
            resp: list[int] = []
            err = ""
            t0 = time.monotonic()
            try:
                dev = hid.device()
                dev.open_path(d["path"])
            except Exception as e:  # noqa: BLE001
                print(f"    {label:<14} {transport:<8} 打不开：{e}")
                continue
            try:
                if transport == "feature":
                    dev.send_feature_report(bytes([P.REPORT_ID]) + payload)
                    time.sleep(wait)
                    try:
                        fr = dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
                        resp = list(fr) if fr else []
                    except Exception as e:  # noqa: BLE001
                        err = f"{type(e).__name__}: {e}"
                else:
                    dev.write(bytes([P.REPORT_ID]) + payload)
                    deadline = time.monotonic() + wait
                    while time.monotonic() < deadline:
                        try:
                            r = dev.read(64, timeout_ms=max(30, int(wait * 1000)))
                        except Exception as e:  # noqa: BLE001
                            err = f"{type(e).__name__}: {e}"
                            break
                        if r:
                            resp = list(r)
                            break
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
            finally:
                try:
                    dev.close()
                except Exception:  # noqa: BLE001
                    pass
            dt = 1000 * (time.monotonic() - t0)

            if err and not resp:
                print(f"    {label:<14} {transport:<8} {dt:5.0f}ms  {err}")
            elif not resp:
                print(f"    {label:<14} {transport:<8} {dt:5.0f}ms  无响应")
            else:
                print(f"    {label:<14} {transport:<8} {dt:5.0f}ms  {hexline(resp, 16)}")
                body = resp[1:] if resp and resp[0] in (P.REPORT_ID, 6) else resp
                if body and body[0] == MARKER:
                    body = body[1:]
                if body and body[0] == cmd:
                    head = body[1:9]
                    print("        ✅ 命令码正确，" +
                          ("参数非零 → 真的转发到了键盘" if any(head)
                           else "但参数全零 → 分不清 0 值和空响应"))
                elif body and body[0] == 0:
                    print("        ⚠ 原样回显（命令没被处理）")
            time.sleep(0.1)

    # 循环模式：一边连发一边让用户敲键盘，看键盘醒来后会不会转发
    if loop > 0:
        print(f"    ── 循环探测 {loop} 次，请同时随便敲几下键盘 ──")
        try:
            dev = hid.device()
            dev.open_path(d["path"])
        except Exception:  # noqa: BLE001
            return
        try:
            for i in range(loop):
                usb = P.build(cmd, None, bit8=True)
                dev.send_feature_report(bytes([P.REPORT_ID]) + usb[1:])
                deadline = time.monotonic() + wait
                got = []
                while time.monotonic() < deadline:
                    r = dev.read(64, timeout_ms=max(30, int(wait * 1000)))
                    if r:
                        got = list(r)
                        break
                print(f"      {i + 1:>2}/{loop}  {hexline(got, 12) if got else '无响应'}")
                time.sleep(0.05)
        finally:
            dev.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=0.5)
    ap.add_argument("--all", action="store_true", help="连非厂商接口也扫")
    ap.add_argument("--loop", type=int, default=0, help="循环探测次数（边敲键盘）")
    ap.add_argument("--cmd", type=lambda s: int(s, 0), default=P.Cmd.GET_LEDPARAM)
    args = ap.parse_args()

    devs = list_ifaces(args.all)
    print("=" * 78)
    print("2.4G 配置通道深挖探测（只读）")
    print("=" * 78)

    if not devs:
        print("\n没找到 3151:* 或 38A9:* 的接口。请确认接收器插在 USB 口上。")
        return 2

    by_dev: dict[tuple[int, int], list[dict]] = {}
    for d in devs:
        by_dev.setdefault((d["vendor_id"], d["product_id"]), []).append(d)

    for (vid, pid), group in by_dev.items():
        print(f"\n【0x{vid:04X}:0x{pid:04X}】{WATCH_IDS.get(vid, '')}")
        for d in group:
            print(f"  itf={d.get('interface_number')} page=0x{d.get('usage_page', 0):04X} "
                  f"usage=0x{d.get('usage', 0):04X}  {d.get('product_string') or ''}")
        # 只对厂商接口发命令（非厂商接口发进去只会被系统吃掉）
        for d in [x for x in group if x.get("usage_page", 0) >= 0xFF00]:
            probe_iface(d, args.cmd, args.wait, args.loop)

    print("\n" + "=" * 78)
    print("怎么读结果：")
    print("  · 任何一格出现「参数非零」→ 2.4G 能配灯，记下那条组合（传输 + 前导）")
    print("  · 全是「无响应/全零/原样回显」→ 才敢下「2.4G 不转发配置」的结论")
    print("  · 全部无响应时，先按几下键盘唤醒再重跑（睡眠时 dongle 只会本地应答）")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
