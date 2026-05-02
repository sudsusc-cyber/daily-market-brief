"""单元测试:LLM client 的 system prompt 拼装与北京时间注入。

不调用 DeepSeek API。
"""

from __future__ import annotations

from src.processors.llm_client import (
    INVESTMENT_FRAMEWORK,
    build_system_prompt,
)


class TestBuildSystemPrompt:
    def test_includes_investment_framework(self) -> None:
        sp = build_system_prompt()
        assert "Buffett" in sp
        assert "Munger" in sp
        assert "DCA" in sp and "lump-sum" in sp.lower()
        assert INVESTMENT_FRAMEWORK in sp

    def test_includes_holdings_list(self) -> None:
        sp = build_system_prompt()
        for ticker in ["MSFT", "NVDA", "BRK.B", "0700.HK", "9992.HK"]:
            assert ticker in sp, f"持仓 {ticker} 应出现在 system prompt 中"

    def test_injects_beijing_time(self) -> None:
        # ADR-0001 §3:必须显式注入北京时间
        sp = build_system_prompt()
        assert "北京时间" in sp
        assert "年" in sp and "月" in sp and "日" in sp
        assert "星期" in sp

    def test_appends_task_extra(self) -> None:
        sp = build_system_prompt(task_extra="额外任务说明:测试一下。")
        assert "额外任务说明:测试一下。" in sp
        # task_extra 必须在 framework 后(顺序很重要)
        idx_framework = sp.index("Buffett")
        idx_extra = sp.index("额外任务说明")
        assert idx_extra > idx_framework

    def test_anti_ai_idiom_clause(self) -> None:
        # PLAN 第 10 节明令禁止 "亲" "赋能" 等 AI 腔
        sp = build_system_prompt()
        for word in ["AI 腔", "赋能", "抓手"]:
            assert word in sp
