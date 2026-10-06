"""Presentation-only sentence endings for already sanitized news HTML."""

import re

from bs4 import BeautifulSoup, NavigableString


def sentence_end(value: str) -> str:
    """One Chinese sentence stop; preserve question/exclamation and closing quotes."""
    text = str(value or "").rstrip()
    if not text:
        return text
    closing = re.search(r'[”’」』"\']+$', text)
    suffix = closing.group() if closing else ""
    stem = text[: -len(suffix)].rstrip() if suffix else text
    # Keep the original interrogative/exclamatory meaning, just normalize glyphs.
    if stem.endswith("…"):
        return text
    endings = {"?": "？", "？": "？", "!": "！", "！": "！"}
    if stem and stem[-1] in endings:
        return stem.rstrip("?？!！") + endings[stem[-1]] + suffix
    if suffix and not stem.endswith(("。", ".")):
        opening = re.search(r'[“‘「『"\'][^“‘「『"\']*$', stem)
        if opening:
            prefix = stem[:opening.start()].rstrip()
            # A quoted object/phrase belongs inside the surrounding sentence;
            # only a standalone reported sentence gets its stop inside quotes.
            reported = not prefix or re.search(
                r'(?:说|称|表示|指出|警告|强调|认为|问|回答|写道)[：:,，]?$', prefix
            )
            if not reported:
                return stem.rstrip(".;；,，:： ") + suffix + "。"
    return stem.rstrip("。.;；,，:： ") + "。" + suffix


def news_paragraphs(value: str) -> str:
    """Format paragraph text, never URLs, source labels, quantities or raw evidence."""
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for block in soup.find_all(["p", "div", "blockquote"]) or [soup]:
        if block.find(["p", "div", "blockquote"]):
            continue
        nodes = [
            node
            for node in block.descendants
            if isinstance(node, NavigableString)
            and str(node).strip()
            and not node.find_parent(["a", "sup", "script", "style"])
        ]
        if nodes:
            last = nodes[-1]
            last.replace_with(sentence_end(str(last)))
    # A complete source sentence followed by the joiner's semicolon is redundant.
    for node in list(soup.find_all(string=True)):
        if not node.find_parent(["a", "sup", "script", "style"]):
            replaced = re.sub(r"([。！？])\s*[；;]", r"\1", str(node))
            if replaced != str(node):
                node.replace_with(replaced)
    return str(soup)
