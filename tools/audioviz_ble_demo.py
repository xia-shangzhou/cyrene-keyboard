"""
蓝牙（BLE）下的音乐律动演示 —— 25Hz 档，看清效果 + 感受打字手感。

为什么要单独一个工具：
  律动在蓝牙下只能跑 25Hz（BLE 一次往返 16ms，链路窄），而「有没有真的跳」
  只能靠眼睛看。这个工具不采集音频，直接推一个明显的扫描图案，跑固定秒数，
  结束时自动恢复原灯效 —— 用来肉眼验收，也用来体感「推帧时打字会不会迟钝」。

用法：
    python tools/audioviz_ble_demo.py               # 15 秒，25Hz
    python tools/audioviz_ble_demo.py --seconds 30
    python tools/audioviz_ble_demo.py --hz 50       # 对照：50Hz 会明显占满链路

跑起来之后请：
  1. 盯着键盘看 —— 应该有一道光柱从左往右扫，循环往复
  2. 同时随便打几个字，感受有没有变迟钝
  3. 结束后灯效会自动回到跑之前的样子
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb.backend import HidBackend      # noqa: E402
from kb import protocol as P           # noqa: E402


def sweep_frame(i: int) -> list[int]:
    """一道光柱从左往右扫：16 段里只有 2 段是亮的。

    固件会把相邻两段求和 clamp 到 8，所以 16 段上的一道窄光柱
    在键盘上就是一根会移动的柱子。
    """
    pos = i % P.AUDIO_BANDS
    return [4 if j in (pos, (pos + 1) % P.AUDIO_BANDS) else 0
            for j in range(P.AUDIO_BANDS)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--hz", type=float, default=25.0,
                    help="推帧频率，蓝牙建议 25；50 用来对照链路被占满的感觉")
    args = ap.parse_args()

    b = HidBackend(readonly=False)
    b.connect()
    if not b.kb:
        print("没有连上键盘")
        return 2
    link = b.kb.link
    print(f"连接方式：{b.kb.link_name}（0x{b.kb.vid:04X}:0x{b.kb.pid:04X}）")
    if not b.config_ok:
        print("配置通道不可用，推帧到不了键盘")
        return 2
    if link != "ble":
        print("提示：这个工具是给蓝牙验收用的，当前不是蓝牙。照样能跑。")

    original = b.get_led()
    if original is None:
        print("读不到当前灯效，为了能恢复原状，先不跑了")
        return 2
    print(f"原灯效：{original.mode_name} / 速度{original.speed} / 亮度{original.brightness}"
          f" / rgb{original.rgb}")

    interval = 1.0 / max(1.0, args.hz)
    total = max(1, int(args.seconds / interval))
    print(f"\n开始演示：{args.hz:.0f}Hz × {total} 帧（约 {args.seconds:.0f} 秒）")
    print("▶ 请看键盘：一道光柱从左往右扫。同时随便打几个字，试试手感。\n")

    lat: list[float] = []
    try:
        # 必须先切到音乐模式，否则固件没有渲染器接手，数据白推（见日志 3.7）
        b.policy.forget("led")
        from kb.backend import LedState
        b.set_led(LedState(mode=P.AUDIO_MODES[1], speed=2, brightness=4,
                           direction=0, colorful=True, rgb=original.rgb))
        time.sleep(0.3)

        t0 = time.perf_counter()
        for i in range(total):
            target = t0 + i * interval
            now = time.perf_counter()
            if target > now:
                time.sleep(target - now)
            t = time.perf_counter()
            b.set_audio_viz(sweep_frame(i))
            lat.append(1000 * (time.perf_counter() - t))
            if (i + 1) % max(1, total // 5) == 0:
                done = (i + 1) / (time.perf_counter() - t0)
                print(f"  {i + 1:>4}/{total} 帧 · 实际 {done:.0f} fps")
    finally:
        lat.sort()
        print("\n恢复原灯效…")
        try:
            b.policy.forget("led")
            b.set_led(original)
        finally:
            time.sleep(0.3)
            back = b.get_led()
            print("恢复结果：", back.mode_name if back else "读不到")
            if lat:
                print(f"单帧耗时 中位 {lat[len(lat) // 2]:.0f}ms / 最大 {lat[-1]:.0f}ms")
            print("统计：", b.policy.summary())
            b.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
