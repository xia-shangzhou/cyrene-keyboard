"""
环境自检：WASAPI loopback 是否真的能抓到「系统正在播放的声音」。

为什么要单独测
--------------
`recorder()` 能打开不代表能拿到数据 —— 声卡、独占模式、采样率不匹配都可能
让数据全零。这里自己放一段正弦波出去，再从 loopback 抓回来比对，
形成闭环，才能断定「链路上每一环都通」。

    python tools/check_loopback.py
"""

from __future__ import annotations

import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import soundcard as sc

SR = 48000
DUR = 1.2
FREQ = 440.0


def make_wav(path: Path, amp: float = 0.12) -> None:
    """生成一段带淡入淡出的 440Hz 正弦，避免爆音。"""
    n = int(SR * DUR)
    t = np.arange(n) / SR
    wave_data = amp * np.sin(2 * np.pi * FREQ * t)
    fade = int(SR * 0.05)
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    pcm = (wave_data * env * 32767).astype(np.int16)
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(stereo.astype("<i2").tobytes())


def main() -> int:
    spk = sc.default_speaker()
    print(f"默认输出设备: {spk.name}")
    mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
    print(f"对应 loopback: {mic.name}  isloopback={mic.isloopback}")

    tmp = Path(tempfile.gettempdir()) / "_xilian_loopback_test.wav"
    make_wav(tmp)

    import winsound

    print(f"\n播放 {DUR}s 440Hz 正弦并从 loopback 抓取…")
    with mic.recorder(samplerate=SR, channels=2, blocksize=2048) as rec:
        winsound.PlaySound(str(tmp), winsound.SND_FILENAME | winsound.SND_ASYNC)
        time.sleep(0.15)
        data = rec.record(numframes=int(SR * DUR))
    winsound.PlaySound(None, winsound.SND_PURGE)

    a = data[:, 0]
    peak = float(np.abs(a).max())
    rms = float(np.sqrt((a ** 2).mean()))

    # 找主频：是不是 440Hz
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    freqs = np.fft.rfftfreq(len(a), 1 / SR)
    dom = float(freqs[int(spec.argmax())])

    print(f"\n抓到 {len(a)} 采样  peak={peak:.4f}  rms={rms:.4f}  主频={dom:.0f}Hz")
    print("=" * 60)
    if peak < 1e-3:
        print("结论：全零 —— loopback 拿不到数据")
        print("排查方向：Windows 声音设置 → 该输出设备是否被独占；")
        print("          增强/音效驱动(如 Nahimic、Realtek APO)是否拦截了 loopback。")
        ok = False
    elif abs(dom - FREQ) < 15:
        print(f"结论：链路完好 ✅ 主频 {dom:.0f}Hz ≈ 播放的 {FREQ:.0f}Hz，幅度正常")
        ok = True
    else:
        print(f"结论：有数据但主频对不上（{dom:.0f}Hz）—— 可能是别的程序在出声，或重采样有问题")
        ok = True

    try:
        tmp.unlink()
    except OSError:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
