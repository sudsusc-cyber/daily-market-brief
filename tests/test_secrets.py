"""tests/test_secrets.py — secrets 脱敏与邮箱 mask 行为。

redact_secrets:已被多个 collector 使用,验证 query-string 凭据 pattern。
mask_email / mask_emails:GH Actions runner 日志的 PII 脱敏。
"""

from __future__ import annotations

from src.utils.secrets import mask_email, mask_emails, redact_secrets

# ── redact_secrets ─────────────────────────────────────────────────


def test_redact_query_string_api_key() -> None:
    text = "GET https://api.foo.com/v1?api_key=sk-12345&other=ok failed"
    out = redact_secrets(text)
    assert "sk-12345" not in out
    assert "api_key=***" in out
    assert "other=ok" in out  # 非凭据参数不动


def test_redact_token_and_access_token() -> None:
    text = "url: https://x?token=abc123 access_token=xyz789"
    out = redact_secrets(text)
    assert "abc123" not in out
    assert "xyz789" not in out
    assert "token=***" in out
    assert "access_token=***" in out


def test_redact_handles_empty() -> None:
    assert redact_secrets("") == ""
    assert redact_secrets(None) == ""  # type: ignore[arg-type]


def test_redact_no_match_unchanged() -> None:
    text = "plain log without credentials"
    assert redact_secrets(text) == text


# ── mask_email ─────────────────────────────────────────────────────


def test_mask_email_keeps_first_char_and_domain() -> None:
    assert mask_email("alice@gmail.com") == "a***@gmail.com"
    assert mask_email("daijiaying00@gmail.com") == "d***@gmail.com"


def test_mask_email_short_local() -> None:
    assert mask_email("a@x.com") == "a***@x.com"


def test_mask_email_invalid_returns_placeholder() -> None:
    # 无 @ → 整段视为不可识别
    assert mask_email("not-an-email") == "***"
    assert mask_email("") == "***"
    assert mask_email("   ") == "***"


def test_mask_email_handles_no_local_part() -> None:
    # 边界:@domain 形式(实际不会发生但要稳健)
    assert mask_email("@example.com") == "***@example.com"


# ── mask_emails ────────────────────────────────────────────────────


def test_mask_emails_lists_count_and_masked_addresses() -> None:
    out = mask_emails(["alice@gmail.com", "bob@qq.com"])
    assert "2 recipients" in out
    assert "a***@gmail.com" in out
    assert "b***@qq.com" in out
    assert "alice@gmail.com" not in out
    assert "bob@qq.com" not in out


def test_mask_emails_empty_list() -> None:
    out = mask_emails([])
    assert out == "0 recipients []"


def test_mask_emails_filters_falsy() -> None:
    # None / 空字符串不计数也不渲染
    out = mask_emails(["alice@gmail.com", "", None])  # type: ignore[list-item]
    assert "1 recipients" in out
    assert "a***@gmail.com" in out
