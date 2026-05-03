"""tests/test_footnote_rebuild.py — 脚注重写正则覆盖。

修复 bug:LLM 偶尔输出 (N) / 【N】 / <sup>(N)</sup> 等变体,
旧正则只匹配 [N] / <sup>[N]</sup> / ^N^,导致变体直接当纯文本输出
→ 既不蓝色也不可点击,且与匹配上的项混编后视觉上"跳号"。

正则与 idx 抽取已上提到 src.processors.html_safe(news_summarizer 与
macro_filter 共用,以前各自定义的 _FOOTNOTE_RE 已移除)。
"""

from __future__ import annotations

from src.processors.html_safe import FOOTNOTE_RE, footnote_idx


def _re(s: str) -> list[int]:
    """提取 s 中所有匹配的脚注 idx。"""
    return [footnote_idx(m) for m in FOOTNOTE_RE.finditer(s)]


class TestRegexVariants:
    """两个 summarizer 共用相同正则,任选其一测,行为一致。"""

    def test_html_sup_brackets(self) -> None:
        assert _re("foo<sup>[1]</sup>bar") == [1]

    def test_html_sup_full_width_brackets(self) -> None:
        """LLM 偶尔输出 <sup>【N】</sup>,旧版漏匹配。"""
        assert _re("foo<sup>【2】</sup>bar") == [2]

    def test_html_sup_parens_half_width(self) -> None:
        assert _re("foo<sup>(3)</sup>bar") == [3]

    def test_html_sup_parens_full_width(self) -> None:
        assert _re("foo<sup>(4)</sup>bar") == [4]

    def test_bare_brackets(self) -> None:
        assert _re("一段话。[5]") == [5]

    def test_bare_full_width_brackets(self) -> None:
        assert _re("一段话。【6】") == [6]

    def test_bare_parens_full_width(self) -> None:
        assert _re("一段话。(7)") == [7]

    def test_caret_markdown(self) -> None:
        assert _re("一段话。^8^") == [8]

    def test_hash_prefix_bare(self) -> None:
        """LLM 偶尔把 prompt 里 '#' 编号当成标记的一部分,输出 [#N]。"""
        assert _re("中东冲突。[#9][#18]") == [9, 18]

    def test_hash_prefix_in_sup(self) -> None:
        assert _re("foo<sup>[#11]</sup>bar") == [11]

    def test_hash_prefix_full_width_brackets(self) -> None:
        assert _re("结尾【#12】。") == [12]

    def test_internal_whitespace_tolerated(self) -> None:
        """[ 9 ] / 【 10 】 这种带空格变体也接(真实场景后跟标点)。"""
        assert _re("结尾[ 9 ]。") == [9]
        assert _re("结尾【 10 】,后续。") == [10]

    def test_no_false_positive_inside_url(self) -> None:
        """URL 里 [foo] 后跟字母不应被当成 [N] —— 这里 N 是数字才有意义。"""
        # [10]a 后跟字母 → 不匹配
        assert _re("foo[10]abc") == []

    def test_multiple_in_one_string(self) -> None:
        """多个混合变体一并提取。"""
        s = "句一<sup>[1]</sup>句二【2】句三(3)句四^4^"
        assert _re(s) == [1, 2, 3, 4]

    def test_shared_regex_same_behavior(self) -> None:
        """两个 summarizer 共用同一 FOOTNOTE_RE,任一变体都应一致命中。"""
        assert _re("foo<sup>(99)</sup>") == [99]
        assert _re("结尾【88】。") == [88]
