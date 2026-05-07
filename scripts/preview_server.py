"""
本地极简静态服务器,只为预览邮件模板渲染效果。

会先调用 preview_email 重新生成 /tmp/email-preview/index.html,
然后在 0.0.0.0:8765 上以静态方式提供。
"""

from __future__ import annotations

import http.server
import os
import socketserver
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

PORT = int(__import__("os").environ.get("PORT", 8766))
# 默认仅绑回环,避免本地预览暴露到 LAN(咖啡店/合住 wifi 等场景)。
# HOST=0.0.0.0 显式覆盖。
HOST = __import__("os").environ.get("HOST", "127.0.0.1")
PREVIEW_DIR = Path("/tmp/email-preview")
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

# 复用 preview_email 的渲染流程(已带 logo data-URI 内联)
from scripts.preview_email import render_preview  # noqa: E402

html = render_preview()
(PREVIEW_DIR / "index.html").write_text(html, encoding="utf-8")
print(f"rendered → {PREVIEW_DIR / 'index.html'}  ({len(html):,} bytes)", flush=True)

os.chdir(PREVIEW_DIR)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        # 静音访问日志,减少噪声
        pass


with socketserver.TCPServer((HOST, PORT), QuietHandler) as httpd:
    display_host = "localhost" if HOST in ("", "127.0.0.1") else HOST
    print(f"serving {PREVIEW_DIR} at http://{display_host}:{PORT}", flush=True)
    httpd.serve_forever()
