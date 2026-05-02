"""tests/test_subject_dryrun.py — 离线 60 条主题生成 dryrun。

按 plan Step 4 + 验证规范:
- 准备 20 组模拟数据,跑 3 轮 = 60 条
- 不发邮件,只生成主题打印
- 表格输出:输入摘要 + 生成主题 + 是否兜底
- 运行方式:
    uv run python -m pytest tests/test_subject_dryrun.py -s
  或直接:
    PYTHONPATH=. uv run python tests/test_subject_dryrun.py

注:此 dryrun 默认会调用 DeepSeek API(消耗 token);若 DEEPSEEK_API_KEY
未设置或想纯离线,会自动回退到只测兜底模板。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

from src.processors.subject.extractor import (
    MoodInfo,
    SignalSummary,
    SubjectData,
)
from src.processors.subject.generator import generate_subject
from src.processors.subject.solar_terms import get_solar_term_context
from src.processors.subject.validator import validate

# ──────────────  20 组测试场景(plan 给的 + 补充)  ──────────────

@dataclass
class Scenario:
    name: str
    today: date
    mood_label: str
    cnn: float | None
    vix: float | None
    rsi: float | None
    dca_count: int
    lump_sum_count: int
    dca_tickers: list[str]
    lump_tickers: list[str]
    holdings_news: str | None
    macro_news: str | None


SCENARIOS: list[Scenario] = [
    # 1. 今日实际:谷雨偏热 MCO DCA
    Scenario("今日 5/1 谷雨", date(2026, 5, 1), "偏热", 63, 18, 48,
             1, 0, ["MCO"], [], "苹果 App Store 案上诉至最高法院", None),

    # 2. 多只 DCA 春分
    Scenario("春分 3 只 DCA", date(2026, 3, 21), "中性", 50, 17, 50,
             3, 0, ["MCO", "AAPL", "AXP"], [], None, None),

    # 3. 单只 LUMP-SUM 霜降
    Scenario("霜降 1 LUMP-SUM", date(2026, 10, 24), "偏冷", 30, 25, 35,
             0, 1, [], ["MCO"], None, None),

    # 4. 多只 LUMP-SUM 大寒
    Scenario("大寒 5 LUMP-SUM", date(2026, 1, 25), "极度恐慌", 12, 38, 22,
             0, 5, [], ["MCO", "AAPL", "AXP", "KO", "GOOG"], None, None),

    # 5. 极度贪婪静默 夏至
    Scenario("夏至极度贪婪", date(2026, 6, 22), "极度贪婪", 85, 11, 75,
             0, 0, [], [], None, None),

    # 6. 极度恐惧静默 冬至
    Scenario("冬至极度恐慌", date(2026, 12, 22), "极度恐慌", 12, 40, 18,
             0, 0, [], [], None, None),

    # 7. 偏热静默 立秋
    Scenario("立秋偏热", date(2026, 8, 8), "偏热", 65, 16, 60,
             0, 0, [], [], None, None),

    # 8. 偏冷静默 立冬
    Scenario("立冬偏冷", date(2026, 11, 8), "偏冷", 35, 22, 35,
             0, 0, [], [], None, None),

    # 9. 完全中性 春分
    Scenario("春分中性", date(2026, 3, 21), "中性", 50, 18, 50,
             0, 0, [], [], None, None),

    # 10. 重大宏观-油价
    Scenario("立秋油价新高", date(2026, 8, 8), "中性", 50, 19, 48,
             0, 0, [], [], None, "布伦特原油创战后新高,突破 130 美元"),

    # 11. 重大宏观-地缘
    Scenario("秋分中东战事", date(2026, 9, 23), "偏冷", 38, 24, 40,
             0, 0, [], [], None, "中东局势升级,以色列与伊朗冲突再起"),

    # 12. 重大宏观-央行
    Scenario("白露美联储加息", date(2026, 9, 8), "偏冷", 35, 21, 38,
             0, 0, [], [], None, "美联储宣布加息 25 个基点"),

    # 13. DCA + 持仓要闻
    Scenario("小满 MCO DCA + 苹果新闻", date(2026, 5, 21), "中性", 55, 17, 50,
             1, 0, ["MCO"], [], "苹果财报业绩超预期,服务收入新高", None),

    # 14. LUMP-SUM + 极度恐惧
    Scenario("大寒 SPGI LUMP-SUM", date(2026, 1, 25), "极度恐慌", 18, 36, 25,
             0, 1, [], ["MCO"], None, None),

    # 15. 节气切换日
    Scenario("立夏当日", date(2026, 5, 6), "中性", 50, 18, 50,
             0, 0, [], [], None, None),

    # 16. 节气前夕
    Scenario("立夏前一日", date(2026, 5, 5), "中性", 50, 18, 50,
             0, 0, [], [], None, None),

    # 17. 偏热 + DCA + 宏观
    Scenario("芒种偏热DCA+原油", date(2026, 6, 6), "偏热", 68, 14, 65,
             1, 0, ["AAPL"], [], None, "OPEC+ 维持减产决定"),

    # 18. 极度贪婪 + LUMP-SUM(罕见组合)
    Scenario("立春极度贪婪+LUMP", date(2026, 2, 4), "极度贪婪", 80, 11, 78,
             0, 1, [], ["KO"], None, None),

    # 19. 节气深处 + 多 DCA
    Scenario("惊蛰 4 DCA", date(2026, 3, 12), "偏冷", 30, 26, 38,
             4, 0, ["MCO", "AAPL", "GOOG", "AXP"], [], None, None),

    # 20. 2027 年某日(测算法跨年)
    Scenario("2027 春分中性", date(2027, 3, 21), "中性", 50, 18, 50,
             0, 0, [], [], None, None),
]


def _build_data(s: Scenario) -> SubjectData:
    return SubjectData(
        solar_term=get_solar_term_context(s.today),
        mood=MoodInfo(label=s.mood_label, cnn_fear_greed=s.cnn, vix=s.vix, hsi_rsi=s.rsi),
        signals=SignalSummary(
            dca_count=s.dca_count, lump_sum_count=s.lump_sum_count,
            dca_tickers=list(s.dca_tickers), lump_sum_tickers=list(s.lump_tickers),
        ),
        holdings_news_top1=s.holdings_news,
        macro_news_top1=s.macro_news,
        email_full_text="(占位:dryrun 不传真实邮件正文)",
    )


def _try_load_llm():
    """尝试初始化 LLMClient,默认离线。

    - 默认(无 RUN_LLM_DRYRUN)→ 返回 None,主题生成走 static_fallback
      纯离线、零网络、零 token 消耗 —— 单测套件可以无副作用跑完
    - 显式 RUN_LLM_DRYRUN=1(或 true)才尝试加载 DEEPSEEK_API_KEY 真调 LLM
      这是"探索性 dryrun"专用,不应在 CI 单测里默认开
    """
    if os.environ.get("RUN_LLM_DRYRUN", "").strip().lower() not in ("1", "true"):
        return None
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        try:
            from src.settings import load_settings
            api_key = load_settings().deepseek_api_key
        except Exception:
            return None
    if not api_key:
        return None
    try:
        from src.processors.llm_client import LLMClient
        return LLMClient(api_key=api_key)
    except Exception:
        return None


def _print_table_header() -> None:
    print()
    print("=" * 130)
    print(f"{'#':>3} {'轮':>3} {'场景':25} {'节气':6} {'情绪':9} {'信号':30} {'生成主题':22} {'层':12}")
    print("-" * 130)


def _format_signals(s: Scenario) -> str:
    if s.lump_sum_count > 0 and s.dca_count > 0:
        return f"L{s.lump_sum_count}/D{s.dca_count}"
    if s.lump_sum_count > 0:
        return f"LUMP-SUM×{s.lump_sum_count}"
    if s.dca_count > 0:
        return f"DCA×{s.dca_count}"
    return "无信号"


def run_dryrun(rounds: int = 3) -> None:
    """跑 N 轮 dryrun,打印表格 + 末尾统计。"""
    llm = _try_load_llm()
    if llm:
        print(f"\n[INFO] 用 LLMClient(DeepSeek)真实调用,跑 {rounds} 轮 × {len(SCENARIOS)} 场景")
    else:
        print("\n[INFO] 无 DEEPSEEK_API_KEY,只跑兜底模板(generator 会走 LLM 错误分支 → static_fallback)")

    _print_table_header()

    valid_count = 0
    fallback_count = 0
    all_subjects: list[str] = []

    for r in range(1, rounds + 1):
        for i, sc in enumerate(SCENARIOS, start=1):
            data = _build_data(sc)
            try:
                subject = generate_subject(
                    data, llm=llm, today_bj=sc.today, use_cache=False,
                )
            except Exception as exc:  # noqa: BLE001
                subject = f"<异常:{type(exc).__name__}>"

            ok, reason = validate(subject)
            valid_marker = "✓" if ok else "✗"
            if ok:
                valid_count += 1
            # 简单识别是否走兜底:静态模板里出现的固定后缀
            is_fallback = any(
                sub in subject for sub in
                ["重锚下水", "拾贝缓行", "缓步入舟", "市气如沸",
                 "霜重欲冬", "市气渐沸", "守寒待春", "持仓如初"]
            )
            if is_fallback:
                fallback_count += 1
            layer = "fb" if is_fallback else "llm"

            all_subjects.append(subject)
            print(f"{i:>3} {r:>3} {sc.name:25} {data.solar_term.current:6} "
                  f"{sc.mood_label:9} {_format_signals(sc):30} "
                  f"{subject:22} {valid_marker} {layer}")

    total = len(SCENARIOS) * rounds
    print("-" * 130)
    print(f"\n统计:总 {total} 条,通过验证 {valid_count}({valid_count/total*100:.0f}%)")
    print(f"      其中走兜底 {fallback_count} 条,LLM 直接通过 {total-fallback_count} 条")

    # 多样性
    unique = len(set(all_subjects))
    print(f"      去重后 {unique} 个不同主题(重复 {total - unique} 条)")
    if total - unique > 6 and llm:
        print(f"⚠️ 重复较多({total - unique} > 6),LLM 风格可能太收敛")


# pytest 入口:跑 1 轮(避免单测时间过长)
def test_dryrun_one_round() -> None:
    """pytest 模式:跑 1 轮快速 smoke test。
    默认无网络无 LLM(走 static_fallback);完整 3 轮真调 DeepSeek 请显式
        RUN_LLM_DRYRUN=1 uv run pytest tests/test_subject_dryrun.py -s
    或:
        python -m tests.test_subject_dryrun
    """
    run_dryrun(rounds=1)
    # 离线模式断言:所有兜底主题都必须能通过 validator
    # (run_dryrun 内部已 print 表格,这里再加一道硬断言防 fallback 模板回归)
    if os.environ.get("RUN_LLM_DRYRUN", "").strip().lower() not in ("1", "true"):
        for sc in SCENARIOS:
            data = _build_data(sc)
            subject = generate_subject(data, llm=None, today_bj=sc.today, use_cache=False)
            ok, reason = validate(subject)
            assert ok, f"{sc.name} 兜底主题未过 validator:{subject!r} reason={reason}"


if __name__ == "__main__":
    run_dryrun(rounds=3)
