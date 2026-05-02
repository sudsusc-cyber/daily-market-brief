"""一次性脚本:发一封极简的测试邮件,只包含刊头图。

用途:验证 Android QQ 邮箱能否显示 inline-CID 头图(原本 Pexels 远程 URL 不显示)。
不影响 idempotency 状态(不写 already_sent_today),不调 LLM,不消耗 token。

用法(从 worktree 跑):
    export TEST_RECIPIENT=xxx@qq.com   # 必须,避免把私人收件人写进公开脚本
    uv run python scripts/send_header_test.py

注意:本脚本只是验证用,合并到 main 后可删,留作历史参考亦可。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ─────────────────────────  路径与 env  ───────────────────────────
_HERE = Path(__file__).resolve()
_WORKTREE_ROOT = _HERE.parent.parent  # 本脚本所在的项目根(worktree 或主项目)
_ENV_FILE = _WORKTREE_ROOT / ".env"
if not _ENV_FILE.exists():
    sys.exit(f"找不到 .env: {_ENV_FILE}（请在项目根 {_WORKTREE_ROOT} 创建 .env）")

# 手动加载 .env(避免 pydantic-settings 的 cwd 依赖)
for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# 优先 import 本 worktree 的 src.*(确保用的是新版 header_image.py)
sys.path.insert(0, str(_WORKTREE_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

from src.collectors.header_image import pick_header_image  # noqa: E402
from src.sender.smtp_sender import InlineImage, send_html_email  # noqa: E402


def _build_html(today_str: str, source: str, sid: str | None) -> str:
    """极简 HTML:只放刊头图 + 一段说明。模拟真实邮件结构,但内容最少。"""
    src_label = {"pexels": "Pexels 白名单", "bing": "Bing 每日壁纸", "local": "本地兜底"}[source]
    sid_html = f"(Pexels id={sid})" if sid else ""
    return f"""\
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>头图渲染测试</title>
</head>
<body style="margin:0; padding:0; background-color:#f4ede2; font-family:'PingFang SC','Hiragino Sans GB',sans-serif;">
<div style="display:none; max-height:0; max-width:0; overflow:hidden; opacity:0; mso-hide:all; font-size:1px; line-height:1px; color:#f4ede2;">
  头图渲染测试 · 验证 Android QQ 邮箱能否显示 inline CID
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4ede2;">
  <tr>
    <td align="center" style="padding:16px 16px 32px 16px;">
      <table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="background-color:#fffaf0; max-width:640px; width:100%;">
        <tr>
          <td style="padding:0; line-height:0; font-size:0; border-bottom:2px solid #1f2933;">
            <img src="cid:header_image"
                 width="640" height="320"
                 alt="刊头图"
                 style="display:block; width:100%; max-width:640px; height:auto; border:0; line-height:0;" />
          </td>
        </tr>
        <tr>
          <td style="padding:24px; font-size:14px; color:#1f2933; line-height:1.7;">
            <p style="margin:0 0 12px 0;"><strong>头图渲染测试邮件</strong></p>
            <p style="margin:0 0 12px 0;">日期:{today_str}</p>
            <p style="margin:0 0 12px 0;">头图来源:{src_label} {sid_html}</p>
            <p style="margin:0 0 12px 0;">嵌入方式:<code>inline CID(cid:header_image)</code></p>
            <p style="margin:0; color:#7a6a5c; font-size:13px;">
              如果在 Android QQ 邮箱也能看到上面的图,说明 inline CID 修复成功;<br/>
              所有客户端(iOS / 桌面 / Android)以后都能稳定显示刊头图。
            </p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""


def main() -> int:
    sender = os.environ["QQ_EMAIL_ADDRESS"]
    auth_code = os.environ["QQ_EMAIL_AUTH_CODE"]
    recipient = (
        os.environ.get("TEST_RECIPIENT")
        or (os.environ.get("EMAIL_RECIPIENT", "").split(",")[0].strip() or None)
    )
    if not recipient:
        sys.exit("请设置 TEST_RECIPIENT 或 EMAIL_RECIPIENT 环境变量")

    now_bj = datetime.now(ZoneInfo("Asia/Shanghai"))
    today_str = now_bj.strftime("%Y-%m-%d %H:%M:%S BJT")

    logger.info("pick_header_image start")
    header = pick_header_image(now_bj.date())
    logger.info("pick_header_image done source=%s id=%s local_path=%s",
                header["source"], header["id"], header["local_path"])

    # sanity check:新版必须返回 cid:header_image,不能是 https://
    if not header["url"].startswith("cid:"):
        sys.exit(f"❌ header_image 返回了非 CID URL: {header['url']} — 改动没生效?")

    html = _build_html(today_str, header["source"], header["id"])

    inline_images = [InlineImage(
        cid="header_image",
        path=Path(header["local_path"]),
        subtype=None,
    )]

    subject = f"【头图测试】{header['source']} · {today_str}"
    logger.info("send to %s subject=%r", recipient, subject)
    send_html_email(
        sender=sender,
        sender_display_name="头图测试",
        auth_code=auth_code,
        recipient=recipient,
        subject=subject,
        html_body=html,
        inline_images=inline_images,
    )
    logger.info("done. 请在 Android QQ 邮箱查收并确认头图显示。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
