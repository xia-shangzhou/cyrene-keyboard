"""固件升级后的接口一致性回归。

用途：换固件之后，逐条比对「之前实测过的接口」是否还是老样子。
基线来自 `工作日志.md` 里 V104 时期的实测记录（§3.4 / §4.2 响应对照表）。

判据分三层，**不同层用不同的严格度** —— 只读命令的返回值里混着用户当前状态
（灯效模式、亮度、睡眠时间），拿它逐字节对比会把"用户调过亮度"误报成"协议变了"：

  L1 形态   ：有无响应 / 是否原样回显 / 命令码回显位置     —— 必须完全一致
  L2 固定值 ：不随用户状态变的字段（版本号、睡眠时间、去抖）—— 必须逐字节一致
  L3 状态值 ：灯效模式/亮度/RGB 这类可变的字段            —— 只校验「位置和量纲」

用法：
    python tools/regress_protocol.py              # 只读，安全
    python tools/regress_protocol.py --confirm-write   # 加写入测试（会临时改灯效，自动恢复）
    python tools/regress_protocol.py --link usb   # 强制指定通道
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import device as D          # noqa: E402
from kb import protocol as P        # noqa: E402

# ---------------------------------------------------------------- 基线
#
# 形态基线：'data' = 有真实数据 / 'echo' = 原样回显（固件不支持）/ 'none' = 无响应
#           / 'not_mine' = 有响应但命令码对不上（2.4G 接收器的缓存槽残留）
#
# 2.4G 那一列 2026-10-27 全部重录：接收器本身不主动转发，但 `Keyboard.send()`
# 会先发 `0xF7` 开闸（等 120ms），之后配置命令就能到键盘，回复靠 `0xFC` 从缓存槽取。
# 形态与有线基本一致，只有 `0x80 / 0xD0 / 0xE6` 三条在 2.4G 下彻底没响应。
FORM_BASELINE = {
    0x80: ("GET_REV",            {"usb": "echo", "ble": "none",  "2.4g": "none"}),
    0x83: ("GET_BATTERY",        {"usb": "echo", "ble": "echo",  "2.4g": "echo"}),
    0x84: ("GET_PROFILE",        {"usb": "echo", "ble": "echo",  "2.4g": "echo"}),
    0x85: ("GET_LEDONOFF",       {"usb": "echo", "ble": "echo",  "2.4g": "echo"}),
    0x86: ("GET_DEBOUNCE",       {"usb": "data", "ble": "data",  "2.4g": "data"}),
    0x87: ("GET_LEDPARAM",       {"usb": "data", "ble": "data",  "2.4g": "data"}),
    0x88: ("GET_SLEDPARAM",      {"usb": "data", "ble": "data",  "2.4g": "data"}),
    0x89: ("GET_KBOPTION",       {"usb": "echo", "ble": "echo",  "2.4g": "echo"}),
    0x8A: ("GET_KEYMATRIX",      {"usb": "data", "ble": "data",  "2.4g": "data"}),
    0x8C: ("GET_USERPIC",        {"usb": "echo", "ble": "echo",  "2.4g": "echo"}),
    0x8F: ("GET_USB_VERSION",    {"usb": "data", "ble": "data",  "2.4g": "data"}),
    0x90: ("GET_FN",             {"usb": "data", "ble": "data",  "2.4g": "data"}),
    0x91: ("GET_SLEEPTIME",      {"usb": "data", "ble": "data",  "2.4g": "data"}),
    0x97: ("GET_AUTOOS_EN",      {"usb": "echo", "ble": "none",  "2.4g": "none"}),
    # 0xD0 / 0xE6：USB 有接口但原样回显；蓝牙通道干脆不回（BLE 没这条命令的转发路径）
    0xD0: ("GET_SKU",            {"usb": "echo", "ble": "none",  "2.4g": "none"}),
    0xE6: ("GET_FEATURE_LIST",   {"usb": "echo", "ble": "none",  "2.4g": "none"}),
    # 0xF7 是唯一在 2.4G 下真正有数据的命令（dongle 本地应答电量）。
    # USB 下没实测过，留 None 跳过，别拿猜的值当基线。
    0xF7: ("GET_DONGLE_STATUS",  {"usb": None,   "ble": "none",  "2.4g": "data"}),
}

# 固定值基线：(命令名, 数据起始偏移, 期望数据段)
#
# ⚠ 偏移分两派，2026-10-27 实测确认 —— 这不是笔误：
#   · 0x86 / 0x87 / 0x88 / 0x8F  数据紧跟命令码，从 resp[2] 起
#   · 0x8A / 0x90 / 0x91          resp[2..7] 全零 padding、resp[7] 是 bit7 校验和、
#                                 resp[8] 也空着，真正的数据从 resp[9] 起
#   拿统一偏移去比会整段错位（第一版就因此误报「GET_FN 变成了 00 00」）。
FIXED_BASELINE = {
    0x86: ("GET_DEBOUNCE",   2, bytes([0x01])),                     # 1ms
    0x90: ("GET_FN",         9, bytes([0x0A, 0x02])),
    0x91: ("GET_SLEEPTIME",  9, bytes([0x78, 0x00, 0x78, 0x00, 0x58, 0x02, 0x58, 0x02])),
}

# 0x8F 版本基线：升级前 = 04 01（V104）
VERSION_PREFIX = bytes([0x10, 0x10, 0x00, 0x00, 0x00, 0x00])
VERSION_OLD = bytes([0x04, 0x01])


def hexline(data: bytes, n: int = 16) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


def classify(req: bytes, resp: bytes | None) -> str:
    """把一次查询的响应归类。

    返回：data / echo / empty_zero / not_mine / none
    `not_mine` = 有响应但命令码对不上 —— 2.4G 接收器缓存槽残留的典型特征。
    """
    if not resp:
        return "none"
    if bytes(resp) == bytes(req):
        return "echo"
    if not any(resp[1:]):
        return "empty_zero"
    if resp[1] != req[1]:
        return "not_mine"
    return "data"


def query_raw(kb: D.Keyboard, cmd: int, *, bit8: bool = False, wait: float = 0.42):
    """带显式等待的查询。

    `Keyboard.query()` 对非 BLE 通道是 send 完立刻 `get_feature_report`，
    有线够快无所谓，但 2.4G 要走接收器往返 —— 不等待只会读回上一次的缓存。
    """
    req = P.build(cmd, None, bit8=bit8)
    kb.send(req)
    time.sleep(wait)
    r = kb.dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
    return req, (bytes(r) if r else None)


# ---------------------------------------------------------------- 只读回归

def run_read(kb: D.Keyboard, link: str) -> list[str]:
    problems: list[str] = []
    print("=" * 78)
    print(f"L1/L2 只读回归 · 通道={link.upper()}")
    print("=" * 78)
    print(f"{'命令':<24}{'形态':<14}{'基线':<10}{'判定':<6}响应")

    saved: dict[int, bytes] = {}

    for cmd in sorted(FORM_BASELINE):
        name, forms = FORM_BASELINE[cmd]
        expect = forms.get(link)
        if expect is None:
            continue
        try:
            # 三个通道都走 kb.query()：2.4G 的开闸 + 0xFC 取缓存在 device 层内部完成。
            # （2026-10-27 之前这里给 2.4G 单开了一个 "send → 等 0.42s → 读 Feature"
            #  的分支，那种读法在 2.4G 下命中率 0/10，是旧结论的来源。）
            req = P.build(cmd, None, bit8=(cmd in (0x87, 0x88)))
            resp = kb.query(cmd, bit8=(cmd in (0x87, 0x88)))
        except Exception as e:  # noqa: BLE001
            print(f"{name:<24}{'异常':<14}{expect:<10}{'✗':<6}{type(e).__name__}: {e}")
            problems.append(f"{name} 0x{cmd:02X} 查询异常：{e}")
            continue

        got = classify(req, resp)
        # 0xF7 是特例：dongle 的本地响应**不回显命令码**（resp[1] 是状态位），
        # 拿命令码判会把正常的电量应答误判成缓存残留。改用「电量在 1..100」判。
        if cmd == 0xF7 and resp and 0 < resp[2] <= 100:
            got = "data"
        if got == "empty_zero" and link == "ble":
            got = "none"          # BLE 下全零 = 没有响应，等价于 none

        ok = got == expect
        mark = "✓" if ok else "✗"
        print(f"{name:<24}{got:<14}{expect:<10}{mark:<6}{hexline(resp) if resp else '—'}")
        if not ok:
            problems.append(f"{name} 0x{cmd:02X} 形态变了：基线={expect} 现在={got}")
        # 只把「真正属于这条命令的响应」拿去比固定值 —— 缓存槽残留里也有字节，
        # 拿它比版本前缀会误报成"固件版本变了"。
        if resp and got == "data":
            saved[cmd] = bytes(resp)

    print()
    print("-" * 78)
    print("L2 固定值比对")
    print("-" * 78)
    for cmd, (name, off, want) in FIXED_BASELINE.items():
        if cmd not in saved:
            print(f"{name:<24}跳过（没有响应）")
            continue
        got = saved[cmd][off: off + len(want)]
        ok = got == want
        print(f"{name:<24}{'✓ 一致' if ok else '✗ 变了':<10}"
              f"偏移{off}  基线={hexline(want)}  现在={hexline(got)}")
        if not ok:
            problems.append(f"{name} 固定值变了：基线={hexline(want)} 现在={hexline(got)}")

    if 0x8F in saved:
        body = saved[0x8F]
        prefix, ver = body[2:8], body[8:10]
        ok_prefix = prefix == VERSION_PREFIX
        ver_changed = ver != VERSION_OLD
        print(f"{'GET_USB_VERSION':<24}"
              f"{'✓ 一致' if ok_prefix else '✗ 变了':<10}"
              f"前缀={hexline(prefix)}（基线={hexline(VERSION_PREFIX)}）")
        if not ok_prefix:
            problems.append(f"GET_USB_VERSION 前缀变了：{hexline(prefix)}")
        print(f"{'  固件版本字节':<24}"
              f"{'★ 已升级' if ver_changed else '· 未变'}"
              f"   基线={hexline(VERSION_OLD)} (V104)  现在={hexline(ver)}")
    print()

    if link == "2.4g":
        problems += run_24g_checks(kb)
    return problems


def run_24g_checks(kb: D.Keyboard) -> list[str]:
    """2.4G 专项：开闸 + 缓存槽取回复。

    2026-10-27 推翻旧结论「接收器从不转发配置」。真实机制三条：
      1. `0xF7` 是**开闸**命令（dongle 本地应答，顺带给出电量）——
         不发它，后面的配置命令会被接收器静默丢弃
      2. 开闸后命令能到键盘，但回复不进 Feature 缓冲区，
         要用 `0xFC` 把它从缓存槽「冲」出来
      3. 开闸窗口实测 ≥1.6 秒，连续推流（音乐律动）可以复用
    """
    problems: list[str] = []
    print("-" * 78)
    print("2.4G 专项：开闸 + 缓存槽")
    print("-" * 78)

    # 1) 电量（顺带确认 dongle 本地应答正常）
    _, r = query_raw(kb, P.Cmd.GET_DONGLE_STATUS)
    if r and 0 < r[2] <= 100:
        print(f"{'0xF7 电量':<24}✓ {r[2]}%  充电={r[4]}  在线={r[6]}")
    else:
        print(f"{'0xF7 电量':<24}✗ 没读到有效电量  {hexline(r) if r else '无响应'}")
        problems.append("0xF7 读不到电量")

    # 2) 完整查询路径：开闸 → 发帧 → 0xFC 取缓存
    t0 = time.monotonic()
    resp = kb.query(P.Cmd.GET_LEDPARAM, bit8=True)
    ms = (time.monotonic() - t0) * 1000
    ok = bool(resp) and len(resp) > 1 and resp[1] == P.Cmd.GET_LEDPARAM
    if ok:
        print(f"{'0x87 经缓存槽':<24}✓ {ms:.0f}ms  {hexline(resp, 9)}")
    else:
        print(f"{'0x87 经缓存槽':<24}✗ {ms:.0f}ms  {hexline(resp, 9) if resp else '无响应'}")
        problems.append("2.4G 下 0x87 拿不到命令码匹配的回复（开闸/取缓存路径失效）")

    # 3) 开闸窗口：连发 20 条，看有没有中途掉
    hit = 0
    for _ in range(20):
        r2 = kb.query(P.Cmd.GET_LEDPARAM, bit8=True)
        if r2 and len(r2) > 1 and r2[1] == P.Cmd.GET_LEDPARAM:
            hit += 1
    if hit == 20:
        print(f"{'连发 20 条':<24}✓ 20/20 命中（开闸窗口正常）")
    else:
        print(f"{'连发 20 条':<24}✗ {hit}/20 命中 —— 窗口可能变短，检查 DONGLE_STREAM_REARM")
        problems.append(f"2.4G 连发命中率 {hit}/20")
    print()
    return problems


# ---------------------------------------------------------------- 写入回归

def run_write(kb: D.Keyboard) -> list[str]:
    """写入回归：写一条已知状态 → 读回 → 恢复原值。

    只跑 0x07 / 0x08 两条（电源灯 + 侧灯），不动 0x0D（流式，单独测）。
    """
    problems: list[str] = []
    print("=" * 78)
    print("L3 写入回归（会临时改灯效，结束自动恢复）")
    print("=" * 78)

    for cmd_get, cmd_set, label in (
        (P.Cmd.GET_LEDPARAM, P.Cmd.SET_LEDPARAM, "主灯 0x07/0x87"),
        (P.Cmd.GET_SLEDPARAM, P.Cmd.SET_SLEDPARAM, "侧灯 0x08/0x88"),
    ):
        try:
            before = kb.query(cmd_get, bit8=True)
        except Exception as e:  # noqa: BLE001
            print(f"{label}  读原值失败：{e}")
            problems.append(f"{label} 读原值失败")
            continue
        if not before or len(before) < 9:
            print(f"{label}  读不到原值，跳过")
            continue

        orig = (before[2], before[3], before[4], before[5], before[6], before[7], before[8])
        print(f"{label}  原值 mode={orig[0]} speed={orig[1]} bright={orig[2]} "
              f"opts=0x{orig[3]:02X} rgb={orig[4:7]}")

        # 写一个和原值明显不同的状态：常亮 / 亮度 2 / 纯蓝
        probe = [1, 2, 2, 0x07, 0x00, 0x00, 0xFF]
        kb.send(P.build_led_cmd(cmd_set, probe))
        time.sleep(0.25 if kb.ble else 0.08)
        back = kb.query(cmd_get, bit8=True)
        if back and len(back) >= 9:
            got = (back[2], back[3], back[4], back[5], back[6], back[7], back[8])
            ok = got[:4] == tuple(probe[:4]) and got[4:7] == tuple(probe[4:7])
            print(f"{label}  写入后读回 mode={got[0]} speed={got[1]} bright={got[2]} "
                  f"opts=0x{got[3]:02X} rgb={got[4:7]}  {'✓ 接受' if ok else '✗ 不一致'}")
            if not ok:
                problems.append(f"{label} 写入未生效：期望 {probe} 读回 {list(got)}")
        else:
            print(f"{label}  写入后读不回来")
            problems.append(f"{label} 写入后无响应")

        # 恢复
        kb.send(P.build_led_cmd(cmd_set, list(orig)))
        time.sleep(0.2 if kb.ble else 0.08)
        rest = kb.query(cmd_get, bit8=True)
        if rest and len(rest) >= 9:
            got = (rest[2], rest[3], rest[4], rest[5], rest[6], rest[7], rest[8])
            print(f"{label}  已恢复 mode={got[0]} speed={got[1]} bright={got[2]} "
                  f"opts=0x{got[3]:02X} rgb={got[4:7]}")
        print()

    return problems


def run_audio(kb: D.Keyboard) -> list[str]:
    """音乐律动频段帧：只验证帧能被固件接受（不判定视觉效果）。"""
    problems: list[str] = []
    print("-" * 78)
    print("音乐律动 0x0D 帧（只看发得出去，视觉要人眼确认）")
    bands = [1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1, 0, 1, 2, 3, 4]
    try:
        # 2.4G 和蓝牙走同一套半字节打包（2026-10-27 实测）：
        # 接收器转发的是键盘原生帧格式，USB 那套校验和格式在 2.4G 下不对
        if kb.ble or getattr(kb, "dongle", False):
            frame = P.build_audio_viz_ble(bands)
            kb.send(frame)
            print(f"  半字节格式（蓝牙/2.4G）已发出  帧体={hexline(frame[1:12])}")
        else:
            frame = P.build_audio_viz(bands)
            kb.send(frame)
            print(f"  USB 整字节格式 已发出  cks={frame[8]:02X} 频段={hexline(frame[9:25])}")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ 发送失败：{type(e).__name__}: {e}")
        problems.append(f"音乐律动帧发送失败：{e}")
    print()
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm-write", action="store_true",
                    help="跑写入回归（会临时改灯效，自动恢复）")
    ap.add_argument("--link", choices=["usb", "ble", "2.4g", "any"], default="any")
    ap.add_argument("--audio", action="store_true", help="顺便推一帧音乐律动数据")
    args = ap.parse_args()

    try:
        kb = D.Keyboard.open_any(readonly=not args.confirm_write)
    except Exception as e:  # noqa: BLE001
        print(f"打不开键盘：{e}")
        return 2

    link = kb.link
    if args.link != "any" and link != args.link:
        print(f"当前通道是 {link}，与 --link {args.link} 不符，退出")
        kb.close()
        return 2

    print(f"设备 0x{kb.vid:04X}:0x{kb.pid:04X}  通道={link}  "
          f"{kb.product or ''}")
    print(f"{kb.path.decode(errors='replace')[:76]}")
    print()

    problems = run_read(kb, link)

    if args.confirm_write:
        problems += run_write(kb)
    if args.audio:
        problems += run_audio(kb)

    kb.close()

    print("=" * 78)
    if problems:
        print(f"发现 {len(problems)} 处差异：")
        for p in problems:
            print(f"  · {p}")
    else:
        print(f"全部一致：老接口在这版固件上行为没变 ✓（通道 {link}）")
    print("=" * 78)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
