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

PORT = 8765
PREVIEW_DIR = Path("/tmp/email-preview")
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

# 渲染最新一次模板到 PREVIEW_DIR/index.html
from scripts.preview_email import _build_mock_signals, _format_pct, _format_price  # noqa: E402

from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402

env = Environment(
    loader=FileSystemLoader(PROJECT_ROOT / "src" / "renderer" / "templates"),
    autoescape=select_autoescape(["html"]),
)
env.filters["price"] = _format_price
env.filters["pct"] = _format_pct
html = env.get_template("email.html.j2").render(
    signals=_build_mock_signals(),
    generated_at=datetime.now(ZoneInfo("Asia/Shanghai")),
)
(PREVIEW_DIR / "index.html").write_text(html, encoding="utf-8")
print(f"rendered → {PREVIEW_DIR / 'index.html'}  ({len(html):,} bytes)", flush=True)

os.chdir(PREVIEW_DIR)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        # 静音访问日志,减少噪声
        pass


with socketserver.TCPServer(("", PORT), QuietHandler) as httpd:
    print(f"serving {PREVIEW_DIR} at http://localhost:{PORT}", flush=True)
    httpd.serve_forever()
