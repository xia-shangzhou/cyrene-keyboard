"""
音频律动引擎：抓系统正在播放的声音 → FFT → 16 段电平。

为什么是「16 段显示成 8 列」
--------------------------
固件渲染器把**相邻两段求和**再 clamp 到 8，也就是说 16 个频段实际只渲染成
8 根柱子。所以这里按 8 个对数频带计算，每个频带的值同时写进相邻两段
（band[2i] = band[2i+1] = v，v 取 0-4，两段和 0-8 正好铺满一列）。
想成 16 根更细的柱子是做不到的 —— 那是固件的物理限制。

采集
----
WASAPI loopback（经 soundcard 库）：不需要「立体声混音」设备、不需要独占，
直接抓取指定输出设备正在播放的内容。

    engine = AudioVizEngine()
    engine.start()
    ...
    engine.snapshot()   # -> [int] * 16，直接喂给 build_audio_viz()
    engine.levels()     # -> [float] * 8，0-1，给界面画柱子
    engine.stop()

引擎只负责算，不负责发。推送节奏由调用方掌握（见 tools/audioviz_live.py）。
"""

from __future__ import annotations

import threading
import time
import warnings
from dataclasses import dataclass, field

import numpy as np

try:
    import soundcard as sc
    from soundcard import SoundcardRuntimeWarning
    # loopback 按固定块长读取时，块边界必然不连续，这个警告纯噪音
    warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)
    HAVE_SOUNDCARD = True
except Exception:  # noqa: BLE001
    sc = None  # type: ignore[assignment]
    HAVE_SOUNDCARD = False


@dataclass
class VizSettings:
    """可调参数。UI 上暴露「灵敏度」和「响应速度」两项就够，其余走默认。"""

    # 频率范围（Hz）。下限太高会丢鼓点，上限太高会全是噪声。
    freq_low: float = 40.0
    freq_high: float = 14000.0
    # 每带动态范围：把「当前峰值往下这么多 dB」映射成 0-1
    dynamic_range_db: float = 36.0
    # 峰值跟踪的衰减速度（dB/秒）。越大回落越快，柱子越"跳"。
    decay_db_per_s: float = 26.0
    # 噪声门：整体最大电平低于此值时直接输出全 0，避免安静时被噪声放大
    noise_floor_db: float = -52.0
    # 峰值跟踪的下限，防止长时间安静后把底噪当成信号
    peak_floor_db: float = -42.0
    # 上升/下降平滑系数（0-1）。attack 大=跟得快，release 小=落得慢
    attack: float = 0.6
    release: float = 0.14
    # 手动增益倍数（乘在幅度上）
    gain: float = 1.0
    # 自动增益：不同输出设备的回环电平能差 16 倍以上
    # （实测 Realtek 扬声器 0.25、vivo TWS 蓝牙耳机 0.015），
    # 固定增益要么蓝牙下没反应，要么扬声器下全程顶格。这里自动把强信号拉到目标电平。
    auto_gain: bool = True
    target_peak_db: float = -8.0


class AudioVizEngine:
    """后台线程持续分析系统音频，结果放在 `levels()` / `snapshot()` 里。"""

    SEGMENTS = 16   # 协议要求的段数
    COLUMNS = 8     # 实际渲染出的柱子数

    def __init__(
        self,
        device_name: str | None = None,
        rate: int = 48000,
        block: int = 1024,
        settings: VizSettings | None = None,
    ) -> None:
        self.device_name = device_name
        self.rate = rate
        self.block = block
        self.cfg = settings or VizSettings()

        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._levels = np.zeros(self.COLUMNS, dtype=np.float32)
        self._raw_db = np.full(self.COLUMNS, -90.0, dtype=np.float32)
        self._peak_db = np.full(self.COLUMNS, self.cfg.peak_floor_db, dtype=np.float32)
        self._gain_db = 0.0     # 自动增益当前值（dB），调试/排查时看这个
        self._last_t = 0.0

        self.error: str = ""
        self.device_label: str = ""

        self._window: np.ndarray | None = None
        self._norm = 1.0
        self._bin_idx: list[np.ndarray] = []
        self._setup_dsp()

    # ------------------------------------------------------------ DSP 预计算

    def _setup_dsp(self) -> None:
        self._window = np.hanning(self.block).astype(np.float32)
        # 归一化：单频正弦幅度 a 经过 Hann + rfft 后的峰值 ≈ a * sum(w) / 2
        self._norm = max(1e-9, float(np.sum(self._window)) / 2.0)

        freqs = np.fft.rfftfreq(self.block, 1.0 / self.rate)
        high = min(self.cfg.freq_high, self.rate * 0.45)
        edges = np.geomspace(self.cfg.freq_low, high, self.COLUMNS + 1)
        self._bin_idx = []
        for i in range(self.COLUMNS):
            lo, hi = edges[i], edges[i + 1]
            idx = np.where((freqs >= lo) & (freqs < hi))[0]
            if idx.size == 0:   # 频带比一个 bin 还窄，取最近的
                idx = np.array([int(np.argmin(np.abs(freqs - (lo + hi) / 2)))])
            self._bin_idx.append(idx)

    # ------------------------------------------------------------ 生命周期

    @staticmethod
    def available() -> bool:
        return HAVE_SOUNDCARD

    @staticmethod
    def list_outputs() -> list[str]:
        """只列出**真正有回环接口**的输出设备，默认设备排第一。

        为什么要过滤：蓝牙耳机之类有时没有 loopback 接口，列出来用户一选就会失败。
        只列能用的，比列全了再报错省事。
        """
        if not HAVE_SOUNDCARD:
            return []
        try:
            loops = {m.name for m in sc.all_microphones(include_loopback=True) if m.isloopback}
            default = ""
            try:
                spk = sc.default_speaker()
                default = spk.name if spk else ""
            except Exception:  # noqa: BLE001
                pass
            names: list[str] = []
            for spk in sc.all_speakers():
                if spk.name in loops and spk.name not in names:
                    names.append(spk.name)
            if default in names:
                names.remove(default)
                names.insert(0, default)
            return names
        except Exception:  # noqa: BLE001
            return []

    def _resolve_mic(self):
        """严格只取 loopback 设备（= 系统正在播放的声音）。

        为什么这么严：`get_microphone(include_loopback=True)` 是按名字/ID 匹配的，
        一旦匹配不上，有可能**退回一颗真实麦克风** —— 那样键盘就会跟着说话声跳，
        而不是跟着音乐跳，而且症状很迷惑（"看起来在动，但不跟节奏"）。
        这里宁可直接报错，也不接受任何非 loopback 的设备。
        """
        cands = []
        try:
            cands = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
        except Exception:  # noqa: BLE001
            cands = []

        want = self.device_name
        if not want:
            try:
                spk = sc.default_speaker()
                want = spk.name if spk else None
            except Exception:  # noqa: BLE001
                want = None

        if want:
            for m in cands:
                if m.name == want:
                    return m
            for m in cands:      # 名字不完全一致时退一步
                if want in m.name or m.name in want:
                    return m
            # 指定了设备却找不到它的回环接口 —— 绝不能随便挑一个顶上。
            # 挑错了的后果是「键盘跟着别的设备的声音跳」，症状极难排查。
            raise RuntimeError(
                f"「{want}」没有可用的回环接口。\n"
                f"可选：{'、'.join(m.name for m in cands) or '无'}\n"
                "如果声音是从蓝牙/耳机出来的，请把采集源切成那个设备的回环接口，"
                "或把 Windows 默认播放设备改成它。"
            )

        if cands:
            return cands[0]
        raise RuntimeError(
            "找不到 loopback 设备。请确认系统有可用的播放设备，"
            "且 soundcard 能枚举出它的回环接口"
        )

    def start(self) -> bool:
        if not HAVE_SOUNDCARD:
            self.error = "缺少 soundcard 库，无法采集系统音频"
            return False
        if self._running:
            return True
        self.error = ""
        self._running = True
        self._thread = threading.Thread(target=self._run, name="audioviz", daemon=True)
        self._thread.start()
        # 等第一帧，确认不是一开就失败
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.2:
            if self.error or self.device_label:
                break
            time.sleep(0.02)
        return not self.error

    def stop(self) -> None:
        self._running = False
        th = self._thread
        if th and th.is_alive():
            th.join(timeout=1.0)
        self._thread = None
        with self._lock:
            self._levels[:] = 0.0

    @property
    def running(self) -> bool:
        return self._running and not self.error

    # ------------------------------------------------------------ 采集循环

    def _run(self) -> None:
        try:
            mic = self._resolve_mic()
            # 标明是不是 loopback：出问题时一眼能看出抓错了设备
            self.device_label = (f"{mic.name}（从输出设备回环抓取）"
                                 if mic.isloopback else f"{mic.name}（⚠ 麦克风，非回环）")
            with mic.recorder(samplerate=self.rate, channels=2,
                              blocksize=self.block) as rec:
                self._last_t = time.monotonic()
                while self._running:
                    data = rec.record(numframes=self.block)
                    if self._running and data is not None:
                        self._process(data)
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self._running = False

    def _process(self, data: np.ndarray) -> None:
        arr = np.asarray(data, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr.mean(axis=1)
        if arr.size < self.block:
            arr = np.pad(arr, (0, self.block - arr.size))
        elif arr.size > self.block:
            arr = arr[-self.block:]

        spec = np.abs(np.fft.rfft(arr * self._window)) / self._norm
        amp = np.array([spec[idx].max() if idx.size else 0.0 for idx in self._bin_idx],
                       dtype=np.float32)
        db_raw = 20.0 * np.log10(amp + 1e-9)

        now = time.monotonic()
        dt = max(1e-3, now - self._last_t)
        self._last_t = now

        # 噪声门必须用**原始电平**判断。
        # 如果用加过自动增益的电平判断，安静时增益会把底噪一路抬上来，
        # 结果就是"没放音乐灯也在乱跳"—— 正是最容易误判成「它在听麦克风」的现象。
        if float(db_raw.max()) < self.cfg.noise_floor_db:
            with self._lock:
                self._levels = (self._levels * np.float32(0.55)).astype(np.float32)
                self._raw_db = db_raw
            return

        # 自动增益（缓慢跟随，负反馈稳定在 target_peak_db 附近）
        # 上限放到 +54dB：蓝牙耳机实测 loopback 只有 -47dB，固定 +40dB 会差一点打不满
        if self.cfg.auto_gain:
            err = self.cfg.target_peak_db - float(db_raw.max() + self._gain_db)
            step = max(-1.0, min(1.0, err * 0.12))
            self._gain_db = float(np.clip(self._gain_db + step, -6.0, 54.0))

        db = db_raw + (20.0 * np.log10(self.cfg.gain) + self._gain_db)

        # 峰值跟踪：跟得快、落得慢，但落不到 peak_floor 以下
        decay = self.cfg.decay_db_per_s * dt
        self._peak_db = np.maximum.reduce([
            db, self._peak_db - decay,
            np.full(self.COLUMNS, self.cfg.peak_floor_db, dtype=np.float32),
        ]).astype(np.float32)

        floor = self._peak_db - self.cfg.dynamic_range_db
        frac = np.clip((db - floor) / self.cfg.dynamic_range_db, 0.0, 1.0)

        with self._lock:
            prev = self._levels
            coef = np.where(frac > prev, self.cfg.attack, self.cfg.release)
            self._levels = (prev + (frac - prev) * coef).astype(np.float32)
            self._raw_db = db_raw

    # ------------------------------------------------------------ 结果

    def levels(self) -> list[float]:
        """8 个柱子的高度，0-1。"""
        with self._lock:
            return [float(x) for x in self._levels]

    def levels_db(self) -> list[float]:
        """8 个频带的原始 dB（未加自动增益），调试用。"""
        with self._lock:
            return [float(x) for x in self._raw_db]

    @property
    def gain_db(self) -> float:
        """自动增益当前值。蓝牙耳机这类低电平设备会明显高于 0。"""
        return round(self._gain_db, 1)

    def snapshot(self) -> list[int]:
        """16 段协议电平（0-4），相邻两段值相同 —— 固件会把它们求和成一列。"""
        out: list[int] = []
        for v in self.levels():
            q = int(round(max(0.0, min(1.0, v)) * 4))
            out.append(q)
            out.append(q)
        return out
