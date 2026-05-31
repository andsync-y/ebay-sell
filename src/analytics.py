"""P&L dashboard & estimate-vs-actual learning (§2.13-E).

Aggregates completed sales into monthly / category / source ROI summaries, and
compares estimated vs actual costs so the pricing model can be tuned. Pure
functions over a list of sale records; storage/source is the caller's choice.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SaleRecord:
    sku: str
    category: str
    source: str
    month: str                 # "YYYY-MM"
    sold_price_jpy: int        # realised revenue in JPY (post fx)
    cost_jpy: int              # actual acquisition cost
    actual_shipping_jpy: int
    actual_fee_jpy: int
    estimated_profit_jpy: int = 0   # what the pipeline predicted

    @property
    def actual_profit_jpy(self) -> int:
        return self.sold_price_jpy - self.cost_jpy - self.actual_shipping_jpy - self.actual_fee_jpy


@dataclass
class Bucket:
    count: int = 0
    revenue_jpy: int = 0
    profit_jpy: int = 0

    @property
    def roi(self) -> float:
        spend = self.revenue_jpy - self.profit_jpy
        return (self.profit_jpy / spend) if spend > 0 else 0.0


@dataclass
class Dashboard:
    by_month: dict[str, Bucket] = field(default_factory=dict)
    by_category: dict[str, Bucket] = field(default_factory=dict)
    by_source: dict[str, Bucket] = field(default_factory=dict)
    total: Bucket = field(default_factory=Bucket)
    estimate_bias_jpy: float = 0.0   # mean(actual - estimated); +ve = we under-promised


def _add(b: dict, key: str, rec: SaleRecord) -> None:
    bucket = b.setdefault(key, Bucket())
    bucket.count += 1
    bucket.revenue_jpy += rec.sold_price_jpy
    bucket.profit_jpy += rec.actual_profit_jpy


def build_dashboard(records: list[SaleRecord]) -> Dashboard:
    dash = Dashboard()
    bias_sum = 0
    for r in records:
        _add(dash.by_month, r.month, r)
        _add(dash.by_category, r.category or "uncategorised", r)
        _add(dash.by_source, r.source, r)
        dash.total.count += 1
        dash.total.revenue_jpy += r.sold_price_jpy
        dash.total.profit_jpy += r.actual_profit_jpy
        bias_sum += (r.actual_profit_jpy - r.estimated_profit_jpy)
    if records:
        dash.estimate_bias_jpy = bias_sum / len(records)
    return dash


def format_dashboard(dash: Dashboard) -> str:
    lines = ["=== P&L Dashboard ==="]
    lines.append(
        f"Total: {dash.total.count} sales | revenue ¥{dash.total.revenue_jpy:,} | "
        f"profit ¥{dash.total.profit_jpy:,} | ROI {dash.total.roi:.1%}"
    )
    lines.append(f"Estimate bias (actual-estimated): ¥{dash.estimate_bias_jpy:,.0f}/sale")
    for label, book in [("Month", dash.by_month), ("Category", dash.by_category),
                        ("Source", dash.by_source)]:
        lines.append(f"\n-- by {label} --")
        for k, b in sorted(book.items()):
            lines.append(f"  {k:<16} {b.count:>3} sales  profit ¥{b.profit_jpy:>10,}  ROI {b.roi:.1%}")
    return "\n".join(lines)
