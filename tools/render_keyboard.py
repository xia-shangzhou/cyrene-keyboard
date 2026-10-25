"""单独渲染键盘视图，用于快速调色。"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout

from kb.backend import LedState
from ui.app import ensure_cjk_font
from ui.keyboard_view import KeyboardView

app = QApplication([])
ensure_cjk_font()

host = QWidget()
host.setStyleSheet("background:#FFF7FA;")
lay = QVBoxLayout(host)
lay.setContentsMargins(20, 20, 20, 20)

kv = KeyboardView()
lay.addWidget(kv)
host.resize(1200, 380)
host.show()

out = Path(__file__).resolve().parent.parent / "docs"
out.mkdir(exist_ok=True)

cases = [
    ("wave_blue", LedState(mode=4, speed=2, brightness=4, rgb=(90, 170, 255))),
    ("rainbow", LedState(mode=16, speed=2, brightness=4, rgb=(255, 0, 0))),
    ("breath_purple", LedState(mode=2, speed=2, brightness=4, rgb=(170, 120, 255))),
    ("ripple_pink", LedState(mode=5, speed=2, brightness=4, rgb=(255, 110, 190))),
]

for name, st in cases:
    kv.load(st)
    kv.load_sled(LedState(mode=st.mode, speed=2, brightness=3, rgb=st.rgb))
    kv._t = 1.7
    app.processEvents()
    p = out / f"kb_{name}.png"
    kv.grab().save(str(p))
    print("saved", p)
