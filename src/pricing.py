"""Sizing, carrier selection, and listing-price math (§2.5).

Pure functions over Config + SourceItem. No network, no I/O.
"""

from __future__ import annotations

import math
from typing import Optional

from .config import Config
from .models import PricingResult, ShippingQuote, SourceItem


# --------------------------------------------------------------------------- #
# Size estimation
# --------------------------------------------------------------------------- #
def estimate_size(item: SourceItem, config: Config) -> tuple[int, tuple[float, float, float]]:
    """Return (weight_g, dims_cm), filling gaps from category defaults."""
    defaults = config.get("size_defaults", {}) or {}
    cat = (item.category or "default").lower()
    cat_def = defaults.get(cat) or defaults.get("default") or {
        "weight_g": 1000,
        "dims_cm": [30, 25, 15],
    }
    weight = item.weight_g if item.weight_g else int(cat_def["weight_g"])
    dims = tuple(item.dims_cm) if item.dims_cm else tuple(cat_def["dims_cm"])
    return weight, dims  # type: ignore[return-value]


def volumetric_weight_g(dims_cm: tuple[float, float, float], divisor: float) -> int:
    l, w, h = dims_cm
    # (cm^3 / divisor) gives kg; *1000 -> grams.
    return int(math.ceil((l * w * h) / divisor * 1000))


# --------------------------------------------------------------------------- #
# Carrier selection
# --------------------------------------------------------------------------- #
def _zone_for(country: str, config: Config) -> str:
    mapping = config.get("shipping.country_to_zone", {}) or {}
    return mapping.get(country.upper(), config.get("shipping.default_zone", "zone2"))


def _band_cost(zone_bands: list, charged_weight_g: int) -> Optional[int]:
    """First band whose max_weight_g >= charged weight wins."""
    for max_w, cost in sorted(zone_bands, key=lambda b: b[0]):
        if charged_weight_g <= max_w:
            return int(cost)
    return None  # exceeds carrier's heaviest band


def quote_carrier(
    carrier_name: str,
    carrier_cfg: dict,
    weight_g: int,
    dims_cm: tuple[float, float, float],
    zone: str,
    divisor: float,
) -> Optional[ShippingQuote]:
    """Return a ShippingQuote for one carrier, or None if ineligible."""
    max_weight = carrier_cfg.get("max_weight_g", 10**9)
    max_longest = carrier_cfg.get("max_longest_cm", 10**9)
    if max(dims_cm) > max_longest:
        return None

    basis = carrier_cfg.get("weight_basis", "actual")
    if basis == "volumetric_max":
        charged = max(weight_g, volumetric_weight_g(dims_cm, divisor))
    else:
        charged = weight_g

    if charged > max_weight:
        return None

    zones = carrier_cfg.get("zones", {})
    bands = zones.get(zone)
    if not bands:
        return None
    cost = _band_cost(bands, charged)
    if cost is None:
        return None

    return ShippingQuote(
        carrier=carrier_name,
        zone=zone,
        charged_weight_g=charged,
        cost_jpy=cost,
        note=f"basis={basis}",
    )


def choose_shipping(
    item: SourceItem, dest_country: str, config: Config
) -> Optional[ShippingQuote]:
    """Pick the cheapest eligible carrier for the destination."""
    weight, dims = estimate_size(item, config)
    zone = _zone_for(dest_country, config)
    divisor = float(config.get("shipping.volumetric_divisor", 5000))
    carriers = config.get("shipping.carriers", {}) or {}

    quotes: list[ShippingQuote] = []
    for name, ccfg in carriers.items():
        q = quote_carrier(name, ccfg, weight, dims, zone, divisor)
        if q:
            quotes.append(q)
    if not quotes:
        return None
    return min(quotes, key=lambda q: q.cost_jpy)


# --------------------------------------------------------------------------- #
# Listing price
# --------------------------------------------------------------------------- #
def _fee_rate_for(item: SourceItem, config: Config) -> float:
    fees = config.get("category_fee", {}) or {}
    cat = (item.category or "default").lower()
    if cat in fees:
        return float(fees[cat])
    return float(config.get("pricing.default_category_fee", fees.get("default", 0.22)))


def compute_price(
    item: SourceItem,
    shipping: Optional[ShippingQuote],
    config: Config,
    usd_jpy: float,
) -> PricingResult:
    """Reverse-engineer the list price that recovers all costs + target margin.

        profit         = cost * target_margin            (cost-basis)
        list_price_jpy = ceil((cost + shipping + profit) / (1 - fee - fx))
        list_price_usd = list_price_jpy / usd_jpy

    margin_basis="price" solves for profit on a sale-price basis instead.
    """
    cost = item.price_jpy
    ship = shipping.cost_jpy if shipping else 0
    fee_rate = _fee_rate_for(item, config)
    fx_rate = float(config.get("pricing.payment_fx_rate", 0.0))
    target_margin = float(config.get("pricing.target_margin", 0.08))
    basis = config.get("pricing.margin_basis", "cost")

    denom = 1.0 - fee_rate - fx_rate
    if denom <= 0:
        return PricingResult(
            cost_jpy=cost, shipping=shipping, ebay_fee_rate=fee_rate,
            payment_fx_rate=fx_rate, target_margin=target_margin,
            profit_jpy=0, list_price_jpy=0, list_price_usd=0.0,
            ok=False, reason="fee+fx >= 100% (check config)",
        )

    if basis == "price":
        # list*(denom) = cost + ship + list*margin  ->  list = (cost+ship)/(denom - margin)
        price_denom = denom - target_margin
        if price_denom <= 0:
            return PricingResult(
                cost_jpy=cost, shipping=shipping, ebay_fee_rate=fee_rate,
                payment_fx_rate=fx_rate, target_margin=target_margin,
                profit_jpy=0, list_price_jpy=0, list_price_usd=0.0,
                ok=False, reason="margin+fee+fx >= 100% (check config)",
            )
        list_jpy = math.ceil((cost + ship) / price_denom)
        profit = int(round(list_jpy * target_margin))
    else:  # cost-basis (default)
        profit = int(round(cost * target_margin))
        list_jpy = math.ceil((cost + ship + profit) / denom)

    min_profit = int(config.get("filter.min_profit_jpy", 0))
    ok = profit >= min_profit
    reason = "" if ok else f"profit {profit} < min {min_profit}"

    return PricingResult(
        cost_jpy=cost,
        shipping=shipping,
        ebay_fee_rate=fee_rate,
        payment_fx_rate=fx_rate,
        target_margin=target_margin,
        profit_jpy=profit,
        list_price_jpy=list_jpy,
        list_price_usd=round(list_jpy / usd_jpy, 2),
        ok=ok,
        reason=reason,
    )
