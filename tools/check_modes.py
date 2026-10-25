"""
灯效模式有效性验证：哪些 mode 编号在你键盘的固件上真的存在。

为什么需要测
------------
界面上的模式名全部来自同平台协议文档（`kb/protocol.py` 的 `LED_MODES`，0-25 共 26 个），
**没有逐台实测过**。不同固件版本的实现并不一致：
  · 有的编号可能根本不存在 —— 切过去没反应，或者固件直接拒绝
  · 有的存在但效果和名字对不上（比如"流星"其实是"雨滴"）
  · 有的需要额外数据才有画面（13 逐键：没颜色数据就是全黑）

第一种能自动判定，后两种只能靠眼睛看。

判定原理
--------
发 `SET_LEDPARAM(mode=n)` 再 `GET_LEDPARAM` 读回：
  · 读回 == n  → 固件接受了这个模式编号，存在
  · 读回 != n  → 被拒绝，该模式在你的固件上无效
这个判据很硬，不依赖肉眼。

用法
----
    python tools/check_modes.py                # 自动测一遍，每个停 1.2 秒
    python tools/check_modes.py --slow         # 每个停 3 秒，方便看清效果
    python tools/check_modes.py --dry          # 只列出要测什么，不发任何东西
    python tools/check_modes.py --skip-hidden  # 跳过界面里已隐藏的（13/20/21/22/25）

**会写 21~26 次 flash**（每个模式一次）。灯效会快速跳变，属正常现象。
结束时自动恢复进入前的灯效。

测完把无效的编号填进 `ui/app.py` 的 `INVALID_MODES = {...}`，界面会自动隐藏它们。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import protocol as P            # noqa: E402
from kb.device import Keyboard          # noqa: E402

# 与 ui/app.py 保持一致的隐藏列表（这个工具不 import UI，避免拉起 Qt）
HIDDEN = {13, 20, 21, 22, 25}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只列计划，不发帧")
    ap.add_argument("--slow", action="store_true", help="每个模式停 3 秒")
    ap.add_argument("--skip-hidden", action="store_true", help="跳过界面已隐藏的模式")
    ap.add_argument("--only", default="", help="只测指定编号，逗号分隔，例如 1,2,3")
    args = ap.parse_args()

    kb = Keyboard.open_any(readonly=False)

    led = P.parse_ledparam(kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True) or b"")
    if not led:
        print("读不到当前灯效，放弃（无法恢复）")
        kb.close()
        return 2

    modes = sorted(P.LED_MODES)
    if args.skip_hidden:
        modes = [m for m in modes if m not in HIDDEN]
    if args.only:
        want = {int(x) for x in args.only.split(",") if x.strip().isdigit()}
        modes = [m for m in modes if m in want]

    def frame_for(mode: int) -> bytes:
        params = [mode & 0xFF, led["speed_raw"] & 0xFF, led["brightness"] & 0xFF,
                  int(led["options"], 16) & 0xFF, *led["rgb"]]
        return P.build_led_cmd(P.Cmd.SET_LEDPARAM, params)

    restore = frame_for(led["mode"])

    print("=" * 66)
    print("灯效模式有效性验证" + ("（DRY-RUN）" if args.dry else ""))
    print("=" * 66)
    print(f"当前：模式 {led['mode']}({led['mode_name']}) 亮度 {led['brightness']} "
          f"速度 {led['speed_raw']} option {led['options']}")
    print(f"待测 {len(modes)} 个模式，每个写 1 次 flash\n")

    if args.dry:
        for m in modes:
            tag = "（界面已隐藏）" if m in HIDDEN else ""
            print(f"  mode {m:>2}  {P.LED_MODES[m]}{tag}")
        print("\nDRY-RUN 结束，未发送任何字节。")
        kb.close()
        return 0

    dwell = 3.0 if args.slow else 1.2
    ok: list[int] = []
    bad: list[int] = []

    try:
        for m in modes:
            kb.send(frame_for(m))
            time.sleep(dwell)
            back = P.parse_ledparam(kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True) or b"")
            got = back.get("mode")
            good = (got == m)
            (ok if good else bad).append(m)
            name = P.LED_MODES[m]
            mark = "OK  " if good else "无效"
            note = "" if good else f"  ← 读回 {got}({back.get('mode_name', '?')})"
            hidden = "  [隐藏]" if m in HIDDEN else ""
            print(f"  [{mark}] mode {m:>2}  {name}{hidden}{note}")
    finally:
        # 恢复原灯效
        kb.send(restore)
        time.sleep(1.2)
        back = P.parse_ledparam(kb.query(P.Cmd.GET_LEDPARAM, None, bit8=True) or b"")
        print(f"\n已恢复：{back.get('mode')}({back.get('mode_name')})")
        kb.close()

    print("\n" + "=" * 66)
    print(f"有效 {len(ok)} 个，无效 {len(bad)} 个")
    if bad:
        print(f"\n无效的模式编号：{sorted(bad)}")
        print("把这行填进 ui/app.py：")
        print(f"    INVALID_MODES: set[int] = {set(bad)}")
    else:
        print("所有测到的编号固件都接受了 —— 说明 LED_MODES 表本身没问题。")
        print("但「效果和名字对不对得上」还得你自己看一遍（比如流星是不是流星）。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
