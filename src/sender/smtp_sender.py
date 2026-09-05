"""
QQ 邮箱 SMTP 发件(SSL 端口 465),支持 inline 图片(CID)。

- HTML 主体 + 0..N 个 inline 图片(用 Content-ID 引用)
- 邮件结构:multipart/related(HTML + images),保证 iOS Mail / 安卓 Gmail /
  Outlook / QQ 默认显示图片,无需用户点"加载远程图片"
- 连接/握手与 4xx 临时拒收有限重试；全拒收抛异常，部分拒收返回结构化结果
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import mimetypes
import smtplib
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.header import Header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from html.parser import HTMLParser
from pathlib import Path

from src.utils.secrets import mask_email, mask_emails

logger = logging.getLogger(__name__)

_PROXY_DNS_NET = ipaddress.ip_network("198.18.0.0/15")


def _resolve_via_dns(host: str, dns_server: str = "8.8.8.8") -> str | None:
    """用 dig 命令通过外部 DNS 解析,绕开本机代理/VPN 接管的 DNS。
    返回首个非 198.18.0.0/15 的 IPv4;失败返回 None,调用方退回系统解析。"""
    import shutil
    import subprocess  # nosec B404

    # argv 固定且 shell=False，仅调用系统 dig。
    dig = shutil.which("dig")
    if not dig:
        return None
    try:
        out = subprocess.run(  # nosec B603
            [dig, "+short", "+time=3", "+tries=1", f"@{dns_server}", host, "A"],
            capture_output=True, text=True, timeout=8,
        ).stdout.strip()
        for line in out.splitlines():
            line = line.strip()
            # 过滤掉 CNAME 行(末尾带点)和代理虚拟 IP
            if not line or line.endswith("."):
                continue
            parts = line.split(".")
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                try:
                    if ipaddress.ip_address(line) not in _PROXY_DNS_NET:
                        return line
                except ValueError:
                    continue
        return None
    except Exception:
        return None


def _system_dns_is_proxy_virtual(host: str) -> bool:
    """仅当系统 DNS 明确落入代理保留段时才启用外部 DNS 兜底。"""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        raw_ip = info[4][0]
        try:
            if ipaddress.ip_address(raw_ip) in _PROXY_DNS_NET:
                return True
        except ValueError:
            continue
    return False


@dataclass(frozen=True)
class InlineImage:
    """一张内嵌附件:HTML 用 <img src='cid:cid'> 引用"""

    cid: str  # 不要含 < >;send 时会自动加上
    path: Path  # 本地图片文件路径
    subtype: str | None = None  # 'png' / 'jpeg' / 'svg+xml';None 时按扩展名推断


@dataclass(frozen=True)
class DeliveryResult:
    """SMTP envelope 最终结果；不把收件地址写入持久化 receipt。"""

    accepted: tuple[str, ...]
    refused: dict[str, tuple[int, bytes | str]]

    @property
    def status(self) -> str:
        return "full" if not self.refused else "partial"


class _PlainTextExtractor(HTMLParser):
    """把本项目生成的 HTML 转成可读的纯文本 alternative。"""

    _BLOCK_TAGS = {"br", "p", "div", "tr", "h1", "h2", "h3", "li"}
    _HIDDEN_TAGS = {"head", "style", "script", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in self._HIDDEN_TAGS:
            self.hidden_tags.append(tag)
        if self.hidden_tags:
            return
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden_tags:
            if tag == self.hidden_tags[-1]:
                self.hidden_tags.pop()
            return
        if tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_tags:
            self.parts.append(data)


def _html_to_plain(html_body: str) -> str:
    parser = _PlainTextExtractor()
    parser.feed(html_body)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip() or "每日市场简报"


def _build_message(
    *,
    sender: str,
    sender_display_name: str | None,
    subject: str,
    html_body: str,
    inline_images: list[InlineImage],
) -> MIMEMultipart:
    """构造 related → alternative(text/plain + text/html) 的兼容 MIME。"""
    msg = MIMEMultipart("related")
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(_html_to_plain(html_body), "plain", "utf-8"))
    alternative.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alternative)
    for img in inline_images:
        msg.attach(_build_image_part(img))

    if sender_display_name:
        encoded_name = Header(sender_display_name, "utf-8").encode()
        msg["From"] = formataddr((encoded_name, sender))
    else:
        msg["From"] = sender
    # envelope recipients 仍由 sendmail 参数传递；头部不暴露四个收件地址。
    msg["To"] = "undisclosed-recipients:;"
    msg["Subject"] = Header(subject, "utf-8")
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="daily-market-brief.local")
    return msg


def _open_server(
    *,
    smtp_host: str,
    smtp_port: int,
    timeout: int,
    context: ssl.SSLContext,
):
    """建立已校验证书的 SMTP 连接；仅为本机代理虚拟 DNS 临时 override。"""
    real_ip = _resolve_via_dns(smtp_host) if _system_dns_is_proxy_virtual(smtp_host) else None
    original_getaddrinfo = socket.getaddrinfo
    if real_ip:
        logger.info("smtp_send.dns_override host=%s real_ip=%s", smtp_host, real_ip)

        def _patched(host, *args, **kwargs):  # noqa: ANN001
            if host == smtp_host:
                return original_getaddrinfo(real_ip, *args, **kwargs)
            return original_getaddrinfo(host, *args, **kwargs)

        socket.getaddrinfo = _patched
    try:
        if smtp_port == 465:
            return smtplib.SMTP_SSL(
                smtp_host, smtp_port, timeout=timeout, context=context,
            )
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout)
        try:
            server.starttls(context=context)
            return server
        except Exception:
            with contextlib.suppress(Exception):
                server.close()
            raise
    finally:
        if real_ip:
            socket.getaddrinfo = original_getaddrinfo


def _connect_and_login(
    *,
    sender: str,
    auth_code: str,
    smtp_host: str,
    smtp_port: int,
    timeout: int,
    context: ssl.SSLContext,
    attempts: int,
):
    """只重试连接与握手；进入 DATA 后不盲重试，避免未知送达状态下重复发。"""
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        server = None
        try:
            server = _open_server(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                timeout=timeout,
                context=context,
            )
            server.login(sender, auth_code)
            return server
        except (OSError, TimeoutError, smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected) as exc:
            last_exc = exc
            if server is not None:
                with contextlib.suppress(Exception):
                    server.quit()
            if attempt == attempts:
                raise
            delay = float(2 ** (attempt - 1))
            logger.warning(
                "smtp_connect.retry attempt=%d/%d delay=%.1fs exc_type=%s",
                attempt, attempts, delay, type(exc).__name__,
            )
            time.sleep(delay)
    if last_exc is None:  # 防御未来重构把 attempts 传成 0。
        raise RuntimeError("SMTP connection attempts exhausted")
    raise last_exc


def _send_envelope(server, sender: str, recipients: list[str], payload: str) -> dict:
    """统一 smtplib 的返回 dict 与 SMTPRecipientsRefused 两条路径。"""
    try:
        result = server.sendmail(sender, recipients, payload)
    except smtplib.SMTPRecipientsRefused as exc:
        return dict(exc.recipients)
    return dict(result) if isinstance(result, dict) else {}


def _is_transient_refusal(reply: object) -> bool:
    try:
        code = int(reply[0])  # type: ignore[index]
    except (TypeError, ValueError, IndexError):
        return False
    return 400 <= code < 500


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
        subtype = guessed.split("/", 1)[1] if guessed and guessed.startswith("image/") else "png"
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
    max_attempts: int = 3,
    on_progress: Callable[[DeliveryResult], None] | None = None,
) -> DeliveryResult:
    """
    发送 HTML 邮件。recipient 可为单个地址字符串或地址列表。

    MIME 固定包含 text/plain + text/html alternative；inline_images 作为 related 附件。
    """
    inline_images = inline_images or []
    recipients: list[str] = [recipient] if isinstance(recipient, str) else list(recipient)

    # 防御:空收件人列表会被 SMTP 静默接收(不报错也不发任何人),返回 success
    # → 主流程标 OK + idempotency 标已发 → 静默漏发风暴。fail-fast 直接抛。
    cleaned = list(dict.fromkeys(r.strip() for r in recipients if r and r.strip()))
    if not cleaned:
        raise ValueError("send_html_email: 收件人列表为空(EMAIL_RECIPIENT 未配置?)")
    recipients = cleaned

    msg = _build_message(
        sender=sender,
        sender_display_name=sender_display_name,
        subject=subject,
        html_body=html_body,
        inline_images=inline_images,
    )

    logger.info(
        "smtp_send.start sender=%s recipients=%s subject=%r inline=%d port=%d",
        mask_email(sender), mask_emails(recipients), subject, len(inline_images), smtp_port,
    )
    tls_context = ssl.create_default_context()
    payload = msg.as_string()
    pending = list(recipients)
    accepted_set: set[str] = set()
    final_refused: dict = {}

    for attempt in range(1, max(1, max_attempts) + 1):
        server = None
        try:
            server = _connect_and_login(
                sender=sender, auth_code=auth_code, smtp_host=smtp_host,
                smtp_port=smtp_port, timeout=timeout, context=tls_context,
                attempts=max(1, max_attempts) if attempt == 1 else 1,
            )
            refused_now = _send_envelope(server, sender, pending, payload)
        except (OSError, smtplib.SMTPException):
            if not accepted_set:
                raise
            # A retry failure must not erase recipients already accepted.
            # Do not blindly retry an envelope whose acceptance is uncertain.
            final_refused.update({address: (451, b"Retry interrupted; acceptance unconfirmed")
                                  for address in pending})
            break
        else:
            accepted_set.update(address for address in pending if address not in refused_now)
            # Persist acceptance before QUIT: even connection cleanup can hang.
            if accepted_set and on_progress is not None:
                on_progress(DeliveryResult(
                    accepted=tuple(address for address in recipients if address in accepted_set),
                    refused={address: refused_now.get(address, final_refused.get(
                        address, (450, b"Pending"))) for address in recipients if address not in accepted_set},
                ))
        finally:
            if server is not None:
                with contextlib.suppress(Exception):
                    server.quit()
        transient = {
            address: reply for address, reply in refused_now.items()
            if _is_transient_refusal(reply)
        }
        final_refused.update({
            address: reply for address, reply in refused_now.items()
            if address not in transient
        })
        if not transient:
            break
        if attempt == max(1, max_attempts):
            final_refused.update(transient)
            break
        pending = list(transient)
        delay = float(2 ** (attempt - 1))
        logger.warning(
            "smtp_send.retry_transient attempt=%d/%d recipients=%s delay=%.1fs",
            attempt, max_attempts, mask_emails(pending), delay,
        )
        time.sleep(delay)

    accepted = [address for address in recipients if address in accepted_set]
    refused_dict = {
        address: final_refused[address]
        for address in recipients if address in final_refused
    }
    if refused_dict:
        logger.warning(
            "smtp_send.partial accepted=%s refused=%s",
            mask_emails(accepted), mask_emails(list(refused_dict)),
        )
    if not accepted:
        raise smtplib.SMTPRecipientsRefused(refused_dict)
    logger.info(
        "smtp_send.done status=%s accepted=%d refused=%d",
        "full" if not refused_dict else "partial", len(accepted), len(refused_dict),
    )
    return DeliveryResult(accepted=tuple(accepted), refused=refused_dict)
