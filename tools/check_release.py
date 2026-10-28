"""发布前最终校验：两个包是否都包含最新代码与最新文案。

便携版是源码形式，可以逐文件哈希对比工作区；exe 是编译产物，只能校验说明书/版本号。

**不需要解压任何包**：直接读 `dist/` 里的 zip 和工作区源码比对。
（原版在 `.workbuddy/_sactest/pre_release_check.py`，2026-10-27 搬到 tools/
并去掉硬编码版本号 —— 那时它写死 `VERSION = "0.4.3"`，升版本就失效。）

用法：
    python tools/check_release.py            # 校验 kb/update.py 里当前的 APP_VERSION
    python tools/check_release.py 0.4.3      # 校验指定版本（补查历史发布）
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb import update as U          # noqa: E402

VERSION = sys.argv[1].lstrip("vV") if len(sys.argv) > 1 else U.APP_VERSION

PORTABLE = ROOT / "dist" / f"CyreneKeyboard-Portable-v{VERSION}.zip"
EXE_ZIP = ROOT / "dist" / f"CyreneKeyboard-v{VERSION}-win64.zip"
NOTES = ROOT / ".workbuddy" / f"release-notes-v{VERSION}.md"

ok = True


def check(cond: bool, msg: str) -> None:
    global ok
    print(("  [OK] " if cond else "  [!!] ") + msg)
    if not cond:
        ok = False


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


print("=" * 66)
print(f"发布前校验 v{VERSION}（源：kb/update.py APP_VERSION = {U.APP_VERSION}）")
print("=" * 66)

if not PORTABLE.exists() or not EXE_ZIP.exists():
    print("\n包还没打出来，先跑：")
    print("  python tools/build_portable.py")
    print("  python tools/build.py --zip")
    raise SystemExit(2)

# ---------------------------------------------------------------- 便携版
print("\n① 便携版")
check(PORTABLE.exists(), f"文件存在 {PORTABLE.name} ({PORTABLE.stat().st_size/1024/1024:.0f} MB)"
      if PORTABLE.exists() else "文件缺失")

with zipfile.ZipFile(PORTABLE) as z:
    names = z.namelist()
    top = "CyreneKeyboard-Portable/"
    root = [n[len(top):] for n in names if n.startswith(top) and n.count("/") == 1 and not n.endswith("/")]
    check(sorted(root) == ["main.py", "使用说明.txt", "启动.bat"],
          f"根目录只有三个文件：{sorted(root)}")

    # 源码逐文件对比
    diff = []
    for sub in ("kb", "ui"):
        for f in sorted((ROOT / sub).glob("*.py")):
            arc = f"{top}{sub}/{f.name}"
            if arc not in z.namelist():
                diff.append(f"{sub}/{f.name} 缺失")
                continue
            if md5(z.read(arc)) != md5(f.read_bytes()):
                diff.append(f"{sub}/{f.name} 内容不同")
    check(not diff, "kb/ ui/ 下所有 .py 与工作区完全一致" if not diff else f"源码不一致: {diff}")
    check(f"{top}main.py" in names and
          md5(z.read(f"{top}main.py")) == md5((ROOT / "main.py").read_bytes()),
          "main.py 一致")

    guide = z.read(f"{top}使用说明.txt").decode("gbk")
    check(f"v{VERSION}" in guide, f"使用说明.txt 版本号 v{VERSION}")
    check("启动.bat" in guide and "README" not in guide, "说明里讲的是 启动.bat（没提 README）")
    check("?" not in guide, "GBK 无乱码问号")

    bat = z.read(f"{top}启动.bat").decode("gbk")
    check('start "" "python\\pythonw.exe" "main.py"' in bat, "启动.bat 用 start + pythonw")
    check("python.exe\"" not in bat.replace("pythonw.exe", ""), "启动.bat 没有前台 python.exe")

    ver_src = z.read(f"{top}kb/update.py").decode("utf-8")
    check(f'APP_VERSION = "{VERSION}"' in ver_src, f"包里 APP_VERSION = {VERSION}")

# ---------------------------------------------------------------- exe 版
print("\n② exe 版")
check(EXE_ZIP.exists(), f"文件存在 {EXE_ZIP.name} ({EXE_ZIP.stat().st_size/1024/1024:.0f} MB)"
      if EXE_ZIP.exists() else "文件缺失")
with zipfile.ZipFile(EXE_ZIP) as z:
    names = z.namelist()
    check("CyreneKeyboard/CyreneKeyboard.exe" in names, "含 CyreneKeyboard.exe")
    check("CyreneKeyboard/_internal/" in "".join(names), "含 _internal/ 依赖目录")
    guide = z.read("CyreneKeyboard/使用说明.txt").decode("gbk")
    check(f"v{VERSION}" in guide, f"说明书版本号 v{VERSION}")
    check("必然会被拦" in guide, "说明书写明 SAC 必然拦截")
    check("启动.bat" in guide and "vbs" not in guide, "说明书指向 启动.bat（不再提 vbs）")
    check("风险提示" in guide, "说明书含风险提示")

# ---------------------------------------------------------------- 发布物料
print("\n③ 发布物料")
check(NOTES.exists(), f"发布说明 {NOTES.name} 存在")
if NOTES.exists():
    body = NOTES.read_text(encoding="utf-8")
    check(f"CyreneKeyboard-v{VERSION}-win64.zip" in body, "说明里附件名带正确版本号")
    check(f"CyreneKeyboard-Portable-v{VERSION}.zip" in body, "说明里便携版名带正确版本号")
    check("启动.bat" in body, "说明里启动方式已更新")
    check("智能应用控制" in body, "说明里写了 SAC 限制")
    # 2026-10-27：固件兼容提醒——v0.4.4 漏了这一条，下个版本必须有。
    # 别只查「固件」两个字：v0.4.4 里「固件」出现了 4 次，全是在讲
    # 「不改固件」「固件决定灯效列数」，跟兼容提醒无关。要匹配具体措辞。
    check(any(k in body for k in ("升级过固件", "刷过固件", "出厂固件")),
          "说明里有固件兼容提醒（升级过固件的同款键盘可能不兼容）")

print("\n④ 版本号一致性")
src = (ROOT / "kb" / "update.py").read_text(encoding="utf-8")
check(f'APP_VERSION = "{VERSION}"' in src, f"源码 APP_VERSION = {VERSION}")

# ---------------------------------------------------------------- 开源合规
print("\n⑤ 开源合规")
check((ROOT / "LICENSE").exists(), "仓库有 LICENSE")
if (ROOT / "LICENSE").exists():
    first = (ROOT / "LICENSE").read_text(encoding="utf-8").strip().splitlines()[0].strip()
    check("AFFERO GENERAL PUBLIC LICENSE" in first, f"LICENSE 是 AGPL（首行：{first}）")
check((ROOT / "THIRD_PARTY_LICENSES.md").exists(), "仓库有 THIRD_PARTY_LICENSES.md")
check((ROOT / "requirements.txt").exists(), "仓库有 requirements.txt（锁定版本）")

# 分发包里必须随附：项目许可证 + LGPL/GPL 正文（PySide6 的 wheel 不含正文）
for zip_path, prefix in ((PORTABLE, "CyreneKeyboard-Portable/python/Lib/site-packages/_licenses/"),
                         (EXE_ZIP, "CyreneKeyboard/_internal/_licenses/")):
    if not zip_path.exists():
        continue
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        tag = zip_path.name.split("-")[0][-6:]
        proj = prefix + "LICENSE"                      # 精确路径，别用 endswith('/LICENSE')
        check(proj in names, f"[{tag}] 含项目 LICENSE")
        if proj in names:
            head = z.read(proj).decode("utf-8", "replace").strip().splitlines()[0]
            check("AFFERO" in head, f"[{tag}] 包内 LICENSE 是 AGPL 正文")
        for f in ("LGPL-3.0.txt", "GPL-3.0.txt", "THIRD_PARTY_LICENSES.md"):
            check(prefix + f in names, f"[{tag}] 含 {f}")

print("\n" + "=" * 66)
print("结论：" + ("全部通过，可以发布" if ok else "有项目未通过，见上面 [!!]"))
print("=" * 66)
raise SystemExit(0 if ok else 1)
