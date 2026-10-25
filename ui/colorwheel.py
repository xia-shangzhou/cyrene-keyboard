"""HSV 色轮取色器：外环选色相，内方块选饱和度和明度。"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QConicalGradient, QLinearGradient, QPainter, QPen,
)
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class ColorWheel(QWidget):
    colorChanged = Signal(QColor)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._h = 0.75      # 色相
        self._s = 0.7       # 饱和度
        self._v = 1.0       # 明度
        self._drag = ""     # "ring" / "square"
        self.setMinimumSize(160, 160)
        self.setCursor(Qt.CrossCursor)

    # ---- 取值 / 设值
    def color(self) -> QColor:
        c = QColor()
        c.setHsvF(self._h, self._s, self._v)
        return c

    def set_color(self, c: QColor) -> None:
        h, s, v, _ = c.getHsvF()
        self._h = 0.0 if h < 0 else h
        self._s, self._v = max(0.0, s), max(0.0, v)
        self.update()

    # ---- 几何
    def _geom(self):
        size = min(self.width(), self.height())
        cx, cy = self.width() / 2, self.height() / 2
        r_out = size / 2 - 2
        ring_w = max(11.0, size * 0.105)
        r_in = r_out - ring_w
        sq = r_in * 1.30
        rect = QRectF(cx - sq / 2, cy - sq / 2, sq, sq)
        return QPointF(cx, cy), r_out, ring_w, r_in, rect

    def _angle_at(self, pos: QPointF, center: QPointF) -> float:
        """点击位置换算成 0..1 的色相。"""
        dx, dy = pos.x() - center.x(), pos.y() - center.y()
        ang = math.degrees(math.atan2(dy, dx))     # -180..180
        return ((ang + 90) % 360) / 360.0

    # ---- 绘制
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        center, r_out, ring_w, r_in, rect = self._geom()

        # 色相环
        cg = QConicalGradient(center, 90)
        for i in range(37):
            cg.setColorAt(i / 36, QColor.fromHsvF((i / 36) % 1.0, 1.0, 1.0))
        ring_rect = QRectF(center.x() - (r_in + ring_w / 2),
                           center.y() - (r_in + ring_w / 2),
                           (r_in + ring_w / 2) * 2, (r_in + ring_w / 2) * 2)
        p.setPen(QPen(QBrush(cg), ring_w))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(ring_rect)

        # 环上的选中点
        ang = math.radians(self._h * 360 - 90)
        rr = r_in + ring_w / 2
        dot = QPointF(center.x() + math.cos(ang) * rr, center.y() + math.sin(ang) * rr)
        p.setPen(QPen(QColor("#ffffff"), 2.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(dot, ring_w * 0.30, ring_w * 0.30)

        # 饱和度/明度方块
        p.setPen(Qt.NoPen)
        g1 = QLinearGradient(rect.topLeft(), rect.topRight())
        g1.setColorAt(0.0, QColor(255, 255, 255))
        g1.setColorAt(1.0, QColor.fromHsvF(self._h, 1.0, 1.0))
        p.setBrush(QBrush(g1))
        p.drawRoundedRect(rect, 6, 6)

        g2 = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g2.setColorAt(0.0, QColor(0, 0, 0, 0))
        g2.setColorAt(1.0, QColor(0, 0, 0, 255))
        p.setBrush(QBrush(g2))
        p.drawRoundedRect(rect, 6, 6)

        # 方块指示器
        hx = rect.left() + self._s * rect.width()
        vy = rect.top() + (1.0 - self._v) * rect.height()
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.8))
        p.drawEllipse(QPointF(hx, vy), 6.5, 6.5)
        p.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
        p.drawEllipse(QPointF(hx, vy), 8.0, 8.0)

    # ---- 交互
    def _apply(self, pos: QPointF) -> None:
        center, r_out, ring_w, r_in, rect = self._geom()
        dist = math.hypot(pos.x() - center.x(), pos.y() - center.y())
        if rect.contains(pos) and self._drag != "ring":
            self._drag = "square"
            self._s = max(0.0, min(1.0, (pos.x() - rect.left()) / rect.width()))
            self._v = max(0.0, min(1.0, 1.0 - (pos.y() - rect.top()) / rect.height()))
        elif dist <= r_out + 3:
            self._drag = "ring"
            self._h = self._angle_at(pos, center)
        self.update()
        self.colorChanged.emit(self.color())

    def mousePressEvent(self, ev) -> None:
        self._drag = ""
        self._apply(ev.position())

    def mouseMoveEvent(self, ev) -> None:
        if self._drag:
            self._apply(ev.position())

    def mouseReleaseEvent(self, _ev) -> None:
        self._drag = ""


class SwatchRow(QWidget):
    """一排预设色块，点一下就选中。"""

    colorPicked = Signal(QColor)

    PRESETS = [
        "#ff3b5c", "#ff7a45", "#ffc53d", "#52e07a",
        "#22d3ee", "#4a7cff", "#8b5cf6", "#ff6ec7",
        "#ffffff", "#8f9bb0",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for hexv in self.PRESETS:
            b = QPushButton("")
            b.setFixedSize(24, 24)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(hexv)
            b.setStyleSheet(
                f"QPushButton{{background:{hexv};border:1px solid rgba(255,255,255,0.16);"
                f"border-radius:7px;}}"
                f"QPushButton:hover{{border:2px solid #ffffff;}}"
            )
            b.clicked.connect(lambda _=False, c=hexv: self.colorPicked.emit(QColor(c)))
            lay.addWidget(b)
        lay.addStretch(1)

