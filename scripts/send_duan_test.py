"""一次性脚本:用 mock 数据渲染含段永平段子的「关键发言」段,
验证邮件视觉:
  - 段永平作为第 4 人物渲染(雪球作为来源)
  - 回复型帖子的"原帖"上下文渲染(短摘要 + @作者)
  - 脚注 [N] 与黄仁勋 / 巴菲特统一格式
  - system_alerts 告警条样例(模拟 XQ_A_TOKEN 过期场景)

完全离线:不调 LLM、不抓雪球、不抓股价,纯本地 mock。
不影响线上幂等性 / state 文件。

用法(从 worktree 跑):
    uv run python scripts/send_duan_test.py
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve()
_WORKTREE_ROOT = _HERE.parent.parent
_ENV_FILE = _WORKTREE_ROOT / ".env"
if not _ENV_FILE.exists():
    sys.exit(f"找不到 .env: {_ENV_FILE}（请在项目根 {_WORKTREE_ROOT} 创建 .env）")
for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

sys.path.insert(0, str(_WORKTREE_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

from src.collectors.header_image import pick_header_image  # noqa: E402
from src.collectors.stocks import StockSignal  # noqa: E402
from src.config import HOLDINGS, Holding  # noqa: E402
from src.processors.figure_filter import (  # noqa: E402
    FigureFootnote,
    FigureKeyPoint,
    FigureSummary,
    assign_footnotes,
)
from src.renderer.render import render_email  # noqa: E402
from src.sender.smtp_sender import InlineImage, send_html_email  # noqa: E402

_LOGOS_DIR = _WORKTREE_ROOT / "assets" / "logos"

# 收件人从环境变量读取,避免把私人 QQ 号硬编码到公开仓库。
# 用法:export TEST_RECIPIENT=xxx@qq.com 后再跑;未设时 fallback 到 EMAIL_RECIPIENT(逗号分隔取第一个)。
RECIPIENT = (
    os.environ.get("TEST_RECIPIENT")
    or (os.environ.get("EMAIL_RECIPIENT", "").split(",")[0].strip() or None)
)
if not RECIPIENT:
    sys.exit("请设置 TEST_RECIPIENT 或 EMAIL_RECIPIENT 环境变量")


def _mock_signals() -> list[StockSignal]:
    cases: list[tuple[Holding, float, float, float]] = [
        (HOLDINGS[0], 414.44, 438.85, 381.78),
        (HOLDINGS[1], 1011.70, 908.45, 756.63),
        (HOLDINGS[2], 280.14, 226.43, 202.18),
        (HOLDINGS[3], 198.45, 140.20, 96.16),
        (HOLDINGS[4], 397.67, 219.97, 167.42),
        (HOLDINGS[5], 478.30, 502.80, 390.50),
        (HOLDINGS[6], 198.40, 185.60, 152.30),
        (HOLDINGS[7], 462.10, 451.20, 388.40),
        (HOLDINGS[8], 64.20, 68.40, 60.10),
        (HOLDINGS[9], 268.90, 295.40, 312.80),
        (HOLDINGS[10], 412.40, 380.60, 340.20),
        (HOLDINGS[11], 157.10, 136.01, 89.67),
    ]
    out: list[StockSignal] = []
    for h, last, s120, s200 in cases:
        d120 = (last - s120) / s120
        d200 = (last - s200) / s200
        sig = "LUMP_SUM" if last <= s200 else ("DCA" if last <= s120 else "NONE")
        out.append(StockSignal(h, last, s120, s200, d120, d200, sig))
    return out


def _build_logos() -> tuple[dict[str, str], list[InlineImage]]:
    cids: dict[str, str] = {}
    images: list[InlineImage] = []
    for h in HOLDINGS:
        path = next(
            (p for ext in ("png", "jpg", "jpeg")
             if (p := _LOGOS_DIR / f"{h.slug}.{ext}").exists()),
            None,
        )
        if path is None:
            continue
        sha8 = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
        cid = f"{h.logo_cid}_{sha8}"
        cids[h.ticker] = cid
        images.append(InlineImage(cid=cid, path=path, subtype=None))
    return cids, images


def _build_figure_summaries() -> tuple[list[FigureSummary], list[FigureFootnote]]:
    """构造三个人物示例:
       1. 黄仁勋(独立观点,无 parent)
       2. 巴菲特(独立观点,无 parent)
       3. 段永平 — 一条独立短文 + 一条带"原帖"上下文的回复
    """
    huang = FigureSummary(person="黄仁勋", person_en="Jensen Huang", items=[
        FigureKeyPoint(
            text="AI 推理需求增长远超预期,数据中心投资仍处早期。",
            source_url="https://example.com/jensen-1",
            source_name="Reuters",
        ),
    ])
    buffett = FigureSummary(person="巴菲特", person_en="Warren Buffett", items=[
        FigureKeyPoint(
            text="持有现金的成本是错过好生意的成本,但仍要等到价格合理。",
            source_url="https://example.com/buffett-1",
            source_name="CNBC",
        ),
    ])
    duan = FigureSummary(person="段永平", person_en="Duan Yongping", items=[
        FigureKeyPoint(
            text="本分就是做对的事,做难而正确的事。短期看慢一点,长期一定快。",
            source_url="https://xueqiu.com/u/duan/1001",
            source_name="雪球",
        ),
        FigureKeyPoint(
            text="想多了,这种估值方法长期看根本撑不住。关键是看自由现金流。",
            source_url="https://xueqiu.com/u/duan/1002",
            source_name="雪球",
            parent_text="茅台 PE=40 已是泡沫,应清仓",   # ≤30 字模拟 LLM 总结后的短句
            parent_author="股民甲",
        ),
    ])
    summaries = [huang, buffett, duan]
    footnotes = assign_footnotes(summaries)
    return summaries, footnotes


def main() -> int:
    sender = os.environ["QQ_EMAIL_ADDRESS"]
    auth_code = os.environ["QQ_EMAIL_AUTH_CODE"]

    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))

    logger.info("pick_header_image")
    header = pick_header_image(now_bj.date())

    logger.info("build mock data")
    signals = _mock_signals()
    logo_cids, logo_images = _build_logos()
    figure_summaries, figure_footnotes = _build_figure_summaries()

    inline_images = list(logo_images)
    inline_images.append(InlineImage(
        cid="header_image",
        path=Path(header["local_path"]),
        subtype=None,
    ))

    holdings_intro = (
        "今日为段永平模块视觉验证邮件:重点查看「关键发言」段——"
        "段永平作为第 4 人物的渲染、回复型帖子的「原帖」上下文短摘要,以及与黄仁勋/巴菲特"
        "在脚注 [N] 编号上的统一性。"
    )

    # 也展示一条 system_alerts(告警条样例),让你看实际告警时的呈现
    system_alerts = [
        "示例告警(本邮件为渲染测试,实际系统正常):假设段永平雪球 XQ_A_TOKEN 已过期,"
        "请在 GitHub Secrets 更新该值。",
    ]

    logger.info("render email")
    html = render_email(
        signals=signals,
        generated_at=now_bj,
        logo_cids=logo_cids,
        header_image_url=header["url"],
        holdings_intro=holdings_intro,
        figures=[object()],  # 触发「关键发言」区段
        figure_summaries=figure_summaries,
        figure_footnotes=figure_footnotes,
        system_alerts=system_alerts,
    )

    Path("/tmp/send_duan_test.html").write_text(html, encoding="utf-8")
    logger.info("debug html size=%d", len(html.encode("utf-8")))

    subject = f"【段永平模块视觉测试】{now_bj.strftime('%Y-%m-%d %H:%M')}"
    logger.info("send to=%s subject=%r inline_images=%d",
                RECIPIENT, subject, len(inline_images))
    send_html_email(
        sender=sender,
        sender_display_name="渲染测试",
        auth_code=auth_code,
        recipient=RECIPIENT,
        subject=subject,
        html_body=html,
        inline_images=inline_images,
    )
    logger.info("done. 请在 QQ 邮箱查收,关注:")
    logger.info("  1. 「关键发言」段段永平作为第 4 人物")
    logger.info("  2. 段永平第 2 条上方有灰色「原帖 @股民甲:茅台 PE=40 已是泡沫,应清仓」")
    logger.info("  3. 引语末尾 [4] 与章节底部 [4] 雪球 一致")
    logger.info("  4. 页脚之上有暗黄色「数据源提示」告警条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
