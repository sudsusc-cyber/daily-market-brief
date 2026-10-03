from datetime import UTC, datetime
from types import SimpleNamespace

from src.collectors.macro_news import MacroNewsItem
from src.processors.macro_filter import _rebuild_safe_html
from src.processors.news_presentation import present, replay_presentation
from src.processors.news_selection import publishable_excerpt


def test_interview_topic_is_not_an_assertion_but_real_statement_survives():
    item=SimpleNamespace(title='Former Minister on economic outlook',summary='',source='News')
    assert not publishable_excerpt(item,'The former Minister discusses persistent inflation and slow hiring.')
    assert publishable_excerpt(item,'The former Minister warns inflation could accelerate.')
    assert publishable_excerpt(item,'The government discusses a new trade agreement.')


def test_employment_breakeven_is_domain_bound_and_replayable():
    source='A researcher believes the jobs report break even could settle at 50,000.'
    zh='研究员认为，就业报告的盈亏平衡点可能稳定在5万左右。'
    output=present(zh,original_text=source)
    assert '维持失业率稳定所需的月度新增就业量' in output.text
    assert '可能' in output.text and '5万' in output.text
    assert replay_presentation(zh,{'presentation_version':15,'excerpt':source})==output.text
    assert present(zh,original_text='Acme expects profit to reach break even.').text==zh
    assert present(zh,original_text=source,_version=14).text==zh


def test_report_attribution_moves_without_becoming_a_confirmed_claim():
    source='Acme warns about existential risks in IPO filing: report'
    text='Acme 警告 IPO 文件中的生存风险：报道。'
    assert present(text,original_text=source).text=='据报道，Acme 警告 IPO 文件中的生存风险'
    assert present(text,original_text='Unrelated source').text==text


def test_data_release_precedes_commentary_and_market_reaction():
    pairs=[('Stocks Rise As Jobs Report Eases Fed-Hike Worries','就业报告缓解美联储加息担忧，股市上涨。'),
           ('Payrolls increased by 29,000 in September.','9 月非农就业人数增加29,000。')]
    rows=[]
    for i,(en,zh) in enumerate(pairs):
        row=MacroNewsItem(en,datetime(2026,10,3,tzinfo=UTC),f'https://example.com/{i}','News','')
        row.source_excerpt=en
        row.translated_excerpt=zh
        rows.append(row)
    html,notes=_rebuild_safe_html('<p>[1][2]</p>',rows)
    assert len(notes)==2 and html.index('29,000')<html.index('股市上涨')
