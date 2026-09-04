"""估值模块共享数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

FreshnessStatus = Literal[
    "current",
    "not_due",
    "new_filing_pending",
    "source_unavailable",
    "conflict",
    "manual_review",
]


@dataclass(frozen=True)
class OfficialDocument:
    """监管机构或公司 IR 发布的一份可唯一识别的正式文件。"""

    document_id: str
    document_type: str
    report_period: str | None
    published_at: datetime
    discovered_at: datetime
    source_url: str
    source_domain: str
    title: str = ""
    content_hash: str | None = None


@dataclass(frozen=True)
class FreshnessResult:
    """某只股票在本次运行截止时的数据新鲜度结论。"""

    ticker: str
    status: FreshnessStatus
    checked_at: datetime
    latest_document: OfficialDocument | None = None
    valuation_document_id: str | None = None
    reason: str | None = None

    @property
    def may_publish_value(self) -> bool:
        """只有底稿明确追上最新官方文件时才允许展示估值。"""
        return (
            self.status in {"current", "not_due"}
            and self.latest_document is not None
            and self.valuation_document_id == self.latest_document.document_id
        )


@dataclass(frozen=True)
class ValuationDisplay:
    """邮件持仓表需要的最小估值视图。"""

    ticker: str
    status: FreshnessStatus
    intrinsic_value: float | None = None
    implied_return: float | None = None
    hurdle_rate: float | None = None
    currency_symbol: str = "$"
    financial_as_of: str | None = None
    approved_at: str | None = None
    source_url: str | None = None
    source_document_id: str | None = None
    formula_id: str | None = None
    model_version: str | None = None
    return_label: str = "IRR"
    value_label: str = "内在价值"
    warnings: tuple[str, ...] = field(default_factory=tuple)
    data_note: str | None = None
    verified_at: str | None = None

    @property
    def is_attractive(self) -> bool:
        return (
            self.implied_return is not None
            and self.hurdle_rate is not None
            and self.implied_return >= self.hurdle_rate
        )

    @property
    def is_pending(self) -> bool:
        return self.intrinsic_value is None
