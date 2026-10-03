from datetime import UTC, datetime
from types import SimpleNamespace

from src.collectors.company_news import CompanyNewsBundle, NewsItem
from src.config import HOLDINGS
from src.processors import news_summarizer
from src.processors.news_selection import business_fact, company_candidate, factual_excerpt


def item(title, ticker, suffix, summary=''):
    return NewsItem(title, datetime(2026, 10, 3, tzinfo=UTC),
                    'https://example.com/' + suffix, 'Reuters', summary,
                    holding_ticker=ticker)


def test_operational_privacy_summary_is_used_before_multi_subject_headline():
    original = 'Apple plans to update macOS to clearly notify users whenever AI agents attempt to access their system data.'
    news = item('Apple Takes Aim at AI Agents, Wants Mac Users to Know When Their Data Is Accessed as Meta Tackles Muse Privacy Claims',
                'AAPL', 'privacy', original)
    assert business_fact(original)
    assert company_candidate(news, 'AAPL')
    assert factual_excerpt(news) == original
    assert news.summary == original


def test_policy_change_event_is_not_specific_to_one_company():
    for statement in (
        'Microsoft changes its data access permissions for enterprise applications.',
        'Google revised its privacy rules for customer accounts.',
        '腾讯调整云服务的数据访问权限。',
    ):
        assert business_fact(statement)


def setup_news():
    apple = item('苹果发布新款芯片。', 'AAPL', 'apple')
    untranslated = item('Nvidia announced a new enterprise chip.', 'NVDA', 'bad')
    alternate = item('英伟达发布面向企业的新款芯片。', 'NVDA', 'alternative')
    bundles = [CompanyNewsBundle(next(h for h in HOLDINGS if h.ticker == ticker), rows)
               for ticker, rows in [('AAPL', [apple]), ('NVDA', [untranslated, alternate])]]
    return bundles


def test_one_valid_row_does_not_stop_reselection_after_other_company_fails(monkeypatch):
    monkeypatch.setattr(news_summarizer, 'recover_selected_translations', lambda *a, **k: False)
    replies = iter([
        '<strong>苹果</strong> —— 苹果发布新款芯片。[1]\n<strong>英伟达</strong> —— Nvidia announced a new enterprise chip.[2]',
        '<strong>英伟达</strong> —— 英伟达发布面向企业的新款芯片。[3]',
    ])
    calls = []
    def chat(payload, **kwargs):
        calls.append((payload, kwargs))
        return SimpleNamespace(text=next(replies), error=None)
    result = news_summarizer.summarize(setup_news(), client=SimpleNamespace(chat=chat))
    assert len(calls) == 2
    assert len(result.footnotes) == 2
    assert {f.url for f in result.footnotes} == {'https://example.com/apple', 'https://example.com/alternative'}
    assert any(row['phase'] == 'coverage_recovery' and row['status'] == 'recovered'
               for row in result.selection_audit)
    assert result.content_rejections  # Original failed evidence is still diagnosable.


def test_supplement_cannot_reuse_rejected_reference_or_erase_valid_row(monkeypatch):
    monkeypatch.setattr(news_summarizer, 'recover_selected_translations', lambda *a, **k: False)
    replies = iter([
        '<strong>苹果</strong> —— 苹果发布新款芯片。[1]\n<strong>英伟达</strong> —— Nvidia announced a new enterprise chip.[2]',
        '<strong>英伟达</strong> —— 英伟达已获监管批准。[2]',
    ])
    result = news_summarizer.summarize(setup_news(), client=SimpleNamespace(
        chat=lambda *a, **k: SimpleNamespace(text=next(replies), error=None)))
    assert len(result.footnotes) == 1
    assert result.footnotes[0].url == 'https://example.com/apple'
    assert '监管批准' not in result.summary_html
    assert next(row for row in result.selection_audit if row['phase'] == 'coverage_recovery')['status'] == 'no_supported_alternative'


def test_recovery_does_not_fill_with_price_flow_or_brand_noise(monkeypatch):
    monkeypatch.setattr(news_summarizer, 'recover_selected_translations', lambda *a, **k: False)
    bundles = setup_news()
    bundles[1].items[-1] = item('Nvidia shares climb to a record high.', 'NVDA', 'price')
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(text='<strong>苹果</strong> —— 苹果发布新款芯片。[1]\n<strong>英伟达</strong> —— Nvidia announced a new enterprise chip.[2]', error=None)
    result = news_summarizer.summarize(bundles, client=SimpleNamespace(chat=chat))
    assert len(calls) == 1
    assert len(result.footnotes) == 1


def test_supplement_timeout_retains_previously_verified_rows(monkeypatch):
    monkeypatch.setattr(news_summarizer, 'recover_selected_translations', lambda *a, **k: False)
    calls = []
    def chat(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise TimeoutError('simulated supplement failure')
        return SimpleNamespace(text='<strong>苹果</strong> —— 苹果发布新款芯片。[1]\n<strong>英伟达</strong> —— Nvidia announced a new enterprise chip.[2]', error=None)
    result = news_summarizer.summarize(setup_news(), client=SimpleNamespace(chat=chat))
    assert len(result.footnotes) == 1
    assert result.footnotes[0].url == 'https://example.com/apple'
    assert any(row['phase'] == 'coverage_recovery_failure' and row['error'] == 'TimeoutError'
               for row in result.selection_audit)


def test_supplement_translation_exception_retains_previously_verified_rows(monkeypatch):
    translations = []
    def recover(*args, **kwargs):
        translations.append(1)
        if len(translations) == 2:
            raise RuntimeError('simulated supplement translation failure')
        return False
    monkeypatch.setattr(news_summarizer, 'recover_selected_translations', recover)
    replies = iter([
        '<strong>苹果</strong> —— 苹果发布新款芯片。[1]\n<strong>英伟达</strong> —— Nvidia announced a new enterprise chip.[2]',
        '<strong>英伟达</strong> —— 英伟达发布面向企业的新款芯片。[3]',
    ])
    result = news_summarizer.summarize(setup_news(), client=SimpleNamespace(
        chat=lambda *a, **k: SimpleNamespace(text=next(replies), error=None)))
    assert len(result.footnotes) == 1
    assert result.footnotes[0].url == 'https://example.com/apple'
    assert any(row['phase'] == 'coverage_recovery_failure' and row['error'] == 'RuntimeError'
               for row in result.selection_audit)
