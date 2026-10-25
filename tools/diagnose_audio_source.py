"""
诊断：音乐律动抓的到底是不是"本机播放的音乐"，有没有混进麦克风。

背景
----
现象可能是「键盘跟着说话声跳，而不是跟着歌跳」。三种可能，症状很像但原因完全不同：
  1. 采集源选错，抓到 VB-Cable 之类的虚拟设备（那里面可能是语音）
  2. loopback 匹配失败，退回了真实麦克风
  3. 声卡驱动的"麦克风侦听 / 侧音"把麦克风混进了播放流

做法
----
同时抓两路：当前播放设备的 loopback + 系统默认麦克风。
先测一段静默（谁都不许出声），再测一段"请你说话"。
  · 说话时 loopback 电平明显抬起来 → loopback 里确实串了麦克风
  · 说话时 loopback 基本不动     → loopback 是干净的，问题在别处

    python tools/diagnose_audio_source.py
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import soundcard as sc  # noqa: E402

from kb.audioviz import AudioVizEngine  # noqa: E402

SR = 48000
BLOCK = 1024


def peak_of(mic, seconds: float, out: list) -> None:
    """抓一段，把每块峰值写进 out。设备打不开就把异常字符串塞进去。"""
    try:
        with mic.recorder(samplerate=SR, channels=2, blocksize=BLOCK) as rec:
            t0 = time.monotonic()
            while time.monotonic() - t0 < seconds:
                data = rec.record(numframes=BLOCK)
                if data is not None and len(data):
                    out.append(float(np.abs(data).max()))
    except Exception as e:  # noqa: BLE001
        out.append(f"ERROR {type(e).__name__}: {e}")


def measure(label: str, mic, seconds: float) -> float | None:
    res: list = []
    th = threading.Thread(target=peak_of, args=(mic, seconds, res), daemon=True)
    th.start()
    th.join(timeout=seconds + 2)
    nums = [x for x in res if isinstance(x, float)]
    errs = [x for x in res if isinstance(x, str)]
    if errs and not nums:
        print(f"   {label:<28} 打不开：{errs[0][:60]}")
        return None
    if not nums:
        print(f"   {label:<28} 没抓到数据")
        return None
    peak = max(nums)
    print(f"   {label:<28} peak={peak:.4f}  块数={len(nums)}")
    return peak


def make_tone(path: Path, freq: float = 440.0, seconds: float = 2.0) -> None:
    import wave

    n = int(SR * seconds)
    t = np.arange(n) / SR
    sig = 0.25 * np.sin(2 * np.pi * freq * t)
    fade = int(SR * 0.04)
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    pcm = (sig * env * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())


def main() -> int:
    list_only = "--list-only" in sys.argv
    print("=" * 70)
    print("音频采集源诊断" + ("（仅列出设备）" if list_only else ""))
    print("=" * 70)

    print("\n【设备清单】")
    try:
        spk = sc.default_speaker()
        print(f"  默认输出设备      : {spk.name if spk else '无'}")
    except Exception as e:  # noqa: BLE001
        print(f"  默认输出设备      : 读取失败 {e}")
        spk = None

    print("  全部 loopback（= 能抓播放声音的）：")
    loops = []
    try:
        for m in sc.all_microphones(include_loopback=True):
            if m.isloopback:
                loops.append(m)
                print(f"    · {m.name!r}")
    except Exception as e:  # noqa: BLE001
        print(f"    枚举失败 {e}")

    print("  真实麦克风（不该被用到）：")
    try:
        for m in sc.all_microphones(include_loopback=False):
            print(f"    · {m.name!r}")
    except Exception as e:  # noqa: BLE001
        print(f"    枚举失败 {e}")

    # ---- 引擎实际会选哪个
    print("\n【引擎实际选中的设备】")
    eng = AudioVizEngine()
    try:
        mic = eng._resolve_mic()
        print(f"  {mic.name!r}")
        print(f"  isloopback = {mic.isloopback}"
              f"{'  ✅ 是回环，只抓播放声音' if mic.isloopback else '  ❌ 这是麦克风！'}")
    except Exception as e:  # noqa: BLE001
        print(f"  选不出来：{e}")
        return 2

    try:
        real_mic = sc.default_microphone()
    except Exception:  # noqa: BLE001
        real_mic = None
    print(f"  对照用真实麦克风  : {real_mic.name if real_mic else '无'}")

    if list_only:
        print("\n（--list-only，未做电平测量）")
        return 0

    # ---- 两段测量
    print("\n【测量 0】自动播放 2 秒 440Hz —— 验证 loopback 确实能抓到播放声音")
    import tempfile
    import winsound

    tone = Path(tempfile.gettempdir()) / "_xilian_diag_tone.wav"
    make_tone(tone)
    th = threading.Thread(
        target=winsound.PlaySound,
        args=(str(tone), winsound.SND_FILENAME),   # 同步播放，放完自己停
        daemon=True,
    )
    th.start()
    time.sleep(0.15)
    tone_loop = measure("loopback（播放中）", mic, 1.5)
    th.join(timeout=3)

    print("\n【测量 1】安静 3 秒 —— 请不要说话、不要放音乐")
    print("   (测量中…)")
    quiet_loop = measure("loopback", mic, 3.0)
    quiet_mic = measure("真实麦克风", real_mic, 3.0) if real_mic else None

    print("\n【测量 2】现在请对着麦克风说 4 秒话（随便念点什么）")
    for i in (4, 3, 2, 1):
        print(f"   {i}…", end="\r", flush=True)
        time.sleep(1.0)
    print("   开始说话 →                 ")
    talk_loop = measure("loopback", mic, 4.0)
    talk_mic = measure("真实麦克风", real_mic, 4.0) if real_mic else None

    # ---- 结论
    print("\n" + "=" * 70)

    # 先看最基础的一条：播放声音时 loopback 有没有反应
    if tone_loop is not None and tone_loop < 0.01:
        print(f"❌ 播放 440Hz 时 loopback 只有 {tone_loop:.4f} —— **抓不到播放声音**")
        print("   说明采集源和你实际听到声音的设备不是同一个。")
        print("   典型情况：声音从耳机 / 蓝牙耳机出来，但抓的是「扬声器 (Realtek)」的回环。")
        print("   处理：在「律动」页把采集源切换到正在出声的那个设备的回环接口，")
        print("         或把 Windows 默认播放设备改成你正在用的那个。")
        try:
            tone.unlink()
        except OSError:
            pass
        return 1
    if tone_loop is not None:
        print(f"✅ 播放 440Hz 时 loopback 有 {tone_loop:.4f} —— 抓播放声音这条路是通的")
    if talk_mic is not None and talk_mic < 0.005:
        print("⚠ 真实麦克风几乎没收到声音 —— 这次测量无效，可能没说话或麦克风被静音。")
        print("  请重跑一次，确认麦克风在工作。")
        return 1

    if quiet_loop is None or talk_loop is None:
        print("⚠ loopback 没测到数据，无法判断。")
        return 1

    ratio = (talk_loop / quiet_loop) if quiet_loop > 1e-6 else (
        float("inf") if talk_loop > 1e-3 else 1.0)
    print(f"loopback：安静 {quiet_loop:.4f} → 说话 {talk_loop:.4f}"
          f"（{ratio:.1f} 倍）")

    if talk_loop > 0.01 and (quiet_loop < 1e-4 or ratio > 5):
        print("\n结论：❌ **loopback 里混进了麦克风**")
        print("  loopback 本该只包含「正在播放」的声音，说话却让它大幅上升。")
        print("  最可能的原因：声卡驱动的麦克风侦听/侧音（Windows「侦听此设备」）开启了。")
        print("  排查：设置 → 系统 → 声音 → 输入 → 麦克风属性 → 取消「侦听此设备」")
        print("  也可能是 Realtek / Nahimic 等音效驱动的「麦克风回放」选项。")
        ok = False
    elif talk_loop > 0.002 and ratio > 3:
        print("\n结论：⚠ loopback 对说话有轻微响应，但幅度不大。")
        print("  可能是侧音泄漏，也可能只是说话声被扬声器放出来又被 loopback 抓到。")
        print("  建议：戴耳机再测一次。戴耳机后仍有响应 = 确实串了。")
        ok = True
    else:
        print("\n结论：✅ loopback 是干净的，只抓系统播放的声音，没串麦克风。")
        print("  如果键盘还是不跟音乐跳，问题在别处（音量太低 / 采集源选错 / 信号在别处）。")
        ok = True

    print("=" * 70)
    try:
        tone.unlink()
    except OSError:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
