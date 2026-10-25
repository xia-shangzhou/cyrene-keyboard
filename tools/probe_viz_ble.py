"""蓝牙 0x0D 新旧线格式对照探针（需要人眼盯着键盘）。

背景
----
同平台文档说蓝牙的 0x0D 是「频段半字节打包、紧跟命令字节、无校验和」，
而我们一直在发 USB 格式 —— 症状正是用户报告的「只有右边 8 个键亮」（镜像且变小）。

这个脚本按顺序试 5 组，每组 3.5 秒：

  [1] 新格式 · 低 nibble 在前 · 竖向柱      ← 预期：全键盘 8 列，从下往上
  [2] 新格式 · 高 nibble 在前 · 竖向柱      ← 若 [1] 左右镜像，这组应该是对的
  [3] 新格式 · 低 nibble 在前 · 横向从中间
  [4] 新格式 · 低 nibble 在前 · 横向从左
  [5] 旧格式（USB 式）· 竖向柱             ← 对照组，应复现"右边 8 个键"

用法：python tools/probe_viz_ble.py
结束后恢复原灯效。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb.backend import HidBackend, LedState        # noqa: E402
from kb import protocol as P                        # noqa: E402

BANDS = [4, 4, 4, 4, 4, 4, 4, 4]                   # 每带中等高度，肉眼最好认
FULL16 = [BANDS[i // 2] for i in range(P.AUDIO_BANDS)]


def push_usb_style(b: HidBackend, seconds: float) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        b.kb.send(P.build_audio_viz(FULL16))
        time.sleep(0.04)


def push_ble(b: HidBackend, seconds: float, high_first: bool) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        b.kb.send(P.build_audio_viz_ble(BANDS, high_first=high_first))
        time.sleep(0.04)


def set_mode(b: HidBackend, style: int) -> None:
    st = LedState(mode=22, speed=2, brightness=4, direction=style,
                  colorful=False, rgb=(255, 120, 180))
    if not b.set_led(st):
        time.sleep(0.4)
        b.set_led(st)
    time.sleep(0.3)


def main() -> int:
    hb = HidBackend(readonly=False)
    hb.connect()
    if hb.name != "hid" or not hb.config_ok:
        print("!! 当前通道不能写配置：", hb.device_label, "（插线或切蓝牙再来）")
        return 2
    original, original_sled = hb.get_led(), hb.get_sled()
    print(f"通道 {hb.device_label}；原灯效 {original.mode_name if original else '?'}")
    print("★ 盯着键盘，每组 3.5 秒：\n")

    plan = [
        ("[1/5] 新格式 · 低nibble在前 · 竖向柱", 0, lambda: push_ble(hb, 3.5, False)),
        ("[2/5] 新格式 · 高nibble在前 · 竖向柱", 0, lambda: push_ble(hb, 3.5, True)),
        ("[3/5] 新格式 · 低nibble在前 · 横向从中间", 1, lambda: push_ble(hb, 3.5, False)),
        ("[4/5] 新格式 · 低nibble在前 · 横向从左", 2, lambda: push_ble(hb, 3.5, False)),
        ("[5/5] 旧格式(USB式) · 竖向柱（对照）", 0, lambda: push_usb_style(hb, 3.5)),
    ]
    try:
        for label, style, fn in plan:
            set_mode(hb, style)
            print(label)
            fn()
            time.sleep(0.6)
    finally:
        print("\n恢复原灯效…")
        if original is not None:
            hb.set_led(original)
        if original_sled is not None:
            hb.set_sled(original_sled)
        hb.disconnect()
        print("完成。告诉我：[1]和[2]哪组柱子是从左往右正常排的（另一组应该是镜像）、"
              "[3][4]有没有出现横向的光。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
