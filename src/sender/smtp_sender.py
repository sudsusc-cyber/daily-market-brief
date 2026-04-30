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
import smtplib
from dataclasses import dataclass
from email.header import Header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path

logger = logging.getLogger(__name__)


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
    recipient: str,
    subject: str,
    html_body: str,
    inline_images: list[InlineImage] | None = None,
    smtp_host: str = "smtp.qq.com",
    smtp_port: int = 465,
    timeout: int = 30,
) -> None:
    """
    发送 HTML 邮件。

    若 inline_images 非空:邮件 MIME 结构为 multipart/related,
    HTML 用 <img src="cid:..."> 引用;否则发简单的 text/html 单部分。
    """
    inline_images = inline_images or []

    if inline_images:
        msg: MIMEMultipart | MIMEText = MIMEMultipart("related")
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        for img in inline_images:
            msg.attach(_build_image_part(img))
    else:
        msg = MIMEText(html_body, "html", "utf-8")

    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = Header(subject, "utf-8")
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="daily-market-brief.local")

    logger.info(
        "smtp_send.start sender=%s recipient=%s subject=%r inline=%d",
        sender, recipient, subject, len(inline_images),
    )
    server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout)
    try:
        server.login(sender, auth_code)
        server.sendmail(sender, [recipient], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
    logger.info("smtp_send.done")
