from src.processors.news_presentation import present, replay_presentation

SOURCE = 'We are eight days out from the expected October 13 launch of Apple’s new smart home device, the HomePad.'
TRANSLATED = '距离 Apple 新款智能家居设备 HomePad 预计于 10 月 13 日发布还有八天。'


def test_actual_homepad_countdown_is_scoped_to_reporting_time():
    result = present(TRANSLATED, original_text=SOURCE)
    assert result.text == '报道当时称，' + TRANSLATED
    assert 'reported_countdown_context' in result.operations
    assert '八天' in result.text and '预计' in result.text and '10 月 13 日' in result.text


def test_absolute_date_and_reported_duration_are_not_countdowns():
    for source in ['Apple expects to launch HomePad on October 13.', 'Apple tested its device for eight days.']:
        assert present(TRANSLATED, original_text=source).text == TRANSLATED


def test_archive_version_19_retains_original_countdown_presentation():
    assert present(TRANSLATED, original_text=SOURCE, _version=19).text == TRANSLATED
    assert replay_presentation(TRANSLATED, {'presentation_version':19, 'excerpt':SOURCE}) == TRANSLATED
