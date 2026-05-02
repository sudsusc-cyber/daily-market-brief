"""
HTML 净化工具 — LLM 与外部新闻源输入一律视为不可信文本。

核心原则:
- LLM 输出的"HTML"作为纯文本对待,**所有标签由 Python 端集中生成**
- 文本内容用 html.escape() 处理 → 杜绝 <script>/<img onerror>/属性注入
- URL 走 scheme 白名单(http/https)→ 杜绝 javascript: / data: / vbscript:
- 白名单允许标签:p / div / span / strong / sup / a(由本模块的 builder 函数生成)

不在本模块解决的问题:
- 模板里 `| safe` 渲染的内容必须是经过本模块净化后的产物。模板侧无法判断,
  约定:summary_html 字段只放本模块生成的安全 HTML。
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse


# 仅允许的 URL scheme(其它如 javascript: / data: / file: / vbscript: 一律拒绝)
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def escape_text(s: str | None) -> str:
    """把不可信文本转成 HTML 安全字符串。None 视为空。

    用 quote=True 同时转义引号,允许结果安全地放进属性值。
    """
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


def is_safe_url(url: str | None) -> bool:
    """URL scheme 白名单检查。
    - 必须是 http:// 或 https:// 开头
    - 必须有 netloc(域名)
    - 大小写不敏感
    - 任何解析异常 → 不安全
    """
    if not url:
        return False
    try:
        parsed = urlparse(str(url).strip())
    except Exception:  # noqa: BLE001
        return False
    return parsed.scheme.lower() in _ALLOWED_URL_SCHEMES and bool(parsed.netloc)


def safe_anchor(
    url: str,
    label: str,
    *,
    style: str = "",
    extra_attrs: str = "",
) -> str:
    """生成安全的 <a> 标签。
    - URL 不在白名单 → 不生成 <a>,只输出 escape 后的 label
    - URL 与 label 都做 HTML escape
    - 总是带 target="_blank" rel="noopener" 防 tabnabbing
    - style 与 extra_attrs 由调用方提供,假定来自 Python 端常量(不来自外部输入)
    """
    safe_label = escape_text(label)
    if not is_safe_url(url):
        return safe_label
    safe_href = escape_text(url)
    style_attr = f' style="{style}"' if style else ""
    extra = f" {extra_attrs}" if extra_attrs else ""
    return (
        f'<a href="{safe_href}" target="_blank" rel="noopener"'
        f'{style_attr}{extra}>{safe_label}</a>'
    )


def strip_all_tags(s: str | None) -> str:
    """剥离所有 HTML 标签,只留文本内容。
    用于把 LLM 输出的 <p><strong>foo</strong>bar</p> 还原成 'foobar'。

    简单基于正则,不解析嵌套结构;对邮件渲染场景已足够。
    """
    if not s:
        return ""
    # 先处理 HTML 实体的 &lt;script&gt; 这种被双重转义后又转回的情况
    text = re.sub(r"<[^>]*>", "", str(s))
    # 不在这里调 unescape:让调用方自己决定要不要保留实体
    return text


def render_text_with_footnotes(
    plain_text: str,
    footnote_re,  # noqa: ANN001
    re_idx_fn,    # noqa: ANN001
    build_anchor,  # noqa: ANN001  (callable: idx -> safe_anchor html)
) -> str:
    """对一段含脚注标记 [N] 的不可信文本,生成"文本 escape + 脚注转安全锚点"的 HTML。

    保证:
    - 标记之间的纯文本一律 html.escape,杜绝 <script>/<img> 注入
    - 脚注标记位置插入 build_anchor(idx) 返回的已生成的安全 HTML 片段
    - build_anchor 应当返回:已经过 safe_anchor / escape_text 处理的字符串

    示例:LLM 输出 `<img src=x onerror=alert(1)>苹果回购<sup>[1]</sup>`
      → 渲染为 `&lt;img src=x onerror=alert(1)&gt;苹果回购<sup><a href="...">[1]</a></sup>`
      → 浏览器把 `<img>` 当文本显示,不触发 onerror
    """
    if not plain_text:
        return ""
    parts: list[str] = []
    last = 0
    for m in footnote_re.finditer(plain_text):
        start, end = m.span()
        # 标记前的纯文本 → escape
        if start > last:
            parts.append(escape_text(plain_text[last:start]))
        # 标记本身 → 调用方提供的安全 HTML(builder 自己保证安全)
        idx = re_idx_fn(m)
        parts.append(build_anchor(idx))
        last = end
    if last < len(plain_text):
        parts.append(escape_text(plain_text[last:]))
    return "".join(parts)
