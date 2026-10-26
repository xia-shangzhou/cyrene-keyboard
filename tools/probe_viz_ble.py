"""蓝牙 0x0D 新旧线格式对照探针（需要人眼盯着键盘）。

背景
----
同平台文档说蓝牙的 0x0D 是「频段半字节打包、紧跟命令字节、无校验和」，
而我们一直在发 USB 格式 —— 症状正是用户报告的「只有右边 8 个键亮」（镜像且变小）。

这个脚本按顺序试 5 组，每组几秒（图案是「左低右高」的阶梯，好判断方向）：

  [1] 新格式 · 低 nibble 在前 · 竖向柱      ← 最高柱应在键盘「右侧」
  [2] 新格式 · 高 nibble 在前 · 竖向柱      ← 若 [1] 左右镜像，这组最高柱会在「左侧」
  [3] 新格式 · 低 nibble 在前 · 横向从中间
  [4] 新格式 · 低 nibble 在前 · 横向从左
  [5] 旧格式（USB 式）· 竖向柱             ← 对照组，应复现"右边 8 个键"

用法：python tools/probe_viz_ble.py
     python tools/probe_viz_ble.py --only 1,2      # 先定 nibble 顺序
     python tools/probe_viz_ble.py --only 3,4,5    # 再看横向样式 + 对照
结束后恢复原灯效。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb.backend import HidBackend, LedState        # noqa: E402
from kb import protocol as P                        # noqa: E402

# 阶梯图案（0-5）：从左到右单调变高 → 一眼看出柱子朝哪边
# ⚠ 绝不能拿等高图案测「左右镜像」：8 列一样高时镜像前后完全相同，肉眼必然分不出。
#   （2026-10-26 第一轮探针就栽在这，主人回报"看不出区别"）
STAIRS = [0, 1, 2, 3, 4, 5, 5, 5]
FULL16 = [STAIRS[i // 2] for i in range(P.AUDIO_BANDS)]


def push_usb_style(b: HidBackend, seconds: float, bands16: list[int]) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        b.kb.send(P.build_audio_viz(bands16))
        time.sleep(0.04)


def push_ble(b: HidBackend, seconds: float, high_first: bool,
             pattern: list[int] | None = None) -> None:
    pat = STAIRS if pattern is None else pattern
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        b.kb.send(P.build_audio_viz_ble(pat, high_first=high_first))
        time.sleep(0.04)


def set_mode(b: HidBackend, style: int) -> None:
    st = LedState(mode=22, speed=2, brightness=4, direction=style,
                  colorful=False, rgb=(255, 120, 180))
    if not b.set_led(st):
        time.sleep(0.4)
        b.set_led(st)
    time.sleep(0.3)


def main() -> int:
    ap = argparse.ArgumentParser(description="蓝牙 0x0D 线格式对照探针（需要人眼盯着键盘）")
    ap.add_argument("--only", default="", help="只跑指定组号，逗号分隔：1,2 / 3,4 / 3,4,5")
    ap.add_argument("--seconds", type=float, default=4.0, help="每组持续秒数（默认 4.0）")
    ap.add_argument("--countdown", type=int, default=3, help="开始前倒计时秒数（默认 3）")
    ap.add_argument("--reverse", action="store_true",
                    help="用反向阶梯（左大右小），验证横向样式的光位置是否跟值走")
    args = ap.parse_args()

    only: set[int] | None = None
    if args.only.strip():
        only = {int(t) for t in args.only.replace("，", ",").split(",") if t.strip().isdigit()}

    hb = HidBackend(readonly=False)
    hb.connect()
    if hb.name != "hid" or not hb.config_ok:
        print("!! 当前通道不能写配置：", hb.device_label, "（插线或切蓝牙再来）")
        return 2
    original, original_sled = hb.get_led(), hb.get_sled()

    pat = STAIRS[::-1] if args.reverse else STAIRS
    bands16 = [pat[i // 2] for i in range(P.AUDIO_BANDS)]
    print(f"图案：{'左大右小' if args.reverse else '左小右大'} {pat}")

    plan = [
        (1, "新格式 · 低nibble在前 · 竖向柱", 0, lambda s: push_ble(hb, s, False, pat)),
        (2, "新格式 · 高nibble在前 · 竖向柱", 0, lambda s: push_ble(hb, s, True, pat)),
        (3, "新格式 · 低nibble在前 · 横向从中间", 1, lambda s: push_ble(hb, s, False, pat)),
        (4, "新格式 · 低nibble在前 · 横向从左", 2, lambda s: push_ble(hb, s, False, pat)),
        (5, "旧格式(USB式) · 竖向柱（对照）", 0, lambda s: push_usb_style(hb, s, bands16)),
    ]
    if only:
        plan = [p for p in plan if p[0] in only]
        if not plan:
            print("!! --only 里没有有效组号（可选 1-5）")
            hb.disconnect()
            return 2
    ran = {p[0] for p in plan}

    print(f"通道 {hb.device_label}；原灯效 {original.mode_name if original else '?'}")
    print(f"本次跑 {len(plan)} 组：{', '.join(f'[{p[0]}]{p[1]}' for p in plan)}")
    for n in range(args.countdown, 0, -1):
        print(f"  {n}…", flush=True)
        time.sleep(1)
    print("★ 盯着键盘：\n", flush=True)

    try:
        for no, label, style, fn in plan:
            set_mode(hb, style)
            print(f"[{no}] {label}   （{args.seconds:.1f} 秒）", flush=True)
            fn(args.seconds)
            time.sleep(1.0)
    finally:
        print("\n恢复原灯效…")
        if original is not None:
            hb.set_led(original)
        if original_sled is not None:
            hb.set_sled(original_sled)
        hb.disconnect()
        print("完成。")
        if {1, 2} & ran:
            print("  → [1][2] 最高的那几根柱在键盘左边还是右边？（两组应该正好相反）")
        if {3, 4} & ran:
            print("  → [3][4] 横向样式：光是从中间往两边铺，还是从左往右铺？全黑就说不亮")
        if 5 in ran:
            print("  → [5] 是不是复现了「只有右边几个键亮」？")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
