"""
昔涟键盘（腹灵代工 / 容源 RY6609 平台）HID 协议层。

平台代号来源：官方升级工具的 support_config.json 里 `38A9:0017` 对应
`method: ["RY6609", ...]`。早期据腹灵网页驱动 JS 的 `yc3121_*` 推断为 YC3121，
那是通用驱动里包含的多平台代码，以 RY6609 为准。

传输：HID Feature Report，Report ID 0，64 字节载荷（Windows API 下帧长 65）
帧布局：frame[0]=ReportID(0), frame[1]=CMD, frame[2..]=参数, 校验和位置随命令而定
"""

from __future__ import annotations

REPORT_ID = 0
FRAME_LEN = 65  # 含 Report ID
PAYLOAD_LEN = 64


class Cmd:
    """命令码（来自容源 RY6609 平台协议规范）。"""

    # 写命令
    SET_RESET = 0x01
    SET_REPORT_RATE = 0x03
    SET_PROFILE = 0x04
    SET_DEBOUNCE = 0x06
    SET_LEDPARAM = 0x07
    SET_SLEDPARAM = 0x08
    SET_KBOPTION = 0x09
    SET_KEYMATRIX = 0x0A
    SET_MACRO = 0x0B
    SET_USERPIC = 0x0C
    SET_AUDIO_VIZ = 0x0D
    SET_SCREEN_COLOR = 0x0E
    SET_FN = 0x10
    SET_SLEEPTIME = 0x11
    SET_USERGIF = 0x12
    SET_AUTOOS_EN = 0x17

    # 读命令
    GET_REV = 0x80
    GET_REPORT_RATE = 0x83  # 亦作 GET_BATTERY（有线电量，语义待实测确认）
    GET_PROFILE = 0x84
    GET_LEDONOFF = 0x85
    GET_DEBOUNCE = 0x86
    GET_LEDPARAM = 0x87
    GET_SLEDPARAM = 0x88
    GET_KBOPTION = 0x89
    GET_KEYMATRIX = 0x8A
    GET_MACRO = 0x8B
    GET_USERPIC = 0x8C
    GET_USB_VERSION = 0x8F
    GET_FN = 0x90
    GET_SLEEPTIME = 0x91
    GET_AUTOOS_EN = 0x97
    GET_SKU = 0xD0
    GET_FEATURE_LIST = 0xE6

    # 2.4G 接收器专用（由 dongle 本地应答，不转发）
    GET_DONGLE_INFO = 0xF0
    GET_DONGLE_STATUS = 0xF7
    GET_RF_INFO = 0xFB
    GET_CACHED_RESPONSE = 0xFC
    GET_DONGLE_ID = 0xFD


# 灯效模式（对应 SET_LEDPARAM 的 mode 字段）
LED_MODES = {
    0: "关闭",
    1: "常亮",
    2: "呼吸",
    3: "霓虹",
    4: "波浪",
    5: "涟漪",
    6: "雨滴",
    7: "贪吃蛇",
    8: "响应",
    9: "汇聚",
    10: "正弦波",
    11: "万花筒",
    12: "线浪",
    13: "自定义逐键(User Picture)",
    14: "激光",
    15: "圆波",
    16: "彩虹",
    17: "雨落",
    18: "流星",
    19: "响应消散",
    20: "音乐节奏",
    21: "屏幕同步",
    22: "音乐律动条",
    23: "列车",
    24: "烟花",
    25: "逐键动画(GIF)",
}

# 方向（Options 字节高 nibble）
DIRECTIONS = {0: "正向", 1: "反向", 2: "左", 3: "右", 4: "上", 5: "下"}


def checksum_bit7(frame: bytes | bytearray) -> int:
    """Bit7 模式：累加 frame[0..6]，取反放 frame[7]。大多数 SET/GET 用这个。"""
    return 255 - (sum(frame[0:7]) & 0xFF)


def checksum_bit8(frame: bytes | bytearray) -> int:
    """Bit8 模式：累加 frame[0..7]，取反放 frame[8]。

    实测结论（本机固件）：校验和位置在 frame[8]，与 MonsGeek 那份文档写的
    frame[9] 不同 —— 用 frame[9] 时键盘会直接回显、不给数据。
    """
    return 255 - (sum(frame[0:8]) & 0xFF)


def build(cmd: int, params: list[int] | bytes | None = None, *, bit8: bool = False) -> bytes:
    """构造 65 字节 Feature Report 帧。

    Args:
        cmd: 命令码
        params: 紧跟命令码的参数（不含校验和）
        bit8: True 用 Bit8 校验（LED 命令），否则 Bit7
    """
    frame = bytearray(FRAME_LEN)
    frame[0] = REPORT_ID
    frame[1] = cmd
    if params:
        n = min(len(params), PAYLOAD_LEN - 1)
        frame[2: 2 + n] = bytes(params[:n])

    if bit8:
        frame[8] = checksum_bit8(frame)
    else:
        frame[7] = checksum_bit7(frame)
    return bytes(frame)


def build_get(cmd: int, params: list[int] | bytes | None = None, *, bit8: bool = False) -> bytes:
    """构造查询帧（GET 命令）。"""
    return build(cmd, params, bit8=bit8)


def build_led_cmd(cmd: int, params: list[int] | bytes) -> bytes:
    """SET_LEDPARAM / SET_SLEDPARAM 专用帧。

    实测结论（本机固件）：
      - 7 个参数 mode/speed/brightness/options/R/G/B 占 frame[2..8]
      - 校验和放 frame[9] = 255 - sum(frame[1:9])
    注意 GET 查询的校验和在 frame[8]，两者位置不同 —— 混用会导致键盘只回显不响应。
    """
    frame = bytearray(FRAME_LEN)
    frame[0] = REPORT_ID
    frame[1] = cmd & 0xFF
    n = min(len(params), 7)
    frame[2: 2 + n] = bytes(params[:n])
    frame[9] = 255 - (sum(frame[1:9]) & 0xFF)
    return bytes(frame)


def build_ledparam(
    mode: int,
    speed: int = 2,
    brightness: int = 4,
    options: int = 0x70,
    rgb: tuple[int, int, int] = (255, 0, 0),
) -> bytes:
    """构造 SET_LEDPARAM (0x07) 帧。

    speed/brightness 取值 0-4；options 高 nibble 是方向，低 nibble 0x7=单色、0x8=彩虹。
    """
    params = [mode & 0xFF, speed & 0xFF, brightness & 0xFF, options & 0xFF, *rgb]
    return build_led_cmd(Cmd.SET_LEDPARAM, params)


# ---------------------------------------------------------------- 音乐律动

AUDIO_BANDS = 16      # 协议固定 16 段
AUDIO_MAX = 6         # USB 帧里每段取值 0-6，超出固件忽略
AUDIO_BLE_MAX = 5     # 蓝牙半字节打包每段取值 0-5（文档口径），超出截断
AUDIO_MODES = (20, 22)  # 20=音乐节奏, 22=音乐律动条，同一渲染器、只看样式


def build_audio_viz_ble(bands: list[int] | bytes | tuple[int, ...],
                        high_first: bool = False) -> bytes:
    """SET_AUDIO_VIZ (0x0D) 帧 —— **蓝牙线格式**，和 USB 完全不同。

    同平台文档（MonsGeek PROTOCOL.md §0x0D）明确：蓝牙/接收器不走 USB 那套
    「padding + 校验和 + 16 字节频段」，而是：

      - 频段**半字节打包**：一个字节装两段，每段 0-5
      - 数据**紧跟命令字节**：没有 padding、也没有校验和
        （BLE 载荷本来就没有校验和的位置，硬塞会覆盖掉一段频段）
      - 拿 USB 格式往蓝牙上发的实测症状：柱子「镜像且变小」——
        频段数据落错位置，固件把 padding/校验和当成了频段

    返回的仍是 FRAME_LEN 标准帧（frame[0]=0、frame[1]=0x0D、frame[2..9]=8 个
    半字节包），走 `Keyboard.send()` 时由 `ble_payload()` 换成 0x55 前导，
    固件看到的就是 `0x55 + 0x0D + 频段半字节`。

    high_first：半字节顺序。2026-10-26 蓝牙实测结论：**两种发法视觉效果完全一致**
    （用「左小右大」阶梯图案各发一组，键盘上都是整排从左到右，看不出镜像差别），
    说明固件不按 nibble 顺序区分，取哪边都不影响显示 —— 默认 False 保持不动。
    真正修掉「只有右半亮 / 横向全黑」的是本函数这套线格式本身（不再塞 padding
    和校验和），而不是这个开关。
    """

    def q(v) -> int:
        v = 0 if v < 0 else (AUDIO_BLE_MAX if v > AUDIO_BLE_MAX else int(v))
        return v & 0x0F

    # 容错：传 8 个频段（UI 的 levels()）就按"相邻两段同值"展开成 16 段，
    # 和音频引擎 snapshot() 的 16 段语义一致
    if len(bands) == AUDIO_BANDS // 2:
        bands = [v for v in bands for _ in range(2)]

    frame = bytearray(FRAME_LEN)
    frame[1] = Cmd.SET_AUDIO_VIZ
    for j in range(AUDIO_BANDS // 2):
        first, second = q(bands[2 * j]), q(bands[2 * j + 1])
        frame[2 + j] = ((first << 4) | second) if high_first else ((second << 4) | first)
    return bytes(frame)


def build_audio_viz(bands: list[int] | bytes | tuple[int, ...]) -> bytes:
    """SET_AUDIO_VIZ (0x0D) 帧 —— USB 有线格式。

    实测确认（2026-10-24，本机 38A9:0017）：
      - 16 个频段值放 frame[9..24]，每字节一段，取值 0-6
      - 校验和在 frame[8]，只累加 frame[0..7]（和 GET 命令同位置）
      - frame[2..7] 必须为 0 padding

    ⚠ 与 LED 命令（校验和在 frame[9]、参数占 frame[2..8]）布局完全不同，
    照 LED 命令的规律构造会完全错位 —— 第一版就栽在这。

    另：24 帧实测未触发 `05 0F` flash 保存事件，属于瞬时数据流，可放心高频推。
    """
    frame = bytearray(FRAME_LEN)
    frame[0] = REPORT_ID
    frame[1] = Cmd.SET_AUDIO_VIZ
    for i in range(min(len(bands), AUDIO_BANDS)):
        v = bands[i]
        frame[9 + i] = 0 if v < 0 else (AUDIO_MAX if v > AUDIO_MAX else int(v))
    frame[8] = 255 - (sum(frame[0:8]) & 0xFF)
    return bytes(frame)


def parse_ledparam(resp: bytes) -> dict:
    """解析 GET_LEDPARAM 响应。索引按含 Report ID 的帧算。"""
    if len(resp) < 9:
        return {}
    mode = resp[2]
    return {
        "mode": mode,
        "mode_name": LED_MODES.get(mode, f"未知({mode})"),
        "speed_raw": resp[3],
        "brightness": resp[4],
        "options": f"0x{resp[5]:02X}",
        "direction": resp[5] >> 4,
        "colorful": (resp[5] & 0x0F) == 8,
        "rgb": (resp[6], resp[7], resp[8]),
    }


def describe(resp: bytes) -> str:
    """把响应帧转成一行可读文本。"""
    body = " ".join(f"{b:02X}" for b in resp[:24])
    return f"len={len(resp)} | {body}"
