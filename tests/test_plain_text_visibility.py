import pytest

from src.sender.smtp_sender import _html_to_plain


def test_email_preheader_and_padding_do_not_leak_into_plain_body():
    html = '''<div style="display:none;max-height:0;mso-hide:all">本期编制于北京时间 11:47</div>
    <div style="display:none;max-height:0;mso-hide:all">&#847;&zwnj;&nbsp;&#65279;&#847;</div>
    <p>朝闻录</p><p>黄仁勋：美国公司应该使用中国 AI 模型。</p>'''
    assert _html_to_plain(html) == '朝闻录\n黄仁勋：美国公司应该使用中国 AI 模型。'


@pytest.mark.parametrize('opening', ['<div hidden>', '<div style="DISPLAY: none !important;">'])
def test_nested_hidden_content_and_void_elements_preserve_following_body(opening):
    html = opening + '<span>隐藏<span>填充</span><br><img src="cid:test">尾部</span></div><p>可见正文</p>'
    assert _html_to_plain(html) == '可见正文'


def test_hidden_void_element_cannot_hide_the_rest_of_the_email():
    assert _html_to_plain('<br hidden><p>可见正文</p>') == '可见正文'


def test_visible_content_and_source_url_still_survive():
    html = '<div style="display: block">真实新闻</div><a class="source-link" href="https://example.com/report">[1] 来源</a>'
    assert _html_to_plain(html) == '真实新闻\n[1] 来源 https://example.com/report'
