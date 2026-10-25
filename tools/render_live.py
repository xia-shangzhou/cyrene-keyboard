"""用真实键盘（只读）渲染界面截图，验证读取链路与界面一致。"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

import kb.backend as B  # noqa: E402
from kb.backend import HidBackend  # noqa: E402

hb = HidBackend(readonly=True)      # 只读，绝不写入
hb.connect()
print(f"已连接真机（只读={hb.readonly}）")
B.open_backend = lambda prefer="auto": hb  # noqa: ARG005

from ui.app import MainWindow, apply_style  # noqa: E402

app = QApplication([])
apply_style(app)
w = MainWindow()
w.resize(1240, 860)
w.show()
for _ in range(8):                  # 让灯效动画跑几帧
    app.processEvents()
    time.sleep(0.04)

out = ROOT / "docs"
out.mkdir(exist_ok=True)

# 截图用：只读模式会把灯效卡片整块置灰（Qt 禁用态），缩略图变成灰条纹，很难看。
# 这里只把控件**启用**用于渲染，`hb.readonly` 仍是 True，全程不会写键盘。
w.page_led.set_readonly(False)
w.page_sled.set_readonly(False)

for i, name in enumerate(["灯光", "侧灯", "律动", "逐键", "快捷键", "连接", "关于"]):
    w._nav(i)
    if name == "律动":
        # 静态截图里塞一组假电平，好看清柱子长什么样（不涉及任何键盘写入）
        w.page_music.bars.set_levels([0.92, 0.74, 0.58, 0.86, 0.66, 0.44, 0.70, 0.33])
        w.page_music.state_lbl.setText("运行中 · 主灯已切到音乐律动条")
        w.page_music.state_lbl.setStyleSheet("color:#2DBE8A; font-size:11.5px;")
        w.page_music.stat_lbl.setText(
            "已推 1420 帧 · 50 fps\n"
            "峰值 -26 dB · 自动增益 +12.4 dB\n"
            "flash 写入 2 次（流式帧不写 flash）")
        w.page_music._refresh_btn()
    for _ in range(4):
        app.processEvents()
        time.sleep(0.03)
    p = out / f"live_{name}.png"
    w.grab().save(str(p))
    print("saved", p.name)

    # 快捷键页很长，再拍一张加高版（一次看全五张卡片），以及一张滚到底的
    if name == "快捷键":
        w.kbview.setMaximumHeight(300)   # 不然键盘区会被拉伸，上面留一大片空白
        w.resize(1240, 1460)
        for _ in range(4):
            app.processEvents()
            time.sleep(0.04)
        p3 = out / "live_快捷键_全.png"
        w.grab().save(str(p3))
        print("saved", p3.name)
        w.kbview.setMaximumHeight(16777215)
        w.resize(1240, 860)

        sa = w.page_keymap.findChild(QScrollArea)
        if sa:
            bar = sa.verticalScrollBar()
            bar.setValue(bar.maximum())
            for _ in range(4):
                app.processEvents()
                time.sleep(0.03)
            p2 = out / "live_快捷键_下.png"
            w.grab().save(str(p2))
            print("saved", p2.name)
            bar.setValue(0)

st, sl, bat = hb.get_led(), hb.get_sled(), hb.get_battery()
st_name = st.mode_name if st else "读取不可用"
sl_name = sl.mode_name if sl else "读取不可用"
print(f"\n后端状态：主灯 {st_name} / 侧灯 {sl_name} / 电量 {bat.label}")
print(f"通道：{hb.device_label}")
print(hb.policy.summary())
hb.disconnect()
