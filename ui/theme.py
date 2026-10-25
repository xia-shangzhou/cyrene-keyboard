"""
浅色樱粉主题。

配色取自键盘实物：左侧粉色键帽、中间白色、右侧淡蓝，整体是奶白 + 樱粉。
浅色底上灯效会弱一些，所以灯效渲染改用「缝隙溢光 + 键帽染色」而不是加色发光。
"""

from __future__ import annotations

# 设计令牌
BG = "#FFF7FA"          # 页面底色：极浅粉
PANEL = "#FFEEF4"       # 侧栏
CARD = "#FFFFFF"        # 卡片
CARD_HI = "#FFF4F8"     # 卡片内控件
BORDER = "#F7DFE8"
BORDER_HI = "#EFCAD9"
TEXT = "#3B2A32"        # 主文字：暖调深褐，不用纯黑
TEXT_DIM = "#8C7480"
TEXT_MUTE = "#C0AAB4"
ACCENT = "#FF7FA8"      # 樱粉
ACCENT2 = "#A97BFF"     # 薰衣草紫
ACCENT3 = "#7FC8E8"     # 淡蓝，取自右侧键帽
OK = "#2DBE8A"
WARN = "#E8A23D"
DANGER = "#E8576E"

FONT_STACK = "__FONT__"

STYLE = f"""
QWidget {{
    font-family: {FONT_STACK};
    font-size: 13px;
    color: {TEXT};
    background: transparent;
}}
QMainWindow, QWidget#root {{ background: {BG}; }}

/* 侧栏 */
QWidget#sidebar {{
    background: {PANEL};
    border-right: 1px solid {BORDER};
}}
QPushButton#navBtn {{
    background: transparent; border: none; border-radius: 12px;
    color: {TEXT_DIM}; padding: 10px 4px; font-size: 11.5px;
}}
QPushButton#navBtn:hover {{ background: #FFE3ED; color: {TEXT}; }}
QPushButton#navBtn:checked {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255,127,168,0.24), stop:1 rgba(169,123,255,0.20));
    color: #C2436E; font-weight: 600;
}}

/* 卡片 */
QFrame#card {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 16px;
}}
QFrame#statCard {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #FFFFFF, stop:1 #FFF4F8);
    border: 1px solid {BORDER}; border-radius: 14px;
}}

/* 文字 */
QLabel#h1 {{ font-size: 17px; font-weight: 600; color: {TEXT}; }}
QLabel#h2 {{ font-size: 14px; font-weight: 600; color: {TEXT}; }}
QLabel#caption {{ font-size: 11px; color: {TEXT_MUTE}; }}
QLabel#muted {{ font-size: 12px; color: {TEXT_DIM}; }}
QLabel#bigNum {{ font-size: 22px; font-weight: 700; color: {ACCENT}; }}
QLabel#accentNum {{ font-size: 22px; font-weight: 700; color: {ACCENT2}; }}

/* 按钮 */
QPushButton {{
    background: {CARD_HI}; border: 1px solid {BORDER_HI}; border-radius: 10px;
    padding: 8px 12px; color: #6C5560;
}}
QPushButton:hover {{ background: #FFEAF2; border-color: {ACCENT}; color: {TEXT}; }}
QPushButton:pressed {{ background: #FFE0EB; }}
QPushButton:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 rgba(255,127,168,0.30), stop:1 rgba(169,123,255,0.24));
    border: 1px solid {ACCENT}; color: #B33A66; font-weight: 600;
}}
QPushButton:disabled {{ color: {TEXT_MUTE}; background: #FBF4F7; border-color: #F2E4EA; }}
QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {ACCENT}, stop:1 {ACCENT2});
    border: none; color: #ffffff; font-weight: 600; padding: 9px 18px;
}}
QPushButton#ghost {{
    background: transparent; border: 1px solid {BORDER_HI}; color: {TEXT_DIM};
}}
QPushButton#danger {{
    background: #FFF2F4; border: 1px solid {DANGER}; color: {DANGER};
    font-weight: 600; padding: 9px 18px;
}}
QPushButton#danger:hover {{ background: #FFE4E9; }}

/* 灯效卡片 */
QToolButton {{
    background: {CARD_HI}; border: 1px solid {BORDER_HI}; border-radius: 12px;
    color: #6C5560; padding: 8px 6px; font-size: 12px;
}}
QToolButton:hover {{ background: #FFEAF2; border-color: {ACCENT}; color: {TEXT}; }}
QToolButton:checked {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 rgba(255,127,168,0.26), stop:1 rgba(169,123,255,0.18));
    border: 1px solid {ACCENT}; color: #B33A66; font-weight: 600;
}}
QToolButton:disabled {{ color: {TEXT_MUTE}; background: #FBF4F7; border-color: #F4E7EE; }}

/* 滑块 */
QSlider::groove:horizontal {{
    height: 5px; border-radius: 3px; background: #F7E4EC;
}}
QSlider::sub-page:horizontal {{
    height: 5px; border-radius: 3px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 {ACCENT}, stop:1 {ACCENT2});
}}
QSlider::handle:horizontal {{
    width: 15px; height: 15px; margin: -6px 0; border-radius: 8px;
    background: #ffffff; border: 2px solid {ACCENT};
}}
QSlider::handle:horizontal:hover {{ border-color: {ACCENT2}; }}

/* 下拉 */
QComboBox {{
    background: {CARD_HI}; border: 1px solid {BORDER_HI}; border-radius: 9px;
    padding: 6px 10px; min-width: 76px; color: #6C5560;
}}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: #ffffff; border: 1px solid {BORDER_HI};
    border-radius: 8px; selection-background-color: #FFE0EB;
    selection-color: {TEXT}; outline: none;
}}

/* 复选框 */
QCheckBox {{ spacing: 8px; color: #6C5560; }}
QCheckBox::indicator {{
    width: 17px; height: 17px; border-radius: 5px;
    border: 1px solid {BORDER_HI}; background: #ffffff;
}}
QCheckBox::indicator:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {ACCENT}, stop:1 {ACCENT2});
    border-color: {ACCENT};
}}

/* 滚动条 */
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: #F2D7E2; border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #E9C2D3; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* 文本域 */
QTextEdit {{
    background: {CARD_HI}; border: 1px solid {BORDER}; border-radius: 12px;
    color: #6C5560; padding: 10px; selection-background-color: #FFD3E3;
}}
/* 弹窗：不显式指定就会继承系统深色调色板，出现黑底灰字 */
QMessageBox, QDialog {{ background: #FFFFFF; }}
QMessageBox QLabel, QDialog QLabel {{ color: {TEXT}; font-size: 13px; }}
QMessageBox QPushButton {{
    background: {CARD_HI}; border: 1px solid {BORDER_HI}; border-radius: 9px;
    padding: 7px 20px; color: #6C5560; min-width: 74px;
}}
QMessageBox QPushButton:hover {{ background: #FFEAF2; border-color: {ACCENT}; }}
QMessageBox QPushButton:default {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2});
    border: none; color: #FFFFFF; font-weight: 600;
}}

/* 右键菜单 / 托盘菜单 */
QMenu {{
    background: #FFFFFF; border: 1px solid {BORDER_HI};
    border-radius: 10px; padding: 6px;
}}
QMenu::item {{
    padding: 7px 26px 7px 16px; border-radius: 7px; color: #6C5560;
    background: transparent;
}}
QMenu::item:selected {{ background: #FFEAF2; color: {TEXT}; }}
QMenu::item:disabled {{ color: {TEXT_MUTE}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 10px; }}
QMenu::icon {{ padding-left: 8px; }}

QToolTip {{
    background: #ffffff; color: {TEXT}; border: 1px solid {BORDER_HI};
    border-radius: 6px; padding: 5px 8px;
}}
"""


def build_qss(font_family: str = "") -> str:
    fam = f'"{font_family}", "Microsoft YaHei UI", "Segoe UI"' if font_family else \
          '"Microsoft YaHei UI", "Segoe UI"'
    return STYLE.replace(FONT_STACK, fam)
