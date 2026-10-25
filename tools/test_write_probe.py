"""
写入帧格式探测：本机固件（YC3121）的 bit8 校验和位置与文档不一致，导致
「7 个参数」与「校验和位置」冲突。这里构造 3 种候选格式，逐一实测哪一种被固件接受。

帧格式候选
----------
A  7 参数占 frame[2..8]，校验和放 frame[9]  = 255 - sum(frame[1:9])   （文档写法）
B  7 参数占 frame[2..8]，frame[9] 留空（不写校验和）
C  6 参数占 frame[2..7]（省略 B），校验和放 frame[8] = 255 - sum(frame[0:8])  （与 GET 一致）

安全设计
--------
- 默认 dry-run，只打印帧，不发一个字节。
- --confirm 才执行；每次只改「亮度」一个字段，写完立刻读回验证，无论成败都写回原值。
- 只碰 0x07（SET_LEDPARAM，在 ALLOWED_WRITE 白名单）。FORBIDDEN 依然硬拦。
- 最坏结果：灯效显示不对 → 按 Fn+Tab 切一次模式即回固件默认序列。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import protocol as P            # noqa: E402
from kb.device import Keyboard          # noqa: E402
from kb.safety import SafetyPolicy      # noqa: E402

FLASH_WAIT = 1.3


def hexline(b: bytes, n: int = 12) -> str:
    return " ".join(f"{x:02X}" for x in b[:n])


def build_variants(mode: int, speed: int, bright: int, opts: int,
                   rgb: tuple[int, int, int]) -> dict[str, bytes]:
    params = [mode, speed, bright, opts, *rgb]          # 7 个

    # A：7 参数 + frame[9] 校验
    a = bytearray(65)
    a[0], a[1] = 0, P.Cmd.SET_LEDPARAM
    a[2:9] = bytes(params)
    a[9] = 255 - (sum(a[1:9]) & 0xFF)

    # B：7 参数，不带校验和
    b = bytearray(65)
    b[0], b[1] = 0, P.Cmd.SET_LEDPARAM
    b[2:9] = bytes(params)

    # C：6 参数（省略 B）+ frame[8] 校验
    c = bytearray(65)
    c[0], c[1] = 0, P.Cmd.SET_LEDPARAM
    c[2:8] = bytes(params[:6])
    c[8] = 255 - (sum(c[0:8]) & 0xFF)

    return {"A": bytes(a), "B": bytes(b), "C": bytes(c)}


def read_led(kb: Keyboard, pol: SafetyPolicy) -> tuple[dict, bytes]:
    time.sleep(0.15)
    pol.assert_allowed(P.Cmd.GET_LEDPARAM)
    resp = kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True)
    if not resp:
        raise RuntimeError("读不到灯效参数")
    return P.parse_ledparam(resp), resp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--variant", default="", choices=["", "A", "B", "C"])
    args = ap.parse_args()

    pol = SafetyPolicy(strict_readonly=not args.confirm)
    kb = Keyboard.open_any(readonly=not args.confirm)
    print("=" * 74)
    print("写入帧格式探测 — " + ("执行写入" if args.confirm else "DRY-RUN（不发送）"))
    print("=" * 74)
    print(f"设备：{kb.product}  {kb.vid:04X}:{kb.pid:04X}\n")

    info, resp = read_led(kb, pol)
    print("① 当前状态")
    print(f"   {hexline(resp)}")
    print(f"   mode={info['mode']}({info['mode_name']}) speed={info['speed_raw']} "
          f"brightness={info['brightness']} opts={info['options']} rgb={info['rgb']}\n")

    old_b = info["brightness"]
    new_b = old_b - 1 if old_b > 0 else min(4, old_b + 1)
    rgb = tuple(info["rgb"])
    opts = int(info["options"], 16)

    tests = build_variants(info["mode"], info["speed_raw"], new_b, opts, rgb)
    restores = build_variants(info["mode"], info["speed_raw"], old_b, opts, rgb)

    print(f"② 候选帧（只把亮度 {old_b} → {new_b}，其余字段沿用读回值）")
    for k in ("A", "B", "C"):
        print(f"   {k}: {hexline(tests[k])}")
    print()

    if not args.confirm:
        print("③ DRY-RUN 结束，未发送任何字节。")
        print("   执行示例： python tools/test_write_probe.py --confirm --variant B")
        kb.close()
        return 0

    v = args.variant or "B"
    print(f"③ 试格式 {v} …")
    ok = False
    try:
        pol.assert_allowed(P.Cmd.SET_LEDPARAM)
        kb.send(tests[v])
        time.sleep(FLASH_WAIT)
        after, raw = read_led(kb, pol)
        print(f"   读回 {hexline(raw)}  brightness={after['brightness']}")
        if after["brightness"] == new_b:
            ok = True
            print("   ✅ 格式生效\n")
        else:
            print(f"   ❌ 未生效（期望 {new_b}）\n")
    except Exception as e:  # noqa: BLE001
        print(f"   ❌ 异常：{type(e).__name__}: {e}\n")
    finally:
        print(f"④ 用同格式 {v} 写回原值 …")
        try:
            pol.assert_allowed(P.Cmd.SET_LEDPARAM)
            kb.send(restores[v])
            time.sleep(FLASH_WAIT)
            back, raw2 = read_led(kb, pol)
            print(f"   读回 {hexline(raw2)}  brightness={back['brightness']}  "
                  + ("✅ 已恢复" if back["brightness"] == old_b else "⚠ 未恢复"))
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠ 恢复失败：{e}（可按 Fn+Tab 切一次模式）")
        kb.close()

    print("\n" + "=" * 74)
    print("结论：" + (f"格式 {v} 可用，写入通道打通" if ok else f"格式 {v} 不生效，换下一个"))
    print(f"安全统计：{pol.summary()}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
