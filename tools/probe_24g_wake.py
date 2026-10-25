"""
2.4G 深度排查第二弹：验证「键盘醒着时 dongle 会不会转发配置命令」。

为什么需要这一步
----------------
`0xF7` 能读到真实电量（`00 00 64 00 00 01 01 01`）⇒ 接收器与键盘的链路是通的、
键盘在线。但 `GET_LEDPARAM` 仍然回全零。两种解释：

  A. 接收器固件真的不转发厂商配置报告（和旧结论一致）
  B. 键盘在睡眠/空闲态时，接收器只回本地缓存；只有键盘**刚被操作过**才真转发

旧测试用的是「发完睡一会儿再读」，从来没在**检测到按键之后立刻发**，
所以 B 一直没被排除。本工具就是干这个的。

做法
----
1. 打开 `itf=1`（有 IN 端点，能收到按键报告）持续监听
2. 一旦收到按键报告 → **立刻**在 `itf=2` 发 `GET_LEDPARAM`，再读回
3. 同时用不同 Report ID 试一遍（0 / 1 / 5 / 6），排除"配置报告要换 ID"
4. 可选：同时监听 `itf=1` 有没有收到非按键的厂商响应

    python tools/probe_24g_wake.py                # 需要你在 10 秒内敲几下键盘
    python tools/probe_24g_wake.py --seconds 20
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

from kb import protocol as P   # noqa: E402

VID, PID = 0x3151, 0x5052


def hexline(b, n: int = 20) -> str:
    return " ".join(f"{x:02X}" for x in list(b)[:n]) if b else "（空）"


def find(itf: int, page: int | None = None, usage: int | None = None) -> dict | None:
    for d in hid.enumerate():
        if d["vendor_id"] != VID or d["product_id"] != PID:
            continue
        if d.get("interface_number") != itf:
            continue
        if page is not None and d.get("usage_page") != page:
            continue
        if usage is not None and d.get("usage") != usage:
            continue
        return d
    return None


def find_kbd() -> dict | None:
    """键盘真正的按键报告在 itf=0 / page=0x0001 / usage=0x0006。

    ⚠ 别用 itf=1 —— 那个接口上挂了 5 个集合（厂商、键盘、多媒体、系统、鼠标），
    按 usage 会挑到厂商集合，读不到按键。
    """
    return find(0, 0x0001, 0x0006) or find(1, 0x0001, 0x0006)


def cache_slot_check(tx: dict) -> str | None:
    """验证「dongle 只回缓存槽里上一条响应」这件事。

    做法：先发 0xF7（接收器本地命令，一定有应答）读一次当基准，
    再连发 3 次 GET_LEDPARAM 读一次 —— 如果读到的还是 0xF7 那串，
    说明 GET_LEDPARAM 根本没产生新响应，我们读到的是残留。
    """
    print("\n=== ③ 缓存槽验证（itf=2）===")
    base = None
    try:
        dev = hid.device()
        dev.open_path(tx["path"])
    except Exception as e:  # noqa: BLE001
        print(f"  打不开：{e}")
        return None
    try:
        dev.send_feature_report(P.build(P.Cmd.GET_DONGLE_STATUS))
        time.sleep(0.3)
        fr = dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
        base = hexline(list(fr), 12) if fr else None
        print(f"  ① 发 0xF7 读回      {base}")

        for _ in range(3):
            dev.send_feature_report(P.build(P.Cmd.GET_LEDPARAM, None, bit8=True))
            time.sleep(0.2)
        fr = dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
        after = hexline(list(fr), 12) if fr else None
        print(f"  ② 连发 3 次 0x87 后  {after}")

        if base and after == base:
            print("  ⇒ 一模一样：0x87 没有产生新响应，读到的是缓存槽残留")
        elif base and after and after != base:
            print("  ⇒ 变了！0x87 真的产生了新响应 —— 需要重新判定")
        else:
            print("  ⇒ 读到空，缓存槽已清空")

        dev.send_feature_report(P.build(P.Cmd.GET_DONGLE_STATUS))
        time.sleep(0.3)
        fr = dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
        print(f"  ③ 再发 0xF7 读回     {hexline(list(fr), 12) if fr else '（空）'}")
    finally:
        dev.close()
    return base


def report_id_sweep(tx: dict) -> None:
    """试不同 Report ID 发同一个 GET，看有没有哪个 ID 能被回。"""
    print("\n=== ① Report ID 扫描（itf=2，GET_LEDPARAM）===")
    payload = P.build(P.Cmd.GET_LEDPARAM, None, bit8=True)   # 65B, [0]=0
    body = payload[1:]                                        # 64B, cmd 起
    for rid in (0, 1, 2, 5, 6):
        for label, data in (("原样", body), ("0x55前导", bytes([0x55]) + body)):
            try:
                dev = hid.device()
                dev.open_path(tx["path"])
            except Exception as e:  # noqa: BLE001
                print(f"  rid={rid} {label:<10} 打不开 {e}")
                continue
            try:
                dev.send_feature_report(bytes([rid]) + data)
                time.sleep(0.45)
                fr = dev.get_feature_report(rid, 65)
                got = list(fr) if fr else []
                mark = ""
                if got and any(got[:8]):
                    b = got[1:] if got[0] == rid else got
                    if b and b[0] == 0x55:
                        b = b[1:]
                    if b and b[0] == P.Cmd.GET_LEDPARAM and any(b[1:9]):
                        mark = "  ✅✅ 拿到真实数据！"
                    else:
                        mark = "  ⚠ 有非零数据，但不像这条命令的响应"
                print(f"  rid={rid} {label:<10} {hexline(got, 12)}{mark}")
            except Exception as e:  # noqa: BLE001
                print(f"  rid={rid} {label:<10} 异常 {type(e).__name__}: {e}")
            finally:
                try:
                    dev.close()
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.1)


def long_wait_check(tx: dict) -> None:
    """缓存槽里没有新响应的另一种解释：RF 往返很慢，缓存是异步更新的。

    做法：发一条命令后分别等 0.5s / 2s / 4s 再读缓存槽，看有没有变化。
    """
    print("\n=== ④ 长等待验证（排除「只是慢」）===")
    try:
        dev = hid.device()
        dev.open_path(tx["path"])
    except Exception as e:  # noqa: BLE001
        print(f"  打不开：{e}")
        return
    try:
        for cmd, name in ((P.Cmd.GET_LEDPARAM, "GET_LEDPARAM 0x87"),
                          (P.Cmd.GET_USB_VERSION, "GET_USB_VERSION 0x8F")):
            dev.send_feature_report(P.build(cmd, None, bit8=True))
            for wait in (0.5, 2.0, 4.0):
                time.sleep(wait)
                fr = dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
                got = list(fr) if fr else []
                print(f"  {name:<20} 等 {wait:.1f}s → {hexline(got, 12)}")
    finally:
        dev.close()


def wake_test(rx: dict | None, tx: dict, seconds: float) -> None:
    """收到按键报告后立刻发 GET —— 排除"键盘睡了不转发"。"""
    print(f"\n=== ② 按键触发测试（{seconds:.0f} 秒）===")
    print("▶ 请在下面这段时间里，用这把 2.4G 键盘敲几下任意字母键。")
    print("  我会在你每次按键后立刻发一条 GET_LEDPARAM。\n")

    try:
        rd = hid.device()
        rd.open_path(rx["path"]) if rx else None
    except Exception as e:  # noqa: BLE001
        print(f"  打不开监听口 itf=1：{e}")
        return
    try:
        wr = hid.device()
        wr.open_path(tx["path"])
    except Exception as e:  # noqa: BLE001
        print(f"  打不开发送口 itf=2：{e}")
        rd.close()
        return

    keys = 0
    trig = 0
    hits: list[str] = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                r = rd.read(64, timeout_ms=60)
            except Exception as e:  # noqa: BLE001
                print(f"  读取按键报告失败（接口不接受读）：{type(e).__name__}: {e}")
                print("  → 这条接口在 Windows 上读不到按键，跳过触发测试")
                return
            if not r:
                continue
            keys += 1
            if keys <= 3:
                print(f"  收到按键报告 #{keys}: {hexline(r, 10)}")
            # 键盘刚被操作，立刻发查询
            trig += 1
            try:
                wr.send_feature_report(P.build(P.Cmd.GET_LEDPARAM, None, bit8=True))
                time.sleep(0.25)
                fr = wr.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
                got = list(fr) if fr else []
            except Exception as e:  # noqa: BLE001
                got = []
                hits.append(f"异常 {type(e).__name__}: {e}")
            if got and any(got[:8]):
                line = hexline(got, 12)
                hits.append(line)
                print(f"    → 按键后查询: {line}")
            elif trig <= 2:
                print("    → 按键后查询: 全零（仍旧没转发）")
            time.sleep(0.05)
    finally:
        rd.close()
        wr.close()

    print(f"\n  统计：收到 {keys} 个按键报告，触发 {trig} 次查询，"
          f"拿到非零数据 {len([h for h in hits if not h.startswith('异常')])} 次")
    if keys == 0:
        print("  ⚠ 一个按键报告都没收到 —— 要么键盘没连上这个接收器，"
              "要么别的程序抢走了输入（试着把焦点放到记事本再敲）。")


def which_ifaces_readable() -> None:
    """逐个集合试 read()，看哪个接口真的收得到报告。

    Windows 上键盘集合（page 0x0001 / usage 0x0006）往往不允许读，
    所以要找的是"不报 read error"的那个 —— 厂商响应可能就落在它上面。
    """
    print("\n=== ⑤ 哪些接口能读（找厂商响应可能落在哪）===")
    seen = set()
    for d in hid.enumerate():
        if d["vendor_id"] != VID or d["product_id"] != PID:
            continue
        key = (d.get("interface_number"), d.get("usage_page"), d.get("usage"),
               d["path"])
        if key in seen:
            continue
        seen.add(key)
        tag = (f"itf={d.get('interface_number')} "
               f"page=0x{d.get('usage_page', 0):04X} u=0x{d.get('usage', 0):04X}")
        try:
            dev = hid.device()
            dev.open_path(d["path"])
        except Exception as e:  # noqa: BLE001
            print(f"  {tag}  打不开 {type(e).__name__}")
            continue
        try:
            r = dev.read(64, timeout_ms=120)
            print(f"  {tag}  可读（本次 {len(r) if r else 0} 字节）")
        except Exception as e:  # noqa: BLE001
            print(f"  {tag}  ✗ {type(e).__name__}: {e}")
        finally:
            dev.close()


def cross_iface_check(tx: dict) -> None:
    """★ 关键试验：itf=2 发、itf=1(厂商集合) 收。

    蓝牙就是"一个端口发、另一个端口收"（Output Report 写、Input 通知回）。
    2.4G 接收器的 itf=1 厂商集合**是唯一可读的厂商接口**，
    如果响应走这条路，之前只在 itf=2 上读就永远看不到。
    """
    print("\n=== ⑥ 跨接口试验：itf=2 发 → itf=1 收 ===")
    rx = find(1, 0xFFFF, 0x0001)
    if not rx:
        print("  itf=1 厂商集合不在，跳过")
        return
    try:
        rd = hid.device()
        rd.open_path(rx["path"])
        wr = hid.device()
        wr.open_path(tx["path"])
    except Exception as e:  # noqa: BLE001
        print(f"  打不开：{e}")
        return
    try:
        # 先清空监听口
        for _ in range(8):
            if not rd.read(64, timeout_ms=20):
                break
        sent = 0
        for cmd, name in ((P.Cmd.GET_LEDPARAM, "0x87"), (P.Cmd.GET_SLEDPARAM, "0x88"),
                          (P.Cmd.GET_USB_VERSION, "0x8F")):
            # feature 发（之前唯一能"发进去"的方式）
            wr.send_feature_report(P.build(cmd, None, bit8=True))
            sent += 1
            t0 = time.monotonic()
            got = []
            while time.monotonic() - t0 < 1.2:
                r = rd.read(64, timeout_ms=200)
                if r:
                    got = list(r)
                    break
            print(f"  {name} → itf=1 收到: {hexline(got, 16) if got else '（无）'}")
            if got and any(got[:8]):
                b = got[1:] if got[0] in (0, 6) else got
                if b and b[0] == 0x55:
                    b = b[1:]
                if b and b[0] == cmd and any(b[1:9]):
                    print("      ✅✅ 这就是真实响应！2.4G 走跨接口收！")
            time.sleep(0.15)

        # 另外试一下：output 写到 itf=1，再在 itf=1 收
        for label, payload in (("原样", P.build(P.Cmd.GET_LEDPARAM, None, bit8=True)[1:]),
                               ("0x55前导", bytes([0x55]) + P.build(
                                   P.Cmd.GET_LEDPARAM, None, bit8=True)[1:])):
            try:
                wr.write(bytes([P.REPORT_ID]) + payload)
            except Exception:  # noqa: BLE001
                continue
            sent += 1
            t0 = time.monotonic()
            got = []
            while time.monotonic() - t0 < 1.2:
                r = rd.read(64, timeout_ms=200)
                if r:
                    got = list(r)
                    break
            print(f"  output({label}) → itf=1 收到: {hexline(got, 16) if got else '（无）'}")
            time.sleep(0.15)
        print(f"  共发出 {sent} 帧，没有任何一帧在 itf=1 上收到响应" if not got else
              f"  共发出 {sent} 帧")
    finally:
        rd.close()
        wr.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()

    print("=" * 78)
    print("2.4G 配置通道排查：键盘醒着时到底转不转发")
    print("=" * 78)

    rx, tx = find_kbd(), find(2)
    print(f"\n  itf=0 键盘按键口: {'在' if rx else '不在'}")
    print(f"  itf=2 配置发送口: {'在' if tx else '不在'}")
    if not tx:
        print("\n没找到接收器的 itf=2。请确认接收器插好、键盘拨到 2.4G。")
        return 2

    report_id_sweep(tx)
    cache_slot_check(tx)
    long_wait_check(tx)
    which_ifaces_readable()
    cross_iface_check(tx)

    if rx:
        wake_test(rx, tx, args.seconds)
    else:
        print("\n键盘按键口不在，跳过按键触发测试")

    print("\n" + "=" * 78)
    print("结论口径：")
    print("  · ②里出现任何一次非零响应 → 2.4G 能配灯，问题在时序/唤醒，可以适配")
    print("  · 按键后仍全零、且 Report ID 全扫过 → 接收器固件确实不转发厂商报告")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
