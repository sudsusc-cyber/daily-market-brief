"""GitHub Actions 内容质量告警标记。"""

from __future__ import annotations

from src.main import _clear_quality_alert, _record_quality_alert


def test_quality_alert_is_cleared_and_appended(tmp_path, monkeypatch) -> None:
    alert_path = tmp_path / ".quality-alert.txt"
    monkeypatch.setenv("QUALITY_ALERT_PATH", str(alert_path))

    alert_path.write_text("stale\n", encoding="utf-8")
    _clear_quality_alert()
    assert not alert_path.exists()

    _record_quality_alert("宏观视野加工失败")
    _record_quality_alert("宏观数据源不可用")

    assert alert_path.read_text(encoding="utf-8") == (
        "宏观视野加工失败\n"
        "宏观数据源不可用\n"
    )
