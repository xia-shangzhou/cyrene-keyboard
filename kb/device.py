"""设备发现与 HID 收发（hidapi 封装）。

两种传输层，对上层保持同一套接口：
  · USB 有线 / 2.4G 接收器：Feature Report，Report ID 0
  · 蓝牙（BLE）           ：Output Report（Report ID 6）+ 0x55 前导，响应走 Input Report 通知
`Keyboard.send()` / `.query()` 内部按 `self.ble` 分支，`query()` 的返回值统一归一化成
USB 形状，所以 backend.py 一行都不用改。

2.4G 接收器（`3151:5052`）另有一条额外的规矩：**它不会自动转发配置命令**，
必须先发 `0xF7` 把它「开闸」，等约 120ms 才开始转发。见 `_dongle_arm()`。
"""

from __future__ import annotations

import time

import hid

from . import protocol as P
from .safety import FORBIDDEN, READ_ONLY, STREAMING, UnsafeCommandError

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

# ---------------------------------------------------------------- 2.4G 接收器
#
# 2026-10-27 实测推翻旧结论「接收器不转发配置」。真实机制是：
#
#   接收器是个中间人，**不会主动转发**主机的 Feature Report。要先「开闸」：
#     ① 发 0xF7 (GET_DONGLE_STATUS)
#     ② 等 ~120ms，接收器才进入转发状态
#     ③ 再发目标命令，键盘的回复才会回来
#
#   读也有两个坑：
#     · 发完**立刻**读，读到的是接收器自己的状态帧
#       `00 00 64 00 00 01 01 01…`（0x64 = 电量 100%）—— 不是"空"，是"太早"
#     · **Feature 缓冲区永远只给状态帧**，键盘的真实回复只进缓存槽，
#       必须发 `0xFC` 把它「冲」进缓冲区才读得到（实测：直接读 0/10 命中，
#       等 10ms 再发 0xFC 则 8/8 命中、单次 17ms）
#     · **判据：真回复的第 2 字节 == 命令码**，状态帧则是 00 00 64… / 00 03 01…
#
#   不握手时，Feature/Output、两种校验位、两个厂商接口全试过，写入一律被丢弃。
#
# 官方驱动（容源 iot_manager）的 `ry_dongle.rs` 里那两句
# `F7 wait send timeout!` / `F7 wait read timeout!` 就是它在等这一步，
# 它界面上「请稍候…」转圈也是这么来的。
DONGLE_ARM_CMD = P.Cmd.GET_DONGLE_STATUS
DONGLE_ARM_WAIT = 0.12      # 开闸后等多久接收器才转发（实测 100ms 已够，留余量）
DONGLE_ARM_TTL = 1.5        # 开闸状态复用时长：这段时间内不再重复握手
DONGLE_REPLY_WAIT = 0.012   # 发完命令等射频往返，再去取缓存（实测 10ms 命中 8/8）
DONGLE_REPLY_TIMEOUT = 0.35  # 万一缓存没命中，兜底重试的额度
DONGLE_POLL = 0.004         # 重试间隔
# 流式推流（音乐律动）的开闸节奏。开闸窗口实测稳定 ≥1.6s
# （60/60 条查询连续成功），这里 1.2s 重开一次留足余量。
# 不能每帧都开闸：那会把每次推流都拖慢 120ms，10Hz 直接掉到 8Hz 还发抖。
DONGLE_STREAM_REARM = 1.2

# 这几条由接收器自己应答，回复**不**回显命令码，不能套用"回显即真回复"的判据
DONGLE_NATIVE_CMDS = frozenset({
    P.Cmd.GET_DONGLE_INFO,
    P.Cmd.GET_DONGLE_STATUS,
    P.Cmd.GET_RF_INFO,
    P.Cmd.GET_DONGLE_ID,
})

# 接收器状态帧的特征：第 2 字节是 0x00/0x03，且不是任何命令的回显。
# 判据统一用「真回复的第 2 字节 == 命令码」，所以没有单独的识别函数 —— 见
# `_dongle_cached()`。曾经写过一个 `is_dongle_status_frame()` 但没人调用，已删。


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


def infer_link(d: dict, vid: int, pid: int) -> str:
    """推断连接类型：先查已知表，再用 usage / product string 兜底。

    这个判断**决定要不要开闸**，判错了在 2.4G 下就是「能识别但读不到任何东西」：
      · 判成 usb → 不发 0xF7 → 命令被接收器静默丢弃（症状和"设备坏了"一样）
      · 判成 2.4g 但实际是有线 → 多发一条无害的 0xF7 查询，代价 120ms

    所以对不确定的情况宁可判 2.4g。别人的键盘 VID/PID 和 PRODUCT 都可能变，
    靠 `2.4G` / `dongle` / `receiver` 这几个词兜底比只看 PID 稳。
    """
    for v, p, _label, link in KNOWN_IDS:
        if (v, p) == (vid, pid):
            return link
    if d.get("usage_page") == BLE_PAGE:
        return "ble"
    prod = (d.get("product_string") or "").lower()
    if any(k in prod for k in ("2.4g", "2.4ghz", "dongle", "receiver")):
        return "2.4g"
    return "usb"


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
        self.dongle = (link == "2.4g")
        # 上次给接收器「开闸」的时间（monotonic 秒），0 = 还没开过
        self._armed_at = 0.0
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

    # ---- 2.4G 接收器底层
    def _dongle_read(self) -> bytes | None:
        """读一次 Feature Report，异常吞掉返回 None。"""
        try:
            return bytes(self.dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN))
        except Exception:  # noqa: BLE001
            return None

    def _dongle_arm(self, force: bool = False) -> None:
        """给接收器「开闸」：发 0xF7 并等它进入转发状态。

        不发这一步，所有写入都会被接收器静默丢掉（实测）。

        ⚠ **开闸是一次性的**（实测 2026-10-27）：写入之后闸就关了，所以普通写入
        一律 `force=True`。复用 TTL 会把写入静默吞掉 —— 症状是 `set_led()` 返回
        成功、读回还是旧值，非常难查。TTL 只对查询有意义（`_dongle_query`
        第一次尝试用它省一次 120ms），流式推流另走 `_dongle_arm_stream()`。

        这里直接用 `self.dev` 而不是 `self.send()`：0xF7 是纯查询命令
        （已列在 `READ_ONLY` 里），走底层是为了避免被只读模式的通用拦截误伤，
        它不含任何参数、也不可能改动键盘。
        """
        now = time.monotonic()
        if not force and (now - self._armed_at) < DONGLE_ARM_TTL:
            return
        try:
            self.dev.send_feature_report(P.build_get(DONGLE_ARM_CMD))
        except Exception:  # noqa: BLE001
            return
        time.sleep(DONGLE_ARM_WAIT)
        self._dongle_read()          # 把接收器自己的状态帧读掉，别留给后面污染
        self._armed_at = time.monotonic()

    def _dongle_arm_stream(self) -> None:
        """流式推流专用的开闸节奏（音乐律动那种高频连发）。

        和普通写入不同：推流帧与帧之间不重新开闸，靠开闸窗口（实测 ≥1.6s）
        连续往下推，只在快过期时补一次。否则每帧都要白等 120ms，
        10Hz 会掉到 8Hz 且节奏发抖。

        实测（2026-10-27）：按这个节奏 10Hz 推 17.7 秒 / 173 帧，
        键盘的阶梯、滚动、闪烁三段全部正常显示。
        """
        if (time.monotonic() - self._armed_at) < DONGLE_STREAM_REARM:
            return
        self._dongle_arm(force=True)

    def _dongle_cached(self, cmd: int, rounds: int = 4) -> bytes | None:
        """用 0xFC (GET_CACHED_RESPONSE) 把接收器缓存里的上一条回复取出来。

        ⚠ 这是 2.4G 读回复的**主路径**，不是兜底 —— 实测（2026-10-27）：
        接收器的 Feature 缓冲区只会给出自己的状态帧（`00 00 64…` 电量 100%），
        键盘的真实回复只进缓存槽，必须发 0xFC 把它「冲」进缓冲区才读得到。
        之前"等几毫秒直接读就能读到"的现象，其实是上一条 0xFC 的残留。
        """
        try:
            self.dev.send_feature_report(P.build_get(P.Cmd.GET_CACHED_RESPONSE))
        except Exception:  # noqa: BLE001
            return None
        for _ in range(rounds):
            resp = self._dongle_read()
            if resp is not None and len(resp) > 1 and resp[1] == cmd:
                return resp
            time.sleep(DONGLE_POLL)
        return None

    def _dongle_query(self, frame: bytes, cmd: int, retries: int = 3) -> bytes | None:
        """2.4G 下的查询：开闸 → 发帧 → 等射频往返 → 0xFC 取回复。

        注意**不能**靠"发完直接读 Feature"：那条路永远拿回接收器的状态帧。
        `DONGLE_REPLY_WAIT` 之后立即取缓存，实测 10ms 就能 8/8 命中，单次约 17ms。
        """
        last_err: Exception | None = None
        for attempt in range(retries):
            # 第 2 次起强制重新开闸（窗口可能已经关了）
            self._dongle_arm(force=(attempt > 0))
            try:
                self.dev.send_feature_report(bytes(frame))
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue

            # 接收器自己应答的命令：不回声命令码，读一次就够
            if cmd in DONGLE_NATIVE_CMDS:
                time.sleep(0.02)
                resp = self._dongle_read()
                if resp is not None and any(resp[:8]):
                    return resp
                continue

            time.sleep(DONGLE_REPLY_WAIT)
            cached = self._dongle_cached(cmd)
            if cached is not None:
                return cached

            # 某些情况下回复会直接落到 Feature 缓冲区，再试一次
            resp = self._dongle_read()
            if resp is not None and len(resp) > 1 and resp[1] == cmd:
                return resp

            # 还没到就等一会儿再取一次
            deadline = time.monotonic() + DONGLE_REPLY_TIMEOUT
            while time.monotonic() < deadline:
                time.sleep(DONGLE_REPLY_WAIT)
                cached = self._dongle_cached(cmd)
                if cached is not None:
                    return cached
        if last_err is not None:
            raise last_err
        return None

    @classmethod
    def open_any(cls, readonly: bool = True, discover: bool = True) -> "Keyboard":
        """找到键盘就开（默认只读）。

        两步走：
          1. **精确匹配** `KNOWN_IDS` 里的 VID/PID —— 自己这台走这条，最快最稳
          2. **兜底自动发现**：VID/PID 不认识时，按 usage 签名找「像配置口」的接口，
             逐个试一条只读查询，通了就用 —— 这样别人机器上型号/批次不同也能用
        """
        # 打开失败的设备不中断整条发现链：可能是拔插瞬态、被另一个实例占用、
        # 或正从有线切到蓝牙。要接着试同型号的备选接口，也要能落到下面的自动发现。
        last_err: Exception | None = None
        for vid, pid, _label, link in KNOWN_IDS:
            cands = [d for d in find_keyboard(vid, pid) if is_vendor_iface(d)]
            if not cands:
                continue
            # 必须优先 usage=0x0002 的配置口：同设备还有个 usage=0x0001 的厂商接口，
            # 拿它发命令只会 read error（2.4G 下尤其明显，两个接口都在 0xFFFF 页）
            cands.sort(key=iface_rank)
            for d in cands:
                try:
                    return cls(d["path"], readonly=readonly, vid=vid, pid=pid,
                               product=d.get("product_string") or "", link=link)
                except Exception as e:  # noqa: BLE001
                    last_err = e

        if discover:
            known = {(v, p) for v, p, _l, _k in KNOWN_IDS}
            for d in discover_config_ifaces():
                if (d["vendor_id"], d["product_id"]) in known:
                    continue          # 已知但没走到上面，说明接口形态不符，别重试
                link = infer_link(d, d["vendor_id"], d["product_id"])
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

        if last_err is not None:
            raise RuntimeError(
                f"找到键盘接口但打不开（可能被别的程序占用，或正在切换连接方式）：{last_err}"
            ) from last_err
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
        link = infer_link(d, vid, pid)
        return cls(d["path"], readonly=readonly, vid=vid, pid=pid,
                   product=d.get("product_string") or "", link=link)

    def config_alive(self, wait: float | None = None) -> bool:
        """探测这条通道是否真的转发配置命令。

        四重判据，缺一不可：
          1. 有响应帧
          2. **响应第 1 字节等于命令码** —— 2.4G 下 dongle 的缓存槽会把它存的上一条
             响应（比如电量 100）原样吐回来，不校验命令码就会被骗
          3. **不是把请求原样回显** —— 不支持该命令的通道会原样返回 req，
             只看命令码+非零会把它误判成可用
          4. 不是全零

        用 GET_LEDPARAM 而不是 GET_USB_VERSION 探测：GET 命令的响应一定回显命令码，
        校验最可靠。
        """
        if wait is None:
            # 有线一轮约 1ms，没必要等；2.4G 走接收器往返（内部自己轮询）
            wait = self._ble_wait() if self.ble else 0.06
        req = P.build(P.Cmd.GET_LEDPARAM, None, bit8=True)
        # 探两次：偶发一次通信错误不应该让软件判定"这条线不能配置"
        for attempt in range(2):
            try:
                if self.ble:
                    resp = self._ble_exchange(req, wait)
                elif getattr(self, "dongle", False):
                    # 2.4G：必须开闸 + 取缓存，否则读到的是接收器状态帧，
                    # 会被下面第 2 条判据判成"命令码不对"，误判成不能配置。
                    #
                    # 这里**强制**开闸，不复用 TTL：这是「重新检测」按钮走的路径，
                    # 用户主动点一次就该真正重新握手。而且刚写完灯时窗口一定是关的，
                    # 省这 120ms 只会换来一次误报。多 120ms 完全值得。
                    self._dongle_arm(force=True)
                    resp = self._dongle_query(req, P.Cmd.GET_LEDPARAM, retries=1)
                else:
                    self.dev.send_feature_report(req)
                    time.sleep(wait)
                    resp = self.dev.get_feature_report(P.REPORT_ID, P.FRAME_LEN)
            except Exception:  # noqa: BLE001
                resp = None
            # 判据四重：有响应 → 命令码对 → **不是把请求原样回显** → 不全零。
            # 回显这条不能省：不支持该命令的通道会把 req 原样丢回来，
            # 只看命令码和非零的话，这种"假通道"会被判成可用，界面短暂开放写入。
            # BLE 的 resp 已被 ble_normalize 归一化成和 req 同形状，所以可以直接比。
            if resp and len(resp) >= 3 and resp[1] == P.Cmd.GET_LEDPARAM:
                same_as_req = bytes(resp) == bytes(req)
                if not same_as_req and not all(b == 0 for b in resp[:8]):
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
        if getattr(self, "dongle", False):
            # 接收器不主动转发：先开闸，命令才到得了键盘（不开闸会被静默丢弃）。
            # 流式推流（律动）走窗口模式：帧与帧之间不重开闸，否则 10Hz 会掉到 8Hz。
            if cmd in STREAMING:
                self._dongle_arm_stream()
                self.dev.send_feature_report(bytes(frame))
                return
            # ⚠ 普通写入必须 force —— 实测「开闸」是一次性的，写完就关；
            #   复用 TTL 会让写入被丢掉（表现为"写成功但读回还是旧值"）。
            self._dongle_arm(force=True)
            self.dev.send_feature_report(bytes(frame))
            # ★ 写完窗口就关了（2026-10-28 实测确认），所以把"上次开闸时间"
            #   清零，让状态如实反映"现在没开闸"。不清零的后果：紧随其后的查询
            #   以为窗口还在，`_dongle_arm(force=False)` 跳过握手 → 裸发 → 读不到，
            #   表现为刚改完灯就点「重新检测」会误报「配置不可用」。
            #   （GLM 提的 P2，此处是根因修复，比给它加 retries 更准。）
            self._armed_at = 0.0
            return
        self.dev.send_feature_report(bytes(frame))

    def query(self, cmd: int, params=None, *, bit8: bool = False, retries: int = 3) -> bytes | None:
        """发一条 GET 命令并读回响应。

        返回的字节无论哪种连接都归一化成 USB 形状（[0]=0, [1]=cmd），上层不用分支。
        注意：Windows 下 get_feature_report 的长度参数是"含 Report ID"的总长。
        """
        # 只读模式也守一道：BLE / 2.4G 两条分支内部直接调 dev 发送，不经过 send()，
        # 不在这里挡的话「任何调用路径都无法绕过」这句话对 query() 就不成立。
        if self.readonly and cmd not in READ_ONLY:
            raise RuntimeError(
                f"只读模式：拒绝发送非查询命令 0x{cmd:02X}（帧未发出）"
            )
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

        if getattr(self, "dongle", False):
            # 2.4G：开闸 → 发帧 → 轮询等真回复（回显命令码的那一帧）
            return self._dongle_query(frame, cmd, retries=retries)

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
