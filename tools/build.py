"""
打包成 exe（onedir，文件夹 + exe）。

    python tools/build.py            # 打包
    python tools/build.py --zip      # 打包完顺便压成 zip（说明放进 CyreneKeyboard/ 里）

产物：
    build/pyi-<时间戳>/CyreneKeyboard/CyreneKeyboard.exe   ← 直接跑这个
    dist/CyreneKeyboard-v<版本>-win64.zip                   ← --zip 时生成

为什么是"文件夹里塞一份说明"而不是桌面快捷方式 vbs
----------------------------------------------------
vbs 建快捷方式要在对方机器上跑 WScript.Shell，而 `cscript`/`WScript.Shell` 恰好是
安全软件重点盯的 LOLBin，被杀软按脚本拦截的概率比程序本身还高；而且本机
（作者机器）策略就把 cscript 拦了，等于发出去的东西自己都没跑过一遍。
→ 只放一个 GBK+CRLF 的纯文本说明在 exe 旁边，双击 exe 就能用，没有第二层依赖。

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
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb.update import APP_VERSION   # noqa: E402

APP_NAME = "CyreneKeyboard"
# 每次打到**新的**目录：PyInstaller 的 COLLECT 会先删旧产物，
# 一次几百个文件既慢又容易被安全软件/沙箱的"批量删除"规则拦下。
# 代价是 build/ 下会留几份旧产物（已在 .gitignore 里，可随时手删）。
_OUT_STAMP = time.strftime("%m%d-%H%M%S")
OUT = ROOT / "build" / f"pyi-{_OUT_STAMP}"
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

README_TXT = """Cyrene Keyboard v{ver} · 使用说明
================================

【风险提示（请先读）】
这是个人自制的第三方工具，不是官方软件，与厂商、游戏公司都没有关系。
它通过逆向出来的厂商协议直接给键盘发灯效参数，协议没有官方文档背书：
· 长时间最高亮度、极限速度、音乐律动连续推流，都属于超出日常使用的负载，
  长期这么玩有加快灯珠/控制芯片老化的可能；
· 因使用本程序导致的硬件异常、灯效错乱、键盘损坏或其他后果，由使用者自行负责，
  作者不提供担保，也不负责赔偿或维修。
程序不会刷固件、不改键、不写宏、不动配对，这些命令在代码里是硬拦截的。
想保守一点：别长时间开最高亮度、律动别整天挂着，觉得不对劲就停掉程序，
用键盘自己的 Fn 组合键恢复出厂灯效。

【怎么用】
1. 打开文件夹，双击 CyreneKeyboard.exe 就能用，不需要安装
2. 想放桌面：右键 CyreneKeyboard.exe →「发送到」→「桌面快捷方式」（只需做一次）
3. 整个 CyreneKeyboard 文件夹可以放到任意位置，但**别只拷 exe**，
   旁边的 _internal 文件夹是运行必需的，少了打不开

【首次运行提示"未知发布者"】
这是正常的 —— 程序没有买代码签名证书。
点「更多信息」→「仍要运行」即可，之后不会再提示。

【如果 exe 跑不起来（很可能，先看这段）】
Windows 11 的「智能应用控制」一旦处于强制模式，没签名的 exe 会被直接拒绝，
提示「应用程序控制策略已阻止此文件」，而且没有「仍要运行」可以点。
这不是误报、也不是文件损坏，是系统策略：每个新版本的 exe 都是全新文件、
没有信誉积累，必然会被拦。

唯一的解决办法：改用同一个发布页上的 **CyreneKeyboard-Portable-v{ver}.zip**，
功能完全一样，解压后双击「启动.bat」即可，几乎不会被安全软件拦。
下载页：https://github.com/xia-shangzhou/cyrene-keyboard/releases/latest

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

    # 说明直接放进 exe 所在文件夹：双击 exe 就能用，不需要先跑脚本建快捷方式。
    # （vbs 建快捷方式要 WScript.Shell，正是杀软重点拦的 LOLBin，发出去自己也验证不了）
    write_gbk(app_dir / "使用说明.txt", README_TXT.format(ver=APP_VERSION))

    # zip 根目录就叫 CyreneKeyboard/，少一层 "xxx-win64/CyreneKeyboard" 的套娃
    total = 0
    print(f"压缩 → {out.name}（文件多，慢一点）")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(app_dir.rglob("*")):
            if f.is_dir():
                continue
            total += f.stat().st_size
            z.write(f, f.relative_to(app_dir.parent).as_posix())
    print(f"  原始 {total / 1024 / 1024:.0f} MB → 压缩后 {out.stat().st_size / 1024 / 1024:.0f} MB")
    return out


def main() -> int:
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true", help="打包后压成 zip")
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过 PyInstaller，只重新压 zip（自动用最近一次的 build/pyi-*）")
    args = ap.parse_args()

    if args.skip_build:
        # 目录名带时间戳，所以"只重压"必须显式找回最近一次产物，否则会算出一个空目录
        cands = sorted(
            (p for p in (ROOT / "build").glob("pyi-*")
             if (p / APP_NAME / f"{APP_NAME}.exe").exists()),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if not cands:
            print("[错误] build/pyi-* 里没有可复用的产物，去掉 --skip-build 重跑")
            return 2
        OUT = cands[0]
        print(f"复用已有产物：{OUT}")

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
            print("   解压后进 CyreneKeyboard 文件夹，双击 CyreneKeyboard.exe（说明书就在旁边）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
