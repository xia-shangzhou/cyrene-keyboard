"""
Cyrene Keyboard（昔涟键盘控制台）—— 主界面。

参考 Razer Synapse / G HUB / iCUE / Keychron Launcher 的布局思路：
设备本体常驻为视觉主体，灯效直接渲染在键帽上，控制项退到下方。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction, QBrush, QColor, QDesktopServices, QIcon, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QStackedWidget, QSystemTrayIcon, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from kb import backend as B
from kb import protocol as P
from kb import update as U
from kb.audioviz import AudioVizEngine, VizSettings
from kb.backend import BatteryInfo, Backend, LedState
from ui import layout_data as L
from ui import theme as T
from ui.colorwheel import ColorWheel, SwatchRow
from ui.keyboard_view import KeyboardView, effect_color

# 实测在本机固件上无效的灯效模式（逐个试完后往这里加，界面会自动隐藏）
INVALID_MODES: set[int] = set()

# 完全不显示的灯效模式
#   13 = 自定义逐键：需要逐键颜色数据，而固件读不回来
#   20/22 = 音乐律动：必须持续推音频数据流才有画面，单独放在「律动」页控制
#   21 = 屏幕同步：需要持续截屏取色 + 0x0E 帧结构未知，已决定不做
#   25 = 逐键动画：逐帧写 flash，磨损不可接受
HIDDEN_MODES: set[int] = {13, 20, 21, 22, 25}

# 「暂不开放但仍显示为灰卡片」的模式。目前为空 —— 要做的都做了，不做的都隐藏了。
MODE_NOTES: dict[int, str] = {}


# ---------------------------------------------------------------- 基础设施

def ensure_cjk_font() -> str:
    """offscreen / 精简环境下 QFontDatabase 可能为空，手动补一份中文字体。"""
    from PySide6.QtGui import QFontDatabase

    if QFontDatabase.families():
        return ""
    for path in (
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhl.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ):
        if Path(path).exists():
            fid = QFontDatabase.addApplicationFont(path)
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                return fams[0]
    return ""


def _light_palette():
    """显式给一套浅色调色板。

    QSS 只能覆盖「设过样式的控件」，弹窗、右键菜单这类由系统绘制的部分会
    继承系统调色板 —— 在深色主题的 Windows 上就变成黑底灰字。这里从调色板
    层面统一成浅色，作为样式表之外的第二道保障。
    """
    from PySide6.QtGui import QColor, QPalette

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#FFFFFF"))
    pal.setColor(QPalette.WindowText, QColor("#3B2A32"))
    pal.setColor(QPalette.Base, QColor("#FFF4F8"))
    pal.setColor(QPalette.AlternateBase, QColor("#FFFFFF"))
    pal.setColor(QPalette.Text, QColor("#3B2A32"))
    pal.setColor(QPalette.Button, QColor("#FFFFFF"))
    pal.setColor(QPalette.ButtonText, QColor("#6C5560"))
    pal.setColor(QPalette.BrightText, QColor("#E8576E"))
    pal.setColor(QPalette.Highlight, QColor("#FF7FA8"))
    pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    pal.setColor(QPalette.ToolTipBase, QColor("#FFFFFF"))
    pal.setColor(QPalette.ToolTipText, QColor("#3B2A32"))
    pal.setColor(QPalette.PlaceholderText, QColor("#C0AAB4"))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#C0AAB4"))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#C0AAB4"))
    return pal


def apply_style(app: QApplication) -> str:
    fam = ensure_cjk_font()
    app.setPalette(_light_palette())
    app.setStyleSheet(T.build_qss(fam))
    return fam


APP_NAME = "Cyrene Keyboard"
APP_NAME_CN = "昔涟键盘控制台"

# 图标文件（assets/icon.ico 含 16~256 多个尺寸，Windows 会自动挑合适的那张）
ICON_ICO = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
ICON_PNG = Path(__file__).resolve().parent.parent / "assets" / "icon_256.png"


def make_app_icon() -> QIcon:
    """优先用 assets 里的图标；找不到就退回程序画的占位图标。

    注意读不到文件不能抛异常 —— 便携版里漏拷 assets 时程序还得能起来。
    """
    for p in (ICON_ICO, ICON_PNG):
        try:
            if p.exists():
                ic = QIcon(str(p))
                if not ic.isNull():
                    return ic
        except Exception:  # noqa: BLE001
            pass

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 64, 64)
    g.setColorAt(0, QColor(T.ACCENT))
    g.setColorAt(1, QColor(T.ACCENT2))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 16, 16)
    p.setBrush(QColor("#3B2A32"))
    for i in range(4):
        p.drawRoundedRect(QRectF(14, 18 + i * 8, 36 - i * 7, 4), 2, 2)
    p.end()
    return QIcon(pm)


_ICON_CACHE: dict[tuple[int, int, int], QIcon] = {}


def effect_icon(mode: int, w: int = 100, h: int = 26) -> QIcon:
    """给每个灯效生成一块预览小图，逐像素采样，避免出现摩尔纹。"""
    key = (mode, w, h)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    from PySide6.QtGui import QImage

    st = LedState(mode=mode, speed=2, brightness=4, rgb=(255, 127, 168))
    W, H = w * 2, h * 2
    img = QImage(W, H, QImage.Format_ARGB32)
    # 浅色卡片上用「浅底 + 按亮度叠加灯效色」，暗处自然融进底色
    base = QColor(252, 238, 244)
    for yy in range(H):
        fy = yy / H * L.TOTAL_H
        for xx in range(W):
            k = L.Key("", xx / W * L.TOTAL_W, fy)
            c = effect_color(mode, k, 1.35, st)
            v = max(c.red(), c.green(), c.blue()) / 255.0
            v = min(1.0, v * 1.25)
            img.setPixelColor(xx, yy, QColor(
                int(base.red() * (1 - v) + c.red() * v),
                int(base.green() * (1 - v) + c.green() * v),
                int(base.blue() * (1 - v) + c.blue() * v),
            ))

    pm = QPixmap(W, H)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, W, H), 12, 12)
    p.setClipPath(path)
    p.drawImage(0, 0, img)
    p.end()

    icon = QIcon(pm)
    _ICON_CACHE[key] = icon
    return icon


# ---------------------------------------------------------------- 侧栏

class Sidebar(QWidget):
    NAV = ["灯光", "侧灯", "律动", "逐键", "快捷键", "连接", "关于"]

    def __init__(self, on_nav, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(82)
        self.on_nav = on_nav

        lay = QVBoxLayout(self)
        lay.setContentsMargins(9, 16, 9, 16)
        lay.setSpacing(6)

        logo = QLabel("昔涟")
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            "font-size:15px; font-weight:700; color:#ffffff;"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            "stop:0 rgba(255,127,168,0.42),stop:1 rgba(169,123,255,0.30));"
            "border-radius:12px; padding:9px 0;"
        )
        lay.addWidget(logo)
        lay.addSpacing(12)

        self.buttons: list[QPushButton] = []
        for idx, text in enumerate(self.NAV):
            b = QPushButton(text)
            b.setObjectName("navBtn")
            b.setCheckable(True)
            b.setFixedHeight(50)
            b.clicked.connect(lambda _=False, i=idx: self.on_nav(i))
            lay.addWidget(b)
            self.buttons.append(b)
        self.buttons[0].setChecked(True)
        lay.addStretch(1)

        ver = QLabel("v0.3")
        ver.setObjectName("caption")
        ver.setAlignment(Qt.AlignCenter)
        lay.addWidget(ver)

    def select(self, idx: int) -> None:
        for i, b in enumerate(self.buttons):
            b.setChecked(i == idx)


# ---------------------------------------------------------------- 顶栏

class TopBar(QWidget):
    def __init__(self, on_lock=None, parent=None):
        super().__init__(parent)
        self.on_lock = on_lock
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        dev = QFrame()
        dev.setObjectName("statCard")
        dev.setFixedHeight(84)
        dl = QHBoxLayout(dev)
        dl.setContentsMargins(18, 12, 18, 12)
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color:#2DBE8A; font-size:18px;")
        dl.addWidget(self.dot)
        dl.addSpacing(2)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.name = QLabel("昔涟 · 往昔的涟漪")
        self.name.setObjectName("h1")
        self.sub = QLabel("正在识别设备…")
        self.sub.setObjectName("muted")
        col.addWidget(self.name)
        col.addWidget(self.sub)
        dl.addLayout(col)
        dl.addStretch(1)
        lay.addWidget(dev, 6)

        st = QFrame()
        st.setObjectName("statCard")
        st.setFixedHeight(84)
        sl = QVBoxLayout(st)
        sl.setContentsMargins(18, 10, 18, 10)
        sl.setSpacing(1)
        self.writes = QLabel("0")
        self.writes.setObjectName("accentNum")
        wl = QLabel("本次写入")
        _wtip = ("本次运行里会写 flash 的写入次数。\n"
                 "音乐律动这类流式帧不写 flash，不计入这里。")
        wl.setToolTip(_wtip)
        self.writes.setToolTip(_wtip)
        wl.setObjectName("caption")
        sl.addWidget(self.writes)
        sl.addWidget(wl)
        lay.addWidget(st, 2)

        # 电量卡**已删除**（2026-10-25）。原因：三种连接没一种能给准确读数 ——
        # 有线固件不响应、蓝牙厂商命令读不到（系统蓝牙设置里那个是 BLE 标准服务，
        # 10% 一档且常滞后，容易被当成"软件显示不准"）、只有 2.4G 能读但没必要。
        # 摆一个不准的数字比不摆更糟，所以界面不再展示电量，改用 Fn+Backspace。

        self.lock_btn = QPushButton("只读模式")
        self.lock_btn.setObjectName("ghost")
        self.lock_btn.setFixedHeight(34)
        self.lock_btn.setMinimumWidth(96)
        self.lock_btn.setToolTip("只读模式下不会向键盘写入任何数据")
        if self.on_lock:
            self.lock_btn.clicked.connect(self.on_lock)
        lay.addWidget(self.lock_btn)

    def update_status(self, backend: Backend) -> None:
        real = backend.name == "hid"
        # 连上了但通道不转发配置（2.4G 实测）：这一格必须明确告警，
        # 否则界面会拿默认值冒充真实灯效，用户以为自己在看键盘的实际状态
        alive = getattr(backend, "config_ok", True)
        if not real:
            dot_color = "#E8A23D"
        elif alive:
            dot_color = "#2DBE8A"
        else:
            dot_color = "#E8A23D"
        self.dot.setStyleSheet(f"color:{dot_color}; font-size:18px;")

        writable = not getattr(backend, "readonly", True)
        if not real:
            self.lock_btn.setText("模拟模式")
            self.lock_btn.setEnabled(False)
            self.lock_btn.setStyleSheet("")
        elif not alive:
            self.lock_btn.setText("配置不可用")
            self.lock_btn.setEnabled(True)
            self.lock_btn.setToolTip(
                "键盘已连上，但这条通道（2.4G 无线）不转发配置命令。\n"
                "点这里会重新探测一次 —— 插好 USB 线后点它就能恢复，不用重启软件。"
            )
            self.lock_btn.setStyleSheet(
                "background:#FFF7E8; border:1px solid #E8A23D;"
                "color:#A9752A; border-radius:10px; font-weight:600;"
            )
        elif writable:
            self.lock_btn.setEnabled(True)
            self.lock_btn.setText("可写入")
            self.lock_btn.setToolTip("点击可重新锁定为只读")
            self.lock_btn.setStyleSheet(
                "background:rgba(255,127,168,0.18); border:1px solid #FF7FA8;"
                "color:#B33A66; border-radius:10px; font-weight:600;"
            )
        else:
            self.lock_btn.setEnabled(True)
            self.lock_btn.setText("只读模式")
            self.lock_btn.setToolTip("点击解锁，允许修改灯效")
            self.lock_btn.setStyleSheet("")

        label = getattr(backend, "device_label", "")
        self.sub.setText(
            (label + " · 只读") if real and getattr(backend, "readonly", False)
            else (label if real else "未检测到键盘 · 模拟预览模式")
        )
        self.writes.setText(str(backend.policy.stats["write"]))
        # 电量不再查询也不再显示（见 __init__ 里的说明）。这里少一次阻塞调用：
        # 蓝牙下 get_battery 要等 3 次超时，约 1 秒卡界面。


# ---------------------------------------------------------------- 灯效页

class EffectsPage(QWidget):
    """一套完整的灯效控制：模式卡片 + 颜色 + 参数。"""

    def __init__(self, on_change, heading: str = "灯效模式", parent=None):
        super().__init__(parent)
        self.on_change = on_change
        self.state = LedState()
        self._loading = False
        self.readonly = False        # 只读模式下，控件变化只更新界面、绝不回写键盘

        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(320)
        self._pending.timeout.connect(self._flush)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # ---- 左：模式卡片
        left = QVBoxLayout()
        left.setSpacing(9)
        head_row = QHBoxLayout()
        head = QLabel(heading)
        head.setObjectName("h2")
        self.current_lbl = QLabel("")
        self.current_lbl.setStyleSheet("color:#C0AAB4; font-size:11.5px;")
        head_row.addWidget(head)
        head_row.addStretch(1)
        head_row.addWidget(self.current_lbl)
        left.addLayout(head_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 14, 0)
        grid.setSpacing(8)

        self.mode_buttons: dict[int, QToolButton] = {}
        cols = 4
        avail = [m for m in sorted(P.LED_MODES)
                 if m not in MODE_NOTES and m not in INVALID_MODES
                 and m not in HIDDEN_MODES]
        for i, mid in enumerate(avail):
            b = QToolButton()
            b.setText(P.LED_MODES[mid])
            b.setIcon(effect_icon(mid))
            b.setIconSize(QSize(100, 26))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setMinimumHeight(68)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, m=mid: self._pick_mode(m))
            grid.addWidget(b, i // cols, i % cols)
            self.mode_buttons[mid] = b

        for j, mid in enumerate(sorted(m for m in P.LED_MODES
                                       if m in MODE_NOTES and m not in HIDDEN_MODES)):
            b = QToolButton()
            b.setText(P.LED_MODES[mid])
            b.setIcon(effect_icon(mid))
            b.setIconSize(QSize(100, 26))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setEnabled(False)
            b.setMinimumHeight(68)
            b.setToolTip(MODE_NOTES[mid])
            idx = len(avail) + j
            grid.addWidget(b, idx // cols, idx % cols)

        scroll.setWidget(holder)
        left.addWidget(scroll, 1)
        root.addLayout(left, 1)

        # ---- 右：参数卡
        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(274)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(9)

        cl.addWidget(self._label("颜色"))
        self.wheel = ColorWheel()
        self.wheel.setFixedHeight(164)
        self.wheel.colorChanged.connect(self._on_wheel)
        cl.addWidget(self.wheel)

        self.swatches = SwatchRow()
        self.swatches.colorPicked.connect(self._on_swatch)
        cl.addWidget(self.swatches)

        self.s_bright, self.l_bright = self._slider("亮度", 4)
        cl.addLayout(self._slider_row(self.l_bright, self.s_bright))
        self.s_speed, self.l_speed = self._slider("速度", 2)
        cl.addLayout(self._slider_row(self.l_speed, self.s_speed))

        row = QHBoxLayout()
        row.addWidget(self._label("方向"))
        row.addStretch(1)
        self.cb_dir = QComboBox()
        for k, v in P.DIRECTIONS.items():
            self.cb_dir.addItem(v, k)
        self.cb_dir.currentIndexChanged.connect(self._changed)
        row.addWidget(self.cb_dir)
        cl.addLayout(row)

        self.chk_rainbow = QCheckBox("彩虹循环")
        self.chk_rainbow.stateChanged.connect(self._changed)
        cl.addWidget(self.chk_rainbow)
        cl.addStretch(1)

        root.addWidget(card, 0)

    # ---- 小组件
    @staticmethod
    def _label(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("muted")
        return lb

    def _slider(self, name: str, val: int):
        lb = QLabel(f"{name}  {val}")
        lb.setObjectName("muted")
        lb.setFixedWidth(56)
        s = QSlider(Qt.Horizontal)
        s.setRange(0, 4)
        s.setValue(val)
        s.valueChanged.connect(
            lambda v, n=name, w=lb: (w.setText(f"{n}  {v}"), self._changed())
        )
        return s, lb

    @staticmethod
    def _slider_row(lb: QLabel, s: QSlider) -> QHBoxLayout:
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(lb)
        r.addWidget(s, 1)
        return r

    # ---- 交互
    def _pick_mode(self, mid: int) -> None:
        for k, b in self.mode_buttons.items():
            b.setChecked(k == mid)
        self.state.mode = mid
        self._changed()

    def _on_wheel(self, c: QColor) -> None:
        self.state.rgb = (c.red(), c.green(), c.blue())
        if self.chk_rainbow.isChecked():
            self.chk_rainbow.setChecked(False)
        self._changed()

    def _on_swatch(self, c: QColor) -> None:
        self.wheel.set_color(c)
        self._on_wheel(c)

    def _changed(self, *_a) -> None:
        if self._loading or self.readonly:
            return
        self.state.brightness = self.s_bright.value()
        self.state.speed = self.s_speed.value()
        self.state.direction = self.cb_dir.currentData() or 0
        self.state.colorful = self.chk_rainbow.isChecked()
        self._pending.start()

    def _flush(self) -> None:
        self.on_change(LedState(**self.state.__dict__))

    # ---- 外部
    def set_readonly(self, ro: bool, hint: str = "") -> None:
        """只读时把交互控件禁用掉，避免「点了没反应」的困惑。"""
        self.readonly = ro
        for b in self.mode_buttons.values():
            b.setEnabled(not ro)
        for w in (self.s_bright, self.s_speed, self.cb_dir, self.chk_rainbow,
                  self.wheel, self.swatches):
            w.setEnabled(not ro)
        if ro:
            self.current_lbl.setText(hint or "只读模式 · 点右上角「只读模式」解锁后才能修改")
            self.current_lbl.setStyleSheet("color:#E8A23D; font-size:11.5px;")

    def load(self, st: LedState) -> None:
        self._loading = True
        self.state = LedState(**st.__dict__)
        self.s_bright.setValue(st.brightness)
        self.s_speed.setValue(st.speed)
        self.l_bright.setText(f"亮度  {st.brightness}")
        self.l_speed.setText(f"速度  {st.speed}")
        idx = self.cb_dir.findData(st.direction)
        if idx >= 0:
            self.cb_dir.setCurrentIndex(idx)
        self.chk_rainbow.setChecked(st.colorful)
        for k, b in self.mode_buttons.items():
            b.setChecked(k == st.mode)

        if self.readonly:
            self.current_lbl.setText("只读模式 · 点右上角「只读模式」解锁后才能修改")
            self.current_lbl.setStyleSheet("color:#E8A23D; font-size:11.5px;")
            self.wheel.set_color(QColor(*st.rgb))
            self._loading = False
            return

        # 当前模式若不在可编辑列表里，明确告诉用户在哪儿切
        if st.mode not in self.mode_buttons:
            self.current_lbl.setText(f"当前：{st.mode_name} · 需在键盘上切回预设模式")
            self.current_lbl.setStyleSheet("color:#E8A23D; font-size:11.5px;")
        else:
            self.current_lbl.setText(f"当前：{st.mode_name}")
            self.current_lbl.setStyleSheet("color:#C0AAB4; font-size:11.5px;")

        self.wheel.set_color(QColor(*st.rgb))
        self._loading = False


# ---------------------------------------------------------------- 音乐律动页

class LevelBars(QWidget):
    """8 根实时电平柱。

    为什么是 8 根不是 16 根：固件把相邻两段求和再 clamp 到 8 才画出来，
    16 个频段物理上只能呈现 8 列。界面上直接按 8 根画，和键盘所见一致 ——
    画 16 根会让用户以为能控制更细，是误导。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._levels = [0.0] * 8
        self.setMinimumHeight(196)

    def set_levels(self, levels) -> None:
        vals = [max(0.0, min(1.0, float(v))) for v in list(levels)[:8]]
        vals += [0.0] * (8 - len(vals))
        self._levels = vals
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        pad_x, pad_y = 16.0, 14.0
        label_h = 16.0
        n = 8
        gap = 10.0
        # 柱宽设上限：窗口拉宽时不要让 8 根柱子变成 8 块板子
        bw = min(54.0, max(10.0, (w - pad_x * 2 - gap * (n - 1)) / n))
        total = n * bw + (n - 1) * gap
        x0 = max(pad_x, (w - total) / 2.0)
        top = pad_y
        base = h - pad_y - label_h
        full = max(10.0, base - top)

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.CARD_HI))
        for i in range(n):
            p.drawRoundedRect(QRectF(x0 + i * (bw + gap), top, bw, full), 7, 7)

        p.setPen(QPen(QColor(T.BORDER), 1, Qt.DashLine))
        for k in range(1, 4):
            y = base - full * k / 4.0
            p.drawLine(int(x0), int(y), int(x0 + total), int(y))

        p.setPen(Qt.NoPen)
        for i, v in enumerate(self._levels):
            bh = max(2.0, full * v)
            grad = QLinearGradient(0, base, 0, top)
            grad.setColorAt(0.0, QColor(T.ACCENT))
            grad.setColorAt(0.55, QColor("#FF9EC0"))
            grad.setColorAt(1.0, QColor(T.ACCENT2))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(x0 + i * (bw + gap), base - bh, bw, bh), 7, 7)

        p.setPen(QColor(T.TEXT_MUTE))
        f = p.font()
        f.setPointSizeF(8.0)
        p.setFont(f)
        p.drawText(QRectF(0, base + 2, w, label_h), Qt.AlignCenter,
                   "低频 ← → 高频")


class MusicPage(QWidget):
    """音乐律动：抓系统声音 → 8 频带 → 推 0x0D。

    启动时会自动把主灯切到音乐模式（22），停止时恢复进入前的灯效。
    """

    STYLES = [
        (0, "竖向柱 · 自下而上"),
        (1, "横向 · 从中间向两边"),
        (2, "横向 · 从左向右"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.backend: Backend | None = None
        self.engine: AudioVizEngine | None = None
        self.original: LedState | None = None
        self.readonly = True
        self.style = 0
        self._frames = 0
        self._silent_ticks = 0
        self._t0 = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(20)          # 50Hz（有线默认）
        self._timer.timeout.connect(self._tick)
        # 实际推帧间隔：有线 20ms(50Hz)，蓝牙可选 40ms(25Hz) / 20ms(50Hz)。start() 时决定
        self.frame_interval = 20
        # 蓝牙档位的记忆值：第一次默认 25Hz(40ms)，用户选了 50Hz 就记住
        self._ble_interval = 40
        self._ble_active = False

        self._stat_timer = QTimer(self)
        self._stat_timer.setInterval(250)
        self._stat_timer.timeout.connect(self._update_stat)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        # ---- 左：频谱
        left = QVBoxLayout()
        left.setSpacing(9)
        head_row = QHBoxLayout()
        head = QLabel("音乐律动")
        head.setObjectName("h2")
        self.state_lbl = QLabel("已停止")
        self.state_lbl.setStyleSheet(f"color:{T.TEXT_MUTE}; font-size:11.5px;")
        head_row.addWidget(head)
        head_row.addStretch(1)
        head_row.addWidget(self.state_lbl)
        left.addLayout(head_row)

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 12, 12, 8)
        self.bars = LevelBars()
        cl.addWidget(self.bars, 1)
        left.addWidget(card, 1)

        note = QLabel(
            "键盘只呈现 8 列 —— 固件会把相邻两个频段求和后 clamp 到 8。"
            "颜色是固件内置的彩虹循环，速度参数无效；亮度按档位整体缩放。"
            "这些是实测结论，不是推测。"
        )
        note.setWordWrap(True)
        note.setObjectName("caption")
        left.addWidget(note)
        root.addLayout(left, 1)

        # ---- 右：控制卡
        ctrl = QFrame()
        ctrl.setObjectName("card")
        ctrl.setFixedWidth(306)
        cl2 = QVBoxLayout(ctrl)
        cl2.setContentsMargins(16, 14, 16, 14)
        cl2.setSpacing(10)

        self.btn_run = QPushButton("开始律动")
        self.btn_run.setObjectName("primary")
        self.btn_run.setFixedHeight(42)
        self.btn_run.clicked.connect(self.toggle)
        cl2.addWidget(self.btn_run)

        self.src_lbl = QLabel("采集源")
        self.src_lbl.setObjectName("muted")
        cl2.addWidget(self.src_lbl)
        self.cb_src = QComboBox()
        self.cb_src.addItem("系统默认输出", "")
        self.cb_src.setToolTip("要抓哪个输出设备正在播放的声音。\n"
                               "选「系统默认输出」时跟随 Windows 默认播放设备。")
        self.cb_src.currentIndexChanged.connect(self._on_src)
        cl2.addWidget(self.cb_src)

        row = QHBoxLayout()
        row.addWidget(self._lab("样式"))
        row.addStretch(1)
        self.cb_style = QComboBox()
        for k, v in self.STYLES:
            self.cb_style.addItem(v, k)
        self.cb_style.currentIndexChanged.connect(self._on_style)
        row.addWidget(self.cb_style)
        cl2.addLayout(row)

        self.s_gain, self.l_gain = self._sld(10, 400, 100, "灵敏度")
        self.s_fall, self.l_fall = self._sld(6, 60, 26, "回落速度")
        self.s_bright, self.l_bright = self._sld(0, 4, 4, "亮度")
        for lay in (self.l_gain, self.l_fall, self.l_bright):
            cl2.addLayout(lay)

        cl2.addSpacing(4)
        self.stat_lbl = QLabel("—")
        self.stat_lbl.setObjectName("caption")
        self.stat_lbl.setWordWrap(True)
        cl2.addWidget(self.stat_lbl)
        cl2.addStretch(1)

        tip = QLabel(
            "需要顶部解锁写入才能启动。\n"
            "只改灯效模式，不碰固件 / 键位 / 宏。\n"
            "停止或关闭窗口会自动恢复原灯效。"
        )
        tip.setObjectName("caption")
        tip.setWordWrap(True)
        cl2.addWidget(tip)
        root.addWidget(ctrl, 0)

        self._load_sources()

    # ---- 小工具
    @staticmethod
    def _lab(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setObjectName("muted")
        return lb

    def _sld(self, lo: int, hi: int, val: int, title: str):
        """返回 (slider, 整个表单行)。"""
        box = QVBoxLayout()
        box.setSpacing(2)
        head = QHBoxLayout()
        name = self._lab(title)
        num = QLabel(str(val))
        num.setStyleSheet(f"color:{T.ACCENT2}; font-weight:600; font-size:11.5px;")
        head.addWidget(name)
        head.addStretch(1)
        head.addWidget(num)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        s.valueChanged.connect(lambda v, n=num: n.setText(str(v)))
        s.valueChanged.connect(self._on_param)
        box.addLayout(head)
        box.addWidget(s)
        return s, box

    def _load_sources(self) -> None:
        try:
            outs = AudioVizEngine.list_outputs()
        except Exception:  # noqa: BLE001
            outs = []
        for name in outs:
            # 虚拟声卡（VB-Cable 之类）要标出来：选它抓到的可能不是你听到的声音，
            # 而是别的程序往这个虚拟设备里灌的流（比如语音输入转发）
            virt = any(k in name for k in ("CABLE", "Virtual", "虚拟", "VoiceMeeter"))
            self.cb_src.addItem(name + ("（虚拟声卡）" if virt else ""), name)

    def _src_name(self) -> str | None:
        return self.cb_src.currentData() or None

    def _on_src(self, _i: int) -> None:
        """运行中换采集源：平滑重启音频线程，不动灯效。"""
        if not self.engine:
            return
        old = self.engine
        old.stop()
        eng = AudioVizEngine(device_name=self._src_name(),
                             settings=VizSettings(gain=self.s_gain.value() / 100.0,
                                                  decay_db_per_s=float(self.s_fall.value())))
        if not eng.start():
            QMessageBox.warning(self, "切换失败",
                                f"抓不到该设备的音频：{eng.error}")
            # 退回默认输出；默认也起不来的话只能停下，别留个空引擎空转
            eng = AudioVizEngine(device_name=None,
                                 settings=VizSettings(gain=self.s_gain.value() / 100.0,
                                                      decay_db_per_s=float(self.s_fall.value())))
            if not eng.start():
                self.stop(restore=False)
                QMessageBox.critical(self, "采集失败", eng.error)
                return
        self.engine = eng
        self.src_lbl.setText(f"采集源 · {eng.device_label}")

    # ---- 状态
    def set_readonly(self, ro: bool) -> None:
        self.readonly = ro
        self._refresh_btn()

    @property
    def running(self) -> bool:
        return bool(self.engine and self._timer.isActive())

    def _refresh_btn(self) -> None:
        if self.running:
            self.btn_run.setText("停止并恢复原灯效")
            self.btn_run.setObjectName("danger")
        else:
            self.btn_run.setText("开始律动")
            self.btn_run.setObjectName("primary")
        self.btn_run.setStyleSheet("")   # 触发 QSS 按新 objectName 重绘
        self.btn_run.style().unpolish(self.btn_run)
        self.btn_run.style().polish(self.btn_run)

    def _music_state(self) -> LedState:
        base = self.original or LedState()
        return LedState(
            mode=P.AUDIO_MODES[1],            # 22 = 音乐律动条
            speed=base.speed,
            brightness=self.s_bright.value(),
            direction=self.style,             # 音乐模式下高 nibble = 样式
            colorful=base.colorful,
            rgb=base.rgb,
        )

    def _apply_params(self) -> None:
        """参数变化时只重发灯效参数（不动音频流）。"""
        b = self.backend
        if not b or not self.running:
            return
        try:
            b.set_led(self._music_state())
        except Exception:  # noqa: BLE001
            pass

    def _on_param(self, *_a) -> None:
        if self.engine:
            self.engine.cfg.gain = self.s_gain.value() / 100.0
            self.engine.cfg.decay_db_per_s = float(self.s_fall.value())
        self._apply_params()

    def _on_style(self, _i: int) -> None:
        self.style = int(self.cb_style.currentData() or 0)
        self._apply_params()

    # ---- 启停
    def toggle(self) -> None:
        if self.running:
            self.stop()
        else:
            self.start()

    def _ask_ble_rate(self) -> int | None:
        """蓝牙下问档位。返回帧间隔（ms），None = 用户取消。

        单独抽出来是为了能测：`exec()` 会阻塞，塞在 `start()` 里就没法自动验证分支。
        """
        box = QMessageBox(self)
        box.setWindowTitle("蓝牙下跑律动")
        box.setText(
            "蓝牙带宽比有线窄：一次往返 16ms，实测帧率上限约 64Hz。\n"
            "选小一点的档位，可以把链路留给按键上报。"
        )
        box.setInformativeText(
            "· 25Hz：省电、几乎不影响打字，灯略不跟手（默认）\n"
            "· 50Hz：和有线的节奏一致、更顺滑，占链路多一些\n"
            "　实测 50Hz 时打字仍可正常办公，只是极轻微迟钝\n\n"
            "想两全就插 Type-C 切有线（一轮约 1ms，完全不占带宽）。"
        )
        btn25 = box.addButton("25Hz（默认）", QMessageBox.AcceptRole)
        btn50 = box.addButton("50Hz", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(btn25 if self._ble_interval == 40 else btn50)
        box.exec()
        hit = box.clickedButton()
        if hit is btn25:
            return 40
        if hit is btn50:
            return 20
        return None

    def start(self) -> None:
        b = self.backend
        if b is None:
            QMessageBox.warning(self, "无法启动", "还没有连接后端。")
            return
        # 幂等：重复点击不能把 original 覆盖成"音乐模式自己"，否则停止时恢复不回去
        if self.running:
            return
        if b.name == "hid" and not getattr(b, "config_ok", True):
            QMessageBox.warning(
                self, "配置不可用",
                "当前是 2.4G 无线，这条通道不转发配置命令，推帧到不了键盘。\n\n"
                "插上 Type-C 线切到有线模式（长按 Fn+5 约 3 秒）再试。",
            )
            return
        is_ble = b.name == "hid" and bool(getattr(getattr(b, "kb", None), "ble", False))
        self.frame_interval = 20               # 默认 50Hz（有线）
        self._ble_active = False
        if b.name == "hid" and getattr(b, "readonly", True):
            QMessageBox.warning(
                self, "需要解锁写入",
                "当前是只读模式，不会向键盘发送任何数据。\n\n"
                "请先点右上角「只读模式」解锁，再回来启动律动。",
            )
            return
        if not AudioVizEngine.available():
            QMessageBox.critical(
                self, "缺少组件",
                "没有安装 soundcard 库，无法采集系统音频。\n\n"
                "在项目环境里执行：pip install soundcard",
            )
            return

        if is_ble:
            # 蓝牙能推帧：25Hz 稳定省电，50Hz 实测也不掉帧、打字仍可正常办公
            # （主人 2026-10-25 实测）。所以不拦，只提示 + 让用户选档位，默认 25Hz。
            interval = self._ask_ble_rate()
            if interval is None:
                return
            self._ble_interval = interval      # 记住，下次弹窗默认停在它上面
            self.frame_interval = interval
            self._ble_active = True

        cfg = VizSettings(
            gain=self.s_gain.value() / 100.0,
            decay_db_per_s=float(self.s_fall.value()),
        )
        # ★ 必须读到真实灯效才敢启动：读不到就用默认值兜底的话，停止时会把这个
        # 默认值（常亮/亮度4/红色）当成"原灯效"写回去，把用户的灯改坏。
        # 这是坑 24「读不到时不要用默认值冒充」的同一个错误，踩过第二次。
        led = b.get_led()
        if led is None:
            time.sleep(0.15)
            led = b.get_led()          # 偶发一次读失败（尤其蓝牙）重试一次
        if led is None:
            QMessageBox.warning(
                self, "读不到当前灯效",
                "读不到键盘当前的灯效，就没法保证停止时能还原，所以先不启动。\n\n"
                "可以点顶部状态条上的「配置不可用」重试一次连接，再回来试。",
            )
            return
        self.original = led
        self.engine = AudioVizEngine(device_name=self._src_name(), settings=cfg)

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ok = self.engine.start()
        finally:
            QApplication.restoreOverrideCursor()

        if not ok:
            QMessageBox.critical(
                self, "采集失败",
                f"抓不到系统音频：{self.engine.error}\n\n"
                "常见原因：输出设备被独占、采样率不支持、声卡驱动加了音效层。",
            )
            self.engine = None
            return

        try:
            b.policy.forget("led")
            b.set_led(self._music_state())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "切换模式失败", str(e))
            self.engine.stop()
            self.engine = None
            return

        self.src_lbl.setText(f"采集源 · {self.engine.device_label}")
        self._frames = 0
        self._silent_ticks = 0
        self._t0 = time.monotonic()
        self._timer.setInterval(self.frame_interval)
        self._timer.start()
        self._stat_timer.start()
        hz = round(1000 / self.frame_interval)
        self.state_lbl.setText(
            f"运行中 · 主灯已切到音乐律动条（蓝牙 {hz}Hz）"
            if self._ble_active else
            "运行中 · 主灯已切到音乐律动条")
        self.state_lbl.setStyleSheet(f"color:{T.OK}; font-size:11.5px;")
        self._refresh_btn()

    def stop(self, restore: bool = True) -> None:
        self._timer.stop()
        self._stat_timer.stop()
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.bars.set_levels([0.0] * 8)
        if restore and self.original is not None and self.backend is not None:
            try:
                self.backend.policy.forget("led")
                self.backend.set_led(self.original)
            except Exception:  # noqa: BLE001
                pass
        self.original = None
        self.src_lbl.setText("采集源")
        self.state_lbl.setText("已停止 · 灯效已恢复" if restore else "已停止")
        self.state_lbl.setStyleSheet(f"color:{T.TEXT_MUTE}; font-size:11.5px;")
        self.stat_lbl.setText("—")
        self._refresh_btn()

    # ---- 推送
    def _tick(self) -> None:
        eng, b = self.engine, self.backend
        if not eng or not b:
            self._timer.stop()
            return
        try:
            b.set_audio_viz(eng.snapshot())
        except Exception as e:  # noqa: BLE001
            self.stop(restore=False)
            self.state_lbl.setText(f"发送中断：{e}")
            self.state_lbl.setStyleSheet(f"color:{T.DANGER}; font-size:11.5px;")
            return
        lv = eng.levels()
        self.bars.set_levels(lv)
        self._frames += 1
        # 连续没信号，多半是采集源和实际出声的设备不是同一个
        self._silent_ticks = self._silent_ticks + 1 if max(lv) < 0.002 else 0

    def _update_stat(self) -> None:
        if not self.running:
            return
        el = max(0.001, time.monotonic() - self._t0)
        fps = self._frames / el
        db = max(self.engine.levels_db()) if self.engine else -90.0
        gain = self.engine.gain_db if self.engine else 0.0
        writes = self.backend.policy.stats["write"] if self.backend else 0
        text = (f"已推 {self._frames} 帧 · {fps:.0f} fps\n"
                f"峰值 {db:.0f} dB · 自动增益 +{gain:.1f} dB\n"
                f"flash 写入 {writes} 次（流式帧不写 flash）")
        # 增益快打满还是不够响：蓝牙耳机音量太低，建议直接调大系统音量
        if gain > 46:
            text += "\n⚠ 信号很弱，自动增益已接近上限 —— 把系统/耳机音量调大效果会更好"
        # 3 秒没任何信号：几乎一定是采集源和出声设备不一致。
        # 阈值按实际帧率折算 —— 蓝牙 25Hz 时 150 帧就是 6 秒，判早了会误报。
        if self._silent_ticks * self.frame_interval > 3000:
            text += "\n⚠ 连续 3 秒没听到声音，检查采集源是不是正在出声的那个设备"
        self.stat_lbl.setText(text)


# ---------------------------------------------------------------- 逐键页

class PerKeyPage(QWidget):
    """逐键 RGB：讲清楚键盘自带的录制方式，以及软件为什么没接管它。"""

    STEPS = [
        ("1", "Fn + Z（或 Fn + X / Fn + C）",
         "进入自定义灯光模式 1 / 2 / 3。三组互相独立，可以分别存三套。"),
        ("2", "Fn + V",
         "开始录制。此时 Fn 键亮红灯并闪烁 —— 闪的是 Fn 键，不是整把键盘的背光。"),
        ("3", "逐个按你想点亮的键",
         "按下一个，那个键就亮一个，可以逐个点亮。（说明书原文只写「录制」，这一步是实测补的）"),
        ("4", "再按 Fn + V",
         "保存。Fn 键停止闪烁，这组灯效就固定下来了，之后照常使用。"),
    ]

    # 说明书原文，逐字照抄（用于对照，别凭记忆改）
    MANUAL_QUOTE = (
        "说明书原文 · 自定义灯光录制（Custom lighting recording）：\n"
        "Fn+Z/X/C ── 自定义灯光 1.2.3（Custom Lighting 1,2,3）\n"
        "Fn+V ── 录制/保存（Record / Save）\n"
        "a）先按 Fn+Z/X/C 组合键进入自定义灯光模式 1.2.3\n"
        "b）按 Fn+「V」组合键开始录制，此 Fn 键红灯闪烁，"
        "录制完成后再按 Fn+「V」组合键保存，Fn 灯停止闪烁。"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        # 左：做法
        left = QFrame()
        left.setObjectName("card")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(22, 18, 22, 18)
        ll.setSpacing(10)
        h = QLabel("逐键 RGB · 用键盘自己做")
        h.setStyleSheet("font-size:17px; font-weight:700; color:#3B2A32;")
        ll.addWidget(h)
        sub = QLabel("这把键盘固件自带逐键录制，直接在键盘上按就能完成，不需要软件。")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#8C7480; font-size:12.5px;")
        ll.addWidget(sub)
        ll.addSpacing(4)

        for num, title, desc in self.STEPS:
            row = QHBoxLayout()
            row.setSpacing(12)
            badge = QLabel(num)
            badge.setFixedSize(24, 24)
            badge.setAlignment(Qt.AlignCenter)
            badge.setStyleSheet(
                "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,"
                "stop:0 #FF7FA8, stop:1 #A97BFF); color:#FFFFFF;"
                "border-radius:12px; font-size:12px; font-weight:700;"
            )
            col = QVBoxLayout()
            col.setSpacing(2)
            t = QLabel(title)
            t.setStyleSheet("color:#3B2A32; font-size:13px; font-weight:600;")
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet("color:#8C7480; font-size:12.5px;")
            col.addWidget(t)
            col.addWidget(d)
            row.addWidget(badge, 0, Qt.AlignTop)
            row.addLayout(col, 1)
            ll.addLayout(row)

        # 说明书原文对照 —— 让用户能自己核对，不用信软件的转述
        q = QLabel(self.MANUAL_QUOTE)
        q.setWordWrap(True)
        q.setStyleSheet(
            "color:#8C7480; background:#FFF9FB; border:1px dashed #EFCAD9;"
            "border-radius:9px; padding:9px 12px; font-size:11.5px;"
        )
        ll.addSpacing(2)
        ll.addWidget(q)

        ll.addStretch(1)
        outer.addWidget(left, 1)

        # 右：为什么软件不做
        right = QFrame()
        right.setObjectName("card")
        right.setFixedWidth(330)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 18, 20, 18)
        rl.setSpacing(9)
        rh = QLabel("为什么软件没接管这个功能")
        rh.setObjectName("h2")
        rl.addWidget(rh)

        tip = QLabel("固件若开放读回通道，软件版逐键编辑可以再补上。")
        tip.setWordWrap(True)
        tip.setStyleSheet(
            "color:#E8A23D; background:rgba(232,162,61,0.10);"
            "border:1px solid rgba(232,162,61,0.30); border-radius:9px;"
            "padding:8px 10px; font-size:11.5px;"
        )
        rl.addWidget(tip)

        for t in [
            "这把键盘的固件不支持读回逐键颜色（查询 0x8C 一律原样回显），"
            "写完无法验证结果，只能靠眼睛看。",
            "108 个键在固件里的索引未知，需要逐个试探才能建立对照表。",
            "盲写一旦错位会出现「点 A 亮 B」，比键盘自带的录制方式更容易出错。",
            "所以这里保留了键盘原生的做法 —— 它更可靠，也不依赖电脑。",
        ]:
            d = QLabel("·  " + t)
            d.setWordWrap(True)
            d.setStyleSheet("color:#8C7480; font-size:12.5px;")
            rl.addWidget(d)

        rl.addStretch(1)
        outer.addWidget(right, 0)


class ConnectionPage(QWidget):
    """连接方式：显示当前实际使用的连接，以及三种方式分别怎么连。"""

    METHODS = [
        ("USB 有线",
         "用 Type-C 数据线连电脑，键盘底部开关拨到有线档（或长按 Fn+5 约 3 秒）。",
         "灯效全部可读可写。\n调逐键 RGB 建议用这个。"),
        ("2.4G 无线",
         "把接收器插进电脑 USB 口，开关拨到 2.4G，长按 Fn+4 约 3 秒配对。",
         "实测：配置命令不转发（接收器固件只应答自己的电量/状态查询）。\n"
         "所以灯效在这个模式下改不了，请插 USB 线。"),
        ("蓝牙",
         "开关拨到蓝牙，长按 Fn+1 / Fn+2 / Fn+3 约 3 秒，分别配对三台设备。",
         "实测可读可写（2026-10-25）。\n"
         "走 Report ID 6 + 0x55 前导的另一套帧格式，程序已适配。\n"
         "律动可跑：默认 25Hz，也可选 50Hz。"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        cur = QFrame()
        cur.setObjectName("card")
        cl = QVBoxLayout(cur)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(6)
        cap = QLabel("当前连接")
        cap.setObjectName("caption")
        self.now = QLabel("正在检测…")
        self.now.setObjectName("h1")
        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        cl.addWidget(cap)
        cl.addWidget(self.now)
        cl.addWidget(self.detail)
        outer.addWidget(cur, 0)

        row = QHBoxLayout()
        row.setSpacing(14)
        for name, how, note in self.METHODS:
            row.addWidget(self._card(name, how, note), 1)
        outer.addLayout(row)
        outer.addStretch(1)
        row.setAlignment(Qt.AlignTop)

    def _card(self, name: str, how: str, note: str) -> QFrame:
        f = QFrame()
        f.setObjectName("card")
        v = QVBoxLayout(f)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(6)
        h = QLabel(name)
        h.setObjectName("h2")
        v.addWidget(h)
        a = QLabel("怎么连")
        a.setObjectName("caption")
        v.addWidget(a)
        d = QLabel(how)
        d.setWordWrap(True)
        d.setStyleSheet("color:#8C7480; font-size:12.5px;")
        v.addWidget(d)
        b = QLabel("能力")
        b.setObjectName("caption")
        v.addWidget(b)
        n = QLabel(note)
        n.setWordWrap(True)
        n.setStyleSheet("color:#8C7480; font-size:12.5px;")
        v.addWidget(n)
        v.addStretch(1)
        return f

    def refresh(self, backend: Backend) -> None:
        label = getattr(backend, "device_label", "")
        if not label:
            self.now.setText("未连接")
            self.now.setStyleSheet("font-size:17px; font-weight:600; color:#E8A23D;")
            self.detail.setText("没检测到键盘，当前显示的是模拟预览")
            return
        parts = label.split(" · ")
        links = ("USB 有线", "2.4G 无线", "蓝牙")
        link = next((p for p in parts if p in links), parts[-1])
        alive = getattr(backend, "config_ok", True)
        self.now.setText(link)
        rest = [p for p in parts if p != link]
        # 电量不再显示：三种连接没一种能给准确读数（详见 TopBar 里的说明）。
        # 需要看电量就按键盘 Fn+Backspace —— 那是固件本地行为，比软件读的准。
        self.detail.setText(" · ".join(rest))
        self.now.setStyleSheet(
            f"font-size:17px; font-weight:600; color:{'#2DBE8A' if alive else '#E8A23D'};")


class _UpdateBridge(QObject):
    """把后台线程的检查结果送回 UI 线程。

    Qt 要求控件只能在主线程动，而网络请求必须放到后台（否则界面卡 6 秒）。
    信号跨线程发出时是排队投递，天然安全。
    """
    done = Signal(object)


class AboutPage(QWidget):
    """软件功能简介（对外分享时给别人看的就是这一页）。"""

    FEATURES = [
        ("灯效切换", "预设灯效一键切换，效果实时预览在键盘图上"),
        ("亮度 / 速度 / 方向", "各 0-4 档，方向可选正反向"),
        ("颜色", "HSV 色轮取色 + 10 个预设色块，支持彩虹循环"),
        ("侧面灯条", "与主键盘独立的第二路灯光，参数单独调"),
        ("逐键 RGB", "按店铺实物分区上色，支持整片涂色（待解锁）"),
        ("快捷键速查", "说明书 FN 组合键整理成表，多媒体键可直接点执行"),
        ("系统托盘", "关窗口缩到托盘，右键快速切灯效"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        left = QFrame()
        left.setObjectName("card")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(22, 18, 22, 18)
        ll.setSpacing(8)
        title = QLabel(f"{APP_NAME}")
        title.setStyleSheet("font-size:19px; font-weight:700; color:#3B2A32;")
        sub = QLabel("为「崩坏:星穹铁道」昔涟 · 往昔的涟漪 108 键机械键盘做的桌面控制工具。")
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#8C7480; font-size:12.5px;")
        ll.addWidget(title)
        ll.addWidget(sub)
        ll.addSpacing(6)

        for name, desc in self.FEATURES:
            row = QHBoxLayout()
            row.setSpacing(10)
            dot = QLabel("·")
            dot.setStyleSheet("color:#FF7FA8; font-size:16px; font-weight:700;")
            dot.setFixedWidth(12)
            n = QLabel(name)
            n.setFixedWidth(128)
            n.setStyleSheet("color:#3B2A32; font-size:12.5px; font-weight:600;")
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet("color:#8C7480; font-size:12.5px;")
            row.addWidget(dot)
            row.addWidget(n)
            row.addWidget(d, 1)
            ll.addLayout(row)
        ll.addStretch(1)
        outer.addWidget(left, 3)

        right = QFrame()
        right.setObjectName("card")
        right.setFixedWidth(250)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 18, 20, 18)
        rl.setSpacing(8)

        h = QLabel("运行统计")
        h.setObjectName("h2")
        rl.addWidget(h)
        self.stats = QTextEdit()
        self.stats.setReadOnly(True)
        self.stats.setMinimumHeight(140)
        self.stats.setMaximumHeight(176)
        rl.addWidget(self.stats)

        note = QLabel(
            "只读模式下不会向键盘写入任何数据。\n"
            "解锁后只改灯效参数，不碰固件、改键、宏与配对。\n"
            "万一灯效异常，按键盘 Fn+Tab 切一次模式即可恢复。"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#8C7480; font-size:11.5px;")
        rl.addWidget(note)

        ver = QLabel(f"{U.APP_VERSION_LABEL} · 本地运行，只在你点「检查更新」时联网")
        ver.setObjectName("caption")
        ver.setWordWrap(True)
        rl.addWidget(ver)

        self.btn_update = QPushButton("检查版本更新")
        self.btn_update.setObjectName("ghost")
        self.btn_update.setFixedHeight(32)
        self.btn_update.setCursor(Qt.PointingHandCursor)
        # 必须显式 padding:0 —— 全局 QSS 的 padding 会把这个小按钮压扁（见日志坑 20）
        self.btn_update.setStyleSheet(
            "QPushButton{background:#FFFFFF;border:1px solid #FF7FA8;"
            "border-radius:9px;color:#B33A66;font-size:12px;padding:0;}"
            "QPushButton:hover{background:#FFEAF2;}"
            "QPushButton:disabled{color:#C9B4BD;border-color:#EFCAD9;background:#FBF7F9;}"
        )
        self.btn_update.clicked.connect(self._check_update)
        rl.addWidget(self.btn_update)

        self.upd_lbl = QLabel("")
        self.upd_lbl.setObjectName("caption")
        self.upd_lbl.setWordWrap(True)
        rl.addWidget(self.upd_lbl)

        self._checker = U.UpdateChecker()
        self._bridge = _UpdateBridge()
        self._bridge.done.connect(self._on_update_result)

        rl.addStretch(1)
        outer.addWidget(right, 0)
        left.setMinimumWidth(420)

    # ---- 检查更新
    def _check_update(self) -> None:
        # 先把 UI 切到"检查中"再起线程：否则极端情况下（请求秒回）回调先到，
        # 会把按钮永远卡在"检查中…"
        if self._checker.running:
            return
        self.btn_update.setEnabled(False)
        self.btn_update.setText("检查中…")
        self.upd_lbl.setText("正在向 GitHub 查询最新版本（只发这一次请求）")
        self._checker.start(lambda r: self._bridge.done.emit(r))

    def _on_update_result(self, r) -> None:
        """回调在后台线程里被调用，靠 signal 转回 UI 线程再动控件。"""
        self.btn_update.setEnabled(True)
        self.btn_update.setText("检查版本更新")

        if r.error:
            self.upd_lbl.setText("检查失败：" + r.error.splitlines()[0])
            QMessageBox.information(self, "检查更新失败", r.error)
            return

        self.upd_lbl.setText(f"远端最新：{r.latest}" + (f"（{r.note}）" if r.note else ""))
        if not r.has_new:
            QMessageBox.information(
                self, "已是最新",
                f"当前 {U.APP_VERSION_LABEL}，远端也是 {r.latest}。\n不用更新。",
            )
            return

        ok = QMessageBox.question(
            self, "发现新版本",
            f"当前 {U.APP_VERSION_LABEL} → 最新 {r.latest}\n"
            f"{r.note}\n\n要打开 Releases 页面去看看吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if ok == QMessageBox.Yes and r.url:
            QDesktopServices.openUrl(QUrl(r.url))

    def refresh(self, backend: Backend) -> None:
        s = backend.policy.stats
        self.stats.setPlainText(
            f"读取命令        {s['read']:>5}\n"
            f"写入命令        {s['write']:>5}\n"
            f"拦截危险命令    {s['blocked_forbidden']:>5}\n"
            f"跳过重复值      {s['skipped_duplicate']:>5}\n"
            f"限流跳过        {s['skipped_throttle']:>5}"
        )


class KeymapPage(QWidget):
    """FN 组合键速查（严格对照官方说明书）。带 action 的行可以点执行。"""

    VK = {"mute": 0xAD, "voldown": 0xAE, "volup": 0xAF,
          "prev": 0xB1, "play": 0xB3, "next": 0xB0, "calc": 0xB7,
          "mail": 0xB4, "home": 0xAC, "mycomp": -1}   # -1 = Win+E，特殊处理

    SECTIONS = [
        ("灯光与灯条", "#FF7FA8", [
            ("Fn + Insert", "切换灯光模式", None),
            ("Fn + Home", "省电模式，关闭背光", None),
            ("Fn + End", "进入常亮模式", None),
            ("Fn + Delete", "切换灯光颜色", None),
            ("Fn + ← / →", "背光变化速度 变慢 / 变快", None),
            ("Fn + ↑ / ↓", "背光亮度 变亮 / 变暗（最低档即关闭）", None),
            ("Fn + PrtSc", "切换装饰灯条灯效", None),
            ("Fn + Scroll", "切换装饰灯条颜色", None),
            ("Fn + - / =", "灯条亮度 - / +", None),
        ]),
        ("自定义灯光与电量", "#A97BFF", [
            ("Fn + Z / X / C", "进入自定义灯光 1 / 2 / 3（三组互相独立）", None),
            ("Fn + V", "开始录制 / 再按一次保存（Fn 键红灯闪烁）", None),
            ("Fn + Backspace", "电量查询：背光熄灭，数字键 1-0 亮起几颗即几十%", None),
        ]),
        ("连接与系统", "#7FC8E8", [
            ("Fn + 5", "切换到有线模式（长按约 3 秒）", None),
            ("Fn + 4", "2.4G 配对（长按约 3 秒）", None),
            ("Fn + 1 / 2 / 3", "蓝牙 1 / 2 / 3 配对（长按约 3 秒）", None),
            ("Fn + `", "长按 2 秒切换 WIN / MAC 模式", None),
            ("Fn + Win", "锁住左 Win 和 APP 键", None),
            ("Fn + Esc", "长按 2 秒恢复出厂；短按切 MAC 下 F 区逻辑", None),
        ]),
        ("Windows / Android", "#2DBE8A", [
            ("Fn + F1 / F2", "亮度 - / +（屏幕背光，台式外接屏无此通道）", None),
            ("Fn + F3", "计算器", "calc"),
            ("Fn + F4", "我的电脑", "mycomp"),
            ("Fn + F5", "邮件", "mail"),
            ("Fn + F6", "主页", "home"),
            ("Fn + F7", "上一曲", "prev"),
            ("Fn + F8", "播放 / 暂停", "play"),
            ("Fn + F9", "下一曲", "next"),
            ("Fn + F10", "静音", "mute"),
            ("Fn + F11", "音量 -", "voldown"),
            ("Fn + F12", "音量 +", "volup"),
        ]),
        ("iOS / macOS", "#E8A23D", [
            ("Fn + F1 / F2", "亮度 - / +", None),
            ("Fn + F3", "任务控制", None),
            ("Fn + F4", "呼出窗口", None),
            ("Fn + F5 / F6", "这两个键无功能", None),
            ("Fn + F7", "上一曲", "prev"),
            ("Fn + F8", "播放 / 暂停", "play"),
            ("Fn + F9", "下一曲", "next"),
            ("Fn + F10", "静音", "mute"),
            ("Fn + F11", "音量 -", "voldown"),
            ("Fn + F12", "音量 +", "volup"),
            ("Fn + Ctrl", "Control", None),
            ("Fn + Win", "Option", None),
            ("Fn + Alt", "Command", None),
        ]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        tip = QLabel(
            "「执行」按钮会真的按下对应的多媒体键（计算器 / 我的电脑 / 邮件 / 主页 / "
            "播放 / 音量 / 上下曲）。Fn 组合键属于键盘固件内部行为，电脑无法代按，"
            "所以那些行没有按钮。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            "color:#8C7480; background:#FFF4F8; border:1px solid #F7DFE8;"
            "border-radius:10px; padding:8px 12px; font-size:11.5px;"
        )
        outer.addWidget(tip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 2, 10, 0)
        grid.setSpacing(12)
        for i, (title, color, rows) in enumerate(self.SECTIONS):
            grid.addWidget(self._card(title, color, rows), i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setAlignment(Qt.AlignTop)
        scroll.setWidget(holder)
        outer.addWidget(scroll)

    def _tap(self, key: str) -> None:
        """模拟系统多媒体键 / 快捷方式（Win32 keybd_event）。"""
        import ctypes
        u = ctypes.windll.user32
        vk = self.VK.get(key)
        if vk is None:
            return
        if vk < 0:
            # 「我的电脑」没有可靠的多媒体 VK，用 Win+E 最稳（打开资源管理器）
            u.keybd_event(0x5B, 0, 0, 0)     # LWIN down
            u.keybd_event(0x45, 0, 0, 0)     # E down
            u.keybd_event(0x45, 0, 2, 0)     # E up
            u.keybd_event(0x5B, 0, 2, 0)     # LWIN up
            return
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, 2, 0)

    def _card(self, title: str, color: str, rows) -> QFrame:
        f = QFrame()
        f.setObjectName("card")
        v = QVBoxLayout(f)
        v.setContentsMargins(16, 13, 16, 13)
        v.setSpacing(5)
        h = QLabel(title)
        h.setObjectName("h2")
        h.setStyleSheet(f"color:{color};")
        v.addWidget(h)
        v.addSpacing(2)
        for key, desc, action in rows:
            r = QHBoxLayout()
            r.setSpacing(8)
            k = QLabel(key)
            k.setFixedWidth(122)
            k.setAlignment(Qt.AlignCenter)
            k.setStyleSheet(
                "background:#FFF4F8; border:1px solid #EFCAD9; border-radius:6px;"
                "padding:2px 4px; color:#B33A66; font-size:11px; font-weight:600;"
            )
            d = QLabel(desc)
            # 固定宽度让「执行」按钮紧跟文字，否则描述会撑满整行把按钮推到最右边，
            # 中间留一大片空白，看起来像两列不相干的内容
            d.setFixedWidth(238)
            d.setStyleSheet("color:#8C7480; font-size:12px;")
            d.setWordWrap(True)
            r.addWidget(k)
            r.addWidget(d)
            if action:
                b = QPushButton("执行")
                # 必须显式 padding:0 —— 全局 QSS 的 QPushButton 有 padding:8px 12px，
                # 套到这么小的按钮上会把内容区压成 4px 高，文字整个被裁掉（看起来是空白框）
                b.setFixedSize(52, 24)
                b.setCursor(Qt.PointingHandCursor)
                b.setStyleSheet(
                    "QPushButton{background:#FFFFFF;border:1px solid #FF7FA8;"
                    "border-radius:7px;color:#B33A66;font-size:11px;padding:0;}"
                    "QPushButton:hover{background:#FFEAF2;}"
                )
                b.setToolTip(f"模拟按下这个键（{desc}）")
                b.clicked.connect(lambda _=False, a=action: self._tap(a))
                r.addWidget(b)
            r.addStretch(1)
            v.addLayout(r)
        return f


# ---------------------------------------------------------------- 主窗口

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} · {APP_NAME_CN}")
        self.setWindowIcon(make_app_icon())
        self.resize(1240, 860)

        self.backend = B.open_backend()
        self._last_led: LedState | None = None
        self._last_sled: LedState | None = None

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = Sidebar(self._nav)
        outer.addWidget(self.sidebar)

        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(18, 16, 18, 16)
        ml.setSpacing(13)
        outer.addWidget(main, 1)

        self.topbar = TopBar(self._toggle_lock)
        ml.addWidget(self.topbar)

        self.kbview = KeyboardView()
        self.kbview.setMinimumHeight(292)
        self.kbview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        ml.addWidget(self.kbview, 6)

        self.stack = QStackedWidget()
        self.page_led = EffectsPage(self._apply_main, "主键盘灯效")
        self.page_sled = EffectsPage(self._apply_side, "侧面灯条")
        self.page_music = MusicPage()
        self.page_music.backend = self.backend
        self.page_perkey = PerKeyPage()
        self.page_keymap = KeymapPage()
        self.page_conn = ConnectionPage()
        self.page_about = AboutPage()
        for w in (self.page_led, self.page_sled, self.page_music, self.page_perkey,
                  self.page_keymap, self.page_conn, self.page_about):
            self.stack.addWidget(w)
        ml.addWidget(self.stack, 5)

        # 只读 OR 这条通道压根不能配置（2.4G）→ 都当只读，控件灰掉，
        # 免得用户在不能写的通道上白点半天
        ro = (bool(getattr(self.backend, "readonly", False))
              or not getattr(self.backend, "config_ok", True))
        self.page_led.set_readonly(ro)
        self.page_sled.set_readonly(ro)
        self.page_music.set_readonly(ro)

        self._setup_tray()
        self._reload()

        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self._refresh)
        self.timer.start()

        # 注：不做轮询。多数场景是无线使用，定期查询会不断唤醒键盘、额外耗电，
        # 收益（键盘上切模式后界面自动同步）不值得这个代价。
        # 界面上的灯效状态在启动时读一次，之后以用户操作为准。

    # ---- 只读锁
    def _toggle_lock(self) -> None:
        b = self.backend
        if b.name != "hid":
            return
        if not getattr(b, "config_ok", True):
            # 插上线之后不用重启软件：这个按钮顺便做一次重连探测
            if not b.reconnect_config():
                QMessageBox.information(
                    self, "这条通道还是不转发配置",
                    "键盘是连上的，但当前的连接方式不转发配置命令：\n"
                    "读取只返回全零帧或接收器缓存的旧数据，写入也到不了键盘。\n\n"
                    "插上 Type-C 数据线、切到有线模式（长按 Fn+5 约 3 秒）后，"
                    "再点一次这个按钮就能恢复，不用重启软件。\n\n"
                    "日常打字、打游戏不受影响。",
                )
                return
            QMessageBox.information(self, "配置已恢复",
                                    "检测到配置通道可用了，现在可以调灯效。")
            self._reload()
        locking = not getattr(b, "readonly", True)

        if locking:
            b.readonly = True
            b.policy.strict_readonly = True
            if b.kb:
                b.kb.readonly = True
            self.page_music.stop(restore=False)
            self.page_led.set_readonly(True)
            self.page_sled.set_readonly(True)
            self.page_music.set_readonly(True)
            self._refresh()
            return

        ok = QMessageBox.question(
            self, "解锁写入",
            "解锁后可以向键盘写入灯效参数。\n\n"
            "· 只会修改灯效（模式/亮度/速度/方向/颜色）\n"
            "· 不会碰固件、改键、宏、配对\n"
            "· 万一灯效显示不对，按键盘 Fn+Tab 切一次模式即可恢复\n\n"
            "确定解锁吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ok != QMessageBox.Yes:
            return

        b.readonly = False
        b.policy.strict_readonly = False
        if b.kb:
            b.kb.readonly = False
        self.page_led.set_readonly(False)
        self.page_sled.set_readonly(False)
        self.page_music.set_readonly(False)
        self._reload()

    # ---- 导航
    def _nav(self, idx: int) -> None:
        self.sidebar.select(idx)
        self.stack.setCurrentIndex(idx)
        self.kbview.set_highlight("sled" if idx == 1 else "main")

    # ---- 数据
    def _reload(self) -> None:
        try:
            # 每条命令只读一次再分发：读取有 50ms 频控，连读两次第二次会被跳过并返回默认值
            led = self.backend.get_led()
            sled = self.backend.get_sled()
            if led is not None:
                self.page_led.load(led)
                self.kbview.load(led)
                self._last_led = led
            if sled is not None:
                self.page_sled.load(sled)
                self.kbview.load_sled(sled)
                self._last_sled = sled
            else:
                self.kbview.load_sled_unknown()
            if led is None:
                # 读不到就当"未知"，不要拿默认值冒充真实状态（否则界面会显示成"关闭"）
                self.kbview.load_unknown()
                self.page_led.current_lbl.setText("读不到当前灯效 · 见顶部设备状态")
                self.page_led.current_lbl.setStyleSheet(
                    f"color:{T.WARN}; font-size:11.5px;")
        except Exception as e:  # noqa: BLE001
            print("读取灯效失败:", e)
        self._refresh()

    def _refresh(self) -> None:
        self.topbar.update_status(self.backend)
        self.page_about.refresh(self.backend)
        self.page_conn.refresh(self.backend)
        if self.tray:
            self.tray.setToolTip(f"{APP_NAME} · {APP_NAME_CN}")

    def _apply_main(self, st: LedState) -> None:
        # 用户在灯光页主动改灯效，说明不想看律动了 —— 让音乐收手，别继续抢控制权。
        # restore=False：用户的目标状态已经是眼前这个，不需要再恢复旧值。
        if self.page_music.running and st.mode not in P.AUDIO_MODES:
            self.page_music.stop(restore=False)
        self._do(self.backend.set_led, st, self.kbview.load)

    def _apply_side(self, st: LedState) -> None:
        self._do(self.backend.set_sled, st, self.kbview.load_sled)

    def _do(self, fn, st: LedState, preview) -> None:
        try:
            fn(st)
            preview(st)
        except Exception as e:  # noqa: BLE001
            print("写入失败:", e)
        self._refresh()

    # ---- 托盘
    def _setup_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(make_app_icon(), self)
        menu = QMenu()
        a = QAction("显示主窗口", self)
        a.triggered.connect(self._show_window)
        menu.addAction(a)
        sub = menu.addMenu("快捷灯效")
        for mid in (0, 1, 2, 4, 16):
            act = QAction(P.LED_MODES[mid], self)
            act.triggered.connect(lambda _=False, m=mid: self._quick(m))
            sub.addAction(act)
        menu.addSeparator()
        b = QAction("刷新状态", self)
        b.triggered.connect(self._refresh)
        menu.addAction(b)
        c = QAction("退出", self)
        c.triggered.connect(QApplication.quit)
        menu.addAction(c)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda r: self._show_window() if r == QSystemTrayIcon.DoubleClick else None
        )
        self.tray.show()

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quick(self, mid: int) -> None:
        try:
            st = self.backend.get_led() or self._last_led
            if st is None:
                # 读不到就用默认值兜底，只改模式，别把亮度/颜色写成瞎猜的
                st = LedState(mode=mid)
            else:
                st.mode = mid
            self.backend.set_led(st)
            self.page_led.load(st)
            self.kbview.load(st)
        except Exception as e:  # noqa: BLE001
            print("快捷切换失败:", e)

    def closeEvent(self, ev) -> None:
        if self.tray:
            ev.ignore()
            self.hide()
            self.tray.showMessage(APP_NAME, "已最小化到托盘。",
                                  QSystemTrayIcon.Information, 1800)
        else:
            super().closeEvent(ev)

    def shutdown(self) -> None:
        """真正退出前的收尾：停掉音频线程并把灯效还原，别让键盘卡在音乐模式。"""
        try:
            if self.page_music.running:
                self.page_music.stop(restore=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.backend.disconnect()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    app = QApplication(sys.argv)
    apply_style(app)
    app.setQuitOnLastWindowClosed(False)
    w = MainWindow()
    app.aboutToQuit.connect(w.shutdown)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
