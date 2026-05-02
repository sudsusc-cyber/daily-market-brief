"""tests/test_xueqiu_duan.py — 段永平雪球 collector 单测。

覆盖点(对应规格关键不变量):
- HTML→纯文本 + 转义实体处理
- 长度 < 20 字过滤、纯转发过滤、> 400 字截断
- id 硬下限(<= last_seen 一律丢)
- 时间下限(<= last_mail_sent 一律丢)
- 冷启动 → 24h 窗口
- commit_state 顺序与原子性,空 quotes 时只推进 last_mail_sent
- 异常路径:DUAN_USER_ID 缺失 / fetch 抛错 → 返回空,主流程不受影响
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.collectors import xueqiu_duan as xd
from src.collectors.xueqiu_duan import (
    DuanQuote,
    _html_to_text,
    _is_pure_retweet,
    _parse_status,
    _truncate,
    commit_state,
    fetch_new_quotes,
    quotes_as_dicts,
    write_voices_json,
)


# ----------------------------------------------------------------------
# 文本清洗
# ----------------------------------------------------------------------
def test_html_to_text_strips_tags_and_unescapes() -> None:
    raw = "段永平&nbsp;说<br/>买股票就是<a href='x'>买公司</a>。"
    out = _html_to_text(raw)
    assert "<" not in out and ">" not in out
    assert "段永平" in out and "买公司" in out
    assert "&nbsp;" not in out and "&amp;" not in out


def test_html_to_text_preserves_paragraph_break() -> None:
    raw = "<p>第一段。</p><p>第二段。</p>"
    out = _html_to_text(raw)
    assert "第一段" in out and "第二段" in out
    assert "\n" in out  # <p> 之间应保留段落分隔


def test_truncate_short_unchanged() -> None:
    text, t = _truncate("短文不超 400")
    assert text == "短文不超 400"
    assert t is False


def test_truncate_long_marks_ellipsis() -> None:
    text, t = _truncate("a" * 500)
    assert t is True
    assert text.endswith("…")
    assert len(text) == 401  # 400 + 省略号


# ----------------------------------------------------------------------
# 纯转发判定
# ----------------------------------------------------------------------
def test_pure_retweet_no_id_is_not_retweet() -> None:
    assert _is_pure_retweet(0, "正常发言内容,长度足够长") is False


def test_pure_retweet_with_id_and_template_text() -> None:
    """只 \"转发\" 模板字 → 纯转发。\"//@\" 不再算入此函数:由长度过滤 +
    `_looks_like_reply` + 无 parent 上下文综合判定(见 orphan reply 测试)。"""
    assert _is_pure_retweet(123456, "转发") is True
    assert _is_pure_retweet(123456, "转发了") is True
    assert _is_pure_retweet(123456, "//@某用户:") is False  # 长度/orphan 路径处理


def test_pure_retweet_with_id_and_real_comment_kept() -> None:
    assert _is_pure_retweet(123456, "我觉得他这话有道理,长期看消费股仍是底仓。") is False


# ----------------------------------------------------------------------
# 单条解析
# ----------------------------------------------------------------------
def _make_status(
    *,
    sid: int = 100,
    ts_ms: int = 1700000000000,
    text: str = "<p>这是一段足够长的段永平发言内容,超过二十个字符。</p>",
    retweet_status_id: int | None = 0,
) -> dict:
    return {
        "id": sid,
        "created_at": ts_ms,
        "text": text,
        "retweet_status_id": retweet_status_id,
    }


def test_parse_status_ok() -> None:
    q = _parse_status(_make_status(), uid="u123")
    assert q is not None
    assert q.id == 100
    assert q.url.endswith("/u123/100")
    assert q.created_at.tzinfo == timezone.utc
    assert "二十个字符" in q.text


def test_parse_status_keeps_short_for_llm_judgment() -> None:
    """短文本不再被规则层硬截,留给 LLM 相关性判断。
    例:"想多了"(3 字)、"本分"(2 字)是段永平经典短句,规则层不应误杀。"""
    q = _parse_status(_make_status(text="<p>想多了</p>"), uid="u123")
    assert q is not None
    assert q.text == "想多了"


def test_parse_status_drops_empty() -> None:
    """完全空内容(HTML 清洗后无字符)→ 丢。"""
    q = _parse_status(_make_status(text="<p></p>"), uid="u123")
    assert q is None


def test_parse_status_drops_pure_retweet() -> None:
    q = _parse_status(_make_status(text="<p>转发</p>", retweet_status_id=999), uid="u123")
    assert q is None


def test_parse_status_truncates_long() -> None:
    long_html = "<p>" + ("买" * 500) + "</p>"
    q = _parse_status(_make_status(text=long_html), uid="u123")
    assert q is not None and q.truncated is True
    assert q.text.endswith("…")


def test_parse_status_bad_id_returns_none() -> None:
    bad = _make_status()
    bad["id"] = "not-a-number"
    assert _parse_status(bad, uid="u123") is None


def test_parse_status_bad_timestamp_returns_none() -> None:
    bad = _make_status()
    bad["created_at"] = None
    assert _parse_status(bad, uid="u123") is None


# ----------------------------------------------------------------------
# fetch_new_quotes:窗口 / 下限 / 冷启动 / 异常
# ----------------------------------------------------------------------
def _ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_fetch_missing_uid_returns_skip(tmp_path: Path) -> None:
    r = fetch_new_quotes(uid="", token="abc", state_dir=tmp_path)
    assert r.quotes == []
    assert r.skip_reason == "DUAN_USER_ID_missing"
    assert r.api_failed is False


def test_fetch_cold_start_uses_24h_window(tmp_path: Path) -> None:
    """无状态文件 → 窗口 = now - 24h,早于 24h 的帖子丢弃。"""
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(hours=2)   # 应保留
    stale = now - timedelta(hours=48)  # 应丢弃

    fake_statuses = [
        _make_status(sid=200, ts_ms=_ts_ms(fresh),
                     text="<p>这是窗口内一条足够长的新发言示例,文字明显超过二十个字符,确保不被短回复过滤。</p>"),
        _make_status(sid=199, ts_ms=_ts_ms(stale),
                     text="<p>这是 48 小时前的旧发言示例,长度也明显超过二十个字符,确保不被短回复过滤。</p>"),
    ]
    with patch.object(xd, "_fetch_timeline", return_value=fake_statuses):
        r = fetch_new_quotes(uid="u123", token="t", state_dir=tmp_path, now=now)

    ids = [q.id for q in r.quotes]
    assert 200 in ids and 199 not in ids
    assert r.api_failed is False
    assert r.raw_count == 2


def test_fetch_id_floor_drops_seen(tmp_path: Path) -> None:
    """已写入 last_seen=300 → 任何 id<=300 一律丢弃,即便时间在窗口内。"""
    (tmp_path / "duan_last_seen.json").write_text(
        json.dumps({"last_seen_id": 300}), encoding="utf-8"
    )
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    (tmp_path / "last_mail_sent.json").write_text(
        json.dumps({"sent_at": (now - timedelta(hours=12)).isoformat()}), encoding="utf-8"
    )
    fake = [
        _make_status(sid=300, ts_ms=_ts_ms(now - timedelta(hours=1)),
                     text="<p>这是已看过的旧帖示例,长度明显超过二十个字符,确保不被短回复过滤。</p>"),
        _make_status(sid=301, ts_ms=_ts_ms(now - timedelta(hours=1)),
                     text="<p>这是新帖应保留示例,长度明显超过二十个字符,确保不被短回复过滤。</p>"),
    ]
    with patch.object(xd, "_fetch_timeline", return_value=fake):
        r = fetch_new_quotes(uid="u123", token="t", state_dir=tmp_path, now=now)
    assert [q.id for q in r.quotes] == [301]


def test_fetch_time_floor_drops_pre_last_mail(tmp_path: Path) -> None:
    """last_mail_sent 在 created_at 之后 → 旧帖丢弃,即便 id 大于 last_seen。"""
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    last_mail_sent = now - timedelta(hours=2)
    (tmp_path / "last_mail_sent.json").write_text(
        json.dumps({"sent_at": last_mail_sent.isoformat()}), encoding="utf-8"
    )
    fake = [
        _make_status(sid=400, ts_ms=_ts_ms(last_mail_sent - timedelta(minutes=30)),
                     text="<p>这是上次发邮件之前的旧发言示例,长度明显超过二十个字符,确保不被短回复过滤。</p>"),
        _make_status(sid=401, ts_ms=_ts_ms(last_mail_sent + timedelta(minutes=30)),
                     text="<p>这是上次发邮件之后的新发言示例,长度明显超过二十个字符,确保不被短回复过滤。</p>"),
    ]
    with patch.object(xd, "_fetch_timeline", return_value=fake):
        r = fetch_new_quotes(uid="u123", token="t", state_dir=tmp_path, now=now)
    assert [q.id for q in r.quotes] == [401]


def test_fetch_marks_api_failed_on_http_error(tmp_path: Path) -> None:
    """fetch 异常 → quotes 空 + api_failed=True + error 非空。"""
    with patch.object(xd, "_fetch_timeline", side_effect=RuntimeError("boom")):
        r = fetch_new_quotes(uid="u123", token="t", state_dir=tmp_path)
    assert r.quotes == []
    assert r.api_failed is True
    assert "boom" in (r.error or "")


def test_fetch_marks_api_failed_on_unexpected_exception(tmp_path: Path) -> None:
    with patch.object(xd, "_fetch_timeline", side_effect=ValueError("weird")):
        r = fetch_new_quotes(uid="u123", token="t", state_dir=tmp_path)
    assert r.quotes == []
    assert r.api_failed is True


def test_fetch_api_ok_but_empty_records_zero_raw_count(tmp_path: Path) -> None:
    """API 通但返回 0 帖 → api_failed=False, raw_count=0(用于驱动 empty_returns 计数)。"""
    with patch.object(xd, "_fetch_timeline", return_value=[]):
        r = fetch_new_quotes(uid="u123", token="t", state_dir=tmp_path)
    assert r.quotes == []
    assert r.api_failed is False
    assert r.raw_count == 0


# ----------------------------------------------------------------------
# 状态更新与 voices.json
# ----------------------------------------------------------------------
def test_commit_state_writes_max_id_and_sent_at(tmp_path: Path) -> None:
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    quotes = [
        DuanQuote(id=10, created_at=now - timedelta(minutes=2),
                  text="t1", url="u", truncated=False),
        DuanQuote(id=12, created_at=now - timedelta(minutes=1),
                  text="t2", url="u", truncated=False),
        DuanQuote(id=11, created_at=now, text="t3", url="u", truncated=False),
    ]
    commit_state(tmp_path, quotes, sent_at=now)
    last_seen = json.loads((tmp_path / "duan_last_seen.json").read_text(encoding="utf-8"))
    last_mail = json.loads((tmp_path / "last_mail_sent.json").read_text(encoding="utf-8"))
    assert last_seen["last_seen_id"] == 12
    assert last_mail["sent_at"].startswith("2026-05-03T12:00")


def test_commit_state_does_not_regress_last_seen(tmp_path: Path) -> None:
    """已有 last_seen=999 不会被新一批小 id 回退。"""
    (tmp_path / "duan_last_seen.json").write_text(
        json.dumps({"last_seen_id": 999}), encoding="utf-8"
    )
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    quotes = [DuanQuote(id=42, created_at=now, text="t", url="u", truncated=False)]
    commit_state(tmp_path, quotes, sent_at=now)
    after = json.loads((tmp_path / "duan_last_seen.json").read_text(encoding="utf-8"))
    assert after["last_seen_id"] == 999


def test_commit_state_empty_quotes_only_advances_time(tmp_path: Path) -> None:
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    commit_state(tmp_path, [], sent_at=now)
    assert (tmp_path / "last_mail_sent.json").exists()
    assert not (tmp_path / "duan_last_seen.json").exists()


def test_write_voices_json_shape(tmp_path: Path) -> None:
    out = tmp_path / "build" / "voices.json"
    quotes = [
        DuanQuote(
            id=1, created_at=datetime(2026, 5, 3, 4, 0, tzinfo=timezone.utc),
            text="买股票就是买公司,长期看自由现金流。", url="https://xueqiu.com/u/1",
            truncated=False,
        ),
    ]
    write_voices_json(out, quotes)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    item = data[0]
    assert item["author"] == "段永平"
    assert item["url"].endswith("/u/1")
    # 北京时间(UTC+8)= 12:00
    assert item["time"].endswith("12:00")
    assert "买股票" in item["content"]


def test_write_voices_json_empty_writes_empty_array(tmp_path: Path) -> None:
    out = tmp_path / "build" / "voices.json"
    write_voices_json(out, [])
    assert json.loads(out.read_text(encoding="utf-8")) == []


def test_quotes_as_dicts_serializable() -> None:
    q = DuanQuote(id=1, created_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
                  text="t", url="u", truncated=False)
    serialized = json.dumps(quotes_as_dicts([q]))
    assert "2026-05-03" in serialized


# ----------------------------------------------------------------------
# 健康跟踪与告警
# ----------------------------------------------------------------------
def test_update_health_first_run_api_ok_with_data(tmp_path: Path) -> None:
    """初次运行 + API 通 + 有数据 → 计数器全 0,两个时间戳都更新。"""
    now = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
    r = xd.FetchResult(quotes=[], raw_count=5, api_failed=False)
    h = xd.update_health(tmp_path, r, now=now)
    assert h.consecutive_api_failures == 0
    assert h.consecutive_empty_returns == 0
    assert h.last_api_success_at == now.isoformat()
    assert h.last_api_returned_data_at == now.isoformat()


def test_update_health_api_ok_but_empty_increments_empty_streak(tmp_path: Path) -> None:
    now1 = datetime(2026, 5, 3, tzinfo=timezone.utc)
    now2 = datetime(2026, 5, 4, tzinfo=timezone.utc)
    xd.update_health(tmp_path, xd.FetchResult([], raw_count=0, api_failed=False), now=now1)
    h2 = xd.update_health(tmp_path, xd.FetchResult([], raw_count=0, api_failed=False), now=now2)
    assert h2.consecutive_empty_returns == 2
    assert h2.consecutive_api_failures == 0
    # last_api_success 推进,last_api_returned_data 未推进
    assert h2.last_api_success_at == now2.isoformat()
    assert h2.last_api_returned_data_at is None


def test_update_health_api_failure_increments_failure_streak(tmp_path: Path) -> None:
    now1 = datetime(2026, 5, 3, tzinfo=timezone.utc)
    now2 = datetime(2026, 5, 4, tzinfo=timezone.utc)
    xd.update_health(tmp_path, xd.FetchResult([], api_failed=True, error="x"), now=now1)
    h2 = xd.update_health(tmp_path, xd.FetchResult([], api_failed=True, error="y"), now=now2)
    assert h2.consecutive_api_failures == 2
    # 失败时不动 last_api_success,且 empty_streak 计数也不变
    assert h2.last_api_success_at is None


def test_update_health_failure_streak_resets_on_success(tmp_path: Path) -> None:
    """连续失败后一次成功 → failure 计数清零。"""
    now1 = datetime(2026, 5, 3, tzinfo=timezone.utc)
    now2 = datetime(2026, 5, 4, tzinfo=timezone.utc)
    xd.update_health(tmp_path, xd.FetchResult([], api_failed=True, error="x"), now=now1)
    h2 = xd.update_health(tmp_path, xd.FetchResult([], raw_count=3, api_failed=False), now=now2)
    assert h2.consecutive_api_failures == 0
    assert h2.consecutive_empty_returns == 0


def test_update_health_skip_does_not_touch_counters(tmp_path: Path) -> None:
    """skip_reason 非空 → 不动 API 计数器(本地运行场景)。"""
    now = datetime(2026, 5, 3, tzinfo=timezone.utc)
    h = xd.update_health(
        tmp_path,
        xd.FetchResult([], skip_reason="DUAN_USER_ID_missing"),
        now=now,
    )
    assert h.consecutive_api_failures == 0
    assert h.consecutive_empty_returns == 0
    assert h.last_skip_reason == "DUAN_USER_ID_missing"


def test_compute_alerts_silent_when_healthy() -> None:
    h = xd.HealthState(consecutive_api_failures=0, consecutive_empty_returns=0)
    assert xd.compute_alerts(h) == []


def test_compute_alerts_fires_on_first_api_failure() -> None:
    """阈值 1:一次失败即报。"""
    h = xd.HealthState(
        consecutive_api_failures=1,
        last_api_success_at="2026-04-30T00:00:00+00:00",
    )
    alerts = xd.compute_alerts(h)
    assert len(alerts) == 1
    assert "雪球抓取连续失败" in alerts[0]
    assert "XQ_A_TOKEN" in alerts[0]


def test_compute_alerts_fires_on_long_empty_streak() -> None:
    """连续 7 天 API 通但无新帖 → 弱告警(可能 user_id 变更)。"""
    h = xd.HealthState(
        consecutive_empty_returns=7,
        last_api_returned_data_at="2026-04-26T00:00:00+00:00",
    )
    alerts = xd.compute_alerts(h)
    assert len(alerts) == 1
    assert "DUAN_USER_ID" in alerts[0]


def test_compute_alerts_does_not_fire_below_empty_threshold() -> None:
    h = xd.HealthState(consecutive_empty_returns=6)
    assert xd.compute_alerts(h) == []


def test_compute_alerts_combines_multiple_conditions() -> None:
    h = xd.HealthState(consecutive_api_failures=3, consecutive_empty_returns=10)
    alerts = xd.compute_alerts(h)
    assert len(alerts) == 2


def test_load_health_corrupted_file_returns_default(tmp_path: Path) -> None:
    """损坏的 health 文件不应让模块崩溃。"""
    (tmp_path / "duan_health.json").write_text("{not valid json", encoding="utf-8")
    h = xd._load_health(tmp_path)
    assert h.consecutive_api_failures == 0
    assert h.last_api_success_at is None


# ----------------------------------------------------------------------
# 回复 / 转评型帖子(parent_text 上下文)
# ----------------------------------------------------------------------
def _status_with_parent(
    *,
    sid: int = 600,
    ts_ms: int = 1700000000000,
    text: str,
    parent_text_html: str,
    parent_author: str = "投资爱好者",
    rt_status_id: int = 9999,
) -> dict:
    return {
        "id": sid,
        "created_at": ts_ms,
        "text": text,
        "retweet_status_id": rt_status_id,
        "retweeted_status": {
            "id": rt_status_id,
            "text": parent_text_html,
            "user": {"screen_name": parent_author},
        },
    }


def test_parse_status_with_parent_context_extracted() -> None:
    """转评型帖子:retweeted_status 嵌入原帖 → parent_text/parent_author 被提取。"""
    s = _status_with_parent(
        sid=600,
        text="<p>想多了,这种估值方法长期看根本撑不住,关键是看自由现金流和资本配置。</p>",
        parent_text_html="<p>茅台 PE=40 已经是泡沫了,要清仓。</p>",
        parent_author="股民甲",
    )
    q = xd._parse_status(s, uid="u123")
    assert q is not None
    assert q.parent_text == "茅台 PE=40 已经是泡沫了,要清仓。"
    assert q.parent_author == "股民甲"
    assert "想多了" in q.text


def test_parse_status_orphan_reply_is_dropped() -> None:
    """以 //@ 开头但 retweeted_status 缺失 → 丢弃,避免断章取义。"""
    s = {
        "id": 700,
        "created_at": 1700000000000,
        "text": "<p>//@股民乙: 完全同意你的判断,长期看就是这样。</p>",
        "retweet_status_id": 0,  # 没有嵌入的 parent
    }
    assert xd._parse_status(s, uid="u123") is None


def test_parse_status_orphan_huifu_marker_is_dropped() -> None:
    """以"回复@" 开头但无 retweeted_status → 丢弃。"""
    s = {
        "id": 701,
        "created_at": 1700000000000,
        "text": "<p>回复@股民丙: 这个观点我不同意,要看自由现金流。</p>",
    }
    assert xd._parse_status(s, uid="u123") is None


def test_parse_status_reply_marker_with_parent_kept() -> None:
    """//@ 开头但 retweeted_status 提供了原帖 → 保留(可上下文化渲染)。"""
    s = _status_with_parent(
        sid=702,
        text="<p>//@股民丁: 这个观点我不同意,自由现金流才是关键判断指标。</p>",
        parent_text_html="<p>苹果回购就是花投资者的钱抬股价。</p>",
        parent_author="股民丁",
    )
    q = xd._parse_status(s, uid="u123")
    assert q is not None
    assert q.parent_author == "股民丁"
    assert "苹果回购" in q.parent_text


def test_parse_status_parent_html_is_cleaned_and_truncated() -> None:
    """原帖 HTML 标签去除,超长截断到 PARENT_TEXT_MAX_CHARS。"""
    long_parent = "<p>" + ("讨" * (xd.PARENT_TEXT_MAX_CHARS + 50)) + "</p>"
    s = _status_with_parent(
        sid=703,
        text="<p>这个讨论方向有道理,但还要看公司治理的本分问题。</p>",
        parent_text_html=long_parent,
    )
    q = xd._parse_status(s, uid="u123")
    assert q is not None and q.parent_text is not None
    assert q.parent_text.endswith("…")
    assert len(q.parent_text) == xd.PARENT_TEXT_MAX_CHARS + 1


def test_parse_status_standalone_post_has_no_parent() -> None:
    """非回复/非转评的独立短文 → parent_text/author 都为 None。"""
    s = _make_status(text="<p>本分就是做对的事,做难而正确的事,这就是企业的护城河。</p>")
    q = xd._parse_status(s, uid="u123")
    assert q is not None
    assert q.parent_text is None
    assert q.parent_author is None


def test_write_voices_json_includes_parent_when_present(tmp_path: Path) -> None:
    """voices.json 的诊断输出在有 parent 时应包含 parent 字段。"""
    out = tmp_path / "voices.json"
    quotes = [
        DuanQuote(
            id=1, created_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
            text="想多了。", url="https://xueqiu.com/u/1", truncated=False,
            parent_text="茅台估值已是泡沫", parent_author="股民甲",
        ),
        DuanQuote(
            id=2, created_at=datetime(2026, 5, 3, tzinfo=timezone.utc),
            text="本分就是做对的事。", url="https://xueqiu.com/u/2", truncated=False,
        ),
    ]
    xd.write_voices_json(out, quotes)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0]["parent"] == {"author": "股民甲", "text": "茅台估值已是泡沫"}
    assert "parent" not in data[1]
