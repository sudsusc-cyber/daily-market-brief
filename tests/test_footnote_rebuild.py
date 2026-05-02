"""tests/test_footnote_rebuild.py — 脚注重写正则覆盖。

修复 bug:LLM 偶尔输出 (N) / 【N】 / <sup>(N)</sup> 等变体,
旧正则只匹配 [N] / <sup>[N]</sup> / ^N^,导致变体直接当纯文本输出
→ 既不蓝色也不可点击,且与匹配上的项混编后视觉上"跳号"。
"""

from __future__ import annotations

from src.processors import macro_filter, news_summarizer


def _re(s: str, *, mod=macro_filter) -> list[int]:
    """提取 s 中所有匹配的脚注 idx。"""
    return [mod._re_idx(m) for m in mod._FOOTNOTE_RE.finditer(s)]


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

    def test_caret_markdown(self) -> None:
        assert _re("一段话。^8^") == [8]

    def test_internal_whitespace_tolerated(self) -> None:
        """[ 9 ] / 【 10 】 这种带空格变体也接(真实场景后跟标点)。"""
        assert _re("结尾[ 9 ]。") == [9]
        assert _re("结尾【 10 】,后续。") == [10]

    def test_no_false_positive_inside_url(self) -> None:
        """URL 里 [foo] 后跟字母不应被当成 [N] —— 这里 N 是数字才有意义。"""
        # [10]a 后跟字母 → 不匹配
        assert _re("foo[10]abc") == []

    def test_no_false_positive_for_naked_half_width_parens(self) -> None:
        """中英文里 (N) 太常见了:'iOS (14)' / '步骤 (1)' / '(3) 月度' 都是日常文本,
        如果当成脚注会被吞掉 → 必须不匹配裸半角圆括号 (N)。"""
        assert _re("iOS (14) 发布。") == []
        assert _re("步骤 (1) 完成。") == []
        assert _re("(3) 月度报告。") == []
        # news_summarizer 同样行为
        assert _re("iOS (14) 发布。", mod=news_summarizer) == []

    def test_no_false_positive_for_naked_full_width_parens(self) -> None:
        """全角圆括号同理 — 日常文本中常见,不当脚注。"""
        assert _re("(图 3) 展示了趋势。") == []
        assert _re("条款 (二) 规定。") == []

    def test_sup_wrapped_parens_still_matched(self) -> None:
        """显式 <sup>(N)</sup> / <sup>(N)</sup> 仍是脚注 —— sup 包裹语义明确,无歧义。"""
        assert _re("结尾<sup>(7)</sup>。") == [7]
        assert _re("结尾<sup>(8)</sup>。") == [8]

    def test_multiple_in_one_string(self) -> None:
        """多个混合变体一并提取(裸圆括号已不当脚注,故用 sup 包裹形式)。"""
        s = "句一<sup>[1]</sup>句二【2】句三<sup>(3)</sup>句四^4^"
        assert _re(s) == [1, 2, 3, 4]

    def test_news_summarizer_regex_same_behavior(self) -> None:
        """news_summarizer 用相同正则,测一个变体足矣(后跟标点)。"""
        assert _re("foo<sup>(99)</sup>", mod=news_summarizer) == [99]
        assert _re("结尾【88】。", mod=news_summarizer) == [88]
