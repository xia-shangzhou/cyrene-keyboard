"""
安全策略：命令白名单 + 写操作限流 + 危险命令硬拦截。

设计原则
--------
1. **默认拒绝**。不在白名单里的命令一律不发，未知命令直接抛异常。
2. **只读优先**。所有查询命令永不产生副作用。
3. **危险命令物理隔离**。固件升级、bootloader、改键/宏（固件无越界检查）、
   恢复出厂等命令写死在 FORBIDDEN 里，任何调用路径都无法绕过。
4. **写操作限流**。灯光类 SET 命令会触发固件写 flash（SettingsAck 0x0F），
   频繁写入会磨损 flash 并缩短寿命，因此设置最小发送间隔并做「值未变不发送」。
5. **流式命令单列**。音乐律动 `0x0D` 是每秒几十帧的瞬时数据，实测**不触发**
   `05 0F` flash 保存事件（24 帧连发 0 次，见 tools/probe_audioviz2.py），
   因此它不在 FLASH_WRITING 里、也不套 350ms 写限流，只留一个防打爆 USB
   管道的兜底间隔。这一条是实测结论，不是推断。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import protocol as P


class UnsafeCommandError(RuntimeError):
    """试图发送被策略禁止的命令。"""


# ---------------------------------------------------------------- 命令分类

# 绝对安全的只读命令
READ_ONLY: frozenset[int] = frozenset({
    P.Cmd.GET_REV,
    P.Cmd.GET_REPORT_RATE,
    P.Cmd.GET_PROFILE,
    P.Cmd.GET_LEDONOFF,
    P.Cmd.GET_DEBOUNCE,
    P.Cmd.GET_LEDPARAM,
    P.Cmd.GET_SLEDPARAM,
    P.Cmd.GET_KBOPTION,
    P.Cmd.GET_KEYMATRIX,
    P.Cmd.GET_MACRO,
    P.Cmd.GET_USERPIC,
    P.Cmd.GET_USB_VERSION,
    P.Cmd.GET_FN,
    P.Cmd.GET_SLEEPTIME,
    P.Cmd.GET_AUTOOS_EN,
    P.Cmd.GET_SKU,
    P.Cmd.GET_FEATURE_LIST,
    P.Cmd.GET_DONGLE_INFO,
    P.Cmd.GET_DONGLE_STATUS,
    P.Cmd.GET_RF_INFO,
    P.Cmd.GET_CACHED_RESPONSE,
    P.Cmd.GET_DONGLE_ID,
})

# 允许的写命令：全部是灯光/外观类，可逆、官方驱动同样会做
ALLOWED_WRITE: frozenset[int] = frozenset({
    P.Cmd.SET_LEDPARAM,    # 灯效模式/速度/亮度/方向/颜色
    P.Cmd.SET_SLEDPARAM,   # 侧面灯条
    P.Cmd.SET_PROFILE,     # 切换配置槽（只切槽位，不改内容）
    P.Cmd.SET_AUDIO_VIZ,   # 音乐律动 16 段电平（瞬时流式数据，见下方 STREAMING）
})

# 流式命令：高频推送，不能套用 SET 的 350ms 写限流，单独一个更短的间隔兜底
STREAMING: frozenset[int] = frozenset({
    P.Cmd.SET_AUDIO_VIZ,
})

# 永久禁止。理由写在注释里，别随手放开。
FORBIDDEN: dict[int, str] = {
    0x01: "SET_RESET 恢复出厂设置 —— 会清空全部自定义配置",
    0x0C: ("SET_USERPIC 逐键静态 RGB —— 帧布局未经验证（每帧塞几个键、起始索引怎么给、"
           "是否分块都未知），且固件读不回来（0x8C 全回显），盲写错位会出现「点 A 亮 B」。"
           "要启用必须先实测出帧格式，再从这里移出"),
    0x0A: "SET_KEYMATRIX 改键 —— 固件对 layer_id 无越界检查，可写坏 RAM",
    0x0B: "SET_MACRO 宏 —— 固件无越界检查，macro_id>=16 可覆盖栈返回地址",
    0x10: "SET_FN 改 Fn 层 —— 固件无越界检查，可写坏 RAM",
    0x12: "SET_USERGIF 逐键动画 —— 大量连续 flash 写入，磨损且不易验证",
    0x0E: "SET_SCREEN_COLOR 屏幕取色 —— 高频流式写入，先不做",
    0x7F: "ENTER_BOOTLOADER 进入 bootloader —— 刷坏即变砖",
    0x30: "SET_OLEDBOOTLOADER —— 变砖风险",
    0x31: "SET_OLEDBOOTSTART —— 变砖风险",
    0xF8: "ENTER_PAIRING 进入配对 —— 会断开当前连接",
    0x7A: "PAIRING_CMD 配对控制 —— 会断开当前连接",
    0xFE: "SET_RESPONSE_SIZE 覆盖 SPI 长度 —— 会打乱接收缓冲区",
    0x1B: "SET_MAGNETISM_REPORT 磁轴深度上报 —— 本键盘非磁轴，无意义",
    0x1C: "SET_MAGNETISM_CAL 磁轴校准 —— 本键盘非磁轴",
    0x1E: "SET_MAGNETISM_MAX_CAL 磁轴校准 —— 本键盘非磁轴",
    0x65: "SET_MULTI_MAGNETISM 磁轴批量写 —— 本键盘非磁轴",
}

# 会触发 flash 保存的命令（当前用于文档说明与人工核对；实际节流按 ALLOWED_WRITE 走）
FLASH_WRITING: frozenset[int] = frozenset({
    P.Cmd.SET_LEDPARAM,
    P.Cmd.SET_SLEDPARAM,
    P.Cmd.SET_PROFILE,
})


@dataclass
class SafetyPolicy:
    """发送前的统一闸门。所有写操作都必须过这一关。"""

    # 写命令最小间隔（秒）。灯光会写 flash，不能刷太快。
    write_interval: float = 0.35
    # 流式命令（0x0D 音乐律动）的最小间隔。这类命令不写 flash，
    # 只做「别把 USB 管道打死」的兜底保护，因此放得很宽（≈80Hz 上限）。
    stream_interval: float = 0.012
    # 读命令最小间隔，避免把无线键盘唤醒太频繁
    read_interval: float = 0.05
    # 是否启用（调试协议时也不能关掉 FORBIDDEN）
    enabled: bool = True
    # 严格只读：连白名单里的写命令也一律拒绝。探测真机时默认打开。
    strict_readonly: bool = False

    _last_sent: dict[int, float] = field(default_factory=dict)
    _last_values: dict[str, tuple] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=lambda: {
        "read": 0, "write": 0, "stream": 0, "blocked_forbidden": 0,
        "skipped_duplicate": 0, "skipped_throttle": 0,
    })

    # ------------------------------------------------------------ 校验

    def assert_allowed(self, cmd: int) -> None:
        """命令是否允许发送。违规直接抛异常，不做「降级放行」。"""
        if self.strict_readonly and cmd not in READ_ONLY:
            self.stats["blocked_forbidden"] += 1
            raise UnsafeCommandError(
                f"只读模式已开启，拒绝发送非查询命令 0x{cmd:02X}"
            )
        if cmd in FORBIDDEN:
            self.stats["blocked_forbidden"] += 1
            raise UnsafeCommandError(
                f"命令 0x{cmd:02X} 已被安全策略禁止：{FORBIDDEN[cmd]}"
            )
        if cmd not in READ_ONLY and cmd not in ALLOWED_WRITE:
            self.stats["blocked_forbidden"] += 1
            raise UnsafeCommandError(
                f"命令 0x{cmd:02X} 不在白名单内，拒绝发送（默认拒绝原则）"
            )

    def is_write(self, cmd: int) -> bool:
        return cmd in ALLOWED_WRITE

    # ------------------------------------------------------------ 限流

    def may_send(self, cmd: int, now: float | None = None) -> bool:
        """按命令类型做频率限制。返回 False 表示这次应跳过。"""
        if not self.enabled:
            return True
        now = time.monotonic() if now is None else now
        if cmd in STREAMING:
            interval = self.stream_interval
        elif self.is_write(cmd):
            interval = self.write_interval
        else:
            interval = self.read_interval
        last = self._last_sent.get(cmd)
        if last is not None and (now - last) < interval:
            self.stats["skipped_throttle"] += 1
            return False
        self._last_sent[cmd] = now
        return True

    def values_unchanged(self, key: str, values: tuple) -> bool:
        """值是否和上次**成功发出**的一样。**纯查询，不改任何状态。**

        ⚠ 这里绝对不能顺手把 values 记下来（2026-10-25 的 bug）：
        原先叫 `values_changed()`，检查的同时就记录了。配合后面的限流判断，
        会造成 —— 本次被限流没发出去，但值已经记成"已发送"，
        于是下一次同样的值被判成"没变"直接 return True，
        **这个值就永远发不出去了**（只有 forget() 能解锁）。
        症状是：拖滑块后界面显示新值、键盘还是旧的，再拖同一位置毫无反应。
        """
        return self._last_values.get(key) == values

    def remember_values(self, key: str, values: tuple) -> None:
        """**只有帧真的发出去了**才调用这里记录。"""
        self._last_values[key] = values

    def forget(self, key: str) -> None:
        self._last_values.pop(key, None)

    def note_read(self) -> None:
        self.stats["read"] += 1

    def note_duplicate(self) -> None:
        """值没变、主动跳过的那次 —— 单独计数，界面会显示「跳过重复值」。"""
        self.stats["skipped_duplicate"] += 1

    def note_write(self) -> None:
        """只在**会写 flash** 的命令上调用。

        音乐律动那种瞬时流式帧不算写 —— 混进来的话，「本次写入」会以 50/秒 的速度
        飙涨，看起来像在疯狂擦写 flash，其实一次都没写。那会让这个计数器彻底失效。
        """
        self.stats["write"] += 1

    def note_stream(self) -> None:
        """流式帧（0x0D / 0x0E）单独计数，不进 write。"""
        self.stats["stream"] += 1

    # ------------------------------------------------------------ 报告

    def summary(self) -> str:
        s = self.stats
        return (
            f"读 {s['read']} 次 / 写 {s['write']} 次 / 流式 {s['stream']} 帧 | "
            f"拦截危险命令 {s['blocked_forbidden']} 次 | "
            f"跳过重复值 {s['skipped_duplicate']} 次 | "
            f"限流跳过 {s['skipped_throttle']} 次"
        )
