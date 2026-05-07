"""敏感信息脱敏工具 — 异常 message / log 内容里 query string 凭据 mask。

使用场景:
- 任何把 `str(exception)` / 异常 message 写到日志或对外暴露字段(如
  SentimentMetric.error 会渲染到邮件)的地方,先经此函数过滤。
- requests / urllib 异常的 str 通常含完整 URL(含 ?api_key=xxx),
  不脱敏会泄露到 GH Actions 公开日志、邮件正文、LLM prompt、state 文件。

设计:保守宽泛,宁可多 mask 也不能漏。仅用于显示 / 日志,不用于业务逻辑。
"""
from __future__ import annotations

import re

# 常见 query-string 凭据格式 — `api_key=xxx`、`apikey=xxx`、`token=xxx`、
# `access_token=xxx`、`auth=xxx`、`key=xxx`(后者宽泛但 key= 之后通常是凭据)
_SECRET_QUERY_RE = re.compile(
    r"((?:api[_-]?key|access[_-]?token|token|auth|key)=)[^&\s\"'>]+",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """把字符串里看起来像 query-string 凭据的部分 mask 成 `***`。

    保守宽泛,宁可多 mask 也不能漏。
    None / 空字符串安全:None → '',空 → ''。
    """
    if not text:
        return text or ""
    return _SECRET_QUERY_RE.sub(r"\1***", text)


def mask_email(addr: str) -> str:
    """把邮箱地址 mask 成 `a***@gmail.com` 形式,仅保留首字母与域名。

    GH Actions runner 日志虽然在 PRIVATE 仓库下只对 owner 可见,但日志会被
    actions/cache 持久化、可能透过错误上报或第三方监控外泄,把整段 PII
    当 INFO 写出去是不必要的暴露。给 SMTP 层与 main.py 用,日志显示数量 +
    最少识别信息即可。

    输入不是合法邮箱(无 @ / 空 / None)→ 返回 "***",不抛异常。
    """
    if not addr or "@" not in addr:
        return "***"
    local, _, domain = addr.partition("@")
    local = local.strip()
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def mask_emails(addrs: list[str] | tuple[str, ...]) -> str:
    """日志友好:`['alice@x.com','bob@y.com']` → `'2 recipients [a***@x.com, b***@y.com]'`。"""
    cleaned = [a for a in addrs if a]
    masked = ", ".join(mask_email(a) for a in cleaned)
    return f"{len(cleaned)} recipients [{masked}]"
