"""
本地极简静态服务器,只为预览邮件模板渲染效果。

会先调用 preview_email 重新生成 /tmp/email-preview/index.html,
然后在 127.0.0.1:8765 上以静态方式提供(只本机访问,不暴露到 LAN)。
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


with socketserver.TCPServer(("127.0.0.1", PORT), QuietHandler) as httpd:
    print(f"serving {PREVIEW_DIR} at http://localhost:{PORT}", flush=True)
    httpd.serve_forever()
