"""
最小可逆写入测试：只改一格亮度，验证协议后立刻恢复原值。

安全设计
--------
- 默认 **dry-run**：只构造帧并打印，一个字节都不发。必须显式加 --confirm 才真正写入。
- 只改 brightness 一个字段（4→3 或 3→2），模式/颜色/方向全部沿用读回的原值。
- 写完等 1.3s（固件写 flash 需要时间），再读回比对。
- 无论成功失败，最后都会把原值写回去。
- 全程只碰 0x07（SET_LEDPARAM），这张命令在安全策略的 ALLOWED_WRITE 白名单里，
  FORBIDDEN 表里的命令依然发不出去。

最坏情况：灯效参数写错，键盘显示不对 —— 按 Fn+Tab 切一次模式，或再用本脚本写回原值即可恢复。
不涉及固件、不涉及改键，硬件层面不存在损坏路径。
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

FLASH_WAIT = 1.3          # 固件保存设置到 flash 的等待时间


def hexline(b: bytes, n: int = 12) -> str:
    return " ".join(f"{x:02X}" for x in b[:n])


def read_led(kb: Keyboard, pol: SafetyPolicy) -> tuple[dict, bytes]:
    time.sleep(0.15)                        # 让读命令的 50ms 频控过去
    pol.assert_allowed(P.Cmd.GET_LEDPARAM)
    resp = kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True)
    if not resp:
        raise RuntimeError("读不到灯效参数")
    return P.parse_ledparam(resp), resp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                    help="真正执行写入（不加则只做 dry-run）")
    args = ap.parse_args()
    mode_txt = "执行写入" if args.confirm else "DRY-RUN（不发送任何写命令）"

    pol = SafetyPolicy(strict_readonly=not args.confirm)
    kb = Keyboard.open_any(readonly=not args.confirm)
    print("=" * 72)
    print(f"最小可逆写入测试 — {mode_txt}")
    print("=" * 72)
    print(f"设备：{kb.product}  {kb.vid:04X}:{kb.pid:04X}\n")

    info, resp = read_led(kb, pol)
    print("① 当前状态（读回）")
    print(f"   {hexline(resp)}")
    print(f"   mode={info['mode']}({info['mode_name']}) speed={info['speed_raw']} "
          f"brightness={info['brightness']} options={info['options']} rgb={info['rgb']}\n")

    old_b = info["brightness"]
    new_b = old_b - 1 if old_b > 0 else min(4, old_b + 1)
    rgb = tuple(info["rgb"])
    opts = int(info["options"], 16)

    test_frame = P.build_ledparam(info["mode"], info["speed_raw"], new_b, opts, rgb)
    restore_frame = P.build_ledparam(info["mode"], info["speed_raw"], old_b, opts, rgb)

    print("② 计划")
    print(f"   只改亮度：{old_b} → {new_b}，其余字段全部沿用读回值")
    print(f"   测试帧 {hexline(test_frame)}")
    print(f"   恢复帧 {hexline(restore_frame)}\n")

    if not args.confirm:
        print("③ DRY-RUN 结束，未发送任何字节。")
        print("   确认无误后加 --confirm 执行。")
        kb.close()
        return 0

    ok = False
    try:
        print("③ 发送测试帧 …")
        pol.assert_allowed(P.Cmd.SET_LEDPARAM)
        kb.send(test_frame)
        time.sleep(FLASH_WAIT)

        after, raw = read_led(kb, pol)
        print(f"   读回 {hexline(raw)}")
        print(f"   brightness = {after['brightness']}")
        if after["brightness"] == new_b:
            ok = True
            print("   ✅ 写入生效，协议正确\n")
        else:
            print(f"   ❌ 未生效（期望 {new_b}，实得 {after['brightness']}）\n")
    except Exception as e:  # noqa: BLE001
        print(f"   ❌ 异常：{type(e).__name__}: {e}\n")
    finally:
        print("④ 恢复原值 …")
        try:
            pol.assert_allowed(P.Cmd.SET_LEDPARAM)
            kb.send(restore_frame)
            time.sleep(FLASH_WAIT)
            back, raw2 = read_led(kb, pol)
            same = back["brightness"] == old_b
            print(f"   读回 {hexline(raw2)}")
            print(f"   brightness = {back['brightness']}  "
                  f"{'✅ 已恢复' if same else '⚠ 未完全恢复，请手动按 Fn+Tab 切一次模式'}")
        except Exception as e:  # noqa: BLE001
            print(f"   ⚠ 恢复失败：{type(e).__name__}: {e}")
            print("     请按键盘 Fn+Tab 切换一次灯光模式，即可回到固件默认序列")
        kb.close()

    print("\n" + "=" * 72)
    print("结论：" + ("写入通道打通，可以进入功能开发" if ok else "写入未生效，需要继续排查协议"))
    print(f"安全统计：{pol.summary()}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
