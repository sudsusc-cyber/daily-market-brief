from datetime import UTC, datetime
from types import SimpleNamespace

from bs4 import BeautifulSoup

from src.collectors.figures import FIGURES, FigureBundle, FigureMention
from src.processors.editorial_history import EditorialHistory
from src.processors.figure_filter import assign_footnotes, filter_one
from src.processors.news_presentation import voice_text
from src.processors.thesis.extractor import _verified_grounding_row
from src.renderer.render import render_email


def test_all_configured_speakers_lose_only_neutral_leading_attribution():
    body = '使用他人的 AI 模型是竞争，与美国视其为窃取的观点相冲突。'
    for cn, _, _, en in FIGURES:
        for name in (cn, en):
            assert voice_text(name + ' 称' + body, cn) == body
            assert voice_text(name + ' 表示：' + body, cn) == body
    assert voice_text('萨提亚·纳德拉称业务增长仍存在不确定性。', '纳德拉') == '业务增长仍存在不确定性。'


def test_context_negation_warning_and_other_people_are_not_removed():
    for text in (
        '巴菲特称黄仁勋的判断仍有不确定性。',
        '黄仁勋否认曾表示模型没有风险。',
        '黄仁勋警告模型使用可能带来风险。',
        '黄仁勋在周二表示模型使用仍有风险。',
        '黄仁勋的同事称模型使用仍有风险。',
        '“黄仁勋称模型没有风险”，这一说法并不属实。',
        '黄仁勋表示了对模型安全的担忧。',
        '黄仁勋称。',
        '黄仁勋称赞了其他公司的技术。',
        '黄仁勋说服董事会继续投资。',
    ):
        assert voice_text(text, '黄仁勋') == text
    assert voice_text('黄仁勋称巴菲特认为模型使用仍有风险。', '黄仁勋') == '巴菲特认为模型使用仍有风险。'


def test_publication_preserves_original_evidence_and_history(tmp_path):
    title = "Jensen Huang says using others' AI models is competition, clashing with U.S. view of theft"
    translation = 'Jensen Huang 称使用他人的 AI 模型是竞争，与美国视其为窃取的观点相冲突'
    now = datetime(2026, 9, 29, 5, tzinfo=UTC)
    item = FigureMention(title, '', now, 'https://example.com/voice', 'Source')
    item.source_excerpt = title
    item.translated_excerpt = translation
    bundle = FigureBundle('黄仁勋', 'query', 'Jensen Huang', [item])
    client = SimpleNamespace(chat=lambda *a, **k: SimpleNamespace(text='▦ 1: yes | score=5 | '+translation))
    result = filter_one(bundle, client=client)
    assert len(result.items) == 1
    point = result.items[0]
    assert point.text == translation.removeprefix('Jensen Huang 称')
    row = point.evidence[0]
    assert row['original_title'] == row['excerpt'] == title
    assert row['validated_text'] == translation
    assert row['output_text'] == point.text
    assert _verified_grounding_row(point, row)
    assert not _verified_grounding_row(point, dict(row, output_text='模型使用完全没有任何风险。'))
    assert not _verified_grounding_row(point, dict(row, presentation_speaker='巴菲特'))
    notes = assign_footnotes([result])
    rendered = render_email(signals=[], generated_at=now, figures=[bundle],
                            figure_summaries=[result], figure_footnotes=notes)
    soup = BeautifulSoup(rendered, 'html.parser')
    quote = soup.select_one('blockquote')
    assert point.text in quote.get_text()
    assert 'Jensen Huang' not in quote.get_text() and '黄仁勋' not in quote.get_text()
    assert '黄仁勋' in soup.get_text() and quote.select_one('sup a')['href'] == item.url
    history = EditorialHistory(tmp_path/'history.json', now.date())
    history.capture(None, [result])
    assert history.rows[0]['text'] == translation
    assert history.filter_figure(result).items == []
