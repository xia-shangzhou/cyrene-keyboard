"""
2.4G 无线模式探测：哪个接口是配置口、协议通不通。

背景
----
有线是 `38A9:0017`，2.4G 是接收器转发的 `3151:5052`，两者接口布局看起来一样
（都有 `itf=2 / usagePage=0xFFFF / usage=0x0002`）。但 2.4G 多了两件事：

  1. **必须走 dongle 转发**，一次往返约 220ms（有线只有 1ms）
  2. **接收器只有一个 64 字节缓存槽** —— 发太快，新响应会覆盖还没读走的旧响应

所以「发完立刻读」在 2.4G 下必然失败，必须发完等一会儿再读。

本工具对每个候选接口分别试 GET_LEDPARAM / GET_USB_VERSION，报告：
  · 有没有数据回来
  · 是不是原样回显（回显 = 固件没实现该命令）
  · 往返耗时

    python tools/probe_wireless.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

from kb import protocol as P   # noqa: E402

# 想知道的所有键盘标识（有线 + 各家 2.4G 接收器）
CANDIDATE_IDS = [
    (0x38A9, 0x0017, "有线"),
    (0x3151, 0x5052, "2.4G 接收器"),
    (0x3151, 0x5038, "2.4G 接收器(2)"),
    (0x3151, 0x5037, "2.4G 接收器(3)"),
]


def hexline(b, n: int = 16) -> str:
    return " ".join(f"{x:02X}" for x in list(b)[:n])


def probe(path: bytes, cmd: int, label: str, wait: float) -> None:
    req = P.build(cmd, None, bit8=True)
    dev = hid.device()
    try:
        dev.open_path(path)
    except Exception as e:  # noqa: BLE001
        print(f"      {label}: 打不开 {e}")
        return
    try:
        t0 = time.monotonic()
        dev.send_feature_report(req)
        # 2.4G 要等 dongle 转发 + 键盘响应，约 220ms；发完立刻读会拿到空或旧缓存
        time.sleep(wait)
        resp = dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
        dt = (time.monotonic() - t0) * 1000
        if not resp:
            print(f"      {label}: 无响应")
        elif bytes(resp) == req:
            print(f"      {label}: **原样回显**（固件不支持该命令）  {dt:.0f}ms")
        else:
            print(f"      {label}: ✅ 有数据  {dt:.0f}ms   {hexline(resp)}")
    except Exception as e:  # noqa: BLE001
        print(f"      {label}: 异常 {type(e).__name__}: {e}")
    finally:
        try:
            dev.close()
        except Exception:  # noqa: BLE001
            pass


def watch_input(seconds: float, vid: int, pid: int) -> list[list[int]]:
    """监听所有能打开的接口，返回这段时间收到的 input report。"""
    import threading

    got: list[list[int]] = []
    devs = []
    for d in hid.enumerate():
        if (d["vendor_id"], d["product_id"]) != (vid, pid):
            continue
        try:
            dv = hid.device()
            dv.open_path(d["path"])
            devs.append(dv)
        except Exception:  # noqa: BLE001
            pass
    if not devs:
        return got

    stop = {"on": False}

    def loop() -> None:
        import time as _t
        while not stop["on"]:
            for dv in devs:
                try:
                    data = dv.read(64, 80)
                except Exception:  # noqa: BLE001
                    continue
                if data:
                    got.append(list(data))
            _t.sleep(0.005)

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    time.sleep(seconds)
    stop["on"] = True
    th.join(timeout=1)
    for dv in devs:
        try:
            dv.close()
        except Exception:  # noqa: BLE001
            pass
    return got


def try_write(path: bytes, vid: int, pid: int) -> None:
    """发一条 SET_LEDPARAM，用 05 0F 事件判断固件有没有接受。

    为什么不看灯：主人不一定在盯着，而且亮度 1→2 的差别肉眼很难确认。
    `05 0F` 是固件"设置已保存"的广播事件，收到就说明这条命令真的被处理了。
    """
    # 先监听，再发
    base = watch_input(0.8, vid, pid)
    print(f"    基线 0.8s: 收到 {len(base)} 个 input report")

    dev = hid.device()
    dev.open_path(path)
    # 盲发：亮度设到 4（最亮），模式用 1（常亮）最不容易看错
    params = [1, 2, 4, 0x08, 0xFF, 0xFF, 0xFF]
    frame = P.build_led_cmd(P.Cmd.SET_LEDPARAM, params)
    print(f"    发送 SET_LEDPARAM(常亮/亮度4): {hexline(frame, 12)}")
    dev.send_feature_report(frame)
    time.sleep(0.4)
    dev.close()

    after = watch_input(1.2, vid, pid)
    print(f"    发送后 1.2s: 收到 {len(after)} 个 input report")
    saves = [p for p in after if len(p) >= 3 and p[0] == 0x05 and p[1] == 0x0F]
    for p in after[:6]:
        print(f"       {hexline(p, 8)}")
    print(f"    含 05 0F（设置已保存）= {bool(saves)}")
    print()
    if saves:
        print("    ✅ 写入通道是通的 —— 2.4G 只是读不到，SET 能用")
    elif after:
        print("    ⚠ 有 input report 但没有 05 0F —— 键盘在通信，但可能没接受这条命令")
    else:
        print("    ❌ 完全收不到键盘的事件 —— 请确认键盘处于 2.4G 且已配对")


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="额外测写入（会发 1 条 SET_LEDPARAM，把灯改成常亮/最亮）")
    args = ap.parse_args()

    print("=" * 74)
    print("2.4G 无线探测")
    print("=" * 74)

    found: dict[tuple[int, int], list[dict]] = {}
    for d in hid.enumerate():
        key = (d["vendor_id"], d["product_id"])
        if key in {(v, p) for v, p, _ in CANDIDATE_IDS}:
            found.setdefault(key, []).append(d)

    if not found:
        print("\n没有发现任何已知的键盘接口。")
        print("请确认：接收器插好、键盘切到 2.4G（Fn+4 长按 3 秒配对）、键盘已开机。")
        return 2

    for vid, pid, name in CANDIDATE_IDS:
        devs = found.get((vid, pid))
        if not devs:
            continue
        print(f"\n【{name}】0x{vid:04X}:0x{pid:04X}  {len(devs)} 个接口")
        # 厂商接口优先
        devs.sort(key=lambda d: (d.get("usage_page", 0) < 0xFF00,
                                 d.get("usage", 0)))
        for d in devs:
            up = d.get("usage_page", 0)
            if up < 0xFF00:
                continue
            print(f"    itf={d.get('interface_number')} usagePage=0x{up:04X} "
                  f"usage=0x{d.get('usage', 0):04X}")
            probe(d["path"], P.Cmd.GET_LEDPARAM, "GET_LEDPARAM", 0.35)
            time.sleep(0.15)
            probe(d["path"], P.Cmd.GET_USB_VERSION, "GET_USB_VER ", 0.35)
            time.sleep(0.15)

    if args.write:
        print("\n【写入测试】—— 会把灯改成「常亮 / 最亮」，测完请手动切回去")
        for vid, pid, name in CANDIDATE_IDS:
            devs = found.get((vid, pid)) or []
            target = [d for d in devs
                      if d.get("usage_page") == 0xFFFF and d.get("usage", 0) == 0x0002]
            if not target:
                continue
            print(f"  设备：{name} 0x{vid:04X}:0x{pid:04X} "
                  f"itf={target[0].get('interface_number')}")
            try:
                try_write(target[0]["path"], vid, pid)
            except Exception as e:  # noqa: BLE001
                print(f"    测试失败：{e}")

    print("\n" + "=" * 74)
    print("怎么读这份结果：")
    print("  · 某个接口「有数据」→ 那就是配置口，用它的 usage 值去修 open_any 的优先级")
    print("  · 全部「原样回显」→ 这个通道不开放配置（很多厂商 2.4G 就这样）")
    print("  · 全部「无响应」→ 转发链没通：键盘没进 2.4G、或接收器缓存被顶掉了")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
