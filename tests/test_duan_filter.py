"""tests/test_duan_filter.py — 段永平雪球相关性筛选单测。

关键不变量:
- LLM yes → 原文一字不改进入返回列表
- LLM no → 丢弃
- LLM 漏判某索引 → 视为 no(保守)
- LLM 调用失败 → 返回空列表(fail-safe,不向章节灌闲聊)
- 空输入 → 空输出,不调 LLM
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.collectors.xueqiu_duan import DuanQuote
from src.processors import duan_filter
from src.processors.duan_filter import _parse_verdicts, judge_relevance
from src.processors.llm_client import LLMResponse, LLMUsage


def _q(sid: int, text: str) -> DuanQuote:
    return DuanQuote(
        id=sid,
        created_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
        text=text,
        url=f"https://xueqiu.com/u/{sid}",
        truncated=False,
    )


class _FakeLLM:
    """最小 LLMClient 桩,只暴露 chat()。"""

    def __init__(self, response_text: str | None, error: str | None = None) -> None:
        self._text = response_text
        self._error = error

    def chat(self, *args, **kwargs) -> LLMResponse:  # noqa: D401
        return LLMResponse(text=self._text, usage=LLMUsage(), error=self._error)


# ----------------------------------------------------------------------
# _parse_verdicts
# ----------------------------------------------------------------------
def test_parse_verdicts_basic() -> None:
    text = "▦ 1: yes\n▦ 2: no\n▦ 3: yes\n"
    assert _parse_verdicts(text, n=3) == {1: True, 2: False, 3: True}


def test_parse_verdicts_ignores_garbage() -> None:
    text = "解释:\n▦ 1: yes\n这是一个解释\n▦ 2: no\n"
    assert _parse_verdicts(text, n=2) == {1: True, 2: False}


def test_parse_verdicts_drops_out_of_range() -> None:
    text = "▦ 1: yes\n▦ 99: yes\n"
    assert _parse_verdicts(text, n=1) == {1: True}


def test_parse_verdicts_case_insensitive() -> None:
    text = "▦ 1: YES\n▦ 2: No\n"
    assert _parse_verdicts(text, n=2) == {1: True, 2: False}


# ----------------------------------------------------------------------
# judge_relevance
# ----------------------------------------------------------------------
def test_judge_empty_input_returns_empty_no_llm() -> None:
    """空输入不应调 LLM。"""
    called = []

    class _Probe:
        def chat(self, *a, **kw):
            called.append(1)
            return LLMResponse(text="x", usage=LLMUsage())

    out = judge_relevance([], client=_Probe())
    assert out == []
    assert called == []  # 没调 LLM


def test_judge_keeps_yes_drops_no_verbatim() -> None:
    quotes = [
        _q(1, "苹果回购就是在替股东省钱,这就是好的资本配置。"),
        _q(2, "今天遛狗,天气真好,心情舒畅。"),
        _q(3, "本分就是做对的事,做难而正确的事。"),
    ]
    fake = _FakeLLM("▦ 1: yes\n▦ 2: no\n▦ 3: yes")
    out = judge_relevance(quotes, client=fake)
    assert [q.id for q in out] == [1, 3]
    # 原文一字不改
    assert out[0].text == "苹果回购就是在替股东省钱,这就是好的资本配置。"
    assert out[1].text == "本分就是做对的事,做难而正确的事。"


def test_judge_missing_verdict_treated_as_no() -> None:
    """LLM 没给某条结论 → 默认 no(保守)。"""
    quotes = [_q(1, "投资就是看 owner earnings"), _q(2, "今天打了场高尔夫")]
    # LLM 只给了第 1 条
    fake = _FakeLLM("▦ 1: yes")
    out = judge_relevance(quotes, client=fake)
    assert [q.id for q in out] == [1]


def test_judge_llm_failure_returns_empty() -> None:
    """LLM 失败 → 全部丢弃(fail-safe)。"""
    quotes = [_q(1, "苹果是好生意"), _q(2, "茅台估值合理")]
    fake = _FakeLLM(None, error="rate_limit")
    out = judge_relevance(quotes, client=fake)
    assert out == []


def test_judge_llm_returns_garbage_returns_empty() -> None:
    """LLM 返回完全无法解析的文本 → 解析为空 verdicts → 全部丢弃。"""
    quotes = [_q(1, "苹果是好生意")]
    fake = _FakeLLM("我无法判断,这超出我的能力。")
    out = judge_relevance(quotes, client=fake)
    assert out == []


def test_judge_does_not_invoke_llm_when_input_empty(monkeypatch) -> None:
    """监控 module-level: 空输入零 LLM 调用。"""
    sentinel = []

    def trap(*a, **kw):
        sentinel.append(1)

    # 拦截 module 内 LLMClient.chat 的可能任何调用路径(防回归)
    monkeypatch.setattr(duan_filter, "_format_input", lambda q: (sentinel.append("fmt"), "")[1])
    judge_relevance([], client=_FakeLLM("▦ 1: yes"))
    assert sentinel == []


def test_judge_input_includes_parent_context_for_replies() -> None:
    """带 parent_text 的 quote → LLM 输入里同时包含原帖与段永平回复。"""
    quotes = [
        DuanQuote(
            id=1, created_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
            text="想多了。", url="https://xueqiu.com/u/1", truncated=False,
            parent_text="茅台 PE=40 已是泡沫", parent_author="股民甲",
        ),
        DuanQuote(
            id=2, created_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
            text="本分就是做对的事。", url="https://xueqiu.com/u/2", truncated=False,
        ),
    ]
    payload = duan_filter._format_input(quotes)
    assert "原帖" in payload and "茅台 PE=40 已是泡沫" in payload
    assert "@股民甲" in payload
    assert "段永平回复" in payload  # 第 1 条要标明是回复


# ----------------------------------------------------------------------
# 原帖上下文 LLM 压缩(段永平正文不动)
# ----------------------------------------------------------------------
from src.processors.duan_filter import (  # noqa: E402
    PARENT_SUMMARY_MAX_CHARS,
    _fallback_truncate,
    _parse_summary_output,
    summarize_parents,
)


def _qp(sid: int, text: str, parent_text: str | None, parent_author: str | None = None) -> DuanQuote:
    return DuanQuote(
        id=sid,
        created_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
        text=text,
        url=f"https://xueqiu.com/u/{sid}",
        truncated=False,
        parent_text=parent_text,
        parent_author=parent_author,
    )


def test_parse_summary_output_basic() -> None:
    text = "▦ 1: 茅台估值偏高\n▦ 2: 苹果中国份额下滑\n"
    assert _parse_summary_output(text, n=2) == {1: "茅台估值偏高", 2: "苹果中国份额下滑"}


def test_parse_summary_output_strips_quotes() -> None:
    text = "▦ 1: \"包含引号\"\n"
    assert _parse_summary_output(text, n=1) == {1: "包含引号"}


def test_fallback_truncate_short_unchanged() -> None:
    s = "短句无需截断"
    assert _fallback_truncate(s) == s


def test_fallback_truncate_long_marks_ellipsis() -> None:
    s = "原" * 200
    out = _fallback_truncate(s)
    assert out.endswith("…")
    assert len(out) <= 51  # 50 + "…"


def test_summarize_parents_replaces_parent_text_keeps_duan_text() -> None:
    """LLM 给出短摘要 → parent_text 被替换;段永平 .text 一字不改。"""
    duan_text_original = "想多了,这种估值方法长期看根本撑不住,关键是看自由现金流。"
    quotes = [_qp(1, duan_text_original,
                  "我觉得茅台 PE=40 已经是历史顶,长期持有逻辑被破坏了,要清仓出。",
                  "股民甲")]
    fake = _FakeLLM("▦ 1: 茅台 PE=40 已是泡沫,应清仓")
    out = summarize_parents(quotes, client=fake)
    assert len(out) == 1
    # 段永平正文不动
    assert out[0].text == duan_text_original
    # parent 被替换为短摘要
    assert out[0].parent_text == "茅台 PE=40 已是泡沫,应清仓"
    # parent_author 透传不丢
    assert out[0].parent_author == "股民甲"


def test_summarize_parents_truncates_when_llm_overshoots() -> None:
    """LLM 不守字数 → 兜底硬截断(防御性)。"""
    long_summary = "茅" * 50  # LLM 本应给 ≤30 字,这里给了 50 字
    quotes = [_qp(1, "本分就是做对的事,做难而正确的事,这就是企业的护城河。",
                  "原帖很长很长的内容...")]
    fake = _FakeLLM(f"▦ 1: {long_summary}")
    out = summarize_parents(quotes, client=fake)
    assert len(out[0].parent_text) <= PARENT_SUMMARY_MAX_CHARS + 1  # +1 for "…"
    assert out[0].parent_text.endswith("…")


def test_summarize_parents_llm_failure_falls_back_to_truncate() -> None:
    """LLM 失败 → 退回截前 50 字 + …;段永平正文仍不动。"""
    duan_text = "想多了,这种估值方法长期看根本撑不住,关键看自由现金流。"
    parent = "原" * 100
    quotes = [_qp(1, duan_text, parent)]
    fake = _FakeLLM(None, error="rate_limit")
    out = summarize_parents(quotes, client=fake)
    assert out[0].text == duan_text  # 段永平正文不动
    assert out[0].parent_text.endswith("…")
    assert len(out[0].parent_text) <= 51


def test_summarize_parents_no_parent_passes_through() -> None:
    """无 parent 的独立短文 → 原对象不动,不调 LLM。"""
    sentinel = []

    class _NeverCalled:
        def chat(self, *a, **kw):
            sentinel.append(1)
            raise AssertionError("不应调 LLM")

    quotes = [_qp(1, "本分就是做对的事,做难而正确的事,这就是企业的护城河。", None)]
    out = summarize_parents(quotes, client=_NeverCalled())
    assert out[0].parent_text is None
    assert sentinel == []


def test_summarize_parents_mixed_with_and_without_parent() -> None:
    """混合输入 → LLM 只看 parent 的;无 parent 的原对象透传。"""
    q1 = _qp(1, "想多了,这种估值方法长期看根本撑不住。", "茅台估值已是泡沫,应该清仓出场", "股民甲")
    q2 = _qp(2, "本分就是做对的事,做难而正确的事。", None)
    fake = _FakeLLM("▦ 1: 茅台估值偏高,应清仓")
    out = summarize_parents([q1, q2], client=fake)
    assert out[0].parent_text == "茅台估值偏高,应清仓"
    assert out[1].parent_text is None  # 不动
    assert out[0].text == q1.text
    assert out[1].text == q2.text


def test_summarize_parents_partial_llm_response_falls_back() -> None:
    """LLM 漏判某条 → 该条走截断兜底,其他条用 LLM 摘要。"""
    q1 = _qp(1, "想多了,这种估值方法长期看根本撑不住。", "原帖一" * 60, "股民甲")
    q2 = _qp(2, "对的,需要看自由现金流而不是 PE。", "原帖二" * 60, "股民乙")
    # LLM 只返回了第 1 条
    fake = _FakeLLM("▦ 1: 第一条摘要")
    out = summarize_parents([q1, q2], client=fake)
    assert out[0].parent_text == "第一条摘要"
    assert out[1].parent_text.endswith("…")  # 兜底截断


def test_summarize_parents_empty_input_returns_empty() -> None:
    assert summarize_parents([], client=_FakeLLM("▦ 1: x")) == []
