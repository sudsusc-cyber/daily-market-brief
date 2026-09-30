"""Bounded, credential-free descriptions of content quality, separate from SMTP."""


def quality_details(report: dict) -> list[str]:
    labels = {
        "company": "个股动态",
        "macro": "宏观视野",
        "figures": "人物观点",
        "frontier": "前沿动态",
        "sentiment": "情绪说明",
        "judgment": "长期判断",
        "holdings_intro": "持仓引言",
        "fuel": "油价",
        "collection": "采集",
    }
    details = []
    observations = report.get("observations", [])
    for state, label in [
        ("carried", "有效沿用"),
        ("missing", "数据缺失"),
        ("conflict", "来源冲突"),
    ]:
        rows = [r for r in observations if r.get("status") == state]
        if rows:
            details.append(
                label
                + "："
                + "、".join(
                    f"{r['key']}（{r.get('observed_at') or '日期未知'}）" for r in rows[:35]
                )
            )
    for section, health in report.get("section_health", {}).items():
        issues = []
        for key, label in [
            ("source_failures", "来源失败"),
            ("processing_failures", "加工失败"),
            ("content_rejections", "候选核验拒绝"),
            ("timeout_count", "超时"),
        ]:
            if health.get(key):
                issues.append(f"{label} {health[key]}")
        if health.get("fallback"):
            issues.append("已使用备用说明" if section == "sentiment" else "已降级处理")
        if issues:
            details.append(f"加工状态·{labels.get(section, section)}：" + "，".join(issues))
    for section, coverage in report.get("news_coverage", {}).items():
        if coverage.get("extractive_fallbacks"):
            details.append(
                f"摘要形式·{labels.get(section, section)}：{coverage['extractive_fallbacks']} 条采用来源摘录或核验译文（不等于发送失败）"
            )
    size = report.get("html_bytes", 0)
    if size > 98304:
        details.append(f"邮件体积超限：{size} bytes，警戒线 98304 bytes")
    return details
