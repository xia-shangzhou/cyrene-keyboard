"""
测量音频→loopback 的端到端延迟，看延时到底花在哪一环。

为什么要分开测
--------------
用户感觉"灯比音乐慢半拍"，可能是三个完全不同的原因，优化手段也完全不同：

  1. **蓝牙链路延迟**（A2DP 编码 + 传输 + 耳机缓冲，通常 150-300ms）
     → 但它只影响"耳朵听到"的时刻。如果 loopback 抓的是系统渲染流，
       灯其实会**比耳朵听到的早**，反而对不上嘴。

  2. **端点缓冲延迟**：某些蓝牙端点在驱动层就有很长的缓冲，
     WASAPI loopback 抓到的是那一层 —— 这时灯会真的滞后。

  3. **我们的处理链**：采集块 2048 @48kHz ≈ 43ms + FFT 窗口 ≈ 43ms + 推送 20ms

测量方法：播一段「前置静音 + 单个短脉冲」，记录提交播放的时刻，
再用极小的采集块（256 ≈ 5ms）盯着 loopback，抓脉冲出现的时刻。

    python tools/measure_latency.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import soundcard as sc  # noqa: E402

SR = 48000
LEAD = 0.35          # 前置静音（秒）
PULSE = 0.05         # 脉冲长度
DETECT_BLOCK = 256   # 检测用小块 ≈ 5.3ms


def make_signal() -> np.ndarray:
    n_lead = int(SR * LEAD)
    n_pulse = int(SR * PULSE)
    sig = np.zeros(n_lead * 2 + n_pulse, dtype=np.float32)
    t = np.arange(n_pulse) / SR
    # 1kHz 短脉冲，前后加余弦渐变避免爆音
    env = np.ones(n_pulse)
    ramp = min(n_pulse // 4, int(SR * 0.005))
    env[:ramp] = np.linspace(0, 1, ramp)
    env[-ramp:] = np.linspace(1, 0, ramp)
    sig[n_lead:n_lead + n_pulse] = 0.5 * np.sin(2 * np.pi * 1000 * t) * env
    return np.repeat(sig[:, None], 2, axis=1)


def main() -> int:
    spk = sc.default_speaker()
    loops = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
    mic = None
    for m in loops:
        if m.name == spk.name:
            mic = m
            break
    if mic is None:
        print("找不到默认输出的回环接口")
        return 2

    print(f"输出设备: {spk.name}")
    print(f"回环设备: {mic.name}")
    print(f"检测块长: {DETECT_BLOCK} 采样 ≈ {DETECT_BLOCK / SR * 1000:.1f}ms\n")

    signal = make_signal()
    hits: list[float] = []
    running = {"on": True}
    started = threading.Event()

    def record_loop() -> None:
        try:
            with mic.recorder(samplerate=SR, channels=2,
                              blocksize=DETECT_BLOCK) as rec:
                started.set()
                while running["on"]:
                    d = rec.record(numframes=DETECT_BLOCK)
                    if d is not None and len(d) and float(np.abs(d).max()) > 0.01:
                        hits.append(time.monotonic())
        except Exception as e:  # noqa: BLE001
            print(f"采集异常: {e}")

    th = threading.Thread(target=record_loop, daemon=True)
    th.start()
    started.wait(timeout=2)
    time.sleep(0.25)     # 让采集稳定

    for round_i in range(3):
        hits.clear()
        time.sleep(0.15)
        t0 = time.monotonic()
        spk.play(signal, samplerate=SR)      # 阻塞，播完返回
        t_submit_done = time.monotonic()
        time.sleep(0.15)

        if not hits:
            print(f"  第 {round_i + 1} 次：没检测到脉冲（蓝牙可能把流缓存了）")
            continue
        # 取第一个超过阈值的时刻，减去前置静音，就是「从提交到可见」的延迟
        lat = (hits[0] - t0) - LEAD
        print(f"  第 {round_i + 1} 次：提交→loopback 可见 = {lat * 1000:.0f} ms"
              f"（播放调用本身耗时 {(t_submit_done - t0) * 1000:.0f} ms）")

    running["on"] = False
    th.join(timeout=1)

    print("\n" + "=" * 62)
    print("怎么解读：")
    print("  · 10~30ms   → 端点缓冲很小，灯基本跟得上（处理链可再优化）")
    print("  · 150~300ms → 蓝牙端点在驱动层就有长缓冲，loopback 也吃到了这份延迟")
    print("                这种只能靠「提前预览」补偿，或改用有线/扬声器")
    print("  · 检测不到  → 蓝牙把整段流缓到播完才送出，说明延迟大到没法做实时律动")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
