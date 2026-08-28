"""tests/test_html_safe.py — HTML 注入防护测试。

覆盖:
- LLM 输出 <img onerror=...> / <script> 等恶意标签 → 最终 HTML 不出现可执行片段
- NewsItem / MacroNewsItem URL 为 javascript: / data: → 不生成 href
- 正常 [N] 仍生成安全 <a href="https://...">
- HTML escape 防引号 / 尖括号 / & 注入属性
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.collectors.macro_news import MacroFeedBundle, MacroNewsItem
from src.config import HOLDINGS
from src.processors import macro_filter, news_summarizer
from src.processors.html_safe import (
    escape_text,
    is_safe_url,
    render_text_with_footnotes,
    safe_anchor,
    strip_all_tags,
)
from src.processors.llm_client import LLMResponse, LLMUsage

_DUMMY_DT = datetime(2026, 5, 3, tzinfo=UTC)


# ────────────────────  低层 helper 单测  ────────────────────


class TestEscapeText:
    def test_escapes_html_chars(self) -> None:
        assert escape_text("<script>") == "&lt;script&gt;"
        assert escape_text("a&b") == "a&amp;b"
        assert escape_text('"hello"') == "&quot;hello&quot;"

    def test_none_to_empty(self) -> None:
        assert escape_text(None) == ""

    def test_passthrough_safe_chars(self) -> None:
        assert escape_text("正常中文") == "正常中文"


class TestIsSafeUrl:
    def test_https_safe(self) -> None:
        assert is_safe_url("https://example.com/x")

    def test_http_safe(self) -> None:
        assert is_safe_url("http://example.com")

    def test_javascript_unsafe(self) -> None:
        assert not is_safe_url("javascript:alert(1)")
        assert not is_safe_url("JAVASCRIPT:alert(1)")  # 大小写不敏感

    def test_data_unsafe(self) -> None:
        assert not is_safe_url("data:text/html,<script>alert(1)</script>")

    def test_vbscript_unsafe(self) -> None:
        assert not is_safe_url("vbscript:msgbox")

    def test_file_unsafe(self) -> None:
        assert not is_safe_url("file:///etc/passwd")

    def test_empty_unsafe(self) -> None:
        assert not is_safe_url("")
        assert not is_safe_url(None)

    def test_no_netloc_unsafe(self) -> None:
        assert not is_safe_url("https://")  # 缺 host
        assert not is_safe_url("http:")


class TestSafeAnchor:
    def test_safe_url_renders_anchor(self) -> None:
        html = safe_anchor("https://x.com", "[1]")
        assert 'href="https://x.com"' in html
        assert 'target="_blank"' in html
        assert 'rel="noopener"' in html
        assert ">[1]</a>" in html

    def test_unsafe_url_drops_anchor(self) -> None:
        html = safe_anchor("javascript:alert(1)", "click")
        assert "<a" not in html
        assert "javascript" not in html
        assert html == "click"

    def test_label_escaped(self) -> None:
        html = safe_anchor("https://x.com", "<img>")
        assert "<img>" not in html
        assert "&lt;img&gt;" in html

    def test_href_escaped(self) -> None:
        # 即便 url 通过白名单,引号也要 escape 防属性突破
        html = safe_anchor('https://x.com/?q="evil"', "ok")
        assert "&quot;" in html
        assert '"evil"' not in html or "&quot;" in html  # 已 escape


class TestStripAllTags:
    def test_removes_tags_keeps_text(self) -> None:
        assert strip_all_tags("<p>hello <b>world</b></p>") == "hello world"

    def test_strips_script(self) -> None:
        out = strip_all_tags("<script>alert(1)</script>safe")
        assert "<script>" not in out
        assert "alert(1)" in out  # 标签内文本保留(但已脱离脚本上下文)

    def test_empty(self) -> None:
        assert strip_all_tags("") == ""
        assert strip_all_tags(None) == ""


class TestRenderTextWithFootnotes:
    def test_text_escaped_markers_replaced(self) -> None:
        import re
        rx = re.compile(r"\[(\d+)\]")
        builder = lambda i: f'<sup>[{i}]</sup>'  # noqa: E731
        out = render_text_with_footnotes(
            "<script>恶意</script>正文[1]后续",
            rx, lambda m: int(m.group(1)), builder,
        )
        # script 被 escape 成纯文本
        assert "<script>" not in out
        assert "&lt;script&gt;" in out
        # 脚注被替换为安全 sup
        assert "<sup>[1]</sup>" in out


# ────────────────────  集成测试:news_summarizer  ────────────────────


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    def chat(self, *a, **kw) -> LLMResponse:  # noqa: D401
        return LLMResponse(text=self._text, usage=LLMUsage())


def _bundle_with_xss_url() -> CompanyNewsBundle:
    """伪造一条 NewsItem,URL 是 javascript: scheme(应被 is_safe_url 拒)。"""
    return CompanyNewsBundle(
        holding=HOLDINGS[0],  # MSFT
        items=[NewsItem(
            title="evil",
            published_at=_DUMMY_DT,
            url="javascript:alert(1)",
            source="evil.com",
        )],
    )


def _bundle_with_https_url() -> CompanyNewsBundle:
    return CompanyNewsBundle(
        holding=HOLDINGS[0],  # MSFT
        items=[NewsItem(
            title="ok",
            published_at=_DUMMY_DT,
            url="https://reuters.com/article/abc",
            source="Reuters",
        )],
    )


def test_news_summarizer_drops_javascript_url() -> None:
    """LLM 引用 [1],对应 NewsItem URL 是 javascript: → 该脚注被丢弃,
    整行因没有可验证来源而回退原始列表。"""
    bundle = _bundle_with_xss_url()
    fake = _FakeLLM("<strong>微软</strong> —— 一些摘要[1]")
    summary = news_summarizer.summarize([bundle], client=fake)
    assert summary is None


def test_news_summarizer_keeps_safe_url() -> None:
    """正常 https URL → 生成安全 <a href="https://...">。"""
    bundle = _bundle_with_https_url()
    fake = _FakeLLM("<strong>微软</strong> —— 推出新产品[1]")
    summary = news_summarizer.summarize([bundle], client=fake)
    assert summary is not None
    assert 'href="https://reuters.com/article/abc"' in summary.summary_html
    assert "<sup>" in summary.summary_html
    assert "color:#0563C1!important" in summary.summary_html


def test_news_summarizer_neutralizes_xss_in_summary() -> None:
    """LLM 输出 <img onerror=...> 在摘要里 → 最终 HTML 不出现 <img 或 onerror。"""
    bundle = _bundle_with_https_url()
    fake = _FakeLLM(
        '<strong>微软</strong> —— <img src=x onerror="alert(1)">推出新产品[1]'
    )
    summary = news_summarizer.summarize([bundle], client=fake)
    assert summary is not None
    html = summary.summary_html
    assert "<img" not in html.lower()
    assert "onerror" not in html.lower()
    # 文本内容应被 escape 后保留(非字符级删除)— "推出新产品" 应可见
    assert "推出新产品" in html


def test_news_summarizer_neutralizes_xss_in_company_name() -> None:
    """LLM 在公司名位置塞 <script>...</script> → 最终不出现执行标签。"""
    bundle = _bundle_with_https_url()
    fake = _FakeLLM('<strong><script>alert(1)</script>微软</strong> —— 摘要[1]')
    summary = news_summarizer.summarize([bundle], client=fake)
    assert summary is not None
    assert "<script>" not in summary.summary_html


# ────────────────────  集成测试:macro_filter  ────────────────────


def _macro_bundle_https() -> MacroFeedBundle:
    return MacroFeedBundle(
        source="Reuters",
        items=[MacroNewsItem(
            title="news",
            published_at=_DUMMY_DT,
            url="https://reuters.com/x",
            source="Reuters",
        )],
    )


def _macro_bundle_javascript() -> MacroFeedBundle:
    return MacroFeedBundle(
        source="Evil",
        items=[MacroNewsItem(
            title="evil",
            published_at=_DUMMY_DT,
            url="javascript:alert(1)",
            source="Evil",
        )],
    )


def test_macro_filter_neutralizes_xss_paragraph() -> None:
    """LLM 整段塞 <script>/<img onerror>,最终 HTML 不出现可执行片段。"""
    bundle = _macro_bundle_https()
    fake = _FakeLLM(
        '<p><strong>能源市场。</strong>'
        '<img src=x onerror="steal()">布伦特原油上涨 2%[1]</p>'
    )
    summary = macro_filter.summarize([bundle], client=fake)
    assert summary is not None
    html = summary.summary_html
    assert "<img" not in html.lower()
    assert "onerror" not in html.lower()
    assert "steal" not in html  # 函数名作为属性值时被 escape
    # 但段落主题与正文文本仍可见
    assert "能源市场" in html
    assert "布伦特原油上涨" in html


def test_macro_filter_drops_javascript_url() -> None:
    """不安全引用导致整段回退，不能保留无来源的财经断言。"""
    bundle = _macro_bundle_javascript()
    fake = _FakeLLM('<p><strong>风险。</strong>某主题陈述[1]</p>')
    summary = macro_filter.summarize([bundle], client=fake)
    assert summary is None


def test_macro_filter_keeps_safe_https_link() -> None:
    bundle = _macro_bundle_https()
    fake = _FakeLLM('<p><strong>能源市场。</strong>原油上涨[1]</p>')
    summary = macro_filter.summarize([bundle], client=fake)
    assert summary is not None
    assert 'href="https://reuters.com/x"' in summary.summary_html
    assert "<sup>" in summary.summary_html
    assert "color:#0563C1!important" in summary.summary_html


def test_macro_filter_always_moves_leading_footnotes_to_paragraph_end() -> None:
    bundle = MacroFeedBundle(
        source="Reuters",
        items=[
            MacroNewsItem(
                title="one",
                published_at=_DUMMY_DT,
                url="https://reuters.com/one",
                source="Reuters",
            ),
            MacroNewsItem(
                title="two",
                published_at=_DUMMY_DT,
                url="https://reuters.com/two",
                source="Reuters",
            ),
        ],
    )
    fake = _FakeLLM("<p>[1][2]<strong>能源市场。</strong>原油波动扩大。</p>")
    summary = macro_filter.summarize([bundle], client=fake)
    assert summary is not None
    html = summary.summary_html
    assert html.index("能源市场") < html.index("[1]") < html.index("[2]")
    assert html.endswith("</sup></p>")


def test_macro_filter_strips_unknown_tags() -> None:
    """LLM 输出 <div onclick> / <iframe> 等任意标签 → 一律剥掉。"""
    bundle = _macro_bundle_https()
    fake = _FakeLLM(
        '<p><iframe src="evil.com"></iframe>'
        '<div onclick="hack()">中国经济复苏[1]</div></p>'
    )
    summary = macro_filter.summarize([bundle], client=fake)
    assert summary is not None
    html = summary.summary_html
    assert "<iframe" not in html.lower()
    assert "onclick" not in html.lower()
    assert "evil.com" not in html or "&quot;" in html  # 已 escape
    # 文本仍可见
    assert "中国经济复苏" in html
