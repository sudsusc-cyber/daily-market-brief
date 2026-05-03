"""tests/test_smtp_partial.py — SMTP 部分收件人被拒时的容错。

场景:QQ 偶发屏蔽某个收件人地址,sendmail() 抛 SMTPRecipientsRefused,
但被拒的人之外其他人已经收到邮件。本测试确保:
- 部分被拒不让整个 send 失败(避免下次幂等重发,导致已收到的人收到第二封)
- 全部被拒才真失败(抛出异常)
"""

from __future__ import annotations

import smtplib
from unittest.mock import MagicMock, patch

import pytest


def _setup_smtp_mocks(refused_recipients: dict, server_factory_path: str):
    """构造一个会抛 SMTPRecipientsRefused 的 SMTP server mock。"""
    mock_server = MagicMock()
    mock_server.sendmail.side_effect = smtplib.SMTPRecipientsRefused(refused_recipients)
    return mock_server


def test_partial_refusal_does_not_raise(monkeypatch, tmp_path) -> None:
    """3 个收件人中 1 个被拒,2 个接受 → 函数不抛异常(视为部分成功)。"""
    from src.sender import smtp_sender

    refused = {
        "blocked@example.com": (550, b"User unknown"),
    }
    mock_server = MagicMock()
    mock_server.sendmail.side_effect = smtplib.SMTPRecipientsRefused(refused)

    # patch SMTP_SSL 构造器返回 mock
    with patch("smtplib.SMTP_SSL", return_value=mock_server):
        # 跳过 DNS 解析(返回 None 走系统解析)
        monkeypatch.setattr(smtp_sender, "_resolve_via_dns", lambda *a, **k: None)

        # 不抛异常
        smtp_sender.send_html_email(
            sender="me@qq.com",
            auth_code="x",
            recipient=["a@ok.com", "blocked@example.com", "b@ok.com"],
            subject="t",
            html_body="<p>t</p>",
        )

    # sendmail 被调用过(说明真的尝试了)
    assert mock_server.sendmail.called


def test_all_refused_raises(monkeypatch, tmp_path) -> None:
    """全部收件人被拒 → 抛 SMTPRecipientsRefused(真失败,run 应该报错)。"""
    from src.sender import smtp_sender

    refused = {
        "a@x.com": (550, b"User unknown"),
        "b@x.com": (550, b"User unknown"),
    }
    mock_server = MagicMock()
    mock_server.sendmail.side_effect = smtplib.SMTPRecipientsRefused(refused)

    with patch("smtplib.SMTP_SSL", return_value=mock_server):
        monkeypatch.setattr(smtp_sender, "_resolve_via_dns", lambda *a, **k: None)

        with pytest.raises(smtplib.SMTPRecipientsRefused):
            smtp_sender.send_html_email(
                sender="me@qq.com",
                auth_code="x",
                recipient=["a@x.com", "b@x.com"],
                subject="t",
                html_body="<p>t</p>",
            )


def test_partial_refusal_via_return_dict_does_not_raise(monkeypatch) -> None:
    """sendmail() 不抛异常,但返回 dict 表示部分被拒(RFC 5321 路径)→ 视为部分成功。"""
    from src.sender import smtp_sender

    refused_dict = {"blocked@example.com": (550, b"User unknown")}
    mock_server = MagicMock()
    mock_server.sendmail.return_value = refused_dict   # 注意:return_value 不是 side_effect

    with patch("smtplib.SMTP_SSL", return_value=mock_server):
        monkeypatch.setattr(smtp_sender, "_resolve_via_dns", lambda *a, **k: None)
        # 不抛异常
        smtp_sender.send_html_email(
            sender="me@qq.com",
            auth_code="x",
            recipient=["a@ok.com", "blocked@example.com", "b@ok.com"],
            subject="t",
            html_body="<p>t</p>",
        )
    assert mock_server.sendmail.called


def test_all_refused_via_return_dict_raises(monkeypatch) -> None:
    """sendmail() 返回 dict 涵盖所有收件人(全部被拒)→ 抛 SMTPRecipientsRefused。"""
    from src.sender import smtp_sender

    refused_dict = {
        "a@x.com": (550, b"User unknown"),
        "b@x.com": (550, b"User unknown"),
    }
    mock_server = MagicMock()
    mock_server.sendmail.return_value = refused_dict

    with patch("smtplib.SMTP_SSL", return_value=mock_server):
        monkeypatch.setattr(smtp_sender, "_resolve_via_dns", lambda *a, **k: None)
        with pytest.raises(smtplib.SMTPRecipientsRefused):
            smtp_sender.send_html_email(
                sender="me@qq.com",
                auth_code="x",
                recipient=["a@x.com", "b@x.com"],
                subject="t",
                html_body="<p>t</p>",
            )


def test_empty_return_dict_succeeds(monkeypatch) -> None:
    """sendmail() 返回空 dict → 全部成功,无 warning,不抛。"""
    from src.sender import smtp_sender

    mock_server = MagicMock()
    mock_server.sendmail.return_value = {}

    with patch("smtplib.SMTP_SSL", return_value=mock_server):
        monkeypatch.setattr(smtp_sender, "_resolve_via_dns", lambda *a, **k: None)
        smtp_sender.send_html_email(
            sender="me@qq.com",
            auth_code="x",
            recipient=["a@ok.com", "b@ok.com"],
            subject="t",
            html_body="<p>t</p>",
        )
    assert mock_server.sendmail.called


def test_empty_recipient_raises_immediately(monkeypatch) -> None:
    """空 recipient 列表 → fail-fast,不调 SMTP。"""
    import pytest

    from src.sender.smtp_sender import send_html_email

    with pytest.raises(ValueError, match="收件人列表为空"):
        send_html_email(
            sender="x@x.com", auth_code="x",
            recipient=[], subject="x", html_body="<p>x</p>",
        )


def test_whitespace_only_recipients_raise(monkeypatch) -> None:
    """全空白 / 全空字符串 也算空。"""
    import pytest

    from src.sender.smtp_sender import send_html_email

    with pytest.raises(ValueError, match="收件人列表为空"):
        send_html_email(
            sender="x@x.com", auth_code="x",
            recipient=["", "  ", None],  # type: ignore[list-item]
            subject="x", html_body="<p>x</p>",
        )


def test_string_recipient_with_whitespace_trimmed(monkeypatch) -> None:
    """单字符串 + 带空格 → 不应失败,被 strip 后正常。"""
    import smtplib
    from unittest.mock import MagicMock, patch

    from src.sender.smtp_sender import send_html_email

    fake_server = MagicMock(spec=smtplib.SMTP_SSL)
    fake_server.sendmail.return_value = {}

    with patch("src.sender.smtp_sender.smtplib.SMTP_SSL", return_value=fake_server):
        send_html_email(
            sender="x@x.com", auth_code="x",
            recipient="  good@x.com  ", subject="x", html_body="<p>x</p>",
        )
    fake_server.sendmail.assert_called_once()
    args = fake_server.sendmail.call_args[0]
    # 第二个 positional arg = recipients list,应该 ['good@x.com']
    assert args[1] == ["good@x.com"]
