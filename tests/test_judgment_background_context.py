from src.processors.thesis.renderer import _rule_for


def test_actual_surface_event_does_not_make_previous_revenue_new_evidence():
    row = {
        'excerpt': 'Microsoft hosts its Windows and Surface event on October 7, with a focus on on-device AI after Windows OEM and Devices revenue fell 7% even as Azure and other cloud services grew 43%.',
        'output_text': '微软于 10 月 7 日举办 Windows 和 Surface 活动，重点放在端侧 AI，此前 Windows OEM 和设备收入下降 7%，即便 Azure 及其他云服务增长 43%。',
    }
    assert _rule_for(row) is None


def test_explicitly_previous_financial_background_is_not_a_current_watchpoint():
    row = {
        'excerpt': 'Microsoft hosts a new event. Previously, revenue rose 43%.',
        'output_text': '微软举办新活动。此前，收入增长 43%。',
    }
    assert _rule_for(row) is None


def test_current_financial_fact_remains_when_followed_by_background():
    row = {
        'excerpt': 'Microsoft revenue rose 10%. Previously, the company released Surface.',
        'output_text': '微软收入增长 10%。此前，公司发布了 Surface。',
    }
    assert _rule_for(row).key == 'operating-performance'


def test_new_financial_sentence_survives_previous_context():
    row = {
        'excerpt': 'Previously, Microsoft revenue rose 5%. Microsoft now reports revenue growth of 10%.',
        'output_text': '此前，微软收入增长 5%；微软现报告收入增长 10%。',
    }
    assert _rule_for(row).key == 'operating-performance'
