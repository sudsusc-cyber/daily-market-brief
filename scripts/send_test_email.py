"""一次性脚本:用 mock 数据渲染完整邮件(头图 + 持仓表格 + logo),
发到 TEST_RECIPIENT(或 EMAIL_RECIPIENT 第一个地址),验证 Android QQ 邮箱里:
  - 头图能否显示(inline CID 修复)
  - 持仓表格里 ticker / 公司名是否被压成一字一行(white-space:nowrap 修复)

不调 LLM、不收新闻,纯本地渲染。
不影响 idempotency 状态。

用法(从 worktree 跑):
    export TEST_RECIPIENT=xxx@qq.com   # 必须,避免把私人收件人写进公开脚本
    uv run python scripts/send_test_email.py

在 CI(GitHub Actions)里跑:
    无 .env 时,直接读取已注入的环境变量(QQ_EMAIL_ADDRESS / QQ_EMAIL_AUTH_CODE /
    TEST_RECIPIENT 等,由 workflow 从 secrets/inputs 注入),见 .github/workflows/test-send.yml。
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

# ─────────────────────────  路径与 env  ───────────────────────────
_HERE = Path(__file__).resolve()
_WORKTREE_ROOT = _HERE.parent.parent
_ENV_FILE = _WORKTREE_ROOT / ".env"
# 本地:有 .env 就加载(os.environ.setdefault 不覆盖已存在的真实环境变量)。
# CI:无 .env 则跳过,直接依赖 workflow 从 secrets/inputs 注入的环境变量。
# 凭据是否齐全的校验交给 main() 开头统一做,这里不再 hard-exit。
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(_WORKTREE_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

from scripts.preview_email import _build_mock_signals  # noqa: E402
from src.collectors.header_image import pick_header_image  # noqa: E402
from src.config import HOLDINGS, Holding  # noqa: E402
from src.renderer.render import render_email  # noqa: E402
from src.sender.smtp_sender import InlineImage, send_html_email  # noqa: E402
from src.utils.secrets import mask_email  # noqa: E402

_LOGOS_DIR = _WORKTREE_ROOT / "assets" / "logos"


def _logo_path(h: Holding) -> Path | None:
    for ext in ("png", "jpg", "jpeg"):
        p = _LOGOS_DIR / f"{h.slug}.{ext}"
        if p.exists():
            return p
    return None


def _build_logos() -> tuple[dict[str, str], list[InlineImage]]:
    """ticker -> CID(同 main.py 风格:logo_<slug>_<sha8>),并构造 InlineImage 列表"""
    cids: dict[str, str] = {}
    images: list[InlineImage] = []
    for h in HOLDINGS:
        p = _logo_path(h)
        if p is None:
            continue
        sha8 = hashlib.sha1(p.read_bytes(), usedforsecurity=False).hexdigest()[:8]
        cid = f"{h.logo_cid}_{sha8}"
        cids[h.ticker] = cid
        images.append(InlineImage(cid=cid, path=p, subtype=None))
    return cids, images


def main() -> int:
    sender = os.environ.get("QQ_EMAIL_ADDRESS", "").strip()
    auth_code = os.environ.get("QQ_EMAIL_AUTH_CODE", "").strip()
    missing = [k for k, v in (("QQ_EMAIL_ADDRESS", sender), ("QQ_EMAIL_AUTH_CODE", auth_code)) if not v]
    if missing:
        sys.exit(f"缺少 SMTP 凭据环境变量: {', '.join(missing)}（本地放 .env,CI 由 secrets 注入）")
    recipient = (
        os.environ.get("TEST_RECIPIENT")
        or (os.environ.get("EMAIL_RECIPIENT", "").split(",")[0].strip() or None)
    )
    if not recipient:
        sys.exit("请设置 TEST_RECIPIENT 或 EMAIL_RECIPIENT 环境变量")

    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))

    logger.info("pick_header_image")
    header = pick_header_image(now_bj.date())
    logger.info("header source=%s id=%s path=%s",
                header["source"], header["id"], header["local_path"])

    logger.info("build mock signals & logos")
    signals = _build_mock_signals()
    logo_cids, logo_images = _build_logos()
    logger.info("logos count=%d/%d", len(logo_cids), len(HOLDINGS))

    inline_images = list(logo_images)
    inline_images.append(InlineImage(
        cid="header_image",
        path=Path(header["local_path"]),
        subtype=None,
    ))

    holdings_intro = (
        "潮水退去,礁石毕现,但我们关心的是潮水之外的那块岩石——它在低处安静堆放,"
        "你须弯腰才能拾起。市场如海,此刻退潮显露的礁石,正是耐心者早已凝视的方向。"
    )

    # 情绪温度计 mock：专门用于验证 0-100 仪表、动态档位色和指标表。
    # 67.5 落在“偏热”档，因此分数徽章和右侧档位文字都应使用偏热色。
    sentiment = SimpleNamespace(metrics=[
        SimpleNamespace(name="CNN Fear & Greed", unit="", stale_from=None, error=None,
                        current=67.5, prior=64.0, delta=3.5),
        SimpleNamespace(name="VIX", unit="", stale_from=None, error=None,
                        current=16.8, prior=17.4, delta=-0.6),
        SimpleNamespace(name="高收益债利差", unit="%", stale_from=None, error=None,
                        current=3.1, prior=3.2, delta=-0.1),
        SimpleNamespace(name="Shiller PE", unit="", stale_from=None, error=None,
                        current=34.2, prior=34.1, delta=0.1),
        SimpleNamespace(name="DXY", unit="", stale_from=None, error=None,
                        current=99.4, prior=99.8, delta=-0.4),
    ])
    sentiment_verdict = {
        "verdict": "今日情绪 · 偏热",
        "argument": "风险偏好温和回升，VIX 回落且高收益债利差收窄。估值仍不便宜，暂缓追高，守住现金仓位。",
        "score": 67.5,
    }

    logger.info("render email")
    html = render_email(
        signals=signals,
        generated_at=now_bj,
        logo_cids=logo_cids,
        header_image_url=header["url"],   # cid:header_image
        holdings_intro=holdings_intro,
        sentiment=sentiment,
        sentiment_verdict=sentiment_verdict,
        # 不传 news / 13F:模板会优雅省略，只渲染头图、持仓信号、情绪温度计和页脚。
    )

    # 调试:先把 HTML 写到 /tmp 备查
    debug_path = Path(tempfile.gettempdir()) / "send_test_email.html"
    debug_path.write_text(html, encoding="utf-8")
    logger.info("debug html bytes=%d path=%s", len(html.encode("utf-8")), debug_path)

    subject = f"【样式预览】新版情绪温度计 · {now_bj.strftime('%Y-%m-%d %H:%M')}"
    logger.info(
        "send to %s subject=%r inline_images=%d",
        mask_email(recipient), subject, len(inline_images),
    )
    send_html_email(
        sender=sender,
        sender_display_name="渲染测试",
        auth_code=auth_code,
        recipient=recipient,
        subject=subject,
        html_body=html,
        inline_images=inline_images,
    )
    logger.info("done. 请在 Android QQ 邮箱查收并确认:")
    logger.info("  1. 头图正常显示")
    logger.info("  2. 持仓表 ticker(MSFT/AAPL/NVDA…)横排,不再一字一行")
    logger.info("  3. 公司名(Microsoft/Apple…)横排")
    return 0


if __name__ == "__main__":
    sys.exit(main())
