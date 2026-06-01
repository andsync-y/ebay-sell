"""Pipeline orchestration (§2.4, §2.7).

Runs the full per-item flow for a given user:

  search -> comps -> size -> shipping -> price -> compliance/catalog
        -> filters -> generate text -> ListingPlan -> (publish)

The pipeline takes a ``user_id`` and a ``CredentialStore`` and runs entirely
with that user's credentials. It works fully offline via fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .auth.credentials import CredentialStore, get_default_store
from .comps import CompsProvider
from .compliance import ComplianceChecker
from .config import Config
from .ebay import EbayClient
from .fx import FxProvider
from .generation import ListingGenerator
from .history import HistoryStore, ListingRecord, now_iso
from .models import ListingPlan, SourceItem, UserProfile
from .pricing import choose_shipping, compute_price
from .sources import get_source
from .users import UserManager


@dataclass
class PipelineContext:
    user_id: str
    config: Config
    store: CredentialStore
    dest_country: str
    marketplace: str
    currency: str
    usd_jpy: float


class Pipeline:
    def __init__(
        self,
        user_id: str,
        config: Optional[Config] = None,
        store: Optional[CredentialStore] = None,
        profile: Optional[UserProfile] = None,
        allow_live_fx: bool = True,
        history: Optional[HistoryStore] = None,
    ):
        base_config = config or Config.load()
        self.store = store or get_default_store()
        # Merge per-user overrides if a profile exists.
        if profile is None:
            profile = UserManager().get_profile(user_id)
        self.profile = profile
        self.config = base_config.for_user(profile)
        self.user_id = user_id
        self.history = history

        self.comps = CompsProvider(user_id, self.store, history=history)
        self.compliance = ComplianceChecker()
        self.generator = ListingGenerator(self.config, self._user_anthropic_key())
        self.ebay = EbayClient(self.config, user_id, self.store)
        fx = FxProvider(self.config).usd_jpy(allow_live=allow_live_fx)
        self.usd_jpy = fx.buffered_rate
        self.fx_source = fx.source

    def _user_anthropic_key(self) -> Optional[str]:
        cred = self.store.get(self.user_id, "anthropic")
        return cred.data.get("api_key") if cred else None

    def _resolve_market(self, dest_country: str) -> tuple[str, str]:
        mk = (
            (self.profile.default_marketplace if self.profile else None)
            or self.config.get("ebay.default_marketplace", "EBAY_US")
        )
        cur = self.config.get(f"ebay.marketplace_currency.{mk}", "USD")
        return mk, cur

    # -- per-item ---------------------------------------------------------- #
    def plan_item(self, item: SourceItem, dest_country: str) -> ListingPlan:
        marketplace, currency = self._resolve_market(dest_country)
        warnings: list[str] = []

        comps = self.comps.get_comps(item)

        # 1) Compliance gate (hard block on prohibited/regulated).
        comp = self.compliance.check(item)
        warnings.extend(comp.warnings)
        if not comp.allowed:
            return ListingPlan(
                item=item, catalog_match=False, photo_mode="manual",
                action="skip", title="", description="", pricing=None, comps=comps,
                warnings=warnings + [f"BLOCKED: {r}" for r in comp.reasons],
                marketplace=marketplace, currency=currency,
            )

        # 2) Shipping + price.
        shipping = choose_shipping(item, dest_country, self.config)
        if shipping is None:
            warnings.append("no eligible carrier for size/destination")
        pricing = compute_price(item, shipping, self.config, self.usd_jpy)

        # 3) Catalog / photo routing (VeRO forces manual).
        cat = self.ebay.catalog_match(item, vero=comp.is_vero)
        if cat.warnings:
            warnings.extend(cat.warnings)

        # 4) Filters (§2.7).
        action, skip_reason = self._decide_action(item, pricing, comps, cat.photo_mode)
        if skip_reason:
            warnings.append(skip_reason)

        # 5) Listing text (always from attributes; never publish a skip).
        title, description = "", ""
        if action != "skip":
            text = self.generator.generate(item)
            title, description = text.title, text.description

        return ListingPlan(
            item=item,
            catalog_match=cat.matched,
            photo_mode=cat.photo_mode,
            action=action,
            title=title,
            description=description,
            pricing=pricing,
            comps=comps,
            warnings=warnings,
            marketplace=marketplace,
            currency=currency,
        )

    def _decide_action(self, item, pricing, comps, photo_mode) -> tuple[str, str]:
        min_st = float(self.config.get("filter.min_sell_through", 0.0))
        if comps.sell_through_score < min_st:
            return "skip", f"sell_through {comps.sell_through_score:.2f} < {min_st:.2f}"
        if not pricing.ok:
            return "skip", f"pricing not ok: {pricing.reason}"
        # Break-even check: required price must not exceed the market (§2.7).
        if comps.est_sell_price_usd is not None and pricing.list_price_usd > comps.est_sell_price_usd:
            return "skip", (
                f"list {pricing.list_price_usd} USD > market "
                f"{comps.est_sell_price_usd} USD (loss)"
            )
        if photo_mode == "catalog":
            return "auto_publish", ""
        return "draft_pending_photo", ""

    # -- batch ------------------------------------------------------------- #
    def run(
        self,
        source_name: str,
        dest_country: str,
        keyword: Optional[str] = None,
        limit: int = 20,
        publish: bool = False,
    ) -> list[ListingPlan]:
        src = get_source(source_name, self.user_id, self.store)
        items = src.search(keyword=keyword, limit=limit)
        plans = [self.plan_item(it, dest_country) for it in items]

        # Record non-skip plans in history to build up training data.
        if self.history:
            ts = now_iso()
            for plan in plans:
                if plan.action == "skip":
                    continue
                p = plan.pricing
                self.history.record_listing(ListingRecord(
                    sku=plan.item.sku,
                    source=plan.item.source,
                    brand=plan.item.brand,
                    category=plan.item.category,
                    title=plan.title or plan.item.title,
                    list_price_usd=p.list_price_usd if p else 0.0,
                    cost_jpy=plan.item.price_jpy,
                    usd_jpy=self.usd_jpy,
                    photo_mode=plan.photo_mode,
                    action=plan.action,
                    listed_at=ts,
                ))

        if publish:
            for plan in plans:
                if plan.action == "auto_publish":
                    self.ebay.publish(plan)
        return plans
