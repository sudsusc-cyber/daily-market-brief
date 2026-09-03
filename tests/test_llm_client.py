"""单元测试:LLM client 的 system prompt 拼装与北京时间注入。

不调用 DeepSeek API。
"""

from __future__ import annotations

from types import SimpleNamespace

import requests

from src.config import BUY_STRATEGIES
from src.processors.llm_client import (
    DEFAULT_MODEL,
    INVESTMENT_FRAMEWORK,
    LLMClient,
    LLMUsage,
    _deepseek_flash_version_key,
    _select_latest_flash_model,
    build_system_prompt,
    resolve_latest_flash_model,
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

    def test_buy_rules_match_shared_strategy_configuration(self) -> None:
        sp = build_system_prompt()
        for strategy in BUY_STRATEGIES:
            line = next(line for line in sp.splitlines() if f"{', '.join(strategy.tickers)}:" in line)
            assert f"现价 ≤ {strategy.line_labels[-1]}均线 → lump-sum" in line
            if strategy.dca_line:
                assert f"现价 ≤ {strategy.line_labels[0]}均线 → DCA" in line
            else:
                assert "不设 DCA" in line
                assert "120 周" not in line
        assert "信号每天持续显示当前区间" in sp
        assert "250 个交易日" in sp

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


class _FakeModelsResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class TestDeepSeekModelResolver:
    def test_flash_version_key(self) -> None:
        assert _deepseek_flash_version_key("deepseek-v4-flash") == (4,)
        assert _deepseek_flash_version_key("deepseek-v4.1-flash") == (4, 1)
        assert _deepseek_flash_version_key("deepseek-v4-pro") is None
        assert _deepseek_flash_version_key("deepseek-chat") is None

    def test_selects_latest_flash_only(self) -> None:
        assert _select_latest_flash_model([
            "deepseek-chat",
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "deepseek-v5-pro",
            "deepseek-v5-flash",
        ]) == "deepseek-v5-flash"

    def test_resolve_latest_flash_model_uses_models_endpoint(self, monkeypatch) -> None:
        calls = []

        def fake_get(url, *, headers, timeout):
            calls.append((url, headers, timeout))
            return _FakeModelsResponse({
                "object": "list",
                "data": [
                    {"id": "deepseek-v4-flash", "object": "model"},
                    {"id": "deepseek-v5-pro", "object": "model"},
                    {"id": "deepseek-v5-flash", "object": "model"},
                ],
            })

        monkeypatch.setattr("src.processors.llm_client.requests.get", fake_get)

        selected = resolve_latest_flash_model("secret-key", base_url="https://api.deepseek.com/")

        assert selected == "deepseek-v5-flash"
        assert calls[0][0] == "https://api.deepseek.com/models"
        assert calls[0][1]["Authorization"] == "Bearer secret-key"
        assert calls[0][2] == 10

    def test_resolve_latest_flash_model_falls_back_on_failure(self, monkeypatch) -> None:
        def fake_get(*_args, **_kwargs):
            raise requests.Timeout("slow")

        monkeypatch.setattr("src.processors.llm_client.requests.get", fake_get)

        assert resolve_latest_flash_model("secret-key") == DEFAULT_MODEL


def test_client_disables_sdk_hidden_retries(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("src.processors.llm_client.OpenAI", FakeOpenAI)
    LLMClient(api_key="secret")

    assert calls[0]["max_retries"] == 0


def test_chat_can_explicitly_disable_deepseek_thinking(monkeypatch) -> None:
    captured: dict = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="已完成"))],
                usage=None,
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("src.processors.llm_client.OpenAI", FakeOpenAI)

    response = LLMClient(api_key="secret").chat("翻译", thinking=False)

    assert response.text == "已完成"
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_cost_does_not_double_count_reasoning(monkeypatch) -> None:
    monkeypatch.setattr("src.processors.llm_client.OpenAI", lambda **_kwargs: object())
    client = LLMClient(api_key="secret")
    client.cumulative = LLMUsage(output_tokens=2048, reasoning_tokens=2048)

    assert client.estimate_cost_cny() == 2048 * 4.0e-6


def test_web_search_is_forced_and_domain_guard_is_injected(monkeypatch) -> None:
    captured: dict = {}

    class FakeResponses:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_text='{"url":"https://www.sec.gov/example"}',
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=5,
                    input_tokens_details=SimpleNamespace(cached_tokens=2),
                    output_tokens_details=SimpleNamespace(reasoning_tokens=1),
                ),
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr("src.processors.llm_client.OpenAI", FakeOpenAI)
    response = LLMClient(api_key="secret").search_web(
        "查找最新季度财报",
        allowed_domains=("sec.gov", "apple.com"),
    )

    assert response.text is not None
    assert captured["tools"] == [{"type": "web_search"}]
    assert captured["tool_choice"] == {"type": "web_search"}
    assert "只允许返回这些域名" in captured["instructions"]
    assert "sec.gov" in captured["instructions"]
    assert response.usage.cache_hit_tokens == 2


def test_web_search_extracts_deepseek_output_content_when_output_text_is_empty(monkeypatch) -> None:
    class FakeResponses:
        def create(self, **_kwargs):
            return SimpleNamespace(
                output_text="",
                output=[SimpleNamespace(content=[SimpleNamespace(text='{"status":"ok"}')])],
                usage=None,
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.responses = FakeResponses()

    monkeypatch.setattr("src.processors.llm_client.OpenAI", FakeOpenAI)
    response = LLMClient(api_key="secret").search_web(
        "查找最新数据", allowed_domains=("invesco.com",)
    )

    assert response.text == '{"status":"ok"}'
    assert response.error is None


def test_web_search_requires_domain_allowlist(monkeypatch) -> None:
    monkeypatch.setattr("src.processors.llm_client.OpenAI", lambda **_kwargs: object())
    response = LLMClient(api_key="secret").search_web(
        "查找财报",
        allowed_domains=(),
    )
    assert response.text is None
    assert response.error == "WebSearchDomainAllowlistRequired"
