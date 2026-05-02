"""
段永平雪球短文采集(纯增量模块,不影响其他 collector)。

监控对象:雪球账号 @大道无形我有型(段永平本人)。

数据源:雪球 web API
    GET https://xueqiu.com/v4/statuses/user_timeline.json?user_id={uid}&count=20
    Headers: 桌面 Chrome UA + Cookie xq_a_token=<env>

环境变量(均可缺失,缺失时本模块直接返回空 → 主流程不受影响):
    DUAN_USER_ID — 段永平雪球 user_id(数字)
    XQ_A_TOKEN   — 雪球登录后的 xq_a_token cookie 值

去重 / 时间窗口(双重过滤,两条都要满足):
    (a) id > .state/duan_last_seen.json 中保存的 last_seen
    (b) created_at > .state/last_mail_sent.json 中保存的上次邮件成功时间
    冷启动(两个文件都不存在)→ 退化为最近 24h 的窗口

状态更新顺序(由 main.py 控制):
    采集成功 → 不立即写状态
    邮件发送成功 → 调 commit_state() → 先写 last_mail_sent 再写 last_seen
    发送失败 → 状态全部不动,下次重跑能正确续上

容错:网络/鉴权/解析失败 一律 log + 返回 空 list,不抛异常。
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.utils.dates import to_beijing
from src.utils.retry import retry

logger = logging.getLogger(__name__)


XUEQIU_API_BASE = "https://xueqiu.com/v4/statuses/user_timeline.json"
XUEQIU_PROFILE_BASE = "https://xueqiu.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)
DEFAULT_FETCH_COUNT = 20
DEFAULT_TIMEOUT_SEC = 15
COLD_START_WINDOW_HOURS = 24
MIN_TEXT_CHARS = 20
MAX_TEXT_CHARS = 400

LAST_SEEN_FILE = "duan_last_seen.json"
LAST_MAIL_SENT_FILE = "last_mail_sent.json"
HEALTH_FILE = "duan_health.json"

# 告警阈值(连续观测计数)
API_FAILURE_ALERT_THRESHOLD = 1   # 一次失败即报(私人系统不容忍静默故障)
EMPTY_RETURN_SOFT_THRESHOLD = 7   # 连续 7 次返回 0 帖才报(段永平偶尔会沉默一周)


PARENT_TEXT_MAX_CHARS = 200  # 原帖上下文渲染上限,超出截断 + 省略号


@dataclass
class DuanQuote:
    """段永平一条雪球短文(规范化后)。"""
    id: int                 # 雪球 status id
    created_at: datetime    # UTC
    text: str               # 段永平本人正文(已 HTML→文本 + 截断)
    url: str                # https://xueqiu.com/{uid}/{id}
    truncated: bool         # 是否因超长被截断
    # 回复 / 转评型帖子的原帖上下文(无则为 None)。渲染时显示在段永平正文之前,
    # 避免单看回复内容断章取义。
    parent_text: str | None = None
    parent_author: str | None = None


@dataclass
class FetchResult:
    """fetch 返回值,带诊断字段供 health 追踪。

    quotes:        通过双下限过滤后的列表(对外可展示)
    raw_count:     API 返回的 statuses 总数(>=0);api_failed 时为 0
    api_failed:    True 表示 HTTP / 解析失败,False 表示 API 调用成功(即便返回空)
    error:         失败原因短串,成功时为 None
    skip_reason:   非 None 时表示 fetch 因配置问题被跳过(如 uid 缺失),
                   不计入 api_failed,但仍可作为告警源
    """
    quotes: list[DuanQuote]
    raw_count: int = 0
    api_failed: bool = False
    error: str | None = None
    skip_reason: str | None = None


# ----------------------------------------------------------------------
# HTTP 抓取
# ----------------------------------------------------------------------
@retry(max_attempts=3, base_delay=5.0, backoff=1.0, jitter=0.0)
def _fetch_timeline(uid: str, token: str | None) -> list[dict]:
    """单次拉取雪球 timeline。失败重试 2 次,固定间隔 5s。

    返回原始 statuses 列表(雪球 API 返回结构是 {"statuses":[...]})。
    无 token 也尝试请求,但雪球通常会返回空或重定向。
    """
    url = f"{XUEQIU_API_BASE}?user_id={uid}&count={DEFAULT_FETCH_COUNT}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", DEFAULT_USER_AGENT)
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Referer", f"{XUEQIU_PROFILE_BASE}/u/{uid}")
    if token:
        req.add_header("Cookie", f"xq_a_token={token}")
    with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SEC) as resp:
        raw = resp.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"xueqiu_response_not_json: {exc}") from exc
    statuses = data.get("statuses") or []
    if not isinstance(statuses, list):
        raise RuntimeError(f"xueqiu_statuses_not_list type={type(statuses).__name__}")
    return statuses


# ----------------------------------------------------------------------
# 解析与清洗
# ----------------------------------------------------------------------
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"[ \t ]+")
_MULTI_BR_RE = re.compile(r"\n\s*\n+")
_PURE_RT_RE = re.compile(r"^\s*转发", re.UNICODE)


def _html_to_text(html: str) -> str:
    """雪球 text 字段是带标签的 HTML。去标签 + 解码实体 + 合并空行。"""
    if not html:
        return ""
    s = _html_to_text_pre(html)
    s = _HTML_TAG_RE.sub("", s)
    s = _html.unescape(s)
    # 合并多余空白
    s = _BLANK_RE.sub(" ", s)
    # 多个换行合并为单个空行
    s = _MULTI_BR_RE.sub("\n\n", s)
    return s.strip()


def _html_to_text_pre(html: str) -> str:
    """先把 <br> 与 </p> 转换为换行,再让 _HTML_TAG_RE 收拾余下标签。"""
    s = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    s = re.sub(r"</\s*p\s*>", "\n", s, flags=re.IGNORECASE)
    return s


def _is_pure_retweet(retweet_status_id: int | None, text: str) -> bool:
    """无评论的转发:有 retweet_status_id 且自己没添加内容(text 是空或转发模板)。"""
    if not retweet_status_id:
        return False
    if not text or _PURE_RT_RE.match(text):
        return True
    return False


def _looks_like_reply(text: str) -> bool:
    """正文以雪球/微博式回复标记开头(回复@xxx 或 //@xxx)。"""
    if not text:
        return False
    return bool(re.match(r"^\s*(?:回复\s*@|//\s*@)", text))


def _extract_parent_context(status: dict) -> tuple[str | None, str | None]:
    """从 retweeted_status 提取原帖上下文。

    雪球 API 对回复/转评型帖子通常嵌入 retweeted_status:
        {"retweeted_status": {"text": "<HTML>", "user": {"screen_name": "xx"}, ...}}

    返回 (parent_text, parent_author),都为 None 表示无可用上下文。
    """
    parent = status.get("retweeted_status")
    if not isinstance(parent, dict):
        return None, None
    parent_html = parent.get("text") or parent.get("description") or ""
    parent_text = _html_to_text(str(parent_html))
    if not parent_text:
        return None, None
    if len(parent_text) > PARENT_TEXT_MAX_CHARS:
        parent_text = parent_text[:PARENT_TEXT_MAX_CHARS].rstrip() + "…"
    user = parent.get("user") or {}
    author = None
    if isinstance(user, dict):
        author = (user.get("screen_name") or user.get("name") or "").strip() or None
    return parent_text, author


def _truncate(text: str, max_chars: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "…", True


def _parse_status(status: dict, uid: str) -> DuanQuote | None:
    """单条状态 → DuanQuote。失败/不合规/缺少上下文 → None。

    回复 / 转评型帖子的处理:
      - 嵌入了 retweeted_status → 提取原帖正文与作者作为 parent_text/parent_author
      - 看起来像回复(text 以 //@ 或 回复@ 开头)但无嵌入原帖 → 丢弃,
        防止读者只看到段永平的一句"想多了"而不知道原帖在讲什么(断章取义)
    """
    try:
        sid = int(status.get("id"))
    except (TypeError, ValueError):
        return None
    ts_ms = status.get("created_at")
    if not isinstance(ts_ms, (int, float)) or ts_ms <= 0:
        return None
    created = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)

    raw_html = status.get("text") or ""
    text = _html_to_text(str(raw_html))

    # retweet_status_id 可能是 0 / None / 实际 id
    rt_id = status.get("retweet_status_id") or 0
    try:
        rt_id = int(rt_id)
    except (TypeError, ValueError):
        rt_id = 0

    if _is_pure_retweet(rt_id, text):
        return None
    if len(text) < MIN_TEXT_CHARS:
        return None

    parent_text, parent_author = _extract_parent_context(status)

    # 上下文必备性约束:文本明显像回复(开头是 //@ / 回复@),但 API 没给嵌入原帖
    # → 丢弃。读者只看到段永平的"对!""想多了""不一定"会完全摸不到上下文。
    if _looks_like_reply(text) and parent_text is None:
        logger.info("duan.parse.dropped_orphan_reply id=%s", sid)
        return None

    text, truncated = _truncate(text)
    url = f"{XUEQIU_PROFILE_BASE}/{uid}/{sid}"
    return DuanQuote(
        id=sid, created_at=created, text=text, url=url, truncated=truncated,
        parent_text=parent_text, parent_author=parent_author,
    )


# ----------------------------------------------------------------------
# 状态文件(独立目录 .state/,不与已有 state/ 混用)
# ----------------------------------------------------------------------
def _load_last_seen(state_dir: Path) -> int | None:
    p = state_dir / LAST_SEEN_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        v = int(data.get("last_seen_id"))
        return v if v > 0 else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("duan.last_seen.parse_failed exc=%s; treat=None", exc)
        return None


def _load_last_mail_sent(state_dir: Path) -> datetime | None:
    p = state_dir / LAST_MAIL_SENT_FILE
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ts = datetime.fromisoformat(str(data.get("sent_at")))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception as exc:  # noqa: BLE001
        logger.warning("duan.last_mail_sent.parse_failed exc=%s; treat=None", exc)
        return None


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------
def fetch_new_quotes(
    *,
    uid: str | None,
    token: str | None,
    state_dir: Path,
    now: datetime | None = None,
) -> FetchResult:
    """采集段永平新发言(纯读,不写状态)。

    主要不变量:
      - id > last_seen 是硬性下限,任何 id <= last_seen 一律丢弃
      - created_at > last_mail_sent 兜底(防 id 比对异常时回填旧帖)
      - 冷启动 → 时间窗 = now - 24h
      - 任何异常 → log + 返回 FetchResult(api_failed=True),quotes 为空

    返回 quotes 按 created_at 升序(便于 commit_state 取 max id)。
    """
    if not uid:
        logger.info("duan.skip reason=DUAN_USER_ID_missing")
        return FetchResult(quotes=[], skip_reason="DUAN_USER_ID_missing")
    if not token:
        logger.info("duan.warn XQ_A_TOKEN_missing 仍尝试请求")

    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last_seen = _load_last_seen(state_dir)
    last_mail_sent = _load_last_mail_sent(state_dir)
    cold_start = last_seen is None and last_mail_sent is None

    if last_mail_sent is not None:
        time_floor = last_mail_sent
    else:
        time_floor = now_utc - timedelta(hours=COLD_START_WINDOW_HOURS)

    logger.info(
        "duan.window cold_start=%s last_seen=%s time_floor=%s",
        cold_start, last_seen, time_floor.isoformat(),
    )

    try:
        statuses = _fetch_timeline(uid, token)
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError) as exc:
        logger.warning("duan.fetch_failed exc=%r 返回空列表,主流程继续", exc)
        return FetchResult(quotes=[], api_failed=True, error=f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("duan.fetch_unexpected exc=%r 返回空列表,主流程继续", exc)
        return FetchResult(quotes=[], api_failed=True, error=f"{type(exc).__name__}: {exc}")

    parsed: list[DuanQuote] = []
    for st in statuses:
        if not isinstance(st, dict):
            continue
        q = _parse_status(st, uid)
        if q is None:
            continue
        if last_seen is not None and q.id <= last_seen:
            continue
        if q.created_at <= time_floor:
            continue
        parsed.append(q)

    parsed.sort(key=lambda x: x.created_at)
    logger.info(
        "duan.parsed total=%d kept=%d (window pre-state-update)",
        len(statuses), len(parsed),
    )
    return FetchResult(quotes=parsed, raw_count=len(statuses))


def write_voices_json(path: Path, quotes: list[DuanQuote]) -> None:
    """写 build/voices.json(列表为空时也写,保持文件存在便于诊断)。"""
    payload = []
    for q in quotes:
        item = {
            "author": "段永平",
            "time": to_beijing(q.created_at).strftime("%Y-%m-%d %H:%M"),
            "content": q.text,
            "url": q.url,
        }
        if q.parent_text:
            item["parent"] = {
                "author": q.parent_author or "",
                "text": q.parent_text,
            }
        payload.append(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def commit_state(
    state_dir: Path,
    quotes: list[DuanQuote],
    sent_at: datetime,
) -> None:
    """邮件发送成功后调用:先写 last_mail_sent,再写 last_seen。

    quotes 为空时只更新 last_mail_sent(下次时间窗口推进)。
    """
    sent_utc = sent_at.astimezone(timezone.utc) if sent_at.tzinfo else sent_at.replace(tzinfo=timezone.utc)
    _write_json_atomic(
        state_dir / LAST_MAIL_SENT_FILE,
        {"sent_at": sent_utc.isoformat()},
    )
    if quotes:
        max_id = max(q.id for q in quotes)
        # 与已有 last_seen 取较大值,避免回退
        prev = _load_last_seen(state_dir) or 0
        _write_json_atomic(
            state_dir / LAST_SEEN_FILE,
            {"last_seen_id": max(max_id, prev)},
        )
    logger.info(
        "duan.state.committed quotes=%d sent_at=%s",
        len(quotes), sent_utc.isoformat(),
    )


# ----------------------------------------------------------------------
# 健康监控:.state/duan_health.json
# ----------------------------------------------------------------------
@dataclass
class HealthState:
    last_run_at: str | None = None
    last_api_success_at: str | None = None
    last_api_returned_data_at: str | None = None
    last_skip_reason: str | None = None
    consecutive_api_failures: int = 0
    consecutive_empty_returns: int = 0


def _load_health(state_dir: Path) -> HealthState:
    p = state_dir / HEALTH_FILE
    if not p.exists():
        return HealthState()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return HealthState(
            last_run_at=data.get("last_run_at"),
            last_api_success_at=data.get("last_api_success_at"),
            last_api_returned_data_at=data.get("last_api_returned_data_at"),
            last_skip_reason=data.get("last_skip_reason"),
            consecutive_api_failures=int(data.get("consecutive_api_failures") or 0),
            consecutive_empty_returns=int(data.get("consecutive_empty_returns") or 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("duan.health.parse_failed exc=%s; reset", exc)
        return HealthState()


def update_health(
    state_dir: Path,
    result: FetchResult,
    *,
    now: datetime | None = None,
) -> HealthState:
    """根据 FetchResult 更新健康状态并落盘。返回更新后的 HealthState。

    更新规则:
      - api_failed=True:        consecutive_api_failures += 1
      - api 成功:               consecutive_api_failures = 0
                                  raw_count > 0 → consecutive_empty_returns = 0
                                  raw_count == 0 → consecutive_empty_returns += 1
      - skip_reason 非空:        视作"尚未尝试 API",计数器都不动,只记 skip_reason
    """
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    h = _load_health(state_dir)
    h.last_run_at = now_utc.isoformat()
    h.last_skip_reason = result.skip_reason

    if result.skip_reason is not None:
        # 跳过:不动 API 计数器,只记原因
        pass
    elif result.api_failed:
        h.consecutive_api_failures += 1
    else:
        h.consecutive_api_failures = 0
        h.last_api_success_at = now_utc.isoformat()
        if result.raw_count > 0:
            h.consecutive_empty_returns = 0
            h.last_api_returned_data_at = now_utc.isoformat()
        else:
            h.consecutive_empty_returns += 1

    _write_json_atomic(state_dir / HEALTH_FILE, {
        "last_run_at": h.last_run_at,
        "last_api_success_at": h.last_api_success_at,
        "last_api_returned_data_at": h.last_api_returned_data_at,
        "last_skip_reason": h.last_skip_reason,
        "consecutive_api_failures": h.consecutive_api_failures,
        "consecutive_empty_returns": h.consecutive_empty_returns,
    })
    return h


def compute_alerts(health: HealthState) -> list[str]:
    """根据 HealthState 派生用户可读的告警字符串。无告警返回空列表。

    告警条件(独立判定,可同时触发):
      - 连续 API 失败 ≥ API_FAILURE_ALERT_THRESHOLD → 立即报
      - 连续空返回 ≥ EMPTY_RETURN_SOFT_THRESHOLD   → 弱报(可能 user_id 变更)
      - skip_reason=DUAN_USER_ID_missing            → 配置缺失(本地运行场景静默)
    """
    alerts: list[str] = []
    if health.consecutive_api_failures >= API_FAILURE_ALERT_THRESHOLD:
        last_ok = health.last_api_success_at or "从未成功"
        alerts.append(
            f"段永平雪球抓取连续失败 {health.consecutive_api_failures} 次"
            f"(上次成功:{last_ok})—— 检查 XQ_A_TOKEN 是否过期 / 雪球是否升级反爬。"
        )
    if health.consecutive_empty_returns >= EMPTY_RETURN_SOFT_THRESHOLD:
        last_data = health.last_api_returned_data_at or "从未返回数据"
        alerts.append(
            f"段永平雪球已连续 {health.consecutive_empty_returns} 天 API 通但无新帖"
            f"(上次有数据:{last_data})—— 可能 DUAN_USER_ID 已变更或账号停更。"
        )
    return alerts


def quotes_as_dicts(quotes: list[DuanQuote]) -> list[dict]:
    """供日志/诊断用,内部 datetime 转 ISO 字符串。"""
    out = []
    for q in quotes:
        d = asdict(q)
        d["created_at"] = q.created_at.isoformat()
        out.append(d)
    return out
