"""
生成绿色便携版（解压即用，对方不用装 Python）。

为什么用这个方案而不是 exe
--------------------------
PyInstaller 出来的 exe 没有代码签名，Windows 的 Smart App Control（SAC，智能应用控制）
会把未签名、无声誉的可执行文件直接拦下，**用户无法为单个文件放行**，只能整体关掉 SAC
（而且关了以后基本不可逆）。所以这里借 Python 官方带签名的 `python.exe` 当启动器，
我们的代码是 .py 脚本（SAC 管不到 PE 之外的脚本），等于绕开了这道门。

用法
----
    python tools/build_portable.py              # 用已有的 build/portable/python
    python tools/build_portable.py --no-zip     # 只更新文件，不打包
    python tools/build_portable.py --clean      # 先删掉旧的 kb/ui 再复制

产物
----
    dist/CyreneKeyboard-Portable-v<版本>.zip
    解压后有一层 CyreneKeyboard-Portable/ 目录
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb.update import APP_VERSION   # noqa: E402

PORTABLE = ROOT / "build" / "portable"
PYDIR = PORTABLE / "python"
SP_DST = PYDIR / "Lib" / "site-packages"
DIST = ROOT / "dist"

# 只带运行必需的东西。WebEngine / Qml / 3D / Pdf 这些能省 100MB+，一律不要
SITE_DEPS = [
    ("soundcard", "soundcard"), ("soundcard-0.4.6.dist-info", "soundcard-0.4.6.dist-info"),
    ("numpy", "numpy"), ("numpy.libs", "numpy.libs"),
    ("numpy-2.5.3.dist-info", "numpy-2.5.3.dist-info"),
    ("cffi", "cffi"), ("cffi-2.1.1.dist-info", "cffi-2.1.1.dist-info"),
    ("pycparser", "pycparser"), ("pycparser-3.0.dist-info", "pycparser-3.0.dist-info"),
]
SITE_FILES = ["_cffi_backend.cp313-win_amd64.pyd", "hid.cp313-win_amd64.pyd"]

VBS = """' 双击这个文件启动昔涟键盘控制台（不会弹出黑色命令行窗口）
' 如果没反应，改用「如果打不开点我.bat」，那个窗口里能看到报错
Option Explicit
Dim sh, fso, base, py, mainpy, q, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
py = base & "\\python\\pythonw.exe"
mainpy = base & "\\main.py"
If Not fso.FileExists(py) Then
    MsgBox "程序文件不完整，请重新解压整个压缩包。" & vbCrLf & vbCrLf & _
           "缺少：" & py, 16, "昔涟键盘控制台"
    WScript.Quit 1
End If
If Not fso.FileExists(mainpy) Then
    MsgBox "程序文件不完整，请重新解压整个压缩包。" & vbCrLf & vbCrLf & _
           "缺少：" & mainpy, 16, "昔涟键盘控制台"
    WScript.Quit 1
End If
q = Chr(34)
cmd = q & py & q & " " & q & mainpy & q
sh.CurrentDirectory = base
sh.Run cmd, 0, False
"""

BAT = r"""@echo off
chcp 936 >nul
cd /d "%~dp0"
echo 正在启动昔涟键盘控制台（这个窗口会留在后台，关掉它等于退出程序）
echo.
if not exist "python\python.exe" goto broken
if not exist "main.py" goto broken
"python\python.exe" "main.py"
if errorlevel 1 (
    echo.
    echo [程序异常退出] 把上面的报错内容发出来就能定位问题。
    pause
)
exit /b 0

:broken
echo [错误] 程序文件不完整，请重新解压整个压缩包。
pause
exit /b 1
"""


def write_text_gbk(path: Path, text: str) -> None:
    """bat / vbs 必须 GBK + CRLF，否则中文乱码、批处理括号块会被解错（日志坑 9）。"""
    data = text.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(data.encode("gbk", errors="replace"))


def copy_code(clean: bool) -> None:
    for sub in ("kb", "ui"):
        dst = PORTABLE / sub
        if clean and dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True, exist_ok=True)
        for f in (ROOT / sub).glob("*.py"):
            shutil.copy2(f, dst / f.name)
        print(f"  代码 {sub}/  {len(list(dst.glob('*.py')))} 个 .py")
    shutil.copy2(ROOT / "main.py", PORTABLE / "main.py")
    # 图标必须跟着走，否则便携版会退回程序画的占位图标
    assets = ROOT / "assets"
    if assets.exists():
        dst = PORTABLE / "assets"
        dst.mkdir(parents=True, exist_ok=True)
        for f in assets.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)
        print(f"  资源 assets/  {len(list(dst.iterdir()))} 个文件")
    for extra in ("README.md", "工作日志.md"):
        src = ROOT / extra
        if src.exists():
            shutil.copy2(src, PORTABLE / extra)
    print("  代码 main.py / README.md")


def copy_deps() -> None:
    src_sp = Path(sys.prefix) / "Lib" / "site-packages"
    if not src_sp.exists():
        raise SystemExit(f"找不到源依赖目录：{src_sp}")
    SP_DST.mkdir(parents=True, exist_ok=True)
    for name, _same in SITE_DEPS:
        s, d = src_sp / name, SP_DST / name
        if not s.exists():
            print(f"  [跳过] 源环境里没有 {name}")
            continue
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(s, d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"  依赖 {name}")
    for name in SITE_FILES:
        s = src_sp / name
        if s.exists():
            shutil.copy2(s, SP_DST / name)
            print(f"  依赖 {name}")


def write_launchers() -> None:
    write_text_gbk(PORTABLE / "双击启动.vbs", VBS)
    write_text_gbk(PORTABLE / "如果打不开点我.bat", BAT)
    old = PORTABLE / "运行.bat"
    if old.exists():
        old.unlink()
    print("  启动器 双击启动.vbs / 如果打不开点我.bat")


def make_zip() -> Path | None:
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / f"CyreneKeyboard-Portable-v{APP_VERSION}.zip"
    if out.exists():
        out.unlink()
    top = "CyreneKeyboard-Portable"
    print(f"  打包 → {out.name}（这一步慢，文件多）")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(PORTABLE.rglob("*")):
            if f.is_dir():
                continue
            if "__pycache__" in f.parts or f.suffix == ".pyc":
                continue
            z.write(f, f"{top}/{f.relative_to(PORTABLE).as_posix()}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    print("=" * 68)
    print(f"打包便携版 v{APP_VERSION}")
    print("=" * 68)

    if not (PYDIR / "python.exe").exists():
        print(f"\n[错误] 没有嵌入式 Python：{PYDIR}")
        print("请先解压官方 python-3.13.12-embed-amd64.zip 到 build/portable/python/")
        return 2

    print("\n① 复制代码");   copy_code(args.clean)
    print("\n② 复制依赖");   copy_deps()
    print("\n③ 写启动器");   write_launchers()

    total = sum(f.stat().st_size for f in PORTABLE.rglob("*") if f.is_file())
    print(f"\n④ 目录体积：{total / 1024 / 1024:.0f} MB")

    if not args.no_zip:
        print("\n⑤ 压缩")
        out = make_zip()
        if out:
            print(f"  完成：{out}  ({out.stat().st_size / 1024 / 1024:.0f} MB)")
    print("\n下一步：解压出来双击「双击启动.vbs」验一遍。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
