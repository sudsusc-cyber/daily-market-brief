"""
巴菲特 / Berkshire 13F 持仓动向(模块 3c)。

数据源:SEC EDGAR
  - Berkshire Hathaway Inc CIK = 1067983
  - Atom feed: https://www.sec.gov/cgi-bin/browse-edgar?...&output=atom

触发条件:每个季度 13F 披露后 1-2 周内(约 2 月、5 月、8 月、11 月中旬)
处理(M3 阶段):
  1. 拉最新 5 条 13F 提交
  2. 与 state/last_13f.json 比对,若发现新条目则记录(accession_no, filed_at)
  3. 输出"是否新发布"+ 最新一条的 accession 与 filed 时间;
     具体持仓 diff 留 M4 在能拉到 13F-HR 实际数据后实现
  4. 仅在新 13F 发布后 7 天内输出展示信号

注意:
  - SEC EDGAR 强制 UA 需含联系方式;ADR-0001 §6 选定 sudsusc@gmail.com
  - 实际 13F-HR 文件解析(InfoTable.xml)是 M4 工作,M3 仅提供"有新提交"事件
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import feedparser
import requests

from src.utils.retry import retry
from src.utils.secrets import redact_secrets

logger = logging.getLogger(__name__)

CIK = "0001067983"
SEC_CONTACT_EMAIL = "sudsusc@gmail.com"  # ADR-0001 §6 锁定
SEC_UA = f"daily-market-brief/0.1 ({SEC_CONTACT_EMAIL})"

ATOM_URL = (
    f"https://www.sec.gov/cgi-bin/browse-edgar"
    f"?action=getcompany&CIK={CIK}&type=13F-HR&dateb=&owner=include&count=5&output=atom"
)


@dataclass
class Filing13F:
    """单次 13F 提交"""
    accession_no: str  # 全球唯一 ID,如 "0001067983-25-000004"
    filed_at: datetime  # aware UTC
    title: str  # "13F-HR / 13F-HR/A [Amend]"


@dataclass
class BuffettBundle:
    latest: Filing13F | None = None
    is_new: bool = False  # 与上次见到的相比是否有新提交
    days_since_filed: int | None = None  # 最新提交距今天几天
    error: str | None = None


@retry(max_attempts=3, base_delay=1.5)
def _fetch_atom() -> list[Filing13F]:
    """SEC EDGAR 要求 UA 含联系方式,所以这里不走 utils.fetch_rss(那个用浏览器 UA),
    直接 requests + SEC_UA + feedparser。"""
    resp = requests.get(
        ATOM_URL,
        headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"},
        timeout=20,
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    out: list[Filing13F] = []
    for e in feed.entries or []:
        # accession 形如 "0001193125-26-054580"。从 id / link 抽,容错多种格式
        raw_id = str(getattr(e, "id", "") or getattr(e, "link", ""))
        accession = ""
        if "accession-number=" in raw_id:
            accession = raw_id.split("accession-number=")[-1].split("&")[0]
        else:
            tail = raw_id.rsplit(":", 1)[-1]
            tail = tail.rsplit("/", 1)[-1]
            accession = tail
        upd = getattr(e, "updated_parsed", None) or getattr(e, "published_parsed", None)
        if not upd:
            continue
        filed_at = datetime(*upd[:6], tzinfo=UTC)
        out.append(Filing13F(
            accession_no=accession.strip(),
            filed_at=filed_at,
            title=str(getattr(e, "title", "") or "").strip(),
        ))
    out.sort(key=lambda x: x.filed_at, reverse=True)
    return out


def _load_last_seen(state_path: Path) -> str | None:
    """state/last_13f.json:{ "accession_no": "...", "first_seen_at": "ISO8601" }"""
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("accession_no")
    except Exception as exc:  # noqa: BLE001
        logger.warning("last_13f.parse_failed exc=%s", exc)
        return None


def fetch(
    state_path: Path, display_window_days: int = 7,
) -> tuple[BuffettBundle, dict | None]:
    """拉 Berkshire 13F atom,对比上次记录;仅在新发布后 display_window_days 天内输出 is_new。

    返回 (bundle, pending_save):
      - bundle:供下游渲染消费
      - pending_save:首次见到新 filing 时的待落盘 payload(含 filing 与 first_seen 时间)。
        无新 filing / fetch 失败时为 None。**fetch 不写盘**;调用方在邮件成功发送后
        调 commit_pushed(state_path, pending_save) 提交。

    为何延后写盘:与 figures / company_news / macro_news / frontier_labs 一致——
    fetch 后 LLM / 渲染 / SMTP 任一失败 → 用户没收到邮件,但 state 已记录"已见过"
    → 下次 run is_new=False → 这次新 13F 永远不会再展示。13F 一年仅 4 次,错过窗口
    很难补救。延后到邮件成功才提交,失败时下次 run 还能重新评估。
    """
    try:
        filings = _fetch_atom()
    except Exception as exc:  # noqa: BLE001
        logger.error("buffett_13f.fetch_failed exc_type=%s msg=%s", type(exc).__name__, redact_secrets(str(exc))[:200])
        return BuffettBundle(error=f"{type(exc).__name__}: {exc}"), None

    if not filings:
        return BuffettBundle(error="EDGAR atom 无条目"), None

    latest = filings[0]
    last_accession = _load_last_seen(state_path)
    now = datetime.now(UTC)

    is_new = last_accession != latest.accession_no
    pending_save: dict | None = None
    if is_new:
        pending_save = {
            "accession_no": latest.accession_no,
            "filed_at": latest.filed_at.isoformat(),
            "first_seen_at": now.isoformat(),
            "title": latest.title,
        }
        logger.info(
            "buffett_13f.new accession=%s filed=%s (pending_save)",
            latest.accession_no, latest.filed_at.isoformat(),
        )

    # 仅在最新 filing 在展示窗口内才视为"有新动向"
    days = (now - latest.filed_at).days
    in_window = days <= display_window_days

    return BuffettBundle(
        latest=latest,
        is_new=is_new and in_window,
        days_since_filed=days,
    ), pending_save


def commit_pushed(state_path: Path, pending_save: dict | None) -> None:
    """邮件发送成功后调用 — 把 fetch 返回的 pending_save 落盘。

    pending_save 为 None 时(无新 filing 或 fetch 失败)直接 no-op。
    """
    if not pending_save:
        return
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(pending_save, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(state_path)
        logger.info(
            "buffett_13f.commit_pushed accession=%s",
            pending_save.get("accession_no"),
        )
    except OSError as exc:
        logger.warning("last_13f.save_failed exc=%s; 下次 run 可能重发,主流程继续", exc)
