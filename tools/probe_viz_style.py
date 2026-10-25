"""在真机上逐个试「音乐律动样式」的参数组合，靠人眼看键盘判断哪种有效。

背景
----
`MusicPage` 里 styles 是 0 竖向柱 / 1 中间向两边 / 2 从左向右，实现方式是
`SET_LEDPARAM` 的 options 高 nibble（`(direction << 4) | 0x07|0x08`）。
实测只有样式 0 有画面，1 和 2 键盘全黑 —— 可能是固件只认低 nibble，或者只认某个 mode。

这个脚本把几种可能的编码挨个试一遍，每组 4 秒，控制台会打印当前是第几组，
用户盯着键盘说"哪一组出现了横向的光"就能定位。

用法
----
    python tools/probe_viz_style.py          # 只试样式（mode 22）
    python tools/probe_viz_style.py --all    # 连 mode 20 一起试

只会发灯效参数和 0x0D 电平，不写 flash、不碰固件；结束时恢复原灯效。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb.backend import HidBackend, LedState          # noqa: E402
from kb import protocol as P                          # noqa: E402

BANDS = [3, 3, 3, 3, 3, 3, 3, 3]                      # 16 段里的前 8 段，全给中等高度
FULL = [3] * P.AUDIO_BANDS


def push_during(b: HidBackend, seconds: float) -> None:
    """边推 0x0D 边等 —— 固件要靠这个流才会有柱子"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        b.set_audio_viz(FULL)
        time.sleep(0.04)


def trial(b: HidBackend, mode: int, opts: int, label: str) -> None:
    st = LedState(mode=mode, speed=2, brightness=4, direction=0, colorful=False,
                  rgb=(255, 120, 180))
    # 直接把 options 塞进去：这里绕过 set_led 的打包，方便试各种取值
    b.kb.send(P.build_ledparam(mode, 2, 4, opts, st.rgb))
    time.sleep(0.35)
    push_during(b, 3.2)
    print("   done")


def main() -> int:
    all_modes = "--all" in sys.argv
    hb = HidBackend(readonly=False)
    hb.connect()
    # ⚠ connect() 的返回值不可靠（成功也常返回 None），判通道要用这两个属性
    if hb.name != "hid" or not hb.config_ok:
        print("!! 当前通道不能写配置：", hb.device_label, "（插线或切蓝牙再来）")
        return 2

    original, original_sled = hb.get_led(), hb.get_sled()
    print(f"当前通道 {hb.device_label}；原灯效 {original.mode_name if original else '读不到'}")
    print("★ 请盯着键盘：下面每组亮 3.5 秒，看哪种出现「横向」的光\n")

    modes = [22, 20] if all_modes else [22]
    plan = []
    for mode in modes:
        plan += [
            (mode, 0x07, f"mode={mode} 竖向（基线，应能看到 8 根竖柱）"),
            (mode, 0x10, f"mode={mode} 样式1 放低 nibble（opts=0x10）"),
            (mode, 0x20, f"mode={mode} 样式2 放低 nibble（opts=0x20）"),
            (mode, 0x17, f"mode={mode} 样式1 放高 nibble（opts=0x17，当前代码的做法）"),
            (mode, 0x27, f"mode={mode} 样式2 放高 nibble（opts=0x27，当前代码的做法）"),
            (mode, 0x08, f"mode={mode} 低 nibble 换 0x08（彩虹位）"),
        ]

    try:
        for i, (mode, opts, label) in enumerate(plan, 1):
            print(f"[{i}/{len(plan)}] {label}")
            trial(hb, mode, opts, label)
    finally:
        print("\n恢复原灯效…")
        if original is not None:
            hb.set_led(original)
        if original_sled is not None:
            hb.set_sled(original_sled)
        hb.disconnect()
        print("完成。哪一组看起来是横向的？告诉我编号，或者告诉我全都只有竖柱。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
