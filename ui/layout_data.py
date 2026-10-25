"""
108 键全尺寸 ANSI 布局坐标（单位 = 1u 键宽）。

坐标系：x 向右、y 向下，行高恒为 1u。
主键区 0~14.25，功能/编辑区 15.25~18.25，数字区 18.5~22.5。
"""

from __future__ import annotations

from dataclasses import dataclass

UNIT = 1.0
GAP = 0.10          # 键间缝隙（u）
RADIUS = 0.16       # 圆角半径（u）

TOTAL_W = 22.5
TOTAL_H = 6.0


@dataclass(frozen=True)
class Key:
    name: str
    x: float
    y: float
    w: float = 1.0
    h: float = 1.0
    zone: str = "main"      # main / func / edit / arrow / numpad / media
    width_class: int = 0    # 0=1u 1=1.25u 2=1.5u 3=1.75u 4=2u 5=2.25u 6=2.75u 7=大键


def _k(name, x, y, w=1.0, h=1.0, zone="main") -> Key:
    wc = 0
    if w >= 6.0:
        wc = 7
    elif w >= 2.5:
        wc = 6
    elif w >= 2.2:
        wc = 5
    elif w >= 1.7:
        wc = 4
    elif w >= 1.45:
        wc = 3
    elif w >= 1.2:
        wc = 1
    elif w > 1.0:
        wc = 2
    return Key(name, x, y, w, h, zone, wc)


KEYS: list[Key] = []


def _add(*keys: Key) -> None:
    KEYS.extend(keys)


# ---- 第 0 行：Esc + F1-F12 + PrtSc/ScrLk/Pause，数字区上方 4 个媒体键
_add(_k("Esc", 0, 0, zone="func"))

_f_row = [2, 3, 4, 5, 6.5, 7.5, 8.5, 9.5, 11, 12, 13, 14]
for i, fx in enumerate(_f_row, start=1):
    _add(_k(f"F{i}", fx, 0, zone="func"))

_add(
    _k("PrtSc", 15.25, 0, zone="edit"),
    _k("ScrLk", 16.25, 0, zone="edit"),
    _k("Pause", 17.25, 0, zone="edit"),
)
_add(
    _k("Calc", 18.5, 0, zone="media"),
    _k("Vol+", 19.5, 0, zone="media"),
    _k("Vol-", 20.5, 0, zone="media"),
    _k("Mute", 21.5, 0, zone="media"),
)

# ---- 第 1 行
_main1 = ["`", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="]
for i, name in enumerate(_main1):
    _add(_k(name, i, 1))
_add(_k("Backspace", 13, 1, 2.0))
_add(
    _k("Ins", 15.25, 1, zone="edit"),
    _k("Home", 16.25, 1, zone="edit"),
    _k("PgUp", 17.25, 1, zone="edit"),
)
_add(
    _k("Num", 18.5, 1, zone="numpad"),
    _k("/", 19.5, 1, zone="numpad"),
    _k("*", 20.5, 1, zone="numpad"),
    _k("-", 21.5, 1, zone="numpad"),
)

# ---- 第 2 行
_add(_k("Tab", 0, 2, 1.5))
for i, name in enumerate(list("QWERTYUIOP")):
    _add(_k(name, 1.5 + i, 2))
_add(_k("[", 11.5, 2), _k("]", 12.5, 2), _k("\\", 13.5, 2, 1.5))
_add(
    _k("Del", 15.25, 2, zone="edit"),
    _k("End", 16.25, 2, zone="edit"),
    _k("PgDn", 17.25, 2, zone="edit"),
)
for i, name in enumerate(["7", "8", "9"]):
    _add(_k(name, 18.5 + i, 2, zone="numpad"))
_add(_k("+", 21.5, 2, 1.0, 2.0, zone="numpad"))

# ---- 第 3 行
_add(_k("Caps", 0, 3, 1.75))
for i, name in enumerate(list("ASDFGHJKL")):
    _add(_k(name, 1.75 + i, 3))
_add(_k(";", 10.75, 3), _k("'", 11.75, 3), _k("Enter", 12.75, 3, 2.25))
for i, name in enumerate(["4", "5", "6"]):
    _add(_k(name, 18.5 + i, 3, zone="numpad"))

# ---- 第 4 行
_add(_k("LShift", 0, 4, 2.25))
for i, name in enumerate(list("ZXCVBNM")):
    _add(_k(name, 2.25 + i, 4))
_add(_k(",", 9.25, 4), _k(".", 10.25, 4), _k("/", 11.25, 4))
_add(_k("RShift", 12.25, 4, 2.75))
_add(_k("Up", 16.25, 4, zone="arrow"))
for i, name in enumerate(["1", "2", "3"]):
    _add(_k(name, 18.5 + i, 4, zone="numpad"))
_add(_k("Enter", 21.5, 4, 1.0, 2.0, zone="numpad"))

# ---- 第 5 行
_add(_k("LCtrl", 0, 5, 1.25))
_add(_k("Win", 1.25, 5, 1.25))
_add(_k("LAlt", 2.5, 5, 1.25))
_add(_k("Space", 3.75, 5, 6.25))
_add(_k("RAlt", 10, 5, 1.25))
_add(_k("Fn", 11.25, 5, 1.25))
_add(_k("Menu", 12.5, 5, 1.25))
_add(_k("RCtrl", 13.75, 5, 1.25))
_add(
    _k("Left", 15.25, 5, zone="arrow"),
    _k("Down", 16.25, 5, zone="arrow"),
    _k("Right", 17.25, 5, zone="arrow"),
)
_add(_k("0", 18.5, 5, 2.0, zone="numpad"))
_add(_k(".", 20.5, 5, zone="numpad"))


def by_zone(zone: str) -> list[Key]:
    return [k for k in KEYS if k.zone == zone]


if __name__ == "__main__":
    print(f"共 {len(KEYS)} 个键")
    from collections import Counter
    print(Counter(k.zone for k in KEYS))
    xs = [k.x + k.w for k in KEYS]
    ys = [k.y + k.h for k in KEYS]
    print(f"最大 x={max(xs)} 最大 y={max(ys)}")
