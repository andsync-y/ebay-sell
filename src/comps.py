"""Sold-based comps & sell-through (§2.13-A).

In production this calls eBay Marketplace Insights / Browse (or Terapeak) for
sold-item data to estimate sale price and turnover. Offline it reads
sample_data/comps.json keyed by source_id, and finally falls back to the
SourceItem's own ``market_price_usd``.

When a HistoryStore is supplied, historical sell-through and sold prices
take precedence over generic estimates once minimum sample thresholds are met.

TODO (§2.14): wire Marketplace Insights (requires access approval) and map the
sold aggregates to est_sell_price_usd / sell_through_score / median_days.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .auth.credentials import CredentialStore
from .config import SAMPLE_DATA_DIR
from .models import Comps, SourceItem

if TYPE_CHECKING:
    from .history import HistoryStore


class CompsProvider:
    def __init__(
        self,
        user_id: str = "",
        store: Optional[CredentialStore] = None,
        history: Optional["HistoryStore"] = None,
    ):
        self.user_id = user_id
        self.store = store
        self.history = history
        self._sample: Optional[dict] = None

    def _load_sample(self) -> dict:
        if self._sample is None:
            path = Path(SAMPLE_DATA_DIR) / "comps.json"
            self._sample = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return self._sample

    def get_comps(self, item: SourceItem) -> Comps:
        # TODO: if eBay token present, call Marketplace Insights here.

        # ── 1. Static sample data keyed by source_id ─────────────────────────
        sample = self._load_sample()
        rec = sample.get(item.source_id)
        base_sell_through = float(rec["sell_through_score"]) if rec else None
        base_price        = rec.get("est_sell_price_usd") if rec else None
        base_days         = rec.get("median_days_to_sell") if rec else None

        # Fall back to item's own market estimate if no sample entry.
        if base_price is None and item.market_price_usd is not None:
            base_price = item.market_price_usd
        if base_sell_through is None:
            base_sell_through = 0.5 if base_price else 0.0

        # ── 2. Historical overrides (higher priority when data is reliable) ───
        if self.history:
            hist_st = self.history.historical_sell_through(item.brand, item.category)
            if hist_st is not None:
                base_sell_through = hist_st  # observed rate replaces estimate

            hist_price = self.history.historical_sold_price(item.brand)
            if hist_price is not None:
                base_price = hist_price  # median sold price replaces estimate

        return Comps(
            est_sell_price_usd=base_price,
            sell_through_score=base_sell_through,
            median_days_to_sell=base_days,
        )

