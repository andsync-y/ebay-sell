"""Source inventory / price sync (§2.13-A).

For each active eBay listing we re-check the source item:
  * sold out / unavailable -> end the eBay listing (prevent non-delivery).
  * price increased        -> recompute; if still profitable reprice, else end.

This is the single most important accident-prevention feature for dropship-
style operation. Offline it runs against the sample sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .auth.credentials import CredentialStore
from .config import Config
from .ebay import EbayClient
from .fx import FxProvider
from .models import SourceItem
from .pricing import choose_shipping, compute_price
from .sources import get_source


@dataclass
class ActiveListing:
    """A currently-live eBay listing tied back to its source via SKU."""

    sku: str               # "<source>:<source_id>"
    listed_price_usd: float
    dest_country: str = "US"

    @property
    def source_name(self) -> str:
        return self.sku.split(":", 1)[0]

    @property
    def source_id(self) -> str:
        return self.sku.split(":", 1)[1]


@dataclass
class SyncDecision:
    sku: str
    action: str            # "keep" | "end" | "reprice"
    reason: str
    new_price_usd: Optional[float] = None


class InventorySync:
    def __init__(
        self,
        user_id: str,
        config: Optional[Config] = None,
        store: Optional[CredentialStore] = None,
        allow_live_fx: bool = True,
    ):
        self.config = config or Config.load()
        self.user_id = user_id
        self.store = store
        self.ebay = EbayClient(self.config, user_id, store)
        self.usd_jpy = FxProvider(self.config).usd_jpy(allow_live=allow_live_fx).buffered_rate

    def _current_source_item(self, listing: ActiveListing) -> Optional[SourceItem]:
        src = get_source(listing.source_name, self.user_id, self.store)
        return src.get_item(listing.source_id)

    def evaluate(self, listing: ActiveListing) -> SyncDecision:
        item = self._current_source_item(listing)
        if item is None:
            return SyncDecision(listing.sku, "end", "source item unavailable / sold out")

        shipping = choose_shipping(item, listing.dest_country, self.config)
        pricing = compute_price(item, shipping, self.config, self.usd_jpy)

        if not pricing.ok:
            return SyncDecision(listing.sku, "end", f"no longer profitable: {pricing.reason}")

        # Price moved up at the source: required list price now exceeds what
        # we have live -> reprice up to stay profitable.
        if pricing.list_price_usd > listing.listed_price_usd + 0.01:
            return SyncDecision(
                listing.sku, "reprice",
                f"source price changed; need {pricing.list_price_usd} USD",
                new_price_usd=pricing.list_price_usd,
            )

        return SyncDecision(listing.sku, "keep", "in stock; price within range")

    def apply(self, decision: SyncDecision) -> dict:
        if decision.action == "end":
            return self.ebay.end_listing(decision.sku, decision.reason)
        if decision.action == "reprice":
            # TODO: call Sell API offer update; stub for now.
            print(f"── [reprice STUB] {decision.sku} -> {decision.new_price_usd} USD")
            return {"status": "stub_repriced", "sku": decision.sku,
                    "price_usd": decision.new_price_usd}
        return {"status": "kept", "sku": decision.sku}

    def run(self, listings: list[ActiveListing], apply: bool = False) -> list[SyncDecision]:
        decisions = [self.evaluate(l) for l in listings]
        if apply:
            for d in decisions:
                if d.action != "keep":
                    self.apply(d)
        return decisions
