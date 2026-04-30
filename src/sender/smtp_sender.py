"""
QQ 邮箱 SMTP 发件(SSL 端口 465)。

M2 简陋版:接受 (sender, auth_code, recipient, subject, html) 直接发送。
失败直接抛异常,M6 阶段会在外层加重试 + 告警邮件。
"""

from __future__ import annotations

import logging
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

logger = logging.getLogger(__name__)


def send_html_email(
    *,
    sender: str,
    auth_code: str,
    recipient: str,
    subject: str,
    html_body: str,
    smtp_host: str = "smtp.qq.com",
    smtp_port: int = 465,
    timeout: int = 30,
) -> None:
    """
    发送一封 HTML 邮件。
    全程 SSL,QQ 的 465 端口要求登录密码是"授权码"而非账号密码。
    """
    msg = MIMEText(html_body, "html", "utf-8")
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = Header(subject, "utf-8")
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="daily-market-brief.local")

    logger.info("smtp_send.start sender=%s recipient=%s subject=%r", sender, recipient, subject)
    server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout)
    try:
        server.login(sender, auth_code)
        server.sendmail(sender, [recipient], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 — quit 失败不应影响发送结果
            pass
    logger.info("smtp_send.done")
