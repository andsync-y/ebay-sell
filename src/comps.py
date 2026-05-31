"""Sold-based comps & sell-through (§2.13-A).

In production this calls eBay Marketplace Insights / Browse (or Terapeak) for
sold-item data to estimate sale price and turnover. Offline it reads
sample_data/comps.json keyed by source_id, and finally falls back to the
SourceItem's own ``market_price_usd``.

TODO (§2.14): wire Marketplace Insights (requires access approval) and map the
sold aggregates to est_sell_price_usd / sell_through_score / median_days.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .auth.credentials import CredentialStore
from .config import SAMPLE_DATA_DIR
from .models import Comps, SourceItem


class CompsProvider:
    def __init__(self, user_id: str = "", store: Optional[CredentialStore] = None):
        self.user_id = user_id
        self.store = store
        self._sample: Optional[dict] = None

    def _load_sample(self) -> dict:
        if self._sample is None:
            path = Path(SAMPLE_DATA_DIR) / "comps.json"
            self._sample = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return self._sample

    def get_comps(self, item: SourceItem) -> Comps:
        # TODO: if eBay token present, call Marketplace Insights here.
        sample = self._load_sample()
        rec = sample.get(item.source_id)
        if rec:
            return Comps(
                est_sell_price_usd=rec.get("est_sell_price_usd"),
                sell_through_score=float(rec.get("sell_through_score", 0.0)),
                median_days_to_sell=rec.get("median_days_to_sell"),
            )
        # Last-resort fallback: use the item's own market estimate, with a
        # neutral-ish sell-through so it isn't auto-skipped purely for missing data.
        if item.market_price_usd is not None:
            return Comps(est_sell_price_usd=item.market_price_usd, sell_through_score=0.5)
        return Comps(est_sell_price_usd=None, sell_through_score=0.0)
