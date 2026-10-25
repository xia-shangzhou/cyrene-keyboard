"""无界面渲染主窗口预览图，用于快速核对 UI。"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

from ui.app import MainWindow, apply_style

app = QApplication([])
fam = apply_style(app)
print("使用字体:", fam)
w = MainWindow()
w.resize(1040, 720)
w.show()
app.processEvents()

out = Path(__file__).resolve().parent.parent / "docs"
out.mkdir(exist_ok=True)

for i in range(w.stack.count()):
    w._nav(i)
    app.processEvents()
    name = ["灯光","侧灯","逐键","快捷键","连接","关于"][i]
    path = out / f"preview_{i}_{name}.png"
    w.grab().save(str(path))
    print("saved", path)
