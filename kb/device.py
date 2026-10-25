"""设备发现与 HID 收发（hidapi 封装）。

两种传输层，对上层保持同一套接口：
  · USB 有线 / 2.4G 接收器：Feature Report，Report ID 0
  · 蓝牙（BLE）           ：Output Report（Report ID 6）+ 0x55 前导，响应走 Input Report 通知
`Keyboard.send()` / `.query()` 内部按 `self.ble` 分支，`query()` 的返回值统一归一化成
USB 形状，所以 backend.py 一行都不用改。
"""

from __future__ import annotations

import time

import hid

from . import protocol as P
from .safety import FORBIDDEN, READ_ONLY, UnsafeCommandError

# 已知的昔涟键盘标识： (vid, pid, 说明, 连接类型)
KNOWN_IDS = [
    (0x38A9, 0x0017, "Air 108 metallic（昔涟键盘）", "usb"),
    (0x38A9, 0x0018, "Air 108 metallic（蓝牙）", "ble"),
    (0x3151, 0x5052, "2.4G Wireless Keyboard（接收器转发）", "2.4g"),
    (0x3151, 0x5038, "腹灵/同平台 2.4G 接收器", "2.4g"),
    (0x3151, 0x5037, "同平台 2.4G 接收器(其他)", "2.4g"),
]

LINK_NAMES = {"usb": "USB 有线", "2.4g": "2.4G 无线", "ble": "蓝牙"}

# 厂商自定义 usage page 的候选（USB 有线用 0xFFFF，蓝牙用 0xFF55）
VENDOR_PAGES = (0xFFFF, 0xFF55, 0xFF00, 0xFFEF)

# 配置口的判定：usagePage=0xFFFF + usage=0x0002。
# 同一设备上还有一个 usage=0x0001 的厂商接口，那个**不是**配置口 —— 拿它发命令会 read error。
CONFIG_USAGE = 0x0002

# ★ 配置口的「特征」（不依赖 VID/PID）。
# 发给别人时，人家键盘的 VID/PID 可能和我们的不一样（不同批次/不同固件/不同代工），
# 但这两组 usage 签名是同平台固件固定的 —— 用它们兜底就能自动认出来。
#   0xFFFF/0x0002 = USB 有线、2.4G 接收器（同平台通用）
#   0xFF55/0x0202 = 蓝牙 BLE（本机实测 38A9:0018）
CONFIG_SIGNATURES = ((0xFFFF, 0x0002), (0xFF55, 0x0202))

# ---------------------------------------------------------------- 蓝牙（BLE）传输层
#
# 2026-10-25 实测（38A9:0018，蓝牙配对后）：
#   · 厂商集合 = usage_page 0xFF55 / usage 0x0202（interface_number=-1，BLE 没有 itf 概念）
#   · 没有 USB 那样的 Feature Report，改成：
#       发 → Output Report（Report ID 6），载荷前加 0x55 标记
#       收 → Input Report 通知，形状 `06 55 <cmd> <参数…>`，参数区和 USB 逐字节相同
#   · 校验和沿用 USB 的位次（累加从 cmd 起算，跳过 0x55），数值不用重算
#   · 通知是**排队**送达的：上一条命令的响应会滞留，必须先排空再发
#     （不排空会读到上一条的结果 —— 表现为读回值永远慢一帧）
BLE_PAGE = 0xFF55
BLE_USAGE = 0x0202
BLE_REPORT_ID = 6
BLE_MARKER_CMD = 0x55
BLE_MARKER_EVT = 0x66

# ⚠ hidapi 在 Windows 上对 BLE 设备传 timeout_ms=0 会**永久阻塞**，必须 ≥1
BLE_MIN_TIMEOUT_MS = 1


def ble_payload(frame: bytes) -> bytes:
    """USB 帧 → BLE 载荷（65 字节）。

    USB: [0]=ReportID(0) [1]=cmd …  →  BLE: [0]=0x55 [1]=cmd …
    校验和原本累加 frame[0..7]，其中 frame[0] 恒为 0；这里把 Report ID 换成 0x55
    并从累加范围里排除，所以校验和位置和数值都不变。
    """
    return bytes([BLE_MARKER_CMD]) + bytes(frame[1:])


def ble_normalize(resp: bytes | list[int]) -> bytes:
    """BLE 响应 → 和 USB 同形状的 65 字节帧，让上层代码不用改。"""
    body = bytes(resp)
    if body and body[0] in (BLE_REPORT_ID, 0x06):
        body = body[1:]
    if body and body[0] in (BLE_MARKER_CMD, BLE_MARKER_EVT):
        body = body[1:]
    out = bytes([0]) + body          # 补一个假的 Report ID，命令码落回 index 1
    return out[:P.FRAME_LEN].ljust(P.FRAME_LEN, b"\x00")


def iface_rank(d: dict) -> int:
    """接口优先级：0=配置口，1=其它厂商接口，2=非厂商接口。"""
    if d.get("usage_page") in VENDOR_PAGES:
        if d.get("usage_page") == BLE_PAGE and d.get("usage") == BLE_USAGE:
            return 0             # 蓝牙的配置口
        return 0 if d.get("usage") == CONFIG_USAGE else 1
    return 2


def list_devices() -> list[dict]:
    """返回所有在线 HID 接口。"""
    return hid.enumerate()


def find_keyboard(vid: int = 0x3151, pid: int = 0x5052) -> list[dict]:
    """找出指定 VID/PID 的所有在线接口。"""
    return [d for d in list_devices() if d["vendor_id"] == vid and d["product_id"] == pid]


def is_vendor_iface(d: dict) -> bool:
    return d.get("usage_page", 0) in VENDOR_PAGES


def is_config_iface(d: dict) -> bool:
    """按 usage 签名判断是不是「可能的配置口」（不看 VID/PID）。"""
    return (d.get("usage_page", 0), d.get("usage", 0)) in CONFIG_SIGNATURES


def discover_config_ifaces() -> list[dict]:
    """扫描所有在线 HID，返回「像配置口」的接口，与已知列表无关。

    用途：别人机器上的键盘 VID/PID 可能和我们不同，靠这个兜底自动认出来。
    **只按 usage 签名筛**，不会去碰鼠标、网卡之类无关的厂商接口。
    """
    cands = [d for d in list_devices() if is_config_iface(d)]
    cands.sort(key=iface_rank)
    return cands


def describe_iface(d: dict) -> str:
    return (
        f"itf={d.get('interface_number')} "
        f"usagePage=0x{d.get('usage_page', 0):04X} "
        f"usage=0x{d.get('usage', 0):04X} "
        f"[{d.get('manufacturer_string') or ''}] {d.get('product_string') or ''}"
    )


class Keyboard:
    """单个厂商接口的读写句柄。"""

    def __init__(self, path: bytes, readonly: bool = True,
                 vid: int = 0, pid: int = 0, product: str = "",
                 link: str = "usb"):
        self.path = path
        self.readonly = readonly
        self.vid = vid
        self.pid = pid
        self.product = product
        self.link = link
        self.ble = (link == "ble")
        # True = VID/PID 不在已知列表里，靠 usage 签名自动发现的（别人机器上常见）
        self.auto_detected = False
        self.dev = hid.device()
        self.dev.open_path(path)

    @property
    def link_name(self) -> str:
        return LINK_NAMES.get(self.link, self.link)

    # ---- BLE 底层
    def _ble_drain(self, rounds: int = 32) -> int:
        """读空通知队列里积压的响应。

        不排空就会读到**上一条命令**的结果（实测：写完"常亮"读回"关闭"）。
        """
        n = 0
        for _ in range(rounds):
            try:
                r = self.dev.read(64, timeout_ms=BLE_MIN_TIMEOUT_MS)
            except Exception:  # noqa: BLE001
                break
            if not r:
                break
            n += 1
        return n

    def _ble_exchange(self, frame: bytes, wait: float) -> bytes | None:
        """发一帧 BLE 输出报告并等一条通知，已归一化成 USB 形状。"""
        self._ble_drain()
        self.dev.write(bytes([BLE_REPORT_ID]) + ble_payload(frame))
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                r = self.dev.read(64, timeout_ms=max(60, int(wait * 1000)))
            except Exception:  # noqa: BLE001
                return None
            if r:
                return ble_normalize(r)
        return None

    def _ble_wait(self) -> float:
        return 0.30

    @classmethod
    def open_any(cls, readonly: bool = True, discover: bool = True) -> "Keyboard":
        """找到键盘就开（默认只读）。

        两步走：
          1. **精确匹配** `KNOWN_IDS` 里的 VID/PID —— 自己这台走这条，最快最稳
          2. **兜底自动发现**：VID/PID 不认识时，按 usage 签名找「像配置口」的接口，
             逐个试一条只读查询，通了就用 —— 这样别人机器上型号/批次不同也能用
        """
        for vid, pid, _label, link in KNOWN_IDS:
            cands = [d for d in find_keyboard(vid, pid) if is_vendor_iface(d)]
            if cands:
                # 必须优先 usage=0x0002 的配置口：同设备还有个 usage=0x0001 的厂商接口，
                # 拿它发命令只会 read error（2.4G 下尤其明显，两个接口都在 0xFFFF 页）
                cands.sort(key=iface_rank)
                d = cands[0]
                return cls(d["path"], readonly=readonly, vid=vid, pid=pid,
                           product=d.get("product_string") or "", link=link)

        if discover:
            known = {(v, p) for v, p, _l, _k in KNOWN_IDS}
            for d in discover_config_ifaces():
                if (d["vendor_id"], d["product_id"]) in known:
                    continue          # 已知但没走到上面，说明接口形态不符，别重试
                link = "ble" if d.get("usage_page") == BLE_PAGE else "usb"
                kb = None
                try:
                    kb = cls(d["path"], readonly=readonly,
                             vid=d["vendor_id"], pid=d["product_id"],
                             product=d.get("product_string") or "", link=link)
                    kb.auto_detected = True
                    if kb.config_alive():
                        return kb
                except Exception:  # noqa: BLE001
                    pass
                if kb is not None:
                    kb.close()

        raise RuntimeError("没找到键盘的厂商接口")

    @classmethod
    def open_vendor(cls, vid: int = 0x38A9, pid: int = 0x0017,
                    readonly: bool = True) -> "Keyboard":
        cands = [d for d in find_keyboard(vid, pid) if is_vendor_iface(d)]
        if not cands:
            cands = find_keyboard(vid, pid)
        if not cands:
            raise RuntimeError(
                f"未找到 {vid:04X}:{pid:04X}。请确认键盘已用 USB 线连上并处于有线模式。"
            )
        cands.sort(key=iface_rank)
        d = cands[0]
        link = next((lk for v, p, _l, lk in KNOWN_IDS if (v, p) == (vid, pid)), "usb")
        return cls(d["path"], readonly=readonly, vid=vid, pid=pid,
                   product=d.get("product_string") or "", link=link)

    def config_alive(self, wait: float | None = None) -> bool:
        """探测这条通道是否真的转发配置命令。

        三重判据，缺一不可：
          1. 有响应帧
          2. **响应第 1 字节等于命令码** —— 2.4G 下 dongle 的缓存槽会把它存的上一条
             响应（比如电量 100）原样吐回来，不校验命令码就会被骗
          3. 不是全零

        用 GET_LEDPARAM 而不是 GET_USB_VERSION 探测：GET 命令的响应一定回显命令码，
        校验最可靠。
        """
        if wait is None:
            # 有线一轮约 1ms，没必要等；2.4G 要走 dongle 往返
            if self.ble:
                wait = self._ble_wait()
            else:
                wait = 0.06 if self.link == "usb" else 0.40
        req = P.build(P.Cmd.GET_LEDPARAM, None, bit8=True)
        # 探两次：偶发一次通信错误不应该让软件判定"这条线不能配置"
        for attempt in range(2):
            try:
                if self.ble:
                    resp = self._ble_exchange(req, wait)
                else:
                    self.dev.send_feature_report(req)
                    time.sleep(wait)
                    resp = self.dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
            except Exception:  # noqa: BLE001
                resp = None
            if resp and len(resp) >= 3 and resp[1] == P.Cmd.GET_LEDPARAM:
                if not all(b == 0 for b in resp[:8]):
                    return True
            if attempt == 0:
                time.sleep(0.08)
        return False

    def send(self, frame: bytes) -> None:
        """发送一帧。

        USB / 2.4G：Feature Report，hidapi 的 send_feature_report 首字节即 Report ID。
        BLE       ：Output Report（Report ID 6）+ 0x55 前导。

        **两道闸都在这一层，任何调用路径都绕不过去**（2026-10-25 补第二道）：
          1. FORBIDDEN 危险命令 —— 无条件拦，跟 readonly 无关
          2. 只读模式下拦掉所有非查询命令
        原先只有第 2 道，靠上层 `backend._send()` 里的 `assert_allowed()` 兜着；
        但日志写着「任何调用路径都无法绕过」，而直接调 `Keyboard.send()` 就绕过了。
        """
        assert len(frame) == P.FRAME_LEN, f"帧长应为 {P.FRAME_LEN}，实际 {len(frame)}"
        cmd = frame[1]
        if cmd in FORBIDDEN:
            raise UnsafeCommandError(
                f"命令 0x{cmd:02X} 已被安全策略禁止：{FORBIDDEN[cmd]}"
            )
        if self.readonly and cmd not in READ_ONLY:
            raise RuntimeError(
                f"只读模式：拒绝发送非查询命令 0x{cmd:02X}（帧未发出）"
            )
        if self.ble:
            # 写之前先排空，否则下一条查询会读到这条命令残留的通知
            self._ble_drain()
            self.dev.write(bytes([BLE_REPORT_ID]) + ble_payload(frame))
            return
        self.dev.send_feature_report(bytes(frame))

    def query(self, cmd: int, params=None, *, bit8: bool = False, retries: int = 3) -> bytes | None:
        """发一条 GET 命令并读回响应。

        返回的字节无论哪种连接都归一化成 USB 形状（[0]=0, [1]=cmd），上层不用分支。
        注意：Windows 下 get_feature_report 的长度参数是"含 Report ID"的总长。
        """
        frame = P.build(cmd, params, bit8=bit8)
        if self.ble:
            last = None
            for _ in range(retries):
                try:
                    resp = self._ble_exchange(frame, self._ble_wait())
                except Exception as e:  # noqa: BLE001
                    last = e
                    continue
                if resp:
                    return resp
            if last:
                raise last
            return None

        last_err = None
        for _ in range(retries):
            try:
                self.send(frame)
                resp = self.dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
                if resp:
                    return bytes(resp)
            except Exception as e:  # noqa: BLE001
                last_err = e
        if last_err:
            raise last_err
        return None

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "Keyboard":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
