"""构建产物完整性校验（CI 和本地都能跑）。

检查 dist/ 下当前版本的两个包：关键文件在不在、版本号对不对、许可证有没有随附。
不做「能不能启动」的验证 —— exe 的启动受 Smart App Control 影响，得在真机上试。

用法：python tools/verify_packages.py
退出码 0 = 通过，1 = 有问题
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb.update import APP_VERSION   # noqa: E402

PORTABLE = ROOT / "dist" / f"CyreneKeyboard-Portable-v{APP_VERSION}.zip"
EXE_ZIP = ROOT / "dist" / f"CyreneKeyboard-v{APP_VERSION}-win64.zip"

problems: list[str] = []


def need(cond: bool, msg: str) -> None:
    print(("  [OK] " if cond else "  [!!] ") + msg)
    if not cond:
        problems.append(msg)


def mb(p: Path) -> str:
    return f"{p.stat().st_size / 1024 / 1024:.0f} MB"


def main() -> int:
    print(f"校验 v{APP_VERSION} 构建产物")

    print("\n[便携版]")
    need(PORTABLE.exists(), f"{PORTABLE.name} 存在" + (f" ({mb(PORTABLE)})" if PORTABLE.exists() else ""))
    if PORTABLE.exists():
        with zipfile.ZipFile(PORTABLE) as z:
            names = set(z.namelist())
            top = "CyreneKeyboard-Portable/"
            root = {n[len(top):] for n in names
                    if n.startswith(top) and n.count("/") == 1 and not n.endswith("/")}
            need(root == {"main.py", "使用说明.txt", "启动.bat"}, f"根目录三文件：{sorted(root)}")
            need(f"{top}python/pythonw.exe" in names, "内置 pythonw.exe")
            need(f"{top}python/main.py" not in names, "代码在根目录而非 python/ 内")
            for sub in ("kb", "ui"):
                cnt = sum(1 for n in names if n.startswith(f"{top}{sub}/") and n.endswith(".py"))
                need(cnt > 0, f"{sub}/ 有 {cnt} 个 .py")
            lic = f"{top}python/Lib/site-packages/_licenses/"
            for f in ("LICENSE", "THIRD_PARTY_LICENSES.md", "LGPL-3.0.txt"):
                need(lic + f in names, f"随附 {f}")
            guide = z.read(f"{top}使用说明.txt").decode("gbk", "replace")
            need(f"v{APP_VERSION}" in guide, f"使用说明.txt 版本号 v{APP_VERSION}")
            need("?" not in guide, "使用说明.txt 无编码乱码")

            # ★ 关键依赖必须在包里。缺一个就少一个功能，而且用户点下去才会发现：
            #   缺 soundcard/numpy → 音乐律动起不来；缺 hid → 连不上键盘；
            #   缺 PySide6 → 程序根本打不开。构建脚本缺包时只会打印一行警告，
            #   这里必须让发布直接失败（2026-10-26 加）。
            sp = f"{top}python/Lib/site-packages/"
            for dep in ("PySide6", "shiboken6", "numpy", "soundcard"):
                need(any(n.startswith(sp + dep + "/") for n in names), f"依赖 {dep} 在包内")
            need(any(n.startswith(sp + "hid.") and n.endswith(".pyd") for n in names),
                 "依赖 hid 在包内")
            need(f"{top}python/python313.zip" in names, "内置标准库 python313.zip")

    print("\n[exe 版]")
    need(EXE_ZIP.exists(), f"{EXE_ZIP.name} 存在" + (f" ({mb(EXE_ZIP)})" if EXE_ZIP.exists() else ""))
    if EXE_ZIP.exists():
        with zipfile.ZipFile(EXE_ZIP) as z:
            names = set(z.namelist())
            need("CyreneKeyboard/CyreneKeyboard.exe" in names, "含 CyreneKeyboard.exe")
            need(any(n.startswith("CyreneKeyboard/_internal/") for n in names), "含 _internal/")
            guide_name = "CyreneKeyboard/使用说明.txt"
            need(guide_name in names, "含 使用说明.txt")
            if guide_name in names:
                guide = z.read(guide_name).decode("gbk", "replace")
                need(f"v{APP_VERSION}" in guide, f"说明书版本号 v{APP_VERSION}")
            lic = "CyreneKeyboard/_internal/_licenses/"
            for f in ("LICENSE", "LGPL-3.0.txt"):
                need(lic + f in names, f"随附 {f}")

            # 关键依赖同样要验：PyInstaller 漏 hiddenimport 时 zip 照样"成功"
            internal = "CyreneKeyboard/_internal/"
            for dep in ("PySide6", "shiboken6", "numpy", "soundcard"):
                need(any(n.startswith(internal + dep + "/") for n in names), f"依赖 {dep} 在包内")
            need(any(n.startswith(internal + "hid.") and n.endswith(".pyd") for n in names),
                 "依赖 hid 在包内")

    print()
    if problems:
        print(f"[失败] {len(problems)} 项未通过")
        return 1
    print("[通过] 产物完整")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
