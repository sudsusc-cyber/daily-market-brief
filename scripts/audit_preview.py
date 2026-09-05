"""Compare fixed, offline email fixtures with an explicitly selected Git baseline.

Checks renderer/template drift without market requests, LLM calls or sending mail.
Run from the repository root: python -m scripts.audit_preview --baseline HEAD
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from jinja2 import DictLoader

from scripts import preview_email
from src.collectors import header_image
from src.renderer import render

ROOT = Path(__file__).resolve().parents[1]
FIXED_TIME = datetime(2026, 9, 5, 0, tzinfo=UTC)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="HEAD")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "audit-preview")
    args = parser.parse_args()
    # Resolve once to a commit ID; subsequent git show operands cannot be options.
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{args.baseline}^{{commit}}"],
        cwd=ROOT, text=True,
    ).strip()

    def baseline_file(path: str) -> str:
        return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT, text=True)

    prior = ModuleType("baseline_renderer")
    prior.__file__ = render.__file__
    exec(compile(baseline_file("src/renderer/render.py"), "baseline_renderer.py", "exec"), prior.__dict__)
    old_env = prior._build_env
    old_template = baseline_file("src/renderer/templates/email.html.j2")

    def baseline_env():
        env = old_env()
        env.loader = DictLoader({"email.html.j2": old_template})
        return env

    prior._build_env = baseline_env
    fixed_clock = SimpleNamespace(now=lambda *_: FIXED_TIME)
    with (
        patch.object(preview_email, "datetime", fixed_clock),
        patch.object(header_image, "pick_header_image", return_value={"url": "cid:header-fixture"}),
    ):
        current = preview_email.render_preview(inline_assets=False)
        with patch.object(render, "render_email", prior.render_email):
            previous = preview_email.render_preview(inline_assets=False)
        browser_html = preview_email.render_preview(inline_assets=True)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "current.html").write_text(current, encoding="utf-8")
    (args.output / "baseline.html").write_text(previous, encoding="utf-8")
    (args.output / "browser.html").write_text(browser_html, encoding="utf-8")
    for name, html in (("baseline", previous), ("current", current)):
        print(f"{name}: bytes={len(html.encode())} sha256={hashlib.sha256(html.encode()).hexdigest()}")
    print(f"baseline_commit={commit}; identical={current == previous}")
    print(f"browser_preview={args.output / 'browser.html'}")
    return 0 if current == previous else 1


if __name__ == "__main__":
    raise SystemExit(main())
