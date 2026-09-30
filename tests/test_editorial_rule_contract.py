"""Original editorial behavior must survive factual and presentation hardening."""
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.collectors.macro_news import MacroFeedBundle, MacroNewsItem
from src.config import HOLDINGS
from src.processors import holdings_intro, macro_filter, news_summarizer
from src.processors.editorial_history import EditorialHistory
from src.processors.news_selection import company_candidate, macro_candidate
from src.processors.thesis.extractor import _verified_grounding_row

NOW = datetime(2026, 9, 30, tzinfo=UTC)
PROSE = "衡量一段旅程，不只看眼前走了多远，也看脚下的路是否值得长行；时间不会替人作答，却能让扎实的判断慢慢显出分量。"
OTHER = "树木的年轮从不催促季节，根系却始终在看不见的地方生长；认真理解一门生意的价值，也需要把热闹留在窗外，给思考留下足够宽阔的余地。"


class Client:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = []

    def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(text=self.texts.pop(0), error="timeout")


def signal(kind="NONE", error=None):
    return SimpleNamespace(signal=kind, error=error)


@pytest.mark.parametrize("states,expected", [
    ([signal()], "尚未出现"),
    ([signal("DCA"), signal()], "部分标的"),
    ([signal("DCA"), signal("LUMP_SUM")], "均处于"),
    ([signal(error="timeout")], "待核验"),
])
def test_dynamic_intro_uses_only_actual_signal_context(states, expected):
    client = Client("{信号背景}" + PROSE)
    text = holdings_intro.write_intro(states, client=client)
    assert expected in text and 60 <= len(text) <= 110
    assert PROSE in text and "只处于" not in text
    assert len(client.calls) == 1 and client.calls[0][1]["thinking"] is False


def test_published_history_retries_repeated_reflection_without_consuming_draft(tmp_path):
    history = EditorialHistory(tmp_path / "history.json", date(2026, 9, 30))
    old = holdings_intro.signal_context([signal()]) + PROSE
    history.remember("holdings_intro", "", old)
    history.commit()
    before = history.path.read_bytes()
    client = Client("{信号背景}" + PROSE, "{信号背景}" + OTHER)
    new = holdings_intro.write_intro([signal("DCA")], client=client, history=history)
    assert new and OTHER in new and len(client.calls) == 2
    assert "recent_repeat" in client.calls[1][0][0]
    assert history.path.read_bytes() == before and len(history.rows) == 1


@pytest.mark.parametrize("invention", [
    "两地各有一处，其余均在参考线之上。", "全部持仓已经突破参考线。",
    "建议立即买入并加仓。", "收益率达到20%。", "今日港股普遍上涨。",
])
def test_literary_intro_cannot_add_current_market_claims(invention):
    client = Client("{信号背景}" + invention + PROSE, "{信号背景}" + invention + PROSE)
    assert holdings_intro.write_intro([signal()], client=client) is None
    assert len(client.calls) == 2


@pytest.mark.parametrize("issuer", ["Microsoft", "Acme", "Northstar"])
def test_price_colour_does_not_delete_an_operating_announcement(issuer):
    good = NewsItem(f"{issuer} shares jump as it reports revenue growth", NOW, "https://example.com/news", "Source")
    noise = NewsItem(f"{issuer} shares jump in trading", NOW, "https://example.com/noise", "Source")
    assert company_candidate(good, "MSFT")
    assert not company_candidate(noise, "MSFT")


def test_symbolic_colour_does_not_delete_substantive_trade_policy():
    assert not macro_candidate(MacroNewsItem("Panda diplomacy brings gifts", NOW, "https://example.com/1", "Source"))
    assert macro_candidate(MacroNewsItem("Panda diplomacy accompanies new trade agreement", NOW, "https://example.com/2", "Source"))


@pytest.mark.parametrize("issuer", ["Acme", "Rivian", "Northstar"])
def test_complete_business_fact_after_promotional_lead_is_not_issuer_whitelisted(issuer):
    from src.processors.news_selection import factual_excerpt
    fact = f"{issuer} announced a cloud contract."
    row = NewsItem("Move over, rivals. " + fact, NOW, "https://example.com/event", "Source")
    assert factual_excerpt(row) == fact


def test_macro_ninth_candidate_can_be_selected_then_translated():
    rows = [MacroNewsItem(f"宏观观察第{i}项。", NOW, f"https://example.com/{i}", "Source") for i in range(8)]
    rows.append(MacroNewsItem("Central bank raises interest rates.", NOW, "https://example.com/decision", "Source"))
    client = Client("<p>货币政策。Central bank raises interest rates.[9]</p>", "▦ 1: 央行上调利率。")
    result = macro_filter.summarize([MacroFeedBundle("Source", rows)], client=client)
    assert result and "央行上调利率" in result.summary_html and len(client.calls) == 2
    assert rows[-1].url in result.summary_html and result.evidence[0]["excerpt"] == rows[-1].title
    assert _verified_grounding_row(result, result.evidence[0])
    assert result.selection_audit[0]["considered"] == 9


def test_company_sixth_candidate_can_be_selected_then_translated():
    rows = [NewsItem(f"Microsoft 宣布产品更新{i}。", NOW, f"https://example.com/{i}", "Source") for i in range(5)]
    rows.append(NewsItem("Microsoft reports revenue growth.", NOW, "https://example.com/results", "Source"))
    holding = next(h for h in HOLDINGS if h.ticker == "MSFT")
    client = Client("<strong>微软</strong> —— Microsoft reports revenue growth.[6]", "▦ 1: Microsoft 公布营收增长。")
    result = news_summarizer.summarize([CompanyNewsBundle(holding, rows)], client=client)
    assert result and "营收增长" in result.summary_html and len(client.calls) == 2
    assert rows[-1].url in result.summary_html and result.evidence[0]["excerpt"] == rows[-1].title
    assert _verified_grounding_row(result, result.evidence[0])


def test_model_event_ranking_not_replaced_by_permanent_topic_priority():
    facts = ["全球跨境支付系统停止清算。", "美联储公布利率决定。", "美国公布通胀数据。", "中国与美国举行贸易峰会。"]
    rows = [MacroNewsItem(text, NOW, f"https://example.com/{i}", "Source") for i, text in enumerate(facts)]
    raw = "".join(f"<p>{fact}[{i}]</p>" for i, fact in enumerate(facts, 1))
    result = macro_filter.summarize([MacroFeedBundle("Source", rows)], client=Client(raw))
    assert result and result.summary_html.count("<p ") == 3
    assert facts[0] in result.summary_html and facts[-1] not in result.summary_html
    assert {r['url'] for r in result.evidence} == {r.url for r in rows[:3]}
