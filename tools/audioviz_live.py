"""
音乐律动实时演示：抓系统声音 → FFT → 推给键盘。

用法
----
    # 先放点音乐，然后跑：
    python tools/audioviz_live.py

    # 换样式（0=竖向柱 1=中间向两边 2=从左向右）
    python tools/audioviz_live.py --style 1

    # 抓指定输出设备的声音（默认抓系统默认输出）
    python tools/audioviz_live.py --device "扬声器 (Realtek(R) Audio)"

    # 不碰键盘，只在终端看频谱（调参数用）
    python tools/audioviz_live.py --dry

    # 跑 30 秒自动退出（默认一直跑到 Ctrl+C）
    python tools/audioviz_live.py --seconds 30

退出时（含 Ctrl+C）会自动恢复进入前的灯效。
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import protocol as P                          # noqa: E402
from kb.audioviz import AudioVizEngine, VizSettings   # noqa: E402
from kb.backend import LedState, open_backend         # noqa: E402

BAR = " ▁▂▃▄▅▆▇█"


def make_demo_wav(path: Path, sr: int = 48000, dur: float = 8.0) -> None:
    """合成一段"有节奏有旋律"的测试音频：鼓点 + 五声音阶 + 高频镲。

    三个声部故意覆盖低/中/高频，这样 8 根柱子才会一起跳，
    一眼就能看出键盘到底有没有跟着动。
    """
    import wave

    import numpy as np

    n = int(sr * dur)
    sig = np.zeros(n, dtype=np.float64)
    rng = np.random.default_rng(7)

    # 鼓点：每 500ms 一个 62Hz 衰减脉冲
    for k in range(int(dur / 0.5) + 1):
        s = int(k * 0.5 * sr)
        ln = min(int(0.18 * sr), n - s)
        if ln <= 0:
            break
        tt = np.arange(ln) / sr
        env = np.exp(-tt * 18)
        sig[s:s + ln] += 0.55 * env * np.sin(2 * np.pi * 62 * tt)
        sig[s:s + ln] += 0.25 * env * np.sin(2 * np.pi * 124 * tt)

    # 旋律：每 250ms 一个音，五声音阶
    notes = [220, 262, 294, 349, 392, 440, 523, 587, 659, 784, 880]
    seq = rng.choice(len(notes), int(dur / 0.25) + 1)
    for k, ni in enumerate(seq):
        s = int(k * 0.25 * sr)
        ln = min(int(0.25 * sr), n - s)
        if ln <= 0:
            break
        tt = np.arange(ln) / sr
        env = np.minimum(1.0, tt * 60) * np.exp(-tt * 3.5)
        f = notes[ni]
        sig[s:s + ln] += 0.22 * env * (np.sin(2 * np.pi * f * tt)
                                       + 0.3 * np.sin(2 * np.pi * f * 2 * tt))

    # 镲：每秒一次高频噪声
    for k in range(int(dur) + 1):
        s = int(k * 1.0 * sr)
        ln = min(int(0.12 * sr), n - s)
        if ln <= 0:
            break
        tt = np.arange(ln) / sr
        sig[s:s + ln] += 0.12 * np.exp(-tt * 40) * rng.standard_normal(ln)

    peak = float(np.abs(sig).max()) or 1.0
    pcm = (sig / peak * 0.42 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())



def draw(levels: list[float], db: list[float], n: int, style: int) -> None:
    cells = "".join(BAR[min(8, int(v * 8 + 0.5))] * 1 for v in levels)
    peak = max(db) if db else -90
    sys.stdout.write(f"\r  音量 {cells}  {peak:6.1f}dB  帧 {n:5d}  style{style}")
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", type=int, default=0, help="音乐样式 0-2")
    ap.add_argument("--mode", type=int, default=22, choices=P.AUDIO_MODES,
                    help="22=音乐律动条 20=音乐节奏（同一渲染器，仅样式不同）")
    ap.add_argument("--fps", type=int, default=50, help="推送频率，默认 50")
    ap.add_argument("--device", default=None, help="输出设备名，默认系统默认输出")
    ap.add_argument("--seconds", type=float, default=0, help="跑多少秒后自动停（0=不限）")
    ap.add_argument("--gain", type=float, default=1.0, help="增益倍数")
    ap.add_argument("--brightness", type=int, default=None,
                    help="音乐模式下的亮度 0-4；不给则沿用当前（--demo 时默认提到 4）")
    ap.add_argument("--dry", action="store_true", help="不连键盘，只在终端看频谱")
    ap.add_argument("--demo", action="store_true",
                    help="播放内置测试音频（鼓点+旋律+镲），不用自己找歌")
    args = ap.parse_args()

    if not AudioVizEngine.available():
        print("缺少 soundcard 库：pip install soundcard")
        return 2

    backend = None
    original: LedState | None = None
    demo_path: Path | None = None
    engine = AudioVizEngine(device_name=args.device,
                            settings=VizSettings(gain=args.gain))

    def restore() -> None:
        engine.stop()
        if demo_path:
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:  # noqa: BLE001
                pass
        if backend and original:
            try:
                backend.policy.forget("led")
                backend.set_led(original)
                time.sleep(0.4)
                print(f"\n已恢复原灯效：{original.mode_name} 亮度 {original.brightness}")
            except Exception as e:  # noqa: BLE001
                print(f"\n⚠ 恢复灯效失败：{e}")

    if not args.dry:
        backend = open_backend()
        if backend.name != "hid":
            print("没有检测到有线键盘，退回模拟模式（数据不会真的到键盘）")
        elif backend.readonly:
            backend.readonly = False
            backend.kb.readonly = False
            backend.policy.strict_readonly = False

        original = backend.get_led()
        if not original:
            print("读不到当前灯效，放弃（无法恢复）")
            return 2
        print(f"当前灯效：{original.mode_name} 亮度 {original.brightness} RGB {original.rgb}")

        # 音乐模式下 option 高 nibble = 样式
        style = max(0, min(2, args.style))
        # 音乐模式的柱高受亮度缩放，亮度 1 基本看不清，验收时提到 4
        if args.brightness is not None:
            bright = max(0, min(4, args.brightness))
        else:
            bright = 4 if args.demo else original.brightness
        music = LedState(
            mode=args.mode, speed=original.speed, brightness=bright,
            direction=style, colorful=original.colorful, rgb=original.rgb,
        )
        backend.policy.forget("led")
        backend.set_led(music)
        time.sleep(0.5)
        check = backend.get_led()
        if check and check.mode != args.mode:
            print(f"⚠ 切模式失败：回读仍是 {check.mode_name}")
            restore()
            return 3
        print(f"已切到 {P.LED_MODES.get(args.mode)} / 样式 {style}")

    print("开始采集系统音频…（Ctrl+C 退出）")
    if not engine.start():
        print(f"启动采集失败：{engine.error}")
        restore()
        return 4
    print(f"采集源：{engine.device_label}")

    if args.demo:
        import tempfile
        import winsound

        demo_path = Path(tempfile.gettempdir()) / "_xilian_demo.wav"
        make_demo_wav(demo_path, dur=max(8.0, args.seconds or 8.0))
        winsound.PlaySound(str(demo_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        print("正在播放内置测试音频（鼓点 + 旋律 + 高频镲）")

    stop = {"flag": False}

    def on_sigint(_sig, _frm):
        stop["flag"] = True

    signal.signal(signal.SIGINT, on_sigint)

    period = 1.0 / max(1, args.fps)
    t_start = time.monotonic()
    n = 0
    try:
        while not stop["flag"] and not engine.error:
            t0 = time.monotonic()
            levels = engine.levels()
            bands = engine.snapshot()
            if backend:
                backend.set_audio_viz(bands)
            draw(levels, engine.levels_db(), n, args.style)
            n += 1
            if args.seconds and (t0 - t_start) >= args.seconds:
                break
            sleep = period - (time.monotonic() - t0)
            if sleep > 0:
                time.sleep(sleep)
    finally:
        if engine.error:
            print(f"\n采集出错：{engine.error}")
        print(f"\n共推送 {n} 帧，平均 {n / max(0.001, time.monotonic() - t_start):.1f} fps")
        if backend:
            print("安全策略统计：" + backend.policy.summary())
            print("  ↑ 「写」应该只有 2 次（切模式 + 恢复）；流式帧再多也不算写入")
        restore()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
