"""收集随分发包一起分发的第三方许可证副本 → build/licenses/

为什么需要：便携版和 exe 版都捆绑了 PySide6（LGPL-3.0）、numpy 等第三方组件。
LGPL-3.0 要求在分发时**随附许可证副本**。原来的打包脚本只带上了 numpy/cffi 的
dist-info/licenses（因为它们是整目录复制的），PySide6 / shiboken6 的却漏了
（site-packages 里的 PySide6 是手工预置的，没有 dist-info）。

用法：python tools/collect_licenses.py
产出：build/licenses/<组件名>/<许可证文件>，以及项目自己的 LICENSE 与第三方声明。
"""

from __future__ import annotations

import importlib.metadata as md
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "licenses"

# 随分发包提供的组件（与 THIRD_PARTY_LICENSES.md 的表格一一对应）
DISTS = [
    "PySide6_Essentials",
    "PySide6_Addons",
    "shiboken6",
    "numpy",
    "soundcard",
    "hidapi",
    "cffi",
    "pycparser",
]

# 项目自己的许可证文件，一并放进包里（用户可能在离线环境下需要查看）
PROJECT_FILES = ["LICENSE", "THIRD_PARTY_LICENSES.md"]


def collect_dist(name: str) -> int:
    try:
        dist = md.distribution(name)
    except md.PackageNotFoundError:
        print(f"  [跳过] 未安装 {name}")
        return 0

    count = 0
    for f in dist.files or []:
        posix = str(f).replace("\\", "/")
        if "/licenses/" not in posix or posix.endswith("/"):
            continue
        src = Path(dist.locate_file(f))
        if not src.is_file():
            continue
        dst = OUT / name / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        count += 1
    if count == 0:
        print(f"  [警告] {name} {dist.version} 没有 dist-info/licenses 目录")
    else:
        print(f"  {name:20s} {dist.version:10s} {count} 个文件")
    return count


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("收集第三方许可证 →", OUT.relative_to(ROOT))
    total = sum(collect_dist(n) for n in DISTS)

    for name in PROJECT_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, OUT / name)
            print(f"  {name:20s} 项目文件")

    # 仓库 licenses/ 下的许可证正文。
    # 必须有：PySide6 的 wheel 只带了 LicenseRef-Qt-Commercial.txt，
    # LGPL-3.0 / GPL-3.0 正文并不随包发，得自己补，否则不满足 LGPL 的「随附副本」义务。
    extra = ROOT / "licenses"
    if extra.exists():
        for f in sorted(extra.glob("*.txt")):
            shutil.copy2(f, OUT / f.name)
            print(f"  {f.name:20s} 许可证正文")

    print(f"合计 {total} 个第三方许可证文件")
    if total == 0:
        print("[失败] 一个许可证都没收集到，检查依赖是否装在本环境")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
