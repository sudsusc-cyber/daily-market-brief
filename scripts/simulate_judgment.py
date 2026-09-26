"""Offline source-bound watchpoint preview with clearly synthetic news, no SMTP/LLM."""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.processors.news_summarizer import CompanyNewsSummary, Footnote  # noqa: E402
from src.processors.thesis.renderer import build_judgment_section  # noqa: E402
from src.renderer.render import render_email  # noqa: E402


def main():
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    facts = [
        "模拟：微软计划投资100亿美元建设云基础设施。",
        "模拟：苹果推出新款手机。",
        "模拟：万事达开通新的支付结算服务。",
    ]
    evidence, footnotes, body = [], [], []
    for index, fact in enumerate(facts, 1):
        url = f"https://example.com/synthetic-news/{index}"
        evidence.append(dict(original_title=fact, original_summary="", excerpt=fact,
                             output_text=fact, validated_text=fact, mode="source_extract",
                             published_at=now.isoformat(), url=url, source_name="模拟来源"))
        footnotes.append(Footnote(index, url, "模拟来源"))
        body.append(f'<p>{fact}<sup><a href="{url}">[{index}]</a></sup></p>')
    summary = CompanyNewsSummary("".join(body), footnotes=footnotes, evidence=evidence)
    section = build_judgment_section(sources={"company_news": [summary]}, today=now.date())
    html = render_email(signals=[], generated_at=now, company_news_summary=summary,
                        judgment_section=section)
    out = Path(tempfile.gettempdir()) / "email_preview_with_judgment.html"
    out.write_text(html, encoding="utf-8")
    print(f"模拟预览：{out} ({len(html.encode('utf-8')):,} bytes); SMTP calls=0")
    for item in section.items if section else []:
        print(item["thesis"], "\n本期依据：", item["fact"], "\n后续验证：", item["watch"])


if __name__ == "__main__":
    main()
