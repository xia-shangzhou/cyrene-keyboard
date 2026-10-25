"""
键盘可视化：把灯效实时渲染在 108 键键帽上。

大厂驱动（Synapse / G HUB / iCUE）的视觉主体都是设备本体预览，
这里用 QPainter 直接画，不依赖任何图片资源，缩放不失真。
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from kb.backend import LedState
from ui import layout_data as L

# 外壳与键帽基础色
CASE_TOP = QColor("#F6E9EF")
CASE_BOTTOM = QColor("#E7D2DC")
CASE_EDGE = QColor("#D8BFCC")
CAP_BASE = QColor("#FFFFFF")
CAP_EDGE = QColor("#E8D5DE")
TEXT_DARK = QColor("#6B5962")

# 键帽分区配色，取自键盘实物：左粉、中白、右淡蓝紫
CAP_ZONE = {
    "func": QColor("#FFD9E5"),
    "func_r": QColor("#FDECF2"),
    "edit": QColor("#D8EBF8"),
    "arrow": QColor("#D8EBF8"),
    "numpad": QColor("#FFE7EF"),
    "media": QColor("#E7DFF9"),
}


def cap_base(k: "L.Key") -> QColor:
    """按物理位置给键帽上色，尽量贴近实物的粉-白-蓝渐变。"""
    if k.zone == "func":
        return CAP_ZONE["func"] if k.x < 6 else CAP_ZONE["func_r"]
    if k.zone in ("edit", "arrow"):
        return CAP_ZONE[k.zone]
    if k.zone == "numpad":
        return CAP_ZONE["numpad"]
    if k.zone == "media":
        return CAP_ZONE["media"]
    # 主键区：左侧功能列粉色，中间白色，右侧一道淡紫
    if k.x < 2.0:
        return QColor("#FFD4E1")
    if k.x > 13.4:
        return QColor("#F2E9FC")
    if k.y >= 4.0:
        return QColor("#FFF2F6")
    return CAP_BASE


def _clamp8(v: float) -> int:
    return max(0, min(255, int(v)))


def scale(c: QColor, k: float, floor: float = 0.0) -> QColor:
    """按系数缩放亮度，k<1 压暗，k>1 提亮（带下限，模拟灯珠余晖）。"""
    k = max(k, floor)
    return QColor(_clamp8(c.red() * k), _clamp8(c.green() * k), _clamp8(c.blue() * k))


def hsv_color(h: float, s: float, v: float) -> QColor:
    c = QColor()
    c.setHsvF(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return c


def effect_color(mode: int, k: L.Key, t: float, st: LedState) -> QColor:
    """给定灯效模式、键位、时间，算出该键此刻的颜色。"""
    # 彩虹循环：颜色不取固定值，而是按横向位置铺色相（真实键盘上涟漪/波浪会变成彩色波）
    if st.colorful and mode not in (0, 3, 11, 16):
        hue = ((k.x / L.TOTAL_W) * 0.85 + t * 0.06) % 1.0
        base = hsv_color(hue, 0.92, 1.0)
    else:
        base = QColor(*st.rgb)
    speed = 0.4 + st.speed * 0.35          # 0..4 -> 0.4..1.8
    phase = t * speed
    cx, cy = k.x + k.w / 2, k.y + k.h / 2
    nx = k.x / L.TOTAL_W
    ny = k.y / L.TOTAL_H
    d = math.hypot(cx - L.TOTAL_W / 2, cy - L.TOTAL_H / 2)

    if mode == 0:                                    # 关闭
        return scale(base, 0.06)

    if mode == 1:                                    # 常亮
        return base

    if mode == 2:                                    # 呼吸
        breath = 0.22 + 0.78 * (0.5 + 0.5 * math.sin(phase * 1.9))
        return scale(base, breath)

    if mode == 3:                                    # 霓虹
        h = (nx * 0.55 + phase * 0.06) % 1.0
        return hsv_color(h, 0.85, 0.55 + 0.45 * math.sin(phase * 2 + nx * 6) ** 2)

    if mode == 4:                                    # 波浪
        w = 0.5 + 0.5 * math.sin(nx * 9.0 - phase * 3.0)
        return scale(base.lighter(115), 0.18 + 0.82 * w)

    if mode == 5:                                    # 涟漪
        ring = (d * 1.6 - phase * 2.2) % 1.0
        w = 1.0 - ring
        return scale(base, 0.14 + 0.86 * (w ** 2.2))

    if mode == 6:                                    # 雨滴
        seed = (int(k.x * 7 + k.y * 13)) % 17
        drop = 0.5 + 0.5 * math.sin(phase * 2.4 + seed * 1.7)
        return scale(base, 0.15 + 0.85 * (drop ** 3))

    if mode == 7:                                    # 贪吃蛇
        pos = (phase * 0.55) % 1.0
        head = nx
        dist = abs(((head - pos + 0.5) % 1.0) - 0.5)
        w = max(0.0, 1.0 - dist * 7.0)
        return scale(base.lighter(120), 0.12 + 0.88 * w)

    if mode in (8, 19):                              # 响应
        seed = (int(k.x * 11 + k.y * 5)) % 13
        pulse = 0.5 + 0.5 * math.sin(phase * 3.1 + seed)
        return scale(base, 0.10 + 0.55 * pulse)

    if mode == 10:                                   # 正弦波
        w = 0.5 + 0.5 * math.sin(nx * 6.0 + ny * 2.0 - phase * 2.6)
        return scale(base.lighter(110), 0.16 + 0.84 * w)

    if mode == 11:                                   # 万花筒
        h = ((nx + ny) * 0.5 + phase * 0.08) % 1.0
        return hsv_color(h, 0.8, 0.85)

    if mode == 12:                                   # 线浪
        w = 0.5 + 0.5 * math.sin(ny * 7.0 - phase * 3.2)
        return scale(base, 0.15 + 0.85 * w)

    if mode == 14:                                   # 激光
        pos = (phase * 0.7) % 1.0
        dist = abs(((nx - pos + 0.5) % 1.0) - 0.5)
        w = max(0.0, 1.0 - dist * 12.0)
        return scale(base.lighter(130), 0.10 + 0.90 * w)

    if mode == 15:                                   # 圆波
        ring = (d * 2.2 + phase * 1.6) % 1.0
        return scale(base, 0.12 + 0.88 * (ring ** 2))

    if mode == 16:                                   # 彩虹
        h = (nx * 0.9 + phase * 0.10) % 1.0
        return hsv_color(h, 0.88, 0.95)

    if mode == 17:                                   # 雨落
        w = 0.5 + 0.5 * math.sin(ny * 8.0 + phase * 3.0)
        return scale(base, 0.14 + 0.86 * (w ** 2))

    if mode == 18:                                   # 流星
        pos = (phase * 0.5) % 1.6
        diag = (nx * 0.6 + ny * 0.4)
        dist = abs(diag - pos)
        w = max(0.0, 1.0 - dist * 5.0)
        return scale(base.lighter(125), 0.10 + 0.90 * w)

    if mode == 23:                                   # 列车
        pos = (phase * 0.45) % 1.0
        w = 0.5 + 0.5 * math.cos((nx - pos) * math.pi * 4)
        return scale(base, 0.12 + 0.88 * max(0.0, w))

    if mode == 24:                                   # 烟花
        seed = (int(phase * 0.9) * 37 + 11) % 97
        rnd = random.Random(seed)
        bx, by = rnd.random() * L.TOTAL_W, rnd.random() * L.TOTAL_H
        dist = math.hypot(cx - bx, cy - by)
        w = max(0.0, 1.0 - dist / 4.0)
        return scale(base.lighter(130), 0.10 + 0.90 * w)

    # 兜底：柔和渐变
    return scale(base, 0.35 + 0.5 * (0.5 + 0.5 * math.sin(phase + nx * 5)))


def music_color(k: L.Key, bands: list[float], style: int, bright: int) -> QColor:
    """音乐律动时单个键的颜色。

    固件只画 8 列、固定彩虹、纵向柱；横向两种样式实测在键盘上不亮，
    所以预览这边把三种样式**都实现**，让用户至少能在界面上看清律动长什么样。

    坐标口径：主键区 0~14.25u、6 行；功能/编辑/数字区不参与律动（固件也不点亮那里）。
    """
    MAIN_W = 14.25
    ROWS = 6
    cx = k.x + k.w / 2
    cy = k.y + k.h / 2

    # 不参与律动的区域：压到很暗，但仍保留键帽轮廓
    if cx > MAIN_W + 0.6:
        return QColor(46, 42, 50)

    lv_of = lambda i: bands[max(0, min(len(bands) - 1, i))]      # noqa: E731
    on = False
    hue = 0.0

    if style == 0:                                  # 竖向柱 · 自下而上
        col = min(7, int(cx / MAIN_W * 8))
        level = lv_of(col)
        rows_from_bottom = ROWS - (k.y + k.h)        # 0 = 最底行，5 = 最顶行
        on = (rows_from_bottom + 1) <= level * ROWS + 0.02
        hue = col / 8.0
    elif style == 1:                                # 横向 · 从中间向两边
        row = min(5, int(cy))
        level = lv_of(int(row * 8 / ROWS))
        dist = abs(cx - MAIN_W / 2) / (MAIN_W / 2)   # 0 = 中间，1 = 最外侧
        on = dist <= level + 0.02
        hue = dist * 0.5 + 0.5
    else:                                           # 横向 · 从左向右
        row = min(5, int(cy))
        level = lv_of(int(row * 8 / ROWS))
        on = (cx / MAIN_W) <= level + 0.02
        hue = cx / MAIN_W

    if not on:
        # 灭掉的键给一点点冷灰，别全黑 —— 全黑会让人以为界面卡住了
        return QColor(52, 47, 56)
    v = 0.62 + 0.10 * max(0, min(4, bright))
    return hsv_color(hue, 0.78, min(1.0, v))


class KeyboardView(QWidget):
    """108 键俯视图 + 实时灯效 + 底部侧灯条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = LedState()
        self.sled = LedState()
        self._t = 0.0
        self._animate = True
        self._hover: L.Key | None = None
        self.highlight = "main"        # main / sled：高亮哪一路通道
        self.unknown = False           # True = 读不到键盘状态，别画成"某种灯效"
        self.sled_unknown = False
        # 音乐律动：有数据时盖掉普通灯效渲染（None = 不在律动）
        self.bands: list[float] | None = None
        self.music_style = 0

        self.timer = QTimer(self)
        self.timer.setInterval(33)          # ~30fps，够顺滑也不费 CPU
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self.setMinimumHeight(180)
        self.setMouseTracking(True)

    # ---- 外部接口
    def set_music(self, bands: list[float] | None, style: int = 0) -> None:
        """律动运行中每帧调用；传 None 表示停止，回到普通灯效渲染。"""
        self.bands = list(bands) if bands is not None else None
        self.music_style = style
        if self.bands is not None:
            self.update()

    def clear_music(self) -> None:
        self.set_music(None)

    def load(self, st: LedState) -> None:
        self.state = LedState(**st.__dict__)
        self.unknown = False
        self.update()

    def load_unknown(self) -> None:
        """读不到状态时调用：键帽画成中性灰，不冒充任何灯效。"""
        self.unknown = True
        self.update()

    def load_sled(self, st: LedState) -> None:
        self.sled = LedState(**st.__dict__)
        self.sled_unknown = False
        self.update()

    def load_sled_unknown(self) -> None:
        self.sled_unknown = True
        self.update()

    def set_highlight(self, which: str) -> None:
        """调侧灯时把键帽压暗，让注意力落到下方灯条上。"""
        self.highlight = which
        self.update()

    def set_animated(self, on: bool) -> None:
        self._animate = on
        if on:
            self.timer.start()
        else:
            self.timer.stop()

    # ---- 内部
    def _needs_animation(self) -> bool:
        """只有"会动的灯效"才需要 30fps 重绘。

        关闭(0) / 常亮(1) 是静态的，重绘纯浪费——笔记本上用这两种模式的人不少。
        律动在跑时必须重绘，否则柱子会冻在某一帧。
        """
        if self.bands is not None:
            return True
        return self.state.mode not in (0, 1) or self.sled.mode not in (0, 1)

    def _tick(self) -> None:
        if not self._animate:
            return
        # 窗口缩到托盘 / 最小化时完全不重绘（timer 空转一次几乎不占 CPU）
        if not self.isVisible() or self.window().isMinimized():
            return
        if not self._needs_animation():
            return
        self._t += 0.033
        self.update()

    def _metrics(self) -> tuple[float, float, float, float]:
        """返回 (scale, offset_x, offset_y, case_pad)。"""
        w, h = self.width(), self.height()
        pad_u = 0.55                    # 外壳四周留白（u）
        side_u = 0.85                   # 底部侧灯条占位（u）
        avail_w = L.TOTAL_W + pad_u * 2
        avail_h = L.TOTAL_H + pad_u * 2 + side_u
        s = min(w / avail_w, h / avail_h)
        ox = (w - avail_w * s) / 2 + pad_u * s
        oy = (h - avail_h * s) / 2 + pad_u * s
        return s, ox, oy, pad_u * s

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)

        s, ox, oy, pad = self._metrics()
        t = self._t

        # ---- 外壳
        case = QRectF(ox - pad, oy - pad, L.TOTAL_W * s + pad * 2,
                      L.TOTAL_H * s + pad * 2 + 0.85 * s)
        g = QLinearGradient(case.topLeft(), case.bottomLeft())
        g.setColorAt(0, CASE_TOP)
        g.setColorAt(1, CASE_BOTTOM)
        p.setPen(QPen(CASE_EDGE, max(1.0, s * 0.02)))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(case, s * 0.5, s * 0.5)

        # ---- 键帽
        font_pt = max(5.5, s * 0.20)
        font = QFont(self.font())
        font.setPointSizeF(font_pt)
        font.setBold(False)

        # 性能：下面这几样每帧只建一次，别放进 108 次的循环里
        #   ① 键帽描边笔（粗细/颜色对所有键都一样）
        #   ② 顶部高光渐变 —— 用 ObjectBoundingMode 让同一份渐变套到任意 rect 上，
        #      否则 108 个键就要建 108 个 QLinearGradient（这是最贵的一步）
        #   ③ 字号缓存：QFont 拷贝 + setPointSizeF 也不便宜，按字号复用
        cap_pen = QPen(CAP_EDGE, max(0.6, s * 0.010))
        hi = QLinearGradient(0.0, 0.0, 0.0, 1.0)
        hi.setCoordinateMode(QLinearGradient.ObjectBoundingMode)
        hi.setColorAt(0.0, QColor(255, 255, 255, 120))
        hi.setColorAt(0.42, QColor(255, 255, 255, 0))
        hi.setColorAt(1.0, QColor(122, 92, 108, 24))
        font_cache: dict[float, QFont] = {}

        def _font_at(pt: float) -> QFont:
            f = font_cache.get(pt)
            if f is None:
                f = QFont(font)
                f.setPointSizeF(pt)
                font_cache[pt] = f
            return f

        for k in L.KEYS:
            x = ox + k.x * s + L.GAP * s / 2
            y = oy + k.y * s + L.GAP * s / 2
            w = k.w * s - L.GAP * s
            h = k.h * s - L.GAP * s
            rect = QRectF(x, y, w, h)
            rad = s * L.RADIUS

            col = (music_color(k, self.bands, self.music_style, self.state.brightness)
                   if self.bands is not None else None)
            if col is None:
                if self.unknown:
                    # 读不到键盘状态时不要画成"某种灯效"—— 那会把"未知"伪装成事实。
                    # 统一画成中性灰，配合界面上的告警文字表示"这里没数据"
                    col = QColor(196, 192, 198)
                else:
                    col = effect_color(self.state.mode, k, t, self.state)

            # 用最大通道而非加权亮度：否则纯蓝/纯紫这类灯的「亮度」会被算得很低，
            # 明明亮着却不给键帽染色，彩虹就只剩粉色段
            lum = max(col.red(), col.green(), col.blue()) / 255.0
            lum = min(1.0, lum * (0.45 + 0.16 * self.state.brightness))
            if self.highlight == "sled":
                lum *= 0.30

            # ① 键缝溢光：先在键位下方铺一层灯效色，键帽盖上后只有缝隙透出
            p.setPen(Qt.NoPen)
            glow = QColor(col)
            glow.setAlpha(_clamp8(225 * (0.10 + 0.90 * lum)))
            p.setBrush(glow)
            p.drawRoundedRect(
                rect.adjusted(-s * 0.05, -s * 0.05, s * 0.05, s * 0.05), rad * 1.3, rad * 1.3
            )

            # ② 键帽本体：按实物分区上色，亮起时叠加灯效色
            bcol = cap_base(k)
            # 暗处保持键帽本色（干净的白/粉），亮处才染上灯效色 —— 浅底上这样对比最清楚
            tint = 0.04 + 0.74 * (lum ** 1.0)
            body = QColor(
                _clamp8(bcol.red() * (1 - tint) + col.red() * tint),
                _clamp8(bcol.green() * (1 - tint) + col.green() * tint),
                _clamp8(bcol.blue() * (1 - tint) + col.blue() * tint),
            )
            p.setPen(cap_pen)
            p.setBrush(body)
            p.drawRoundedRect(rect, rad, rad)

            # ③ 键帽顶部曲面高光（复用同一份 ObjectBoundingMode 渐变）
            p.setPen(Qt.NoPen)
            p.setBrush(hi)
            p.drawRoundedRect(rect, rad, rad)

            # ④ 字符：接近白色并带灯效色调，保证可读
            if w > s * 0.40 and h > s * 0.40:
                if len(k.name) > 4 and w < s * 1.9:
                    f2 = _font_at(font_pt * 0.72)
                elif len(k.name) > 2 and w < s * 1.1:
                    f2 = _font_at(font_pt * 0.80)
                else:
                    f2 = font
                p.setFont(f2)
                p.setPen(TEXT_DARK)
                p.drawText(rect, Qt.AlignCenter, k.name)

        # ---- 底部侧灯条（独立通道）
        bar = QRectF(ox, oy + L.TOTAL_H * s + 0.16 * s,
                     L.TOTAL_W * s, 0.24 * s)
        sg = QLinearGradient(bar.topLeft(), bar.topRight())
        st = self.sled
        boost = 1.0 if self.highlight == "main" else 1.30
        for i in range(7):
            f = i / 6.0
            c = effect_color(st.mode, L.Key("", f * L.TOTAL_W, 7.0), t, st)
            if self.sled_unknown:
                c = QColor(196, 192, 198)     # 读不到就画中性灰，别冒充灯效
            c = scale(c, boost, 1.0)
            c.setAlpha(232)
            sg.setColorAt(f, c)
        p.setPen(Qt.NoPen)
        p.setBrush(sg)
        p.drawRoundedRect(bar, 0.12 * s, 0.12 * s)

        # 灯条辉光
        ag = QLinearGradient(bar.topLeft(), bar.topRight())
        for i in range(7):
            f = i / 6.0
            c = effect_color(st.mode, L.Key("", f * L.TOTAL_W, 7.0), t, st)
            if self.sled_unknown:
                c = QColor(196, 192, 198)
            c.setAlpha(60)
            ag.setColorAt(f, c)
        p.setBrush(ag)
        p.drawRoundedRect(bar.adjusted(0, -0.18 * s, 0, 0.10 * s), 0.2 * s, 0.2 * s)
