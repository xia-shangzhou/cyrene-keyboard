"""为 dist/ 下当前版本的发布包生成 SHA256SUMS.txt。

用法：
    python tools/make_checksums.py

产出：dist/SHA256SUMS.txt，格式与 `sha256sum -c` 兼容：
    <64 位十六进制>  <文件名>

用途有两个：
  1. 用户下载后可以校验文件是否完整、有没有在传输中被改过；
  2. 发布说明里贴出来，别人能对上。

⚠ 它只能证明「文件与发布时一致」，不能证明软件安全、也不代表经过任何认证。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kb.update import APP_VERSION   # noqa: E402

DIST = ROOT / "dist"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    targets = [
        DIST / f"CyreneKeyboard-v{APP_VERSION}-win64.zip",
        DIST / f"CyreneKeyboard-Portable-v{APP_VERSION}.zip",
    ]

    missing = [p.name for p in targets if not p.exists()]
    if missing:
        print(f"[失败] 缺少文件：{', '.join(missing)}")
        print("       先跑 tools/build.py --zip 和 tools/build_portable.py")
        return 1

    lines = []
    for p in targets:
        digest = sha256_of(p)
        size_mb = p.stat().st_size / 1024 / 1024
        lines.append(f"{digest}  {p.name}")
        print(f"  {digest}  {p.name}  ({size_mb:.0f} MB)")

    out = DIST / "SHA256SUMS.txt"
    # CRLF：Windows 用户会用记事本打开
    out.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    print(f"\n✅ 已写入 {out.relative_to(ROOT)}（{len(lines)} 个文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
