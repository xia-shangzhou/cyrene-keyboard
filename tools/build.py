"""
打包成 exe（onedir，文件夹 + exe）。

    python tools/build.py            # 打包
    python tools/build.py --zip      # 打包完顺便压成 zip（带"创建快捷方式.vbs"）

产物：
    build/exe/CyreneKeyboard/CyreneKeyboard.exe   ← 直接跑这个
    dist/CyreneKeyboard-v<版本>-win64.zip         ← --zip 时生成

为什么用 onedir 而不是 onefile
------------------------------
`--onefile` 的做法是"运行时把自己解压到 %TEMP% 再起子进程"，
这个行为和加壳恶意软件高度相似，Defender / SmartScreen / SAC 会重点照顾，
而且每次启动都要解压 100MB+，慢且占临时空间。
`--onedir` 就是一个 exe + 一堆 dll，直接加载，误报率低得多。

⚠ 未签名的 exe 首次运行仍可能弹"Windows 已保护你的电脑"（SmartScreen），
   用户点「更多信息 → 仍要运行」即可。这不是签名的替代品。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb.update import APP_VERSION   # noqa: E402

APP_NAME = "CyreneKeyboard"
# 每次打到干净目录，避免 PyInstaller 去删旧产物（删几百个文件既慢又容易被安全软件拦）
OUT = ROOT / "build" / "pyi-dist"
DIST = ROOT / "dist"

# 排除用不到的 Qt 模块，能把体积压下来一大截。
# ⚠ 千万别排 numpy —— soundcard 依赖它，排掉以后音乐律动会直接不可用。
EXCLUDES = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "tkinter", "matplotlib", "PIL", "pytest", "IPython", "notebook",
]

SHORTCUT_VBS = """' 双击本文件，在桌面创建「Cyrene Keyboard」快捷方式
Option Explicit
Dim sh, fso, base, exe, lnk
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
exe = base & "\\CyreneKeyboard\\CyreneKeyboard.exe"
If Not fso.FileExists(exe) Then
    MsgBox "找不到程序文件，请先完整解压整个压缩包。" & vbCrLf & exe, 16, "Cyrene Keyboard"
    WScript.Quit 1
End If
lnk = sh.SpecialFolders("Desktop") & "\\Cyrene Keyboard.lnk"
Dim s
Set s = sh.CreateShortcut(lnk)
s.TargetPath = exe
s.WorkingDirectory = base & "\\CyreneKeyboard"
s.IconLocation = exe & ",0"
s.Description = "昔涟键盘控制台"
s.Save
MsgBox "已在桌面创建快捷方式。" & vbCrLf & vbCrLf & lnk, 64, "Cyrene Keyboard"
"""

README_TXT = """Cyrene Keyboard v{ver}
================================

【怎么用】
1. 双击「创建快捷方式.vbs」，桌面会出现 Cyrene Keyboard 图标（只需做一次）
2. 以后从桌面图标启动；也可以直接双击 CyreneKeyboard\\CyreneKeyboard.exe
3. 整个文件夹可以放到任意位置，但**别只拷 exe**，旁边的 _internal 是运行必需的

【首次运行提示"未知发布者"】
这是正常的 —— 程序没有买代码签名证书。
点「更多信息」→「仍要运行」即可，之后不会再提示。

【删掉就是卸载】
不写注册表、不装服务。删掉整个文件夹 + 桌面快捷方式即可。

【连接方式】
USB 有线 / 蓝牙：可以调灯效
2.4G 无线：只能打字，调不了灯（接收器不转发灯效指令）

【电量】
按键盘 Fn + Backspace 看，数字键亮几颗就是几十%。

有问题欢迎在 GitHub 提 Issue：
https://github.com/xia-shangzhou/cyrene-keyboard/issues
"""


def write_gbk(path: Path, text: str) -> None:
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("gbk", "replace"))


def build() -> int:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--windowed",
        "--name", APP_NAME,
        "--distpath", str(OUT),
        "--workpath", str(ROOT / "build" / "pyi-work"),
        "--specpath", str(ROOT / "build"),
        "--hidden-import", "hid",
        "--paths", str(ROOT),
        # 图标跟着走，否则 exe 会用默认图标
        "--add-data", f"{ROOT / 'assets'}{__import__('os').pathsep}assets",
    ]
    for m in EXCLUDES:
        args += ["--exclude-module", m]

    icon = ROOT / "assets" / "icon.ico"
    if icon.exists():
        args += ["--icon", str(icon)]

    args.append(str(ROOT / "main.py"))

    print("开始打包（几分钟，第一次会更久）…")
    return subprocess.call(args, cwd=str(ROOT))


def package() -> Path | None:
    app_dir = OUT / APP_NAME
    exe = app_dir / f"{APP_NAME}.exe"
    if not exe.exists():
        print(f"[错误] 没找到 {exe}")
        return None

    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / f"CyreneKeyboard-v{APP_VERSION}-win64.zip"
    if out.exists():
        out.unlink()

    write_gbk(app_dir.parent / "创建快捷方式.vbs", SHORTCUT_VBS)
    write_gbk(app_dir.parent / "使用说明.txt", README_TXT.format(ver=APP_VERSION))

    top = "CyreneKeyboard-win64"
    total = 0
    print(f"压缩 → {out.name}（文件多，慢一点）")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(app_dir.parent.rglob("*")):
            if f.is_dir():
                continue
            total += f.stat().st_size
            z.write(f, f"{top}/{f.relative_to(app_dir.parent).as_posix()}")
    print(f"  原始 {total / 1024 / 1024:.0f} MB → 压缩后 {out.stat().st_size / 1024 / 1024:.0f} MB")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true", help="打包后压成 zip")
    ap.add_argument("--skip-build", action="store_true", help="跳过 PyInstaller，只重新压 zip")
    args = ap.parse_args()

    print("=" * 66)
    print(f"打包 exe v{APP_VERSION}（onedir）")
    print("=" * 66)

    if not args.skip_build:
        rc = build()
        if rc != 0:
            print(f"\n[失败] PyInstaller 返回 {rc}")
            return rc
        exe = OUT / APP_NAME / f"{APP_NAME}.exe"
        if exe.exists():
            print(f"\n✅ 生成: {exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)")
            d = OUT / APP_NAME
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            print(f"   整个文件夹: {size / 1024 / 1024:.0f} MB")

    if args.zip:
        out = package()
        if out:
            print(f"\n✅ 分发包: {out}")
            print("   解压后先双击「创建快捷方式.vbs」")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
