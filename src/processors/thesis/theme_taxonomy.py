"""Stable theme normalization for the long-term judgment ledger."""

from __future__ import annotations

import hashlib
import re

from .models import ThesisEvidence

_COMPANY_PREFIXES = (
    "openai-", "anthropic-", "msft-", "goog-", "nvda-",
    "tsm-", "aapl-", "cost-", "mco-", "ko-", "axp-", "brk-",
)

# High-confidence aliases curated from the 2026-05-08..2026-08-05 production
# ledger.  Unrelated singletons remain untouched and mature normally.
THEME_ALIASES: dict[str, str] = {
    # AI infrastructure demand / capex duration.
    "ai-demand-sustained": "ai-infrastructure-demand",
    "ai-enterprise-adoption": "ai-infrastructure-demand",
    "ai-demand": "ai-infrastructure-demand",
    "google-ai-infrastructure-capex": "ai-infrastructure-demand",
    "google-cloud-ai-capex": "ai-infrastructure-demand",
    "ai-infra-energy": "ai-infrastructure-demand",
    "ai-physical-ai": "ai-infrastructure-demand",
    "ai-capex-cycle": "ai-infrastructure-demand",
    "microsoft-ai-leadership": "ai-infrastructure-demand",
    "nvidia-revenue-growth": "ai-infrastructure-demand",
    "ai-chip-demand": "ai-infrastructure-demand",
    "ai-demand-2030": "ai-infrastructure-demand",
    "ai-infrastructure-cycle": "ai-infrastructure-demand",
    "cloud-ai-infrastructure-rental": "ai-infrastructure-demand",
    "nvidia-vertical-ai": "ai-infrastructure-demand",
    "azure-ai-partnership": "ai-infrastructure-demand",
    "ai-agent-compute-demand": "ai-infrastructure-demand",
    "ai-infrastructure-leasing": "ai-infrastructure-demand",
    "space-ai-compute": "ai-infrastructure-demand",
    "ai-semiconductor-tam": "ai-infrastructure-demand",

    # Accelerator competition / substitution risk.
    "amd-ai-chip-competition": "ai-accelerator-competition",
    "ai-chip-competition": "ai-accelerator-competition",
    "google-ai-chip": "ai-accelerator-competition",
    "ai-gpu-competition": "ai-accelerator-competition",
    "ai-inference-alternatives": "ai-accelerator-competition",
    "amd-hbm4-supply": "ai-accelerator-competition",
    "ai-gpu-oversupply-risk": "ai-accelerator-competition",

    # AI regulation and legal constraints.
    "ai-model-safety-regulation": "ai-regulation-risk",
    "ai-safety-regulation": "ai-regulation-risk",
    "ai-regulation-power-shift": "ai-regulation-risk",
    "ai-legal-clarity": "ai-regulation-risk",
    "enterprise-ai-data-risk": "ai-regulation-risk",

    # AI platform value capture and openness.
    "microsoft-ai-strategy": "ai-platform-economics",
    "open-ai-ecosystem": "ai-platform-economics",
    "in-house-ai-model": "ai-platform-economics",
    "ai-inference-cost-war": "ai-platform-economics",
    "open-source-ai-adoption": "ai-platform-economics",
    "ai-open-source-competition": "ai-platform-economics",
    "ai-portfolio-diversification": "ai-platform-economics",
    "multi-cloud": "ai-platform-economics",

    # Advanced-node economics and execution.
    "advanced-packaging": "advanced-node-economics",
    "advanced-node-demand": "advanced-node-economics",
    "capex-cycle": "advanced-node-economics",
    "tsmc-margin-superiority": "advanced-node-economics",
    "profitability-outlook": "advanced-node-economics",
    "overseas-expansion": "advanced-node-economics",

    # Holding-specific long-lived investment questions.
    "apple-ai-hardware": "device-ecosystem-growth",
    "apple-ai-china": "device-ecosystem-growth",
    "apple-ai-china-approval": "device-ecosystem-growth",
    "apple-iphone-demand-softening": "device-ecosystem-growth",
    "india-market-share": "device-ecosystem-growth",
    "tencent-share-buyback": "capital-allocation-tencent",
    "tencent-divestiture": "capital-allocation-tencent",
    "tencent-ai-investment": "capital-allocation-tencent",
    "tencent-buyback": "capital-allocation-tencent",
    "tencent-buyback-dividend": "capital-allocation-tencent",
    "amex-premium-customer-moat": "premium-card-economics",
    "amex-travel-spending": "premium-card-economics",
    "amex-pricing-power": "premium-card-economics",
    "amex-revenue-outlook-upgrade": "premium-card-economics",
    "amex-revenue-guidance-upgrade": "premium-card-economics",
    "membership-retail-revenue": "membership-retail-economics",
    "costco-earnings-momentum": "membership-retail-economics",
    "costco-membership-traffic": "membership-retail-economics",
    "healthcare-services-expansion": "membership-retail-economics",
    "mastercard-capital-allocation": "payment-network-expansion",
    "mastercard-virtual-card-expansion": "payment-network-expansion",
    "mastercard-portfolio-optimization": "payment-network-expansion",
    "ma-digital-payments-expansion": "payment-network-expansion",
    "ma-agency-commerce": "payment-network-expansion",
    "stablecoin-infrastructure": "payment-network-expansion",
    "coca-cola-cost-efficiency": "beverage-brand-economics",
    "coke-bottling-consolidation": "beverage-brand-economics",
    "india-bottling-ipo": "beverage-brand-economics",
    "consumer-demand-steady": "beverage-brand-economics",
    "moody-data-workflow": "ratings-data-moat",
    "moody-debt-issuance-cycle": "ratings-data-moat",
    "moody-credit-rating-demand": "ratings-data-moat",
    "linde-sanctions-litigation": "industrial-gas-economics",
    "linde-renewable-power": "industrial-gas-economics",
    "linde-chip-gas-capex": "industrial-gas-economics",
    "linde-order-backlog-record": "industrial-gas-economics",
    "popmart-ip-globalization": "collectible-ip-economics",
    "pop-mart-growth-slowdown": "collectible-ip-economics",
    "founder-alignment": "collectible-ip-economics",
    "berkshire-operating-earnings": "berkshire-capital-and-operations",
    "berkshire-succession": "berkshire-capital-and-operations",
    "berkshire-portfolio-allocation": "berkshire-capital-and-operations",
    "nvidia-roadmap": "accelerator-product-execution",
    "nvidia-product-roadmap": "accelerator-product-execution",
    "nvidia-supply-chain-expansion": "accelerator-product-execution",
}


def canonicalize_theme(raw: str) -> str:
    """Return a stable lowercase key and fold known semantic aliases."""
    theme = raw.strip().lower()
    theme = re.sub(r"[\s_]+", "-", theme)
    theme = re.sub(r"-+", "-", theme).strip("-")

    for prefix in _COMPANY_PREFIXES:
        if theme.startswith(prefix):
            theme = theme[len(prefix):]
            break

    seen: set[str] = set()
    while theme in THEME_ALIASES and theme not in seen:
        seen.add(theme)
        theme = THEME_ALIASES[theme]
    return theme


def normalize_text(text: str) -> str:
    """Collapse whitespace and lowercase for stable evidence IDs."""
    return re.sub(r"\s+", " ", text.strip()).lower()


def make_evidence_id(ev: ThesisEvidence) -> str:
    parts = [
        ev.date,
        ev.source_section,
        ev.source_name,
        ev.url or "",
        ev.theme,
        normalize_text(ev.text),
    ]
    return hashlib.sha1(
        "|".join(parts).encode("utf-8"), usedforsecurity=False,
    ).hexdigest()[:16]
