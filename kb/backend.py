"""
后端抽象：真实 HID 后端 + 模拟后端。

UI 只依赖 Backend 接口，所以键盘没插线时可以用 MockBackend 开发和看效果。
"""

from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from . import device as D
from . import protocol as P
from .safety import SafetyPolicy, UnsafeCommandError


class QueryThrottled(RuntimeError):
    """本地读频控把这次读取跳过了（没往设备发任何东西）。

    ⚠ 绝不能当成"设备读不到"—— 那会把正常的 USB/蓝牙误判成配置不可用。
    只有"真的发出去了但响应无效/没响应"才算读失败。
    """


# ---------------------------------------------------------------- 数据模型

@dataclass
class LedState:
    mode: int = 1
    speed: int = 2
    brightness: int = 4
    direction: int = 0
    colorful: bool = False
    rgb: tuple[int, int, int] = (255, 0, 0)

    @property
    def mode_name(self) -> str:
        return P.LED_MODES.get(self.mode, f"未知({self.mode})")


@dataclass
class BatteryInfo:
    percent: int | None = None
    charging: bool = False
    online: bool = True
    source: str = ""          # usb / dongle / ble / unknown
    note: str = ""

    @property
    def label(self) -> str:
        if self.percent is None:
            return "电量未知"
        suffix = " 充电中" if self.charging else ""
        return f"{self.percent}%{suffix}"


@dataclass
class PerKey:
    """逐键 RGB 颜色表：键索引 -> (r, g, b)。"""
    colors: dict[int, tuple[int, int, int]] = field(default_factory=dict)


# ---------------------------------------------------------------- 抽象接口

class Backend(ABC):
    """键盘控制后端。"""

    name: str = "backend"

    def __init__(self) -> None:
        self.policy = SafetyPolicy()
        self._connected = False

    # ---- 生命周期
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @property
    def connected(self) -> bool:
        return self._connected

    # ---- 读
    @abstractmethod
    def get_led(self) -> LedState | None: ...

    @abstractmethod
    def get_sled(self) -> LedState | None: ...

    @abstractmethod
    def get_battery(self) -> BatteryInfo: ...

    # ---- 写（全部经过安全策略）
    @abstractmethod
    def set_led(self, state: LedState) -> bool: ...

    @abstractmethod
    def set_sled(self, state: LedState) -> bool: ...

    @abstractmethod
    def set_perkey(self, perkey: PerKey) -> bool: ...

    @abstractmethod
    def set_audio_viz(self, bands: list[int]) -> bool: ...


# ---------------------------------------------------------------- 真实后端

class HidBackend(Backend):
    """通过 HID Feature Report 与键盘通信。"""

    name = "hid"

    def __init__(self, vid: int | None = None, pid: int | None = None, readonly: bool = True):
        super().__init__()
        self.vid, self.pid = vid, pid
        self.readonly = readonly
        self.kb: D.Keyboard | None = None
        self._last_battery = 0.0
        # 这条通道是否真的转发配置命令。2.4G 实测不转发（GET 全零或回缓存残留），
        # 这时绝不能让界面把"读不到"显示成"模式=关闭"。
        self.config_ok = True
        # 连续失败计数：**不能一次失败就永久禁用** —— 有线时偶发一次通信错误，
        # 也会让软件以为这条线不能配置，只能重启才能恢复，那太脆了。
        self._config_fail = 0
        # 只读模式下策略层同步收紧，写命令连策略都过不去
        self.policy.strict_readonly = readonly

    def connect(self) -> None:
        if self.vid is not None and self.pid is not None:
            self.kb = D.Keyboard.open_vendor(self.vid, self.pid, readonly=self.readonly)
        else:
            self.kb = D.Keyboard.open_any(readonly=self.readonly)
        self._connected = True
        # 连上后把缓存值清空，确保第一次设置一定下发
        for k in ("led", "sled"):
            self.policy.forget(k)
        self.config_ok = self.kb.config_alive()

    @property
    def device_label(self) -> str:
        if not self.kb:
            return ""
        base = (f"0x{self.kb.vid:04X}:0x{self.kb.pid:04X} · "
                f"{self.kb.link_name}")
        if getattr(self.kb, "auto_detected", False):
            # 别人的键盘 VID/PID 可能不同，靠 usage 签名认出来的
            base += " · 自动识别"
        if not self.config_ok:
            return base + " · 该通道不转发配置（只读不可用）"
        return base

    def disconnect(self) -> None:
        if self.kb:
            self.kb.close()
        self.kb = None
        self._connected = False

    @staticmethod
    def _is_echo(req: bytes, resp: bytes | None) -> bool:
        """响应 == 请求 说明固件不支持该命令、把请求原样回显，绝不可当数据用。"""
        return resp is not None and bytes(resp) == bytes(req)

    # ---- 底层收发（唯一出口，统一过安全策略）
    def _query(self, cmd: int, params=None, *, bit8: bool = False) -> bytes | None:
        self.policy.assert_allowed(cmd)
        if not self.kb:
            raise RuntimeError("未连接")
        if not self.policy.may_send(cmd):
            # 本地频控跳过 —— **这不是设备故障**，必须和"设备没响应"区分开。
            # 以前这里返回 None，调用方把它当成读失败记账，连续 3 次就会把
            # config_ok 置 False，好端端的 USB/蓝牙被误判成"配置不可用"。
            raise QueryThrottled()
        self.policy.note_read()
        return self.kb.query(cmd, params, bit8=bit8)

    def _send(self, cmd: int, params=None, *, bit8: bool = False, key: str = "",
              values: tuple | None = None) -> bool:
        """发一条写命令。返回 False = **这次没发出去**（被限流），调用方必须处理。

        ⚠ 顺序不能换（2026-10-25 修）：先查「值有没有变」（纯查询），
        再查「能不能发」（限流），**两者都过了才真正发送、最后才记录值**。
        反过来先记录的话，被限流那一次会把值记成"已发送"，
        下次同样的值直接被判成"没变"，于是这个值永远发不出去。
        """
        self.policy.assert_allowed(cmd)
        if not self.kb:
            raise RuntimeError("未连接")
        # 值没变就不发，省一次 flash 擦写（注意：只查不改）
        if key and values is not None and self.policy.values_unchanged(key, values):
            self.policy.note_duplicate()
            return True
        if not self.policy.may_send(cmd):
            return False          # 没发出去，也绝不记录值
        if cmd in (P.Cmd.SET_LEDPARAM, P.Cmd.SET_SLEDPARAM):
            frame = P.build_led_cmd(cmd, params or [])
        else:
            frame = P.build(cmd, params, bit8=bit8)
        self.kb.send(frame)
        if key and values is not None:
            self.policy.remember_values(key, values)   # 真发出去了才记
        self.policy.note_write()
        return True

    def _mark_read_fail(self) -> None:
        """读失败记账。连续 3 次才判定这条线不能配置 —— 偶发错误不该一票否决。"""
        self._config_fail += 1
        if self._config_fail >= 3:
            self.config_ok = False

    def _mark_read_ok(self) -> None:
        self._config_fail = 0

    def reconnect_config(self) -> bool:
        """重新探测配置通道。

        插上 USB 线 / 换连接方式之后可以**不用重启软件**，点一下就走这条路：
        重新找设备（VID/PID 会变）、重新探测、通了就恢复读写。
        """
        old = self.kb
        try:
            self.kb = D.Keyboard.open_any(readonly=self.readonly)
        except Exception:  # noqa: BLE001
            self.kb = old
            return False
        if old is not None and old is not self.kb:
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
        self.config_ok = self.kb.config_alive()
        self._config_fail = 0
        if self.config_ok:
            for k in ("led", "sled"):
                self.policy.forget(k)
        return self.config_ok

    # ---- 读
    def get_led(self) -> LedState | None:
        """读主灯状态。返回 None 表示这条通道读不到（不是"模式=关闭"）。"""
        if not self.config_ok:
            return None
        req = P.build(P.Cmd.GET_LEDPARAM, None, bit8=True)
        try:
            resp = self._query(P.Cmd.GET_LEDPARAM, bit8=True)
        except QueryThrottled:
            return None          # 本地频控跳过，不记账（见 QueryThrottled 注释）
        if not resp or len(resp) < 9 or self._is_echo(req, resp):
            self._mark_read_fail()
            return None
        if resp[1] != P.Cmd.GET_LEDPARAM:
            # 命令码不对 = 不是这条命令的响应。dongle 的缓存槽会把它存的上一条
            # 响应（比如电量 100）原样吐回来，不校验就会把它当灯效模式
            self._mark_read_fail()
            return None
        if all(b == 0 for b in resp):
            # 全零帧 = 没转发。不能当数据用，否则界面会显示"模式=关闭"，
            # 把"读不到"伪装成"灯关着"
            self._mark_read_fail()
            return None
        info = P.parse_ledparam(resp)
        if not info:
            self._mark_read_fail()
            return None
        self._mark_read_ok()
        return LedState(
            mode=info["mode"],
            speed=info["speed_raw"],
            brightness=info["brightness"],
            direction=info["direction"],
            colorful=info["colorful"],
            rgb=info["rgb"],
        )

    def get_sled(self) -> LedState | None:
        if not self.config_ok:
            return None
        req = P.build(P.Cmd.GET_SLEDPARAM, None, bit8=True)
        try:
            resp = self._query(P.Cmd.GET_SLEDPARAM, bit8=True)
        except QueryThrottled:
            return None          # 本地频控跳过，不记账
        if not resp or len(resp) < 9 or self._is_echo(req, resp):
            self._mark_read_fail()
            return None
        if resp[1] != P.Cmd.GET_SLEDPARAM or all(b == 0 for b in resp):
            self._mark_read_fail()
            return None
        info = P.parse_ledparam(resp)
        if not info:
            self._mark_read_fail()
            return None
        self._mark_read_ok()
        return LedState(
            mode=info["mode"], speed=info["speed_raw"], brightness=info["brightness"],
            direction=info["direction"], colorful=info["colorful"], rgb=info["rgb"],
        )

    def get_battery(self) -> BatteryInfo:
        """尝试读取电量。读不到就如实返回「未知」，绝不猜。"""
        now = time.monotonic()
        if now - self._last_battery < 1.0 and getattr(self, "_battery_cache", None):
            return self._battery_cache
        self._last_battery = now

        # 路径一：2.4G 接收器状态（byte2 = 电量，byte4 = 充电中）
        req = P.build(P.Cmd.GET_DONGLE_STATUS)
        try:
            resp = self._query(P.Cmd.GET_DONGLE_STATUS)
            if (resp and len(resp) >= 9 and not self._is_echo(req, resp)
                    and 0 < resp[2] <= 100):
                info = BatteryInfo(percent=resp[2], charging=bool(resp[4]),
                                   online=bool(resp[6]), source="dongle")
                self._battery_cache = info
                return info
        except Exception:  # noqa: BLE001
            pass

        # 路径二：有线 0x83（本机实测该固件对 0x83 只回显，不支持）
        req2 = P.build(P.Cmd.GET_REPORT_RATE)
        try:
            resp = self._query(P.Cmd.GET_REPORT_RATE)
            if (resp and len(resp) >= 3 and not self._is_echo(req2, resp)
                    and 0 < resp[2] <= 100):
                info = BatteryInfo(percent=resp[2], source="usb")
                self._battery_cache = info
                return info
        except Exception:  # noqa: BLE001
            pass

        # 蓝牙：实测厂商通道读不到电量（0x83 回全零、0xF7 无响应），
        # 电量走标准 BLE Battery Service(0x180F)，hidapi 拿不到。
        link = getattr(getattr(self, "kb", None), "link", "")
        if link == "ble":
            note = ("蓝牙下厂商命令读不到电量，请看键盘 Fn+Backspace "
                    "或 Windows 蓝牙设置里的电量")
        elif link == "2.4g":
            note = "接收器没回电量，可能是键盘刚唤醒，稍后刷新再看"
        else:
            note = "该固件在 USB 下不响应电量查询，请用 Fn+Backspace 在键盘上查看"
        info = BatteryInfo(source="unknown", note=note)
        self._battery_cache = info
        return info

    # ---- 写
    def set_led(self, st: LedState) -> bool:
        opts = (st.direction << 4) | (0x08 if st.colorful else 0x07)
        params = [st.mode, st.speed, st.brightness, opts, *st.rgb]
        return self._send(
            P.Cmd.SET_LEDPARAM, params, bit8=True,
            key="led", values=(st.mode, st.speed, st.brightness, opts, *st.rgb),
        )

    def set_sled(self, st: LedState) -> bool:
        opts = (st.direction << 4) | (0x08 if st.colorful else 0x07)
        params = [st.mode, st.speed, st.brightness, opts, *st.rgb]
        return self._send(
            P.Cmd.SET_SLEDPARAM, params, bit8=True,
            key="sled", values=(st.mode, st.speed, st.brightness, opts, *st.rgb),
        )

    def set_perkey(self, perkey: PerKey) -> bool:
        """逐键静态 RGB（SET_USERPIC 0x0C）—— **当前禁用，调用即报错**。

        帧布局没有实测过（每帧塞几个键、起始索引怎么给、是否分块都未知），
        而且固件读不回来（0x8C 全回显），盲写错位会出现「点 A 亮 B」。
        2026-10-25 把这把命令移进 FORBIDDEN：留着一个"能调但结果不可控"的写接口，
        和日志里「任何调用路径都无法绕过」的承诺不一致。

        要启用：先用真机实测出帧格式 → 从 safety.FORBIDDEN 移出 →
        在 ALLOWED_WRITE 里加回 → 再打开这里的实现。
        """
        raise UnsafeCommandError(
            "逐键 RGB 未实现：SET_USERPIC(0x0C) 的帧格式未经验证，"
            "且固件不支持读回（0x8C 全回显），已按安全策略禁用。"
            "请用键盘自带的 Fn+Z/X/C 进入 → Fn+V 录制 → Fn+V 保存。"
        )

    def set_audio_viz(self, bands: list[int]) -> bool:
        """音乐律动：推一帧 16 段电平（SET_AUDIO_VIZ 0x0D）。

        只发数据，不切模式 —— 调用方必须先用 set_led() 把模式设成 20/22，
        否则固件没有渲染器接手，数据白推。

        **USB 和蓝牙的线格式完全不同**（2026-10-25 实测踩坑）：
        - USB/2.4G：校验和 frame[8] + 16 个整字节频段 frame[9..24]
        - 蓝牙：频段**半字节打包**紧跟命令字节，没有 padding 和校验和；
          拿 USB 格式往蓝牙上发，症状是柱子「镜像且变小」——
          频段全落错位置，固件把 padding/校验和当成了频段
        """
        self.policy.assert_allowed(P.Cmd.SET_AUDIO_VIZ)
        if not self.kb:
            raise RuntimeError("未连接")
        if not self.policy.may_send(P.Cmd.SET_AUDIO_VIZ):
            return False
        if getattr(self.kb, "ble", False):
            self.kb.send(P.build_audio_viz_ble(bands))
        else:
            self.kb.send(P.build_audio_viz(bands))
        # 不记 write：0x0D 不写 flash，记进去会让「本次写入」每秒涨 50 次
        self.policy.note_stream()
        return True


# ---------------------------------------------------------------- 模拟后端

class MockBackend(Backend):
    """内存模拟，用来在没有硬件时把 UI 跑通。"""

    name = "mock"

    def __init__(self) -> None:
        super().__init__()
        self.led = LedState(mode=4, speed=2, brightness=4, colorful=True, rgb=(120, 180, 255))
        self.sled = LedState(mode=1, speed=2, brightness=3, rgb=(200, 120, 255))
        self.perkey = PerKey()
        self.last_bands: list[int] = [0] * P.AUDIO_BANDS
        self._t0 = time.monotonic()
        self._battery = 78

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def get_led(self) -> LedState:
        time.sleep(0.01)
        self.policy.note_read()
        return LedState(**self.led.__dict__)

    def get_sled(self) -> LedState:
        time.sleep(0.01)
        return LedState(**self.sled.__dict__)

    def get_battery(self) -> BatteryInfo:
        # 模拟缓慢放电
        elapsed = int(time.monotonic() - self._t0)
        pct = max(5, self._battery - elapsed // 120)
        return BatteryInfo(percent=pct, charging=False, source="mock",
                           note="模拟数据")

    def set_led(self, st: LedState) -> bool:
        # 和 HidBackend 同样的顺序：先查（不改状态）→ 再限流 → 通过后才落值
        vals = (st.mode, st.speed, st.brightness, st.direction, st.colorful, st.rgb)
        if self.policy.values_unchanged("led", vals):
            self.policy.note_duplicate()
            return True
        if not self.policy.may_send(P.Cmd.SET_LEDPARAM):
            return False
        self.led = LedState(**st.__dict__)
        self.policy.remember_values("led", vals)
        self.policy.note_write()
        return True

    def set_sled(self, st: LedState) -> bool:
        vals = (st.mode, st.speed, st.brightness, st.direction, st.colorful, st.rgb)
        if self.policy.values_unchanged("sled", vals):
            self.policy.note_duplicate()
            return True
        if not self.policy.may_send(P.Cmd.SET_SLEDPARAM):
            return False
        self.sled = LedState(**st.__dict__)
        self.policy.remember_values("sled", vals)
        self.policy.note_write()
        return True

    def set_perkey(self, perkey: PerKey) -> bool:
        """与真实后端保持一致：逐键 RGB 已禁用（帧格式未验证）。"""
        raise UnsafeCommandError("逐键 RGB 未实现：SET_USERPIC(0x0C) 帧格式未经验证，已禁用")

    def set_audio_viz(self, bands: list[int]) -> bool:
        """模拟模式下只存最后一批，用来在无硬件时跑通 UI。"""
        self.last_bands = list(bands)
        self.policy.note_stream()
        return True


def open_backend(prefer: str = "auto") -> Backend:
    """优先真实设备，找不到就退回模拟。"""
    if prefer in ("auto", "hid"):
        try:
            b = HidBackend()
            b.connect()
            return b
        except Exception:  # noqa: BLE001
            if prefer == "hid":
                raise
    b = MockBackend()
    b.connect()
    return b
