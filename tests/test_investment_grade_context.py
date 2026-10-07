from src.processors.thesis.renderer import _rule_for


def test_actual_spacex_debt_grade_does_not_supply_investment_action():
    row = {
        'excerpt': 'Space Exploration Technologies Corp. is reportedly planning to raise $40 billion in bank loans and investment-grade debt, led by Apollo Global Management, to buy Nvidia Corp. chips.',
        'output_text': '据报道，Space Exploration Technologies Corp.计划通过银行贷款和投资级债券筹集 400 亿美元，由 Apollo Global Management 牵头，用于购买 Nvidia Corp.芯片。',
    }
    assert _rule_for(row) is None


def test_real_investment_stays_eligible_alongside_a_debt_grade():
    row = {
        'excerpt': 'Nvidia plans to invest $4 billion in chips, financed by investment-grade debt.',
        'output_text': '英伟达计划投资 40 亿美元用于芯片，资金来自投资级债券。',
    }
    assert _rule_for(row).key == 'strategic-negotiation'
