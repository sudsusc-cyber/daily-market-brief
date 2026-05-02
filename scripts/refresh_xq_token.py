"""本地一键续雪球 xq_a_token —— 减少手动 F12 翻 cookie 的繁琐。

流程:
  1. Playwright headless Chromium 访问 https://xueqiu.com/ 暖身 ~5 秒
  2. 从 browser context 取 xq_a_token cookie
  3. 复制到系统剪贴板(macOS pbcopy / Linux xclip / Windows clip)
  4. 用默认浏览器打开 GitHub Secret 编辑页(自动从 git remote 推断仓库)
  5. 用户 ⌘V 粘贴 → Update secret 完成

为什么不全自动:
  雪球 API /v4/statuses/user_timeline.json 端点被阿里云 WAF 滑块验证挡住,
  即使 Playwright 拿到 xq_a_token 也无法在 GH Actions 直接调 API。
  本脚本的首页暖身能拿到 token,但调 API 必须走"浏览器复用 fingerprint"的路径,
  这只有用户的真实浏览器(或本机 Playwright)能做到。所以保留 GH Secret 流转,
  只是把"5 分钟 F12 翻 cookie"压缩成"30 秒一行命令"。

依赖(本地一次性安装,不进 pyproject 以免污染 prod):
    uv pip install playwright
    uv run playwright install chromium

用法:
    uv run python scripts/refresh_xq_token.py
"""
from __future__ import annotations

import logging
import platform
import re
import subprocess
import sys
import webbrowser

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

WARMUP_URL = "https://xueqiu.com/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
WAIT_MS = 5000
DEFAULT_REPO_SLUG = "sudsusc-cyber/daily-market-brief"
SECRET_NAME = "XQ_A_TOKEN"


def get_repo_slug() -> str:
    """从 git remote origin 推断 owner/repo,失败 fallback 到默认值。"""
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return DEFAULT_REPO_SLUG
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", out)
    return f"{m.group(1)}/{m.group(2)}" if m else DEFAULT_REPO_SLUG


def copy_to_clipboard(text: str) -> bool:
    system = platform.system()
    cmds: list[list[str]]
    if system == "Darwin":
        cmds = [["pbcopy"]]
    elif system == "Linux":
        cmds = [["xclip", "-selection", "clipboard"], ["xsel", "-b", "-i"]]
    elif system == "Windows":
        cmds = [["clip"]]
    else:
        return False
    for cmd in cmds:
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return False


def fetch_xq_token() -> str | None:
    """启 headless Chromium → 访问首页暖身 → 取 xq_a_token cookie。"""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        sys.exit(
            "Playwright 未安装。先跑:\n"
            "    uv pip install playwright\n"
            "    uv run playwright install chromium"
        )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                user_agent=USER_AGENT,
                locale="zh-CN",
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()
            page.goto(WARMUP_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(WAIT_MS)
            cookies = ctx.cookies()
            xq = next((c for c in cookies if c["name"] == "xq_a_token"), None)
            return xq["value"] if xq else None
        finally:
            browser.close()


def main() -> int:
    logger.info("→ 启动 Playwright headless Chromium 暖身雪球首页 (~5s)...")
    token = fetch_xq_token()
    if not token:
        logger.error(
            "✗ 没拿到 xq_a_token —— 雪球可能改了 cookie 名,"
            "请手动 F12 → Application → Cookies 排查"
        )
        return 1
    logger.info("✓ 抓到 xq_a_token  len=%d  prefix=%s…", len(token), token[:10])

    if copy_to_clipboard(token):
        logger.info("✓ 已复制到系统剪贴板")
    else:
        logger.warning("⚠ 剪贴板复制失败,请手动复制下面这行:")
        print(token)

    repo = get_repo_slug()
    secret_url = f"https://github.com/{repo}/settings/secrets/actions/{SECRET_NAME}"
    logger.info("→ 打开 GitHub Secret 编辑页:")
    logger.info("  %s", secret_url)
    try:
        webbrowser.open(secret_url)
        logger.info("✓ 浏览器已打开。在 Secret 字段 ⌘V → Update secret 即可。")
    except Exception as exc:  # noqa: BLE001
        logger.info("(浏览器自动打开失败 %s,请手动访问上面 URL)", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
