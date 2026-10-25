"""
音频引擎自检：不需要键盘、不需要你手动放歌。

自己合成一段 40Hz→14kHz 的对数扫频放出去，再从 loopback 抓回来分析，
检查 8 个频带是不是依次被点亮。任何一环断了（没声音、采样率不匹配、
分带算错）都会在这里暴露。

    python tools/test_audioviz.py
"""

from __future__ import annotations

import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb.audioviz import AudioVizEngine  # noqa: E402

SR = 48000
DUR = 3.0
F_LO, F_HI = 40.0, 14000.0


def make_sweep(path: Path) -> None:
    n = int(SR * DUR)
    t = np.arange(n) / SR
    # 对数扫频：瞬时频率从 F_LO 指数上升到 F_HI
    k = np.log(F_HI / F_LO) / DUR
    phase = 2 * np.pi * F_LO * (np.exp(k * t) - 1) / k
    sig = 0.28 * np.sin(phase)
    fade = int(SR * 0.05)
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    pcm = (sig * env * 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(stereo.tobytes())


def main() -> int:
    if not AudioVizEngine.available():
        print("缺少 soundcard 库")
        return 2

    tmp = Path(tempfile.gettempdir()) / "_xilian_sweep.wav"
    make_sweep(tmp)

    engine = AudioVizEngine()
    print(f"采集源将自动选择默认输出…")
    if not engine.start():
        print(f"启动失败：{engine.error}")
        return 3
    print(f"实际采集源：{engine.device_label}\n")

    # 先测安静时的底噪（应该全 0）
    time.sleep(0.6)
    quiet = engine.levels()
    print(f"安静时电平: {[round(v, 2) for v in quiet]}")

    import winsound

    winsound.PlaySound(str(tmp), winsound.SND_FILENAME | winsound.SND_ASYNC)

    hits = [0.0] * 8
    t0 = time.monotonic()
    while time.monotonic() - t0 < DUR + 0.3:
        lv = engine.levels()
        for i, v in enumerate(lv):
            hits[i] = max(hits[i], v)
        time.sleep(0.03)
    winsound.PlaySound(None, winsound.SND_PURGE)

    time.sleep(0.3)
    engine.stop()

    print("\n各频带扫频过程中达到的最大电平：")
    names = ["40-82", "82-168", "168-343", "343-700",
             "700-1430", "1430-2917", "2917-5949", "5949-14000"]
    ok_count = 0
    for i, (nm, v) in enumerate(zip(names, hits)):
        bar = "█" * int(v * 20)
        flag = "✓" if v >= 0.55 else "✗"
        if v >= 0.55:
            ok_count += 1
        print(f"  {nm:>12} Hz  {v:4.2f}  {bar:<20} {flag}")

    print("=" * 62)
    if max(quiet) > 0.25:
        print(f"✗ 安静时有输出（最大 {max(quiet):.2f}）—— 噪声门阈值偏低")
        ok = False
    elif ok_count >= 7:
        print(f"✓ 8 个频带中 {ok_count} 个被有效点亮，分带与 AGC 正常")
        ok = True
    elif ok_count >= 4:
        print(f"⚠ 只有 {ok_count}/8 个频带响应明显 —— 检查 freq_low/freq_high 与增益")
        ok = True
    else:
        print(f"✗ 只有 {ok_count}/8 个频带响应 —— 采集链路或分带逻辑有问题")
        ok = False

    try:
        tmp.unlink()
    except OSError:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
