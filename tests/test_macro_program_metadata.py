from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.collectors.macro_news import MacroNewsItem
from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_selection import factual_excerpt, publishable_excerpt


@pytest.mark.parametrize('publisher,firm,person', [('Bloomberg','Robust AI Inc.','Veronica Willis'), ('Other TV','Northstar Corp.','Jane Smith')])
def test_program_roster_fragments_cannot_be_published_as_macro_facts(publisher, firm, person):
    summary = f"{publisher} brings you the latest news and analysis. Today's guests are Alex Doe, {firm} CEO&Founder, {person} is an Investment Strategy Analyst, Dr Lee, President of an Institute."
    item = SimpleNamespace(title='Stocks Rise As Jobs Report Eases Fed-Hike Worries', summary=summary, source=publisher)
    fragment = f'CEO&Founder, {person} is an Investment Strategy Analyst, Dr Lee, President of an Institute.'
    assert not publishable_excerpt(item, fragment)
    assert not publishable_excerpt(item, summary)
    assert factual_excerpt(item) == item.title
    item.title = 'Central bank raises interest rates by 25 basis points.'
    assert factual_excerpt(item) == item.title


def test_reported_speech_survives_adjacent_roster():
    summary = "Our guests include Dr Lee, Acme Inc. CEO. Lee said the central bank will raise rates by 25 basis points."
    item = SimpleNamespace(title='Interview', summary=summary, source='TV')
    assert publishable_excerpt(item, 'Lee said the central bank will raise rates by 25 basis points.')
    assert publishable_excerpt(item, 'Acme appoints Lee as CEO.')


def item(title, translated, index):
    row = MacroNewsItem(title, datetime(2026,10,3,tzinfo=UTC), f'https://example.com/{index}', 'News', '')
    row.source_excerpt = title
    row.translated_excerpt = translated
    return row


def test_repeated_causal_clause_mid_sentence_is_compacted_with_all_sources():
    rows = [item('Oil prices lower as G7 nations to release diesel stocks, Saudis reportedly plan attack on Houthis',
                 '油价下跌，G7 国家将释放柴油库存，据报道沙特计划袭击 Houthis',1),
            item('G7 nations to release diesel stocks as wars in Europe and Middle East constrain fuel supplies',
                 'G7 国家将释放柴油库存，因欧洲和中东的战争制约燃料供应',2)]
    evidence = []
    html, notes = _rebuild_safe_html('<p>[1][2]</p>', rows, evidence)
    assert html.count('G7 国家将释放柴油库存') == 1
    assert '欧洲和中东的战争制约燃料供应' in html
    assert len(notes) == len(evidence) == 2
    assert len({r['url'] for r in evidence}) == 2


def test_changed_state_is_not_elided_as_repeated_fact():
    rows = [item('G7 nations will release diesel stocks', 'G7 国家将释放柴油库存',1),
            item('G7 nations released diesel stocks as wars constrain fuel supplies', 'G7 国家已释放柴油库存，因战争制约燃料供应',2)]
    html, notes = _rebuild_safe_html('<p>[1][2]</p>',rows)
    assert '将释放' in html and '已释放' in html
    assert len(notes) == 2


def test_cause_first_translation_compacts_same_fact_and_preserves_cause():
    rows = [item('Oil prices lower as G7 nations to release diesel stocks, Saudis reportedly plan attack on Houthis',
                 '油价下跌，G7 国家将释放柴油库存，据报道沙特计划袭击 Houthis',1),
            item('G7 nations to release diesel stocks as wars in Europe and Middle East constrain fuel supplies',
                 '由于欧洲和中东的战争制约燃料供应，G7 国家将释放柴油库存。',2)]
    evidence=[]
    html, notes = _rebuild_safe_html('<p>[1][2]</p>',rows,evidence)
    assert html.count('G7 国家将释放柴油库存') == 1
    assert '欧洲和中东的战争制约燃料供应' in html and len(notes) == 2
    from src.processors.news_presentation import replay_presentation
    assert replay_presentation(evidence[1]['validated_text'],evidence[1]) == evidence[1]['output_text']


def test_exact_repeated_terminal_fact_does_not_need_a_causal_keyword():
    from src.processors.news_presentation import macro_context_text
    assert macro_context_text('欧洲和中东的战争制约燃料供应，G7 国家将释放柴油库存。','G7 国家将释放柴油库存') == '欧洲和中东的战争制约燃料供应'
    assert macro_context_text('供应紧张，G7 国家已释放柴油库存。','G7 国家将释放柴油库存') == '供应紧张，G7 国家已释放柴油库存。'
