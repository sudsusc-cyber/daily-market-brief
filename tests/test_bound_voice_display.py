from src.processors.news_presentation import PRESENTATION_VERSION, replay_presentation, voice_text


def test_surname_is_elided_only_with_same_source_binding_and_speech_act_stays():
    text = 'Smith 警告称，过度限制可能加快替代技术的发展。'
    binding = {'person': 'Jane Smith', 'kind': 'resolved_surname', 'full_name': 'Jane Smith'}
    assert voice_text(text, 'Jane Smith') == text
    assert voice_text(text, 'Jane Smith', binding=binding) == '警告称，过度限制可能加快替代技术的发展。'
    assert voice_text(text, 'John Smith', binding=binding) == text
    assert voice_text('John ' + text, 'Jane Smith', binding=binding) == 'John ' + text
    assert voice_text(text, 'Jane Smith', binding=binding, _version=15) == text


def test_bound_neutral_attribution_does_not_leave_redundant_surname():
    binding = {'person': 'Jane Smith', 'kind': 'resolved_surname', 'full_name': 'Jane Smith'}
    assert voice_text('Smith 表示，长期需求仍有不确定性。', 'Jane Smith', binding=binding) == '长期需求仍有不确定性。'
    assert voice_text('Smith 的同事表示长期需求不确定。', 'Jane Smith', binding=binding).startswith('Smith 的同事')
    assert voice_text('Smith 否认曾表示长期需求没有风险。', 'Jane Smith', binding=binding) == '否认曾表示长期需求没有风险。'


def test_presentation_replay_keeps_legacy_byline_behavior():
    text = 'Smith 警告称，过度限制可能加快替代技术的发展。'
    row = {'presentation_version': PRESENTATION_VERSION, 'presentation_speaker': 'Jane Smith',
           'speaker_attribution': {'person': 'Jane Smith', 'kind': 'resolved_surname', 'full_name': 'Jane Smith'}}
    assert replay_presentation(text, row) == '警告称，过度限制可能加快替代技术的发展。'
    assert replay_presentation(text, dict(row, presentation_version=15)) == text


def test_speaker_date_failures_have_distinct_audit_reasons():
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from src.processors.speaker_attribution import source_date_error

    item = SimpleNamespace(url='https://news.google.com/rss/articles/example', title='Jane Smith says demand is rising.',
                           snippet='', source='Source', published_at=datetime(2026, 10, 3, tzinfo=UTC))
    assert source_date_error(item) == 'speaker_date_unverified'
    item.source_published_at = '2026-08-31T10:00:00Z'
    assert source_date_error(item) == 'speaker_date_outside_window'
    item.source_published_at = '2026-10-02T10:00:00'
    assert source_date_error(item) == 'speaker_date_invalid'
    item.source_published_at = '2026-10-02T10:00:00Z'
    assert not source_date_error(item)
