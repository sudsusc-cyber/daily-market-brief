"""
M1 阶段:数据源可行性验证脚本

逐项调用 PLAN.md 第 4-5 节列出的全部数据源,打印关键样例,
最终汇总每个源的可达性(✅ / ⚠️ / ❌)。

用法:
    # 1) 复制 .env.example -> .env,填好密钥
    # 2) 安装依赖:
    #       uv sync   或   pip install -e .
    # 3) 运行:
    #       python scripts/verify_sources.py
    #
    # 可选参数:
    #       --skip-email    跳过 SMTP 测试发信
    #       --skip-llm      跳过 DeepSeek 调用
"""

from __future__ import annotations

import argparse
import contextlib
import os
import smtplib
import sys
import textwrap
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any

# 把项目根加进 sys.path,避免被 cwd 影响
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ---------- 加载 .env(本地用) ----------
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass


# ---------- 状态枚举 ----------
STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_SKIP = "skip"

ICON = {
    STATUS_OK: "✅",
    STATUS_WARN: "⚠️ ",
    STATUS_FAIL: "❌",
    STATUS_SKIP: "⏭️ ",
}


@dataclass
class CheckResult:
    """单个数据源的验证结果"""

    name: str
    status: str  # ok / warn / fail / skip
    detail: str  # 一句话说明
    samples: list[str] = field(default_factory=list)  # 关键样例(打印用)
    elapsed_ms: int = 0


# ---------- 通用工具 ----------
def _now_utc() -> datetime:
    return datetime.now(UTC)


def _yesterday_iso() -> str:
    return (_now_utc() - timedelta(days=1)).strftime("%Y-%m-%d")


def _today_iso() -> str:
    return _now_utc().strftime("%Y-%m-%d")


def _print_section(title: str) -> None:
    bar = "─" * 60
    print(f"\n{bar}\n{title}\n{bar}")


def _safe_run(name: str, fn: Callable[[], CheckResult]) -> CheckResult:
    """统一异常包装,任何失败都不会中断整体流程"""
    _print_section(name)
    t0 = time.time()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 — 验证脚本要捕获一切
        tb = traceback.format_exc(limit=3)
        print(f"[EXCEPTION] {exc}\n{tb}")
        result = CheckResult(name=name, status=STATUS_FAIL, detail=f"未捕获异常: {exc}")
    result.elapsed_ms = int((time.time() - t0) * 1000)
    print(f"\n→ {ICON[result.status]} {result.status.upper()}  ({result.elapsed_ms} ms)")
    return result


# 通用 User-Agent — 直接 feedparser.parse(url) 在很多站点会被拒
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Safari/605.1.15"
)


def _fetch_rss(url: str, timeout: int = 20):
    """拉 RSS:先 requests 带 UA,再交给 feedparser 解析字节流"""
    import feedparser
    import requests

    resp = requests.get(
        url,
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return feedparser.parse(resp.content)


# =============================================================
# 1. yfinance — 股价 + 周均线
# =============================================================
def check_yfinance_stocks() -> CheckResult:
    import yfinance as yf

    ticker = "NVDA"
    hist = yf.Ticker(ticker).history(period="5y", interval="1wk", auto_adjust=False)
    if hist is None or hist.empty:
        return CheckResult("yfinance/stocks", STATUS_FAIL, f"{ticker} 周线为空")

    rows = len(hist)
    last_close = float(hist["Close"].iloc[-1])
    sma_120 = float(hist["Close"].tail(120).mean()) if rows >= 120 else float("nan")
    sma_200 = float(hist["Close"].tail(200).mean()) if rows >= 200 else float("nan")

    samples = [
        f"周线行数:{rows}",
        f"最新收盘:{last_close:.2f}",
        f"SMA120: {sma_120:.2f}   SMA200: {sma_200:.2f}",
        f"最近 3 周收盘:{[round(float(x), 2) for x in hist['Close'].tail(3)]}",
    ]
    for s in samples:
        print(s)

    if rows >= 200:
        return CheckResult("yfinance/stocks", STATUS_OK, f"{ticker} 周线 {rows} 行,均线可计算", samples)
    return CheckResult(
        "yfinance/stocks", STATUS_WARN, f"{ticker} 周线仅 {rows} 行,< 200 周可能不足以算 200w SMA", samples
    )


# =============================================================
# 2. Finnhub — 公司新闻
# =============================================================
def check_finnhub_news() -> CheckResult:
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        return CheckResult("finnhub/company_news", STATUS_FAIL, "缺少环境变量 FINNHUB_API_KEY")

    import finnhub  # type: ignore

    client = finnhub.Client(api_key=api_key)
    news = client.company_news("NVDA", _from=_yesterday_iso(), to=_today_iso())
    if not isinstance(news, list):
        return CheckResult("finnhub/company_news", STATUS_FAIL, f"返回非列表:{type(news).__name__}")

    samples = [f"NVDA 24h 新闻条数:{len(news)}"]
    for n in news[:3]:
        ts = datetime.fromtimestamp(n.get("datetime", 0), tz=UTC).isoformat()
        samples.append(f"  · [{ts}] {n.get('headline', '(no title)')[:90]}")
    for s in samples:
        print(s)

    if len(news) == 0:
        return CheckResult(
            "finnhub/company_news",
            STATUS_WARN,
            "API 通,但 NVDA 昨日无新闻(可能是周末/节假日,非节假日复测)",
            samples,
        )
    return CheckResult(
        "finnhub/company_news", STATUS_OK, f"NVDA 昨日 {len(news)} 条新闻", samples
    )


# =============================================================
# 3. Google News — 黄仁勋发言
# =============================================================
def check_google_news() -> CheckResult:
    url = "https://news.google.com/rss/search?q=%22Jensen+Huang%22+when:1d&hl=en-US&gl=US&ceid=US:en"
    feed = _fetch_rss(url)
    entries = feed.entries or []

    samples = [f"Google News 'Jensen Huang' 24h 条目数:{len(entries)}"]
    for e in entries[:3]:
        title = getattr(e, "title", "")[:90]
        published = getattr(e, "published", "")
        samples.append(f"  · [{published}] {title}")
    for s in samples:
        print(s)

    if len(entries) == 0:
        return CheckResult(
            "google_news/figures",
            STATUS_WARN,
            "RSS 通,但 24h 内无 'Jensen Huang' 条目(非高频日属正常)",
            samples,
        )
    return CheckResult(
        "google_news/figures", STATUS_OK, f"Google News 24h 命中 {len(entries)} 条", samples
    )


# =============================================================
# 4. CNN Fear & Greed
# =============================================================
def check_cnn_fear_greed() -> CheckResult:
    import requests

    url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; daily-market-brief/0.1; verify-script)",
        "Accept": "application/json, text/plain, */*",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        return CheckResult(
            "cnn/fear_greed",
            STATUS_FAIL,
            f"HTTP {resp.status_code},body 前 200 字: {resp.text[:200]}",
        )

    try:
        data = resp.json()
    except Exception as exc:
        return CheckResult("cnn/fear_greed", STATUS_FAIL, f"返回非 JSON: {exc}")

    fg = (data or {}).get("fear_and_greed", {})
    score = fg.get("score")
    rating = fg.get("rating")
    samples = [f"CNN F&G 当前值:{score}  评级:{rating}", f"返回 keys: {list(data.keys())[:8]}"]
    for s in samples:
        print(s)

    if score is None:
        return CheckResult("cnn/fear_greed", STATUS_WARN, "JSON 结构异常,未拿到 score", samples)
    return CheckResult("cnn/fear_greed", STATUS_OK, f"F&G={score} ({rating})", samples)


# =============================================================
# 5. yfinance — VIX / DXY
# =============================================================
def check_yfinance_macro_indices() -> CheckResult:
    import yfinance as yf

    tickers = {"^VIX": "VIX", "DX-Y.NYB": "DXY"}
    samples: list[str] = []
    fail = []

    for code, label in tickers.items():
        hist = yf.Ticker(code).history(period="1mo")
        if hist is None or hist.empty:
            fail.append(label)
            samples.append(f"{label} ({code}): 无数据")
            continue
        last = float(hist["Close"].iloc[-1])
        samples.append(f"{label} ({code}): {last:.2f}  日期: {hist.index[-1].date()}")

    for s in samples:
        print(s)

    if not fail:
        return CheckResult("yfinance/macro_indices", STATUS_OK, "VIX/DXY 全部可达", samples)
    if len(fail) < len(tickers):
        return CheckResult(
            "yfinance/macro_indices",
            STATUS_WARN,
            f"部分指数失败: {','.join(fail)}",
            samples,
        )
    return CheckResult("yfinance/macro_indices", STATUS_FAIL, "全部指数失败", samples)


# =============================================================
# 6. multpl.com — Shiller PE
# =============================================================
def check_shiller_pe() -> CheckResult:
    import requests
    from bs4 import BeautifulSoup

    url = "https://www.multpl.com/shiller-pe"
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; daily-market-brief/0.1)"},
        timeout=20,
    )
    if resp.status_code != 200:
        return CheckResult("multpl/shiller_pe", STATUS_FAIL, f"HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "lxml")
    current = soup.select_one("#current")
    if not current:
        return CheckResult(
            "multpl/shiller_pe", STATUS_WARN, "页面通,但未找到 #current 节点(选择器可能要调)"
        )

    text = current.get_text(strip=True, separator=" ")
    text_oneline = " ".join(text.split())  # 折叠换行,便于进表格
    samples = [f"#current 文本(裁前 200 字):{text[:200]}"]
    for s in samples:
        print(s)

    return CheckResult(
        "multpl/shiller_pe", STATUS_OK, f"Shiller PE 抓到原文: {text_oneline[:60]}", samples
    )


# =============================================================
# 7. 宏观 RSS — WSJ / FT / Bloomberg
# =============================================================
def check_macro_rss() -> CheckResult:
    sources = {
        "WSJ World News": "https://feeds.content.dowjones.io/public/rss/RSSWorldNews",
        "FT Home": "https://www.ft.com/?format=rss",
        # Bloomberg 没有公开 RSS,这里用常见的 proxy(失败则在报告里降级处理)
        "Bloomberg Markets (proxy)": "https://feeds.bloomberg.com/markets/news.rss",
    }

    samples: list[str] = []
    ok_count = 0
    fail_count = 0

    cutoff = _now_utc() - timedelta(hours=24)
    for name, url in sources.items():
        try:
            feed = _fetch_rss(url)
            entries = feed.entries or []
            recent: list[Any] = []
            for e in entries:
                pp = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
                if pp:
                    pub_dt = datetime(*pp[:6], tzinfo=UTC)
                    if pub_dt >= cutoff:
                        recent.append(e)
                else:
                    recent.append(e)  # 没有时间戳就一律计入
            samples.append(f"{name}: 总 {len(entries)},24h 内约 {len(recent)}")
            if entries:
                ok_count += 1
                first = entries[0]
                samples.append(f"   · 样例: {getattr(first, 'title', '')[:90]}")
            else:
                fail_count += 1
        except Exception as exc:  # noqa: BLE001
            fail_count += 1
            samples.append(f"{name}: 异常 {exc}")

    for s in samples:
        print(s)

    if ok_count == len(sources):
        return CheckResult("rss/macro_news", STATUS_OK, "WSJ/FT/Bloomberg RSS 全部可达", samples)
    if ok_count >= 2:
        return CheckResult(
            "rss/macro_news", STATUS_WARN, f"{ok_count}/{len(sources)} 个 RSS 可用", samples
        )
    return CheckResult("rss/macro_news", STATUS_FAIL, f"仅 {ok_count} 个 RSS 可用", samples)


# =============================================================
# 8. SEC EDGAR — Berkshire 13F
# =============================================================
def check_sec_edgar_13f() -> CheckResult:
    import requests

    cik = "0001067983"  # Berkshire Hathaway
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik}&type=13F-HR&dateb=&owner=include&count=5&output=atom"
    )
    headers = {
        # SEC 强制要求 UA 中带联系方式
        "User-Agent": "daily-market-brief verify-script (kaiyuan@example.com)",
        "Accept-Encoding": "gzip, deflate",
    }
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        return CheckResult(
            "sec_edgar/13f", STATUS_FAIL, f"HTTP {resp.status_code} body[0:200]: {resp.text[:200]}"
        )

    import feedparser

    feed = feedparser.parse(resp.text)
    entries = feed.entries or []
    samples = [f"Berkshire 13F atom 条目数:{len(entries)}"]
    for e in entries[:3]:
        samples.append(f"  · {getattr(e, 'title', '')[:90]}  | updated: {getattr(e, 'updated', '')}")
    for s in samples:
        print(s)

    if not entries:
        return CheckResult("sec_edgar/13f", STATUS_WARN, "返回 200,但 atom 无条目", samples)
    return CheckResult("sec_edgar/13f", STATUS_OK, f"拿到 {len(entries)} 条 13F 历史", samples)


# =============================================================
# 9. DeepSeek V4-Flash — LLM API
# =============================================================
def check_deepseek_llm(skip: bool = False) -> CheckResult:
    if skip:
        return CheckResult("deepseek/llm", STATUS_SKIP, "用户指定 --skip-llm")

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return CheckResult("deepseek/llm", STATUS_FAIL, "缺少环境变量 DEEPSEEK_API_KEY")

    from openai import OpenAI

    # PLAN 第 3 节指定 model = deepseek-v4-flash;若该模型名不存在,在结果里标 ⚠️ 等用户决策
    model_name = "deepseek-v4-flash"
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    try:
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "用一句话回答:今天股市开盘了吗?"}],
            max_tokens=64,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        # 模型不存在或鉴权失败都会到这里
        msg = str(exc)
        samples = [f"调用异常:{msg[:300]}"]
        for s in samples:
            print(s)
        # 区分:鉴权 vs 模型名错
        if "model" in msg.lower() and ("not" in msg.lower() or "invalid" in msg.lower()):
            return CheckResult(
                "deepseek/llm",
                STATUS_WARN,
                f"鉴权通,但模型 '{model_name}' 不被识别,需用户确认应使用的实际模型 ID",
                samples,
            )
        return CheckResult("deepseek/llm", STATUS_FAIL, f"调用失败: {msg[:200]}", samples)

    text = resp.choices[0].message.content if resp.choices else ""
    usage = getattr(resp, "usage", None)
    samples = [
        f"模型:{getattr(resp, 'model', model_name)}",
        f"回答:{(text or '').strip()[:160]}",
        f"用量:{usage}",
    ]
    for s in samples:
        print(s)
    return CheckResult("deepseek/llm", STATUS_OK, f"模型 {model_name} 可用", samples)


# =============================================================
# 10. QQ 邮箱 SMTP — 测试发信
# =============================================================
def check_qq_smtp(skip: bool = False) -> CheckResult:
    if skip:
        return CheckResult("qq_smtp/send", STATUS_SKIP, "用户指定 --skip-email")

    addr = os.getenv("QQ_EMAIL_ADDRESS")
    code = os.getenv("QQ_EMAIL_AUTH_CODE")
    rcpt = os.getenv("EMAIL_RECIPIENT") or addr

    missing = [k for k, v in {"QQ_EMAIL_ADDRESS": addr, "QQ_EMAIL_AUTH_CODE": code}.items() if not v]
    if missing:
        return CheckResult("qq_smtp/send", STATUS_FAIL, f"缺少环境变量: {','.join(missing)}")

    body = textwrap.dedent(
        f"""\
        这是 daily-market-brief 项目 M1 阶段的测试邮件。

        若你能在 QQ 邮箱看到这封信,说明 SMTP 通路可用,M1 邮件验收点已满足。

        发送时间(UTC):{_now_utc().isoformat(timespec='seconds')}
        发送方:{addr}
        接收方:{rcpt}
        """
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = addr
    msg["To"] = rcpt
    msg["Subject"] = Header("[M1 测试] daily-market-brief 数据源验证邮件", "utf-8")
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="daily-market-brief.local")

    server = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30)
    try:
        server.login(addr, code)
        server.sendmail(addr, [rcpt], msg.as_string())
    finally:
        with contextlib.suppress(Exception):
            server.quit()

    samples = [f"已发送 {addr} → {rcpt}"]
    for s in samples:
        print(s)
    return CheckResult("qq_smtp/send", STATUS_OK, "测试邮件已发出,请到 QQ 邮箱确认", samples)


# =============================================================
# 入口
# =============================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="daily-market-brief 数据源可行性验证 (M1)")
    parser.add_argument("--skip-email", action="store_true", help="跳过 SMTP 测试发信")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 DeepSeek 调用")
    parser.add_argument(
        "--report",
        type=str,
        default=str(PROJECT_ROOT / "docs" / "feasibility-report.md"),
        help="可行性报告输出路径",
    )
    args = parser.parse_args()

    print("=" * 64)
    print(" daily-market-brief — 数据源可行性验证 (M1)")
    print(f" 时间:{_now_utc().isoformat(timespec='seconds')}")
    print("=" * 64)

    checks = [
        ("yfinance/stocks", check_yfinance_stocks),
        ("finnhub/company_news", check_finnhub_news),
        ("google_news/figures", check_google_news),
        ("cnn/fear_greed", check_cnn_fear_greed),
        ("yfinance/macro_indices", check_yfinance_macro_indices),
        ("multpl/shiller_pe", check_shiller_pe),
        ("rss/macro_news", check_macro_rss),
        ("sec_edgar/13f", check_sec_edgar_13f),
        ("deepseek/llm", lambda: check_deepseek_llm(skip=args.skip_llm)),
        ("qq_smtp/send", lambda: check_qq_smtp(skip=args.skip_email)),
    ]

    results: list[CheckResult] = []
    for name, fn in checks:
        results.append(_safe_run(name, fn))

    # ---------- 汇总打印 ----------
    _print_section("汇 总")
    ok_n = sum(r.status == STATUS_OK for r in results)
    warn_n = sum(r.status == STATUS_WARN for r in results)
    fail_n = sum(r.status == STATUS_FAIL for r in results)
    skip_n = sum(r.status == STATUS_SKIP for r in results)
    for r in results:
        print(f"  {ICON[r.status]} {r.name:<28} — {r.detail}")
    print(f"\n合计:✅ {ok_n}   ⚠️  {warn_n}   ❌ {fail_n}   ⏭ {skip_n}   /共 {len(results)}")

    # ---------- 写报告 ----------
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_report(report_path, results, args)
    print(f"\n报告已写入:{report_path.relative_to(PROJECT_ROOT)}")

    # ---------- 退出码 ----------
    # 只要有一项 FAIL,退出码 1(便于 CI 早发现)
    return 0 if fail_n == 0 else 1


def _write_report(path: Path, results: list[CheckResult], args: argparse.Namespace) -> None:
    """生成 docs/feasibility-report.md"""
    ok_n = sum(r.status == STATUS_OK for r in results)
    warn_n = sum(r.status == STATUS_WARN for r in results)
    fail_n = sum(r.status == STATUS_FAIL for r in results)
    skip_n = sum(r.status == STATUS_SKIP for r in results)

    lines: list[str] = []
    lines.append("# 数据源可行性验证报告(M1)\n")
    lines.append(f"- 生成时间(UTC):{_now_utc().isoformat(timespec='seconds')}")
    lines.append("- 脚本:`scripts/verify_sources.py`")
    lines.append(f"- 命令行参数:`--skip-email={args.skip_email} --skip-llm={args.skip_llm}`")
    lines.append("")
    lines.append("## 总览\n")
    lines.append("| 状态 | 个数 |\n|---|---|")
    lines.append(f"| ✅ OK | {ok_n} |")
    lines.append(f"| ⚠️ WARN | {warn_n} |")
    lines.append(f"| ❌ FAIL | {fail_n} |")
    lines.append(f"| ⏭ SKIP | {skip_n} |")
    lines.append(f"| **合计** | **{len(results)}** |\n")

    lines.append("## 各数据源逐项结果\n")
    lines.append("| # | 名称 | 状态 | 一句话 | 耗时(ms) |")
    lines.append("|---|---|---|---|---|")
    for i, r in enumerate(results, 1):
        detail = r.detail.replace("|", "\\|")
        lines.append(f"| {i} | `{r.name}` | {ICON[r.status]} {r.status} | {detail} | {r.elapsed_ms} |")
    lines.append("")

    lines.append("## 详情(含样例输出)\n")
    for r in results:
        lines.append(f"### {ICON[r.status]} `{r.name}`\n")
        lines.append(f"- 状态:**{r.status}**")
        lines.append(f"- 耗时:{r.elapsed_ms} ms")
        lines.append(f"- 说明:{r.detail}")
        if r.samples:
            lines.append("")
            lines.append("**样例输出**:")
            lines.append("```text")
            for s in r.samples:
                lines.append(s)
            lines.append("```")
        lines.append("")

    lines.append("## 验收标准对照(PLAN.md 第 7 节)\n")
    lines.append("- [ ] 用户收到测试邮件(见上文 `qq_smtp/send`)")
    lines.append(
        f"- [{'x' if ok_n >= 8 else ' '}] `feasibility-report.md` 显示至少 8/10 数据源 ✅(当前 {ok_n}/10)"
    )
    if fail_n:
        lines.append("- [ ] 任何 ❌ 的源,用户已经决定'降级'或'放弃该模块'(见下文)")
        lines.append("")
        lines.append("### 待用户决策的失败项\n")
        for r in results:
            if r.status == STATUS_FAIL:
                lines.append(f"- `{r.name}` — {r.detail}")
        lines.append("")
        lines.append("> CC 不会自作主张降级或放弃,请用户回复决策。")

    if warn_n:
        lines.append("\n### 待留意的告警项(可能不影响验收,但建议关注)\n")
        for r in results:
            if r.status == STATUS_WARN:
                lines.append(f"- `{r.name}` — {r.detail}")

    lines.append("\n---\n")
    lines.append("> 本报告由 `scripts/verify_sources.py` 自动生成。\n")

    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
