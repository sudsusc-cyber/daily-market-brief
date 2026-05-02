"""
QQ 邮箱 SMTP 发件(SSL 端口 465),支持 inline 图片(CID)。

- HTML 主体 + 0..N 个 inline 图片(用 Content-ID 引用)
- 邮件结构:multipart/related(HTML + images),保证 iOS Mail / 安卓 Gmail /
  Outlook / QQ 默认显示图片,无需用户点"加载远程图片"
- 失败直接抛异常,M6 阶段会在外层加重试 + 告警邮件
"""

from __future__ import annotations

import logging
import mimetypes
import socket
import smtplib
import struct
from dataclasses import dataclass
from email.header import Header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from email.utils import formatdate, make_msgid
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_via_dns(host: str, dns_server: str = "8.8.8.8") -> str | None:
    """用 dig 命令通过外部 DNS 解析,绕开本机代理/VPN 接管的 DNS。
    返回首个非 198.18.x 的 IPv4;失败返回 None,调用方退回系统解析。"""
    import subprocess
    try:
        out = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", f"@{dns_server}", host, "A"],
            capture_output=True, text=True, timeout=8,
        ).stdout.strip()
        for line in out.splitlines():
            line = line.strip()
            # 过滤掉 CNAME 行(末尾带点)和代理虚拟 IP
            if not line or line.endswith(".") or line.startswith("198.18."):
                continue
            parts = line.split(".")
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                return line
        return None
    except Exception:
        return None


@dataclass(frozen=True)
class InlineImage:
    """一张内嵌附件:HTML 用 <img src='cid:cid'> 引用"""

    cid: str  # 不要含 < >;send 时会自动加上
    path: Path  # 本地图片文件路径
    subtype: str | None = None  # 'png' / 'jpeg' / 'svg+xml';None 时按扩展名推断


def _detect_image_subtype(data: bytes) -> str | None:
    """按 magic bytes 推 MIME subtype。识别 PNG / JPEG / GIF / WebP / SVG"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    head = data[:200].lstrip()
    if head.startswith(b"<?xml") or b"<svg" in head:
        return "svg+xml"
    return None


def _build_image_part(image: InlineImage) -> MIMEImage:
    """构造单个 inline 图像 MIME 部件;自动按文件内容判断 MIME 子类型"""
    data = image.path.read_bytes()
    subtype = image.subtype or _detect_image_subtype(data)
    if subtype is None:
        guessed, _ = mimetypes.guess_type(str(image.path))
        if guessed and guessed.startswith("image/"):
            subtype = guessed.split("/", 1)[1]
        else:
            subtype = "png"
    part = MIMEImage(data, _subtype=subtype)
    part.add_header("Content-ID", f"<{image.cid}>")
    part.add_header("Content-Disposition", "inline", filename=image.path.name)
    part.add_header("X-Attachment-Id", image.cid)
    return part


def send_html_email(
    *,
    sender: str,
    auth_code: str,
    recipient: str | list[str],
    subject: str,
    html_body: str,
    sender_display_name: str | None = None,
    inline_images: list[InlineImage] | None = None,
    smtp_host: str = "smtp.qq.com",
    smtp_port: int = 465,
    timeout: int = 30,
) -> None:
    """
    发送 HTML 邮件。recipient 可为单个地址字符串或地址列表。

    若 inline_images 非空:邮件 MIME 结构为 multipart/related,
    HTML 用 <img src="cid:..."> 引用;否则发简单的 text/html 单部分。
    """
    inline_images = inline_images or []
    recipients: list[str] = [recipient] if isinstance(recipient, str) else recipient

    if inline_images:
        msg: MIMEMultipart | MIMEText = MIMEMultipart("related")
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        for img in inline_images:
            msg.attach(_build_image_part(img))
    else:
        msg = MIMEText(html_body, "html", "utf-8")

    # From:含中文显示名时用 formataddr + Header utf-8 编码,否则中文会乱码
    if sender_display_name:
        encoded_name = Header(sender_display_name, "utf-8").encode()
        msg["From"] = formataddr((encoded_name, sender))
    else:
        msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = Header(subject, "utf-8")
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="daily-market-brief.local")

    logger.info(
        "smtp_send.start sender=%s recipients=%s subject=%r inline=%d port=%d",
        sender, recipients, subject, len(inline_images), smtp_port,
    )
    # 本机若有代理(Surge/ClashX 等)接管 DNS,smtp.qq.com 会被解到 198.18.x.x
    # 虚拟 IP 导致 SSL 握手被截。先用外部 DNS 拿真实 IP,临时打补丁让
    # socket.getaddrinfo 返回真实 IP(SNI 仍用 smtp_host,证书校验正确)。
    real_ip = _resolve_via_dns(smtp_host)
    _orig_getaddrinfo = socket.getaddrinfo
    if real_ip:
        logger.info("smtp_send.dns_override host=%s real_ip=%s", smtp_host, real_ip)

        def _patched(host, *args, **kwargs):  # noqa: ANN001
            if host == smtp_host:
                return _orig_getaddrinfo(real_ip, *args, **kwargs)
            return _orig_getaddrinfo(host, *args, **kwargs)
        socket.getaddrinfo = _patched

    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout)
            server.starttls()
    finally:
        if real_ip:
            socket.getaddrinfo = _orig_getaddrinfo
    try:
        server.login(sender, auth_code)
        try:
            server.sendmail(sender, recipients, msg.as_string())
        except smtplib.SMTPRecipientsRefused as exc:
            # 部分收件人被拒(QQ 偶发屏蔽某地址)。已接受的收件人 SMTP 服务器
            # 已经收到邮件,本次视为"部分成功":记录被拒清单但不抛异常,避免
            # 下次幂等重试导致已收到的人收到第二封。
            refused = list(exc.recipients.keys())
            accepted = [r for r in recipients if r not in refused]
            logger.warning(
                "smtp_send.partial accepted=%s refused=%s",
                accepted, refused,
            )
            if not accepted:
                # 全部被拒 → 真失败,抛出
                raise
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
    logger.info("smtp_send.done")
