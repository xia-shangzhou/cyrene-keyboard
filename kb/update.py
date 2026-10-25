"""
版本信息与「检查更新」（走 GitHub Releases API）。

设计原则
--------
1. **不自动联网**。只有用户点「检查版本更新」才发一次请求 —— 界面上那句
   「本地运行，不联网」必须始终为真。
2. **只用标准库**（urllib + json + threading），不引入 requests，
   便携版不用多带任何依赖。
3. **失败要说人话**：没网 / 仓库没发布 Release / 404 / 限流，都给明确原因，
   不做"检查失败"这种含糊提示。

改发布仓库只需要改下面 `GITHUB_REPO` 一处。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass

# ---------------------------------------------------------------- 常量

# 发布仓库（owner/repo 形式，不带域名）
GITHUB_REPO = "xia-shangzhou/cyrene-keyboard"

APP_VERSION = "0.4.1"
APP_VERSION_LABEL = f"v{APP_VERSION}"

API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"
RELEASES_PAGE = "https://github.com/{repo}/releases/latest"

TIMEOUT = 6.0


@dataclass
class UpdateResult:
    ok: bool = False            # 请求本身是否成功
    has_new: bool = False       # 是否有比当前更新的版本
    latest: str = ""            # 远端版本号（原样）
    url: str = ""               # 发布页地址
    note: str = ""              # 给人的说明（大小、发布时间等）
    error: str = ""             # 失败原因


def parse_version(text: str) -> tuple[int, ...]:
    """把 'v0.4.1' / '0.4' 之类解析成可比较的元组。解析不出来的部分忽略。"""
    s = (text or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in s.replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def is_repo_configured() -> bool:
    return bool(GITHUB_REPO) and "/" in GITHUB_REPO and not GITHUB_REPO.startswith("owner/")


def check_sync() -> UpdateResult:
    """同步检查（不要在 UI 线程直接调，用 check_async）。"""
    if not is_repo_configured():
        return UpdateResult(error=(
            "还没有配置发布仓库地址。\n"
            "请在 kb/update.py 里把 GITHUB_REPO 改成 你的用户名/仓库名。"
        ))

    url = API_LATEST.format(repo=GITHUB_REPO)
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"CyreneKeyboard/{APP_VERSION}",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return UpdateResult(error=(
                "找不到 Release（404）。\n"
                "仓库可能还没发布过 Release，或者仓库地址写错了。\n"
                f"当前配置：{GITHUB_REPO}"
            ))
        if e.code == 403:
            return UpdateResult(error=(
                "被 GitHub 限流了（403）。\n"
                "没登录的请求每小时只有 60 次，过一会儿再试。"
            ))
        return UpdateResult(error=f"GitHub 返回 HTTP {e.code}")
    except urllib.error.URLError as e:
        return UpdateResult(error=(
            f"连不上 GitHub：{e.reason}\n"
            "检查一下网络/代理。这个功能是唯一会联网的地方，其它功能不受影响。"
        ))
    except Exception as e:  # noqa: BLE001
        return UpdateResult(error=f"检查失败：{type(e).__name__}: {e}")

    tag = str(data.get("tag_name") or data.get("name") or "").strip()
    page = data.get("html_url") or RELEASES_PAGE.format(repo=GITHUB_REPO)
    if not tag:
        return UpdateResult(error="Release 里没有版本号（tag_name 为空）")

    notes = []
    if data.get("published_at"):
        notes.append(str(data["published_at"])[:10])
    assets = data.get("assets") or []
    if assets:
        notes.append(f"{len(assets)} 个附件")
    if data.get("prerelease"):
        notes.append("预发布版")

    newer = parse_version(tag) > parse_version(APP_VERSION)
    return UpdateResult(
        ok=True,
        has_new=newer,
        latest=tag,
        url=page,
        note=" · ".join(notes),
    )


class UpdateChecker:
    """在后台线程里检查，用回调把结果送回 UI 线程。

    用法：
        chk = UpdateChecker()
        chk.start(lambda r: print(r.error or r.latest))
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, on_done) -> bool:
        """启动检查。已在跑就不重复启动，返回 False。"""
        if self.running:
            return False

        def _work() -> None:
            result = check_sync()
            try:
                on_done(result)
            except Exception:  # noqa: BLE001
                pass

        self._thread = threading.Thread(target=_work, name="update-check", daemon=True)
        self._thread.start()
        return True


if __name__ == "__main__":   # python -m kb.update  —— 命令行验一下能不能连上
    print(f"当前版本 {APP_VERSION_LABEL}")
    print(f"仓库配置 {GITHUB_REPO}  （已配置={is_repo_configured()}）")
    r = check_sync()
    if r.error:
        print("失败：", r.error)
    else:
        print(f"远端 {r.latest}  有新版={r.has_new}  {r.note}")
        print("发布页", r.url)
