"""
蓝牙（BLE）链路配置能力探测。

背景（2026-10-25 实测）：
  有线模式设备是 38A9:0017，厂商配置口 = itf=2 usage_page=0xFFFF usage=0x0002。
  切到蓝牙后变成 38A9:0018，只有一个厂商集合：
      usage_page=0xFF55 usage=0x0202 Col06（interface_number=-1，BLE 没有 itf 概念）
  `tools/probe_link.py` 按 usage==0x0002 过滤，会把这个口当"不是配置口"跳过，
  所以蓝牙必须用本工具单独测。

传输层差异（同平台文档 §2.3 + 本机实测）：
  · USB  : Feature Report，Report ID 0，载荷 64B
  · BLE  : Output Report 写入（Report ID 6），响应走 Input Report 通知
           载荷前面多一个 0x55 标记；校验和从 cmd 起算，**跳过 0x55**
           实测响应形状：`06 55 <cmd> <参数…>`，参数区和 USB 完全一致

实测已确认可用（2026-10-25，38A9:0018）：
  GET_LEDPARAM     → 06 55 87 05 02 01 08 FF FF FF
  GET_USB_VERSION  → 06 55 8F 10 10 00 00 00 00 04 01
  与有线读到的数据逐字节相同 ⇒ 蓝牙可以读写配置，不只是只读。

用法：
    python tools/probe_ble.py                  # 只读：探测帧格式 + 全量 GET 扫描
    python tools/probe_ble.py --confirm-write  # 额外做一次写入验证（会改灯，结束时恢复）
    python tools/probe_ble.py --wait 1.2       # 蓝牙响应慢时加大等待
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import hid  # noqa: E402

from kb import protocol as P   # noqa: E402

BLE_USAGE_PAGE = 0xFF55        # 同平台文档里的 BLE 厂商页
BLE_REPORT_ID = 6              # 文档 + 实测：BLE 用 Report ID 6
MARKER_CMD = 0x55              # 命令/响应通道标记
MARKER_EVT = 0x66              # 事件通道标记


def hexline(b, n: int = 24) -> str:
    return " ".join(f"{x:02X}" for x in list(b)[:n])


def find_ble() -> list[dict]:
    return [d for d in hid.enumerate() if d.get("usage_page") == BLE_USAGE_PAGE]


# ------------------------------------------------------------------ 帧构造

def ble_frame(cmd: int, params: list[int] | bytes | None = None,
              *, bit8: bool = True) -> bytes:
    """构造 BLE 载荷（不含 Report ID，65 字节 = 0x55 + 64B USB 内容）。

    校验和沿用 USB 的位次：USB 里它在 frame[8] 且累加 frame[0..7]，
    其中 frame[0] 是 Report ID 恒为 0。BLE 版本只是把 Report ID 换成 0x55，
    所以把校验和留在同一位置、累加范围去掉 0x55（即从 cmd 起算）即可 —— 数值不变。
    """
    usb = P.build(cmd, params, bit8=bit8)
    return bytes([MARKER_CMD]) + usb[1:]


def ble_led_frame(mode: int, speed: int, brightness: int,
                  options: int, rgb: tuple[int, int, int]) -> bytes:
    """SET_LEDPARAM 的 BLE 版本（校验和在 frame[9]，累加 frame[1..8]）。"""
    value = bytearray(65)
    value[0] = MARKER_CMD
    usb = P.build_ledparam(mode, speed, brightness, options, rgb)
    value[1:] = usb[1:]          # 去掉 USB 的 Report ID 0，其余原样（含 frame[9] 校验）
    return bytes(value)


# ------------------------------------------------------------------ 收发

class BleLink:
    def __init__(self, path, wait: float = 0.8):
        self.dev = hid.device()
        self.dev.open_path(path)
        self.wait = wait

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:  # noqa: BLE001
            pass

    def drain(self, rounds: int = 24) -> int:
        """把输入队列里积压的通知读空。

        ★ 必须做：BLE 的通知是排队送达的，上一条命令的响应会滞留，
        导致「读到的是上一条命令的结果」——表现为读回值永远慢一帧
        （2026-10-25 实测：写完"常亮"读回"关闭"，恢复后读回"常亮"）。
        """
        n = 0
        for _ in range(rounds):
            # ⚠ timeout_ms 必须是 ≥1：本机 hidapi 传 0 会永久阻塞（实测）
            r = self.dev.read(64, timeout_ms=1)
            if not r:
                break
            n += 1
        return n

    def send(self, value: bytes) -> list[int]:
        """发一帧并等一条通知。返回去掉 Report ID 后的原始字节（可能为空）。"""
        self.drain()
        self.dev.write(bytes([BLE_REPORT_ID]) + value)
        deadline = time.time() + self.wait
        while time.time() < deadline:
            r = self.dev.read(64, timeout_ms=max(50, int(self.wait * 1000)))
            if r:
                return list(r)
        return []


def strip_prefix(resp: list[int]) -> list[int]:
    """去掉 Report ID(6) 和 0x55 标记，剩下从命令码开始的部分。"""
    body = list(resp)
    if body and body[0] == BLE_REPORT_ID:
        body = body[1:]
    if body and body[0] in (MARKER_CMD, MARKER_EVT):
        body = body[1:]
    return body


def verdict(resp: list[int], cmd: int) -> str:
    if not resp:
        return "无响应"
    body = strip_prefix(resp)
    if body and body[0] == cmd:
        # ⚠ 命令码对不代表有数据：全零时既可能是"值就是 0"，也可能是"空响应"。
        # 日志 §3.3 踩过这个坑，所以这里分开标，不硬判成通。
        if any(body[1:9]):
            return "✅ 有真实数据"
        return "🟡 命令码正确但数据全零（分不清 0 值和空响应）"
    if all(v == 0 for v in body[:8]):
        return "❌ 全零帧"
    if body and body[0] == 0:
        return "⚠ 原样回显（固件没实现这条命令）"
    return f"⚠ 命令码不符（收到 0x{body[0]:02X}）"


# ------------------------------------------------------------------ 主流程

READ_SWEEP = [
    ("GET_LEDPARAM", P.Cmd.GET_LEDPARAM, True),
    ("GET_SLEDPARAM", P.Cmd.GET_SLEDPARAM, True),
    ("GET_USB_VERSION", P.Cmd.GET_USB_VERSION, True),
    ("GET_FN", P.Cmd.GET_FN, True),
    ("GET_DEBOUNCE", P.Cmd.GET_DEBOUNCE, True),
    ("GET_SLEEPTIME", P.Cmd.GET_SLEEPTIME, True),
    ("GET_KBOPTION", P.Cmd.GET_KBOPTION, True),
    ("GET_KEYMATRIX", P.Cmd.GET_KEYMATRIX, True),
    ("GET_REPORT_RATE(疑电量)", P.Cmd.GET_REPORT_RATE, True),
    ("GET_LEDONOFF", P.Cmd.GET_LEDONOFF, True),
    ("GET_DONGLE_STATUS", P.Cmd.GET_DONGLE_STATUS, True),
]


def phase_framing(link: BleLink) -> str | None:
    """试三种帧格式，返回可用格式的名字。"""
    print("\n=== ① 帧格式探测（GET_LEDPARAM 0x87）===")
    cmd = P.Cmd.GET_LEDPARAM
    usb_body = P.build(cmd, None, bit8=True)[1:]

    variants = [
        ("A 无0x55（直接照搬USB）", bytes([0]) + usb_body),
        ("B 0x55前导（文档格式）", bytes([MARKER_CMD]) + usb_body),
        ("C 0x55前导+校验含0x55", _variant_c(cmd)),
    ]
    winner = None
    for label, value in variants:
        resp = link.send(value)
        v = verdict(resp, cmd)
        print(f"  {label:<24} {v}")
        if resp:
            print(f"      收: {hexline(resp)}")
        if v.startswith("✅") and winner is None:
            winner = label
        time.sleep(0.2)
    return winner


def _variant_c(cmd: int) -> bytes:
    value = bytearray(65)
    value[0] = MARKER_CMD
    value[1] = cmd
    value[8] = 255 - (sum(value[0:8]) & 0xFF)
    return bytes(value)


def phase_sweep(link: BleLink) -> dict:
    print("\n=== ② 全量 GET 扫描（只读）===")
    got = {}
    for name, cmd, bit8 in READ_SWEEP:
        resp = link.send(ble_frame(cmd, None, bit8=bit8))
        v = verdict(resp, cmd)
        print(f"  {name:<22} 0x{cmd:02X}  {v}")
        if resp:
            print(f"      收: {hexline(resp)}")
        if v.startswith("✅"):
            got[name] = strip_prefix(resp)
        time.sleep(0.15)
    return got


def phase_write(link: BleLink, orig: dict) -> None:
    """写入验证：改成常亮/红/亮度4 → 读回确认 → 恢复原值 → 再读回确认。"""
    print("\n=== ③ 写入验证（会改灯，结束自动恢复）===")
    if not orig:
        print("  跳过：没读到原始灯效，不敢写（怕恢复不回去）")
        return
    mode = orig.get("mode", 5)
    speed = orig.get("speed_raw", 2)
    bright = orig.get("brightness", 1)
    opts = int(orig.get("options", "0x70"), 16)
    rgb = orig.get("rgb", (255, 255, 255))
    print(f"  原状态: 模式={mode} 速度={speed} 亮度={bright} options=0x{opts:02X} rgb={rgb}")

    # 常亮(1) + 单色(options 低 nibble 0x7) + 纯红 + 亮度 4 —— 肉眼和读回都好认
    target = (1, 2, 4, 0x07, (255, 0, 0))
    print(f"  写入  : 模式={target[0]}(常亮) 亮度={target[2]} "
          f"options=0x{target[3]:02X}(单色) rgb={target[4]}")

    link.send(ble_led_frame(*target))
    time.sleep(0.4)
    resp = link.send(ble_frame(P.Cmd.GET_LEDPARAM, None, bit8=True))
    got = P.parse_ledparam([resp[0]] + strip_prefix(resp)) if resp else {}
    ok = got.get("mode") == target[0]
    print(f"  读回  : {got.get('mode_name', '读不到')} 亮度={got.get('brightness')} "
          f"rgb={got.get('rgb')}  → {'✅ 固件接受了这次写入' if ok else '❌ 没生效'}")

    print("  恢复原值…")
    link.send(ble_led_frame(mode, speed, bright, opts, rgb))
    time.sleep(0.4)
    resp = link.send(ble_frame(P.Cmd.GET_LEDPARAM, None, bit8=True))
    got = P.parse_ledparam([resp[0]] + strip_prefix(resp)) if resp else {}
    back = got.get("mode") == mode
    print(f"  恢复后: {got.get('mode_name', '读不到')} → {'✅ 已还原' if back else '⚠ 未确认还原'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vid", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=None)
    ap.add_argument("--wait", type=float, default=0.8)
    ap.add_argument("--confirm-write", action="store_true",
                    help="做一次写入验证（会改灯，结束恢复原值）")
    args = ap.parse_args()

    devs = find_ble()
    if args.vid is not None:
        devs = [d for d in devs if d["vendor_id"] == args.vid]
    if args.pid is not None:
        devs = [d for d in devs if d["product_id"] == args.pid]

    print("=" * 76)
    print("蓝牙链路配置能力探测")
    print("=" * 76)
    if not devs:
        print("\n没发现 usage_page=0xFF55 的厂商集合。")
        print("→ 确认键盘已切到蓝牙并配对成功（Windows 蓝牙设置里在线）")
        print("→ 有线是 0xFFFF/usage=0x0002，不会出现这个页")
        return 2

    for d in devs:
        print(f"\n【0x{d['vendor_id']:04X}:0x{d['product_id']:04X}】"
              f"  up=0x{d['usage_page']:04X} u=0x{d['usage']:04X}"
              f"  {d.get('product_string') or ''}")
        try:
            link = BleLink(d["path"], args.wait)
        except Exception as e:  # noqa: BLE001
            print(f"  打不开：{e}")
            continue
        try:
            winner = phase_framing(link)
            got = phase_sweep(link)
            if args.confirm_write:
                phase_write(link, P.parse_ledparam([0] + got.get("GET_LEDPARAM", []))
                            if "GET_LEDPARAM" in got else {})
        finally:
            link.close()

    print("\n" + "=" * 76)
    print("结论口径：")
    print("  · ①有 ✅ + ②多条 ✅ → 蓝牙可以像有线一样读写灯效")
    print("  · ①全无响应       → 蓝牙不开放配置通道，只能插线")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
