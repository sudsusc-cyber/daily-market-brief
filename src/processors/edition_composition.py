"""Compose verified source rows without treating similarity as equivalence."""
import re
from copy import deepcopy

from src.processors.news_presentation import company_body
from src.utils.news_facts import canonical_fact


def _lease_record(text):
    # Closed affirmative lease grammar. Unknown wording, negation, dates,
    # changed counterparties or quantities remain independent facts.
    text = re.sub(r'\s+', '', text).rstrip('。.')
    text = re.sub(r'^英媒：', '', text)
    actor = r'(?P<actor>[\w\u4e00-\u9fff]+?)'
    partner = r'(?P<partner>[\w\u4e00-\u9fff]+?)'
    quantity = r'(?P<count>\d+(?:\.\d+)?(?:万|亿)?)(?:枚|片)'
    chip = r'(?P<ai>AI)?(?:芯片|晶片)'
    simple = re.fullmatch(actor + r'向' + partner + r'(?:租用|租赁)' + quantity + chip + r'(?:加速推进AI布局)?', text)
    detailed = re.fullmatch(actor + r'与' + partner + r'签订(?P<term>[一二三四五六七八九十\d]+年)协议，租赁' + quantity + chip, text)
    match = detailed or simple
    if not match:
        return None
    return (match['actor'], match['partner'], match['count']), bool(detailed), bool(match['ai'])


def _broad_expansion_covered(broad, detailed):
    """A tentative country-level expansion teaser adds no separate event when
    a same-issuer report specifies additional investment in that country.

    Require the same monetary baseline and explicit geography; unknown
    jurisdictions/qualifiers remain separate rows rather than guessed matches.
    """
    from src.processors.translation_guard import _quantities

    if str(broad.get('published_at', ''))[:10] != str(detailed.get('published_at', ''))[:10]:
        return False
    title = broad.get('original_title', '')
    match = re.fullmatch(r"(?P<issuer>[A-Za-z .-]+)['’]s (?P<amount>\$[\d.,]+ (?:Billion|Million)) (?P<country>U\.S\.|US|United States) Expansion May Be Getting Even Bigger", title, re.I)
    other = detailed.get('original_title', '')
    if not match or not other.casefold().startswith(match['issuer'].casefold() + ' '):
        return False
    planned = re.fullmatch(re.escape(match['issuer']) + r" (?:Reportedly )?(?:Weighs|Considers) (?P<new_place>[A-Za-z ]+) (?:Chip |Factory |Manufacturing )?Investment On Top Of (?P<amount>\$[\d.,]+(?:B|M| Billion| Million)) (?P<old_place>[A-Za-z ]+) Push", other, re.I)
    if not planned:
        return False
    # This is a geographic relation, not an issuer exception. Unknown locations
    # do not acquire country identity from proximity or an LLM assertion.
    us_states = {'arizona', 'texas', 'california', 'ohio', 'new york', 'oregon', 'washington'}
    new_place = re.sub(r' (?:Chip|Factory|Manufacturing)$', '', planned['new_place'], flags=re.I).casefold()
    if new_place not in us_states or planned['old_place'].casefold() not in us_states:
        return False
    return _quantities(match['amount']) == _quantities(planned['amount'])


def compose_company_rows(rows, ticker):
    rows = deepcopy(rows)
    # Introduce an explicitly named product before "the new model" references.
    # The full sentences and their attached sources move together.
    rows.sort(key=lambda row: bool(re.search(r'该(?:新)?(?:模型|产品|平台)|the (?:new )?(?:model|product|platform)', row['output_text'], re.I)))
    # Keep a full source mapping for a subsumed teaser, attached to the more
    # specific report; source links are preserved by the caller.
    removed = set()
    for index, row in enumerate(rows):
        for other in rows:
            if other is not row and _broad_expansion_covered(row, other):
                other.setdefault('supporting_sources', []).append(deepcopy(row))
                removed.add(index)
                break
    rows = [row for index, row in enumerate(rows) if index not in removed]
    kept = []
    for row in rows:
        record = _lease_record(row['output_text'])
        duplicate = next((other for other in kept if canonical_fact(other['output_text']) == canonical_fact(row['output_text'])), None)
        if not duplicate and record:
            for other in kept:
                prior = _lease_record(other['output_text'])
                if (prior and prior[0] == record[0] and prior[1] != record[1]
                        and str(other.get('published_at', ''))[:10] == str(row.get('published_at', ''))[:10]):
                    # A generic chip record is contained in an explicit AI chip
                    # record, but never erase an AI qualifier in the reverse.
                    richer, weaker = (row, other) if record[1] else (other, row)
                    rich_record, weak_record = (record, prior) if record[1] else (prior, record)
                    if weak_record[2] and not rich_record[2]:
                        continue
                    richer.setdefault('supporting_sources', []).append(weaker)
                    if richer is row:
                        kept[kept.index(other)] = row
                    duplicate = other
                    break
        if duplicate:
            if canonical_fact(duplicate['output_text']) == canonical_fact(row['output_text']):
                duplicate.setdefault('supporting_sources', []).append(row)
            continue
        kept.append(row)
    for row in kept:
        if row.get('presentation_version', 0) >= 10:
            row['presentation_company'] = ticker
            row['output_text'] = company_body(row['output_text'], ticker)
    return kept
