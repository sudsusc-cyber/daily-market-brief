from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from scripts import monitor
from src.collectors.sentiment import SentimentBundle, SentimentMetric
from src.processors.macro_filter import _rebuild_safe_html
from src.processors.macro_topics import macro_topic
from src.processors.news_presentation import publication_text
from src.processors.sentiment_judge import judge, score_sentiment
from src.utils.quality_details import quality_details


@pytest.mark.parametrize(
    "text,topic",
    [
        ("The Fed's main inflation measure will be released Wednesday", "通胀数据"),
        ("美联储的主要通胀指标将于周三发布", "通胀数据"),
        ("Fed cuts rates after inflation data release", "货币政策"),
        ("美联储在通胀数据公布后降息", "货币政策"),
        ("海湾原油出口恢复，油价下跌", "中东局势"),
        ("Oil prices fall as Gulf crude exports recover", "中东局势"),
        ("Gulf of Mexico oil output rises", "能源市场"),
        ("墨西哥湾石油生产恢复", "能源市场"),
    ],
)
def test_specific_macro_scope(text, topic):
    assert macro_topic(text) == topic


def test_actual_news_groups_related_gulf_fact_with_middle_east_without_losing_sources():
    facts = [
        "美联储主要通胀指标将于周三发布。",
        "亚洲股市有望上涨，投资者关注中东进展及美伊僵局。",
        "海湾原油出口恢复，油价下跌。",
    ]
    items = [
        SimpleNamespace(title=t, summary="", source="Source", url=f"https://example.com/{i}")
        for i, t in enumerate(facts)
    ]
    evidence = []
    html, notes = _rebuild_safe_html("<p>任意模型标题。[1][2][3]</p>", items, evidence)
    p = BeautifulSoup(html, "html.parser").select("p")
    assert [x.select_one("[data-macro-heading]").text for x in p] == ["通胀数据。", "中东局势。"]
    assert len(p[1].select("[data-macro-fact]")) == 2
    assert len(notes) == len(evidence) == 3
    assert set(r["output_text"] for r in evidence) == set(facts)


def test_tencent_full_name_listing_and_fed_display():
    assert (
        publication_text("腾讯控股(00700.HK)连续32日回购，累计斥资52.12亿港元")
        == "腾讯控股连续32日回购，累计斥资52.12亿港元"
    )
    assert publication_text("Fed 通胀指标") == "美联储通胀指标"
    assert publication_text("FedEx 推出服务") == "FedEx 推出服务"


def bundle():
    return SentimentBundle(
        [
            SentimentMetric("CNN Fear & Greed", 31.63, 33.83, None),
            SentimentMetric("VIX", 16.04, 16.07, None),
        ],
        datetime.now(UTC),
    )


def test_sentiment_retry_is_actionable_and_diagnostics_do_not_change_score():
    prompts = []
    replies = iter(["CNN 31.63 上涨，VIX 16.04。", "CNN 31.63 回落，VIX 16.04 回落。"])

    def chat(*args, **kwargs):
        prompts.append(kwargs["task_extra"])
        return SimpleNamespace(text='{"argument":"' + next(replies) + '"}', error=None)

    result = judge(bundle(), client=SimpleNamespace(chat=chat))
    assert result["score"] == score_sentiment(bundle())["score"]
    assert result["argument_fallback"] is False
    assert result["argument_rejections"][0]["reasons"] == ["wrong_up_direction"]
    assert "wrong_up_direction" in prompts[1]


def test_sentiment_failed_attempts_are_bounded_and_redacted():
    result = judge(
        bundle(),
        client=SimpleNamespace(
            chat=lambda *a, **kw: SimpleNamespace(
                text='{"argument":"CNN 999。a@example.com"}', error=None
            )
        ),
    )
    assert result["argument_fallback"] and len(result["argument_rejections"]) == 2
    assert "unsupported_number" in result["argument_rejections"][0]["reasons"]
    assert "@" not in str(result["argument_rejections"])
    assert result["score"] == score_sentiment(bundle())["score"]


def test_quality_details_distinguish_carry_missing_failure_and_extracts():
    report = {
        "observations": [{"key": "QQQM", "status": "carried", "observed_at": "2026-09-25"}],
        "section_health": {"sentiment": {"fallback": True}, "frontier": {"silence": True}},
        "news_coverage": {"macro": {"extractive_fallbacks": 3}},
        "html_bytes": 90056,
    }
    text = "\n".join(quality_details(report))
    assert "有效沿用：QQQM（2026-09-25）" in text
    assert "情绪说明" in text and "摘要形式" in text
    assert "数据缺失" not in text and "前沿动态" not in text and "体积超限" not in text


def test_annotation_details_paginate_and_ignore_other_attempt_editions(monkeypatch):
    today = "2026-09-30"
    calls = []

    def api(url, token):
        calls.append(url)
        if "/jobs" in url:
            return {
                "jobs": [
                    {
                        "check_run_url": "https://api.github.com/repos/a/b/check-runs/12",
                        "steps": [
                            {
                                "name": "Report content quality warning | " + today,
                                "conclusion": "success",
                            }
                        ],
                    },
                    {
                        "check_run_url": "https://api.github.com/repos/a/b/check-runs/13",
                        "steps": [
                            {
                                "name": "Report content quality warning | 2026-09-29",
                                "conclusion": "success",
                            }
                        ],
                    },
                ]
            }
        if url.endswith("&page=1"):
            return [{"title": "unrelated", "message": "ignore"}] * 100
        return [
            {
                "title": "Daily brief content quality",
                "message": "有效沿用：QQQM 2026-09-25 a@example.com",
            }
        ]

    monkeypatch.setattr(monitor, "_github_json", api)
    result = monitor._quality_annotation_details("a/b", "token", 1, today)
    assert "QQQM" in result and "ignore" not in result and "@" not in result
    assert len(calls) == 3


def test_quality_detail_api_failure_preserves_full_delivery(monkeypatch):
    monkeypatch.setenv("GH_REPO", "a/b")
    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setattr(monitor, "should_send_today", lambda *a: (True, ""))
    monkeypatch.setattr(
        monitor, "workflow_runs", lambda *a: [{"id": 1, "created_at": "2026-09-30T00:00:00Z"}]
    )
    monkeypatch.setattr(monitor, "_today_beijing_iso", lambda: "2026-09-30")
    monkeypatch.setattr(
        monitor, "run_evidence", lambda *a, **kw: {"accepted": True, "full": True, "degraded": True}
    )

    def fail(*a):
        raise RuntimeError("network")

    monkeypatch.setattr(monitor, "_github_json", fail)
    ok, reason = monitor.check_today_status()
    assert (
        not ok
        and "全体 SMTP 接受" in reason
        and "禁止整封重发" in reason
        and "明细读取失败" in reason
    )


def test_quality_alert_has_specific_subject_and_no_credential_troubleshooting(monkeypatch):
    sent = []
    monkeypatch.setattr(
        monitor,
        "load_email_settings",
        lambda: SimpleNamespace(
            qq_email_address="sender@example.com",
            qq_email_auth_code="secret",
            email_recipient="receiver@example.com",
        ),
    )
    monkeypatch.setattr(
        monitor,
        "send_html_email",
        lambda **kw: sent.append(kw) or SimpleNamespace(refused=[], accepted=["receiver"]),
    )
    monitor.send_alert("邮件已全体 SMTP 接受，但内容降级\n有效沿用：QQQM\n<script>bad</script>")
    assert len(sent) == 1
    assert "邮件已发送" in sent[0]["subject"]
    assert "PAT 过期" not in sent[0]["html_body"] and "SMTP 授权码" not in sent[0]["html_body"]
    assert "<script>" not in sent[0]["html_body"] and "&lt;script&gt;" in sent[0]["html_body"]
