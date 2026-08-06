"""单元测试:Settings 校验 — 空收件人必须 fail-fast。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.settings import EmailSettings, Settings


def _env(monkeypatch, **overrides) -> None:
    """注入完整 env,允许 override 个别字段。"""
    base = {
        "QQ_EMAIL_ADDRESS": "test@qq.com",
        "QQ_EMAIL_AUTH_CODE": "test_auth",
        "EMAIL_RECIPIENT": "a@example.com,b@example.com",
        "FINNHUB_API_KEY": "x",
        "FRED_API_KEY": "x",
        "DEEPSEEK_API_KEY": "x",
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
    }
    base.update({k.upper(): v for k, v in overrides.items()})
    for k in (
        "QQ_EMAIL_ADDRESS", "QQ_EMAIL_AUTH_CODE", "EMAIL_RECIPIENT",
        "FINNHUB_API_KEY", "FRED_API_KEY", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in base.items():
        if v is not None:
            monkeypatch.setenv(k, v)


def test_normal_settings_load(monkeypatch) -> None:
    _env(monkeypatch)
    s = Settings()
    assert s.qq_email_address == "test@qq.com"
    assert "a@example.com" in s.email_recipient
    assert s.deepseek_model == "deepseek-v4-flash"


def test_deepseek_model_can_be_explicitly_pinned(monkeypatch) -> None:
    _env(monkeypatch, deepseek_model="deepseek-v4.1-flash")
    assert Settings().deepseek_model == "deepseek-v4.1-flash"


def test_empty_deepseek_model_raises(monkeypatch) -> None:
    _env(monkeypatch, deepseek_model="   ")
    with pytest.raises(ValidationError, match="DEEPSEEK_MODEL"):
        Settings()


def test_empty_email_recipient_raises(monkeypatch) -> None:
    """EMAIL_RECIPIENT="" 应在启动时抛 ValidationError,而非走完全流程后才发现。"""
    _env(monkeypatch, email_recipient="")
    with pytest.raises(ValidationError, match="EMAIL_RECIPIENT"):
        Settings()


def test_email_recipient_only_commas_raises(monkeypatch) -> None:
    """全是逗号 / 空白 也算空。"""
    _env(monkeypatch, email_recipient=" , , ")
    with pytest.raises(ValidationError, match="EMAIL_RECIPIENT"):
        Settings()


def test_empty_qq_address_raises(monkeypatch) -> None:
    _env(monkeypatch, qq_email_address="")
    with pytest.raises(ValidationError, match="qq_email_address"):
        Settings()


def test_empty_qq_auth_code_raises(monkeypatch) -> None:
    _env(monkeypatch, qq_email_auth_code="   ")
    with pytest.raises(ValidationError, match="qq_email_auth_code"):
        Settings()


def test_single_recipient_passes(monkeypatch) -> None:
    """逗号分隔可选;单个邮箱也合法。"""
    _env(monkeypatch, email_recipient="only@example.com")
    s = Settings()
    assert s.email_recipient == "only@example.com"


def test_email_settings_does_not_require_data_api_keys(monkeypatch) -> None:
    _env(monkeypatch, finnhub_api_key=None, fred_api_key=None, deepseek_api_key=None)
    settings = EmailSettings()
    assert settings.qq_email_address == "test@qq.com"
    assert settings.email_recipient == "a@example.com,b@example.com"
