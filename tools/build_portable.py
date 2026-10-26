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
import subprocess
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

# 唯一启动器：双击后立刻退出，不留命令行窗口。
# 关键在 `start "" pythonw.exe` —— pythonw 没有控制台，start 又不等待子进程，
# 所以 bat 自身秒退、程序独立存活。这是 v0.3 验证过的形态。
#
# ⚠ 两个坑都在 2026-10-26 由主人实测反馈后回滚掉，别再犯：
#   · 用 `python.exe` 前台跑 → 控制台跟着进程走，那个黑窗口会一直开着
#   · 用 vbs + `sh.Run cmd, 0, False` → 那个 0 是「隐藏窗口」启动样式，
#     Qt 主窗口会继承它，表现为"后台有进程，但双击怎么都打不开"
LAUNCHER_BAT = r"""@echo off
chcp 936 >nul
cd /d "%~dp0"

if not exist "python\pythonw.exe" goto broken
if not exist "main.py" goto broken

start "" "python\pythonw.exe" "main.py"
exit /b 0

:broken
echo.
echo [错误] 程序文件不完整，请重新解压整个压缩包。
echo.
pause
exit /b 1
"""

# 给普通用户看的唯一入口：纯文本、GBK、CRLF，双击用记事本就能读。
# ⚠ 别用 .md —— Windows 上多半没有关联程序，普通人不会去点；
#   也别塞 emoji（GBK 编不进去，会变成问号）。
GUIDE_TXT = """Cyrene Keyboard v{ver} · 使用说明
================================

【这是什么】
给「昔涟 · 往昔的涟漪」108 键键盘做的灯光控制台。
不用安装，解压出来双击就能用。

【怎么启动】
1. 双击这个文件夹里的「启动.bat」
2. 会闪过一个黑框然后马上消失 —— 这是正常的，它在后台把程序拉起来
3. 等一两秒，程序窗口就出来了

想放桌面：右键「启动.bat」→ 发送到 → 桌面快捷方式（只需做一次）
注意：别只把「启动.bat」拷走，旁边的 python 文件夹是运行必需的。

【第一次打开】
· 默认是「只读」状态，不会动你的键盘
· 想调灯：点右上角「解锁」→ 二次确认 → 灯效就能点了
· 键盘要插 USB 线，或者切到蓝牙；2.4G 接收器调不了灯（下面有说明）

【关窗口不等于退出】
点右上角 × 只会缩到右下角托盘，灯效还在生效。
真要退出：右键右下角托盘里的小图标 → 退出。

【打不开怎么办】
1. 先确认整个文件夹是完整的（别只拷出一个文件）
2. 想看具体报错：
   在文件夹空白处按住 Shift 点右键 →「在此处打开 PowerShell 窗口」
   输入  .\\python\\python.exe main.py  然后回车
   红字报错复制出来发出来就能定位

【会被杀毒软件拦吗】
通常不会。这个包里没有 exe 文件，程序由官方签名的 Python 解释器执行，
所以一般不会触发 Windows 11「智能应用控制」对未签名程序的拦截。
注意：解释器有签名不等于本程序有签名。不同电脑的安全设置不一样，
不能保证每台都放行；如果被拦，请把提示截图发到 GitHub Issues。

【连接方式】
USB 有线：能调灯，音乐律动最跟手（50Hz）
蓝牙：能调灯，音乐律动默认 25Hz（也可以手动切到 50Hz）
2.4G 接收器：只能打字，调不了灯
  —— 是接收器固件只转发按键、不转发灯效指令，各种办法都试过，
     不是软件没做。想调灯就插线或者切到蓝牙。

【怎么看电量】
软件里不显示（三种连接里两种读不准）。
按键盘 Fn + Backspace，数字键亮几颗就是几十%。

【音乐律动】
抓电脑正在放的声音，变成键盘上的 8 列音柱。
· 只有 8 列，颜色是键盘固件内置的彩虹，改不了
· 延迟约 130 毫秒，听歌够用，卡点视频对不上
· 样式建议用「竖向柱」或「横向 · 从中间向两边」；
  「横向 · 从左向右」实测只亮左侧一小段（固件行为，软件改不了）

【风险提示（请先读）】
这是个人自制的第三方工具，不是官方软件，跟键盘厂商、游戏公司都没有关系。
协议是逆向出来的，没有官方文档，所以：
· 长时间最高亮度、极限速度、音乐律动连续推流，都属于超出日常使用的负载，
  长期这么玩有加快灯珠/控制芯片老化的可能
· 用出任何问题（灯效错乱、键盘损坏等）由你自己负责，作者不担保、不赔偿
程序不会刷固件、不改键、不写宏、不动配对，这些命令在代码里是硬拦截的。
觉得不对劲就停掉程序，用键盘自己的 Fn 组合键恢复灯效。

【删掉就是卸载】
不写注册表、不装服务、不开机自启。
删掉整个文件夹和桌面快捷方式就干净了。

下载新版 / 反馈问题：
https://github.com/xia-shangzhou/cyrene-keyboard
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
    # ⚠ 不复制 README.md：
    #   1) 那是给 GitHub 仓库看的，里面还引用了 docs/ 的截图，分发包里没有 docs/，
    #      打开就是一排裂图；
    #   2) 普通用户不会去点 .md 文件（Windows 可能根本没有关联程序）。
    #   用户看的入口统一为根目录的「使用说明.txt」，见 write_launchers()。
    print("  代码 main.py")


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
        # 不先 rmtree：一次删上千个文件会被沙箱/杀软的"批量删除"规则拦下（报
        # SAFE_DELETE_BULK_CONFIRM_REQUIRED，numpy 目录 957 个文件正好踩中）。
        # 依赖版本固定，直接覆盖写即可，重复跑还能自动补齐上次中断的残缺文件。
        shutil.copytree(s, d, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"  依赖 {name}")
    for name in SITE_FILES:
        s = src_sp / name
        if s.exists():
            shutil.copy2(s, SP_DST / name)
            print(f"  依赖 {name}")


def copy_licenses() -> None:
    """随附第三方许可证副本（LGPL-3.0 的硬性义务）。

    放到 site-packages/_licenses/：用户不会误点（也不破坏根目录三文件），
    但文件确实在分发包里，满足「随附许可证副本」。

    来源 `build/licenses/` 是**生成物**（`build/` 在 .gitignore 里），
    干净环境里必然不存在 —— 所以这里就地生成，不靠"记得先手动跑一次"。
    （2026-10-26 CI 首次实跑就是因为这个报「校验产物完整性」失败。）
    """
    if collect_licenses() != 0:
        print("  [警告] 许可证生成失败，分发包会缺 LGPL 副本")
        return
    src = ROOT / "build" / "licenses"
    if not src.exists():
        print(f"  [警告] {src} 仍然不存在")
        return
    dst = SP_DST / "_licenses"
    shutil.copytree(src, dst, dirs_exist_ok=True)
    n = sum(1 for f in dst.rglob("*") if f.is_file())
    print(f"  许可证 {n} 个文件 → site-packages/_licenses/")


def collect_licenses() -> int:
    """调用 tools/collect_licenses.py 生成 build/licenses/。"""
    script = ROOT / "tools" / "collect_licenses.py"
    r = subprocess.run([sys.executable, str(script)], cwd=str(ROOT),
                       capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        print((r.stderr or r.stdout or "")[-400:])
    return r.returncode


def write_launchers() -> None:
    write_text_gbk(PORTABLE / "启动.bat", LAUNCHER_BAT)
    write_text_gbk(PORTABLE / "使用说明.txt", GUIDE_TXT.format(ver=APP_VERSION))
    # 清掉历史版本留下的文件：两个旧启动器、旧 README.md（普通人不会点 .md，
    # 而且它引用的 docs/ 截图在分发包里根本不存在）
    for stale in ("双击启动.vbs", "如果打不开点我.bat", "运行.bat", "README.md"):
        old = PORTABLE / stale
        if old.exists():
            old.unlink()
            print(f"  清理 {stale}")
    print("  启动器 启动.bat（双击即启动，不留命令行窗口）")
    print("  说明 使用说明.txt（纯文本，双击用记事本看）")


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
    print("\n②b 随附许可证"); copy_licenses()
    print("\n③ 写启动器");   write_launchers()

    total = sum(f.stat().st_size for f in PORTABLE.rglob("*") if f.is_file())
    print(f"\n④ 目录体积：{total / 1024 / 1024:.0f} MB")

    if not args.no_zip:
        print("\n⑤ 压缩")
        out = make_zip()
        if out:
            print(f"  完成：{out}  ({out.stat().st_size / 1024 / 1024:.0f} MB)")
    print("\n下一步：解压出来双击「启动.bat」验一遍（应该闪一下就没有窗口，程序正常出现）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
