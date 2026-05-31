"""Competitive repricer (§2.13-C).

Adjusts the listing price to follow competitors *down*, but never below the
floor implied by the minimum profit line. The floor is recomputed from the
same pricing math so the minimum margin is always preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import Config
from .models import PricingResult, SourceItem
from .pricing import choose_shipping, compute_price


@dataclass
class RepriceResult:
    old_price_usd: float
    new_price_usd: float
    floor_usd: float
    changed: bool
    reason: str


def compute_floor_usd(
    item: SourceItem, dest_country: str, config: Config, usd_jpy: float
) -> float:
    """Minimum price that still meets the configured target margin.

    This is exactly the standard listing price from pricing.py: undercutting
    below it would breach the minimum profit line.
    """
    shipping = choose_shipping(item, dest_country, config)
    pricing: PricingResult = compute_price(item, shipping, config, usd_jpy)
    return pricing.list_price_usd


def reprice(
    item: SourceItem,
    current_price_usd: float,
    lowest_competitor_usd: Optional[float],
    dest_country: str,
    config: Config,
    usd_jpy: float,
) -> RepriceResult:
    floor = compute_floor_usd(item, dest_country, config, usd_jpy)
    step = float(config.get("repricer.step_usd", 0.50))

    if lowest_competitor_usd is None:
        return RepriceResult(current_price_usd, current_price_usd, floor, False,
                             "no competitor data")

    target = round(lowest_competitor_usd - step, 2)

    if target >= current_price_usd:
        # Don't raise prices automatically; only follow down.
        return RepriceResult(current_price_usd, current_price_usd, floor, False,
                             "competitor not cheaper; hold")

    if target < floor:
        # Clamp to floor; never breach minimum margin.
        if current_price_usd <= floor:
            return RepriceResult(current_price_usd, current_price_usd, floor, False,
                                 "already at/below floor; hold")
        return RepriceResult(current_price_usd, floor, floor, True,
                             "clamped to profit floor")

    return RepriceResult(current_price_usd, target, floor, True,
                         f"undercut competitor by ${step:.2f}")
