"""eBay integration & the photo-routing decision tree (§2.6, §2.8).

This module is the swap point between offline stubs and the real eBay APIs.
It receives ``user_id`` + ``CredentialStore`` and uses that user's OAuth token.
Without a real token it prints a console stub instead of calling the Sell API.

HARD COMPLIANCE RULES (enforced in catalog_match / photo routing):
  * Auto photos come ONLY from the eBay catalog, and ONLY for new items that
    are identifiable by UPC/EAN and not under a strict brand exclusion.
  * We never fetch/transform/re-host manufacturer, search-result, or seller
    images. Everything else routes to manual-photo draft.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .auth.credentials import CredentialStore
from .auth.ebay_oauth import EbayOAuthConfig, get_valid_access_token
from .config import Config
from .models import ListingPlan, SourceItem


@dataclass
class CatalogResult:
    matched: bool
    photo_mode: str          # "catalog" | "manual"
    epid: Optional[str] = None   # eBay product id, when matched
    warnings: Optional[list[str]] = None


class EbayClient:
    def __init__(
        self,
        config: Config,
        user_id: str = "",
        store: Optional[CredentialStore] = None,
        oauth_cfg: Optional[EbayOAuthConfig] = None,
    ):
        self.config = config
        self.user_id = user_id
        self.store = store
        self.oauth_cfg = oauth_cfg or EbayOAuthConfig()

    # -- auth -------------------------------------------------------------- #
    def access_token(self) -> Optional[str]:
        if not self.store or not self.user_id:
            return None
        return get_valid_access_token(self.store, self.user_id, self.oauth_cfg)

    @property
    def is_live(self) -> bool:
        tok = self.access_token()
        return bool(tok) and not tok.startswith("STUB-")

    # -- photo routing (§2.6) --------------------------------------------- #
    def catalog_match(self, item: SourceItem, vero: bool = False) -> CatalogResult:
        """Decision tree determining photo_mode.

        brand in exclusions / VeRO     -> manual
        new AND (upc or ean)           -> catalog
        not new                        -> manual
        no upc/ean                     -> manual
        """
        warnings: list[str] = []
        exclusions = {b.lower() for b in (self.config.get("ebay.brand_image_exclusions", []) or [])}
        brand = (item.brand or "").lower()

        if brand and brand in exclusions:
            warnings.append(
                f"brand {item.brand!r} excluded from catalog photos (takedown history)"
            )
            return CatalogResult(False, "manual", warnings=warnings)
        if vero:
            warnings.append("VeRO brand: manual photo required")
            return CatalogResult(False, "manual", warnings=warnings)

        if item.condition == "new" and (item.upc or item.ean):
            # TODO: call Browse/Catalog API to resolve a real EPID and confirm
            # a catalog image exists. Offline we assume a match.
            epid = self._lookup_epid(item)
            return CatalogResult(True, "catalog", epid=epid)

        if item.condition != "new":
            warnings.append("used/non-new: real photos required")
        elif not (item.upc or item.ean):
            warnings.append("no UPC/EAN: cannot identify catalog entry")
        return CatalogResult(False, "manual", warnings=warnings)

    def _lookup_epid(self, item: SourceItem) -> Optional[str]:
        # TODO: real Browse API lookup by gtin. Offline: synthesise a token.
        gtin = item.upc or item.ean
        return f"EPID-{gtin}" if gtin else None

    # -- publish (§2.8) ---------------------------------------------------- #
    def publish(self, plan: ListingPlan) -> dict:
        """Create/publish an eBay listing.

        Live: Sell Inventory + Offer APIs (catalog image linked via EPID when
        photo_mode == "catalog"). Offline: console stub.
        Only ``auto_publish`` plans should reach here; drafts/skips are handled
        by the queue.
        """
        if not self.is_live:
            return self._publish_stub(plan)
        # TODO (§2.14): implement real Sell API calls:
        #   1) PUT  /sell/inventory/v1/inventory_item/{sku}
        #   2) POST /sell/inventory/v1/offer
        #   3) POST /sell/inventory/v1/offer/{offerId}/publish
        # Link catalog image via product.epid when plan.photo_mode == "catalog".
        raise NotImplementedError(
            "Live eBay publish not yet implemented (TODO §2.14). "
            "Token present but Sell API calls are stubbed."
        )

    def _publish_stub(self, plan: ListingPlan) -> dict:
        sku = plan.item.sku
        price = plan.pricing.list_price_usd if plan.pricing else None
        print("── [eBay publish STUB] ───────────────────────────────")
        print(f"  SKU         : {sku}")
        print(f"  Marketplace : {plan.marketplace} ({plan.currency})")
        print(f"  Title       : {plan.title}")
        print(f"  Price       : {price} {plan.currency}")
        print(f"  Photo mode  : {plan.photo_mode}")
        if plan.warnings:
            print(f"  Warnings    : {plan.warnings}")
        print("───────────────────────────────────────────────────────")
        return {
            "status": "stub_published",
            "sku": sku,
            "listing_id": f"STUB-LISTING-{sku}",
            "marketplace": plan.marketplace,
        }

    def end_listing(self, sku: str, reason: str = "") -> dict:
        """Take a listing down (used by sync.py on sold-out/price-up)."""
        if not self.is_live:
            print(f"── [eBay end-listing STUB] sku={sku} reason={reason}")
            return {"status": "stub_ended", "sku": sku, "reason": reason}
        # TODO: DELETE offer / withdraw via Sell API.
        raise NotImplementedError("Live end_listing not yet implemented (TODO §2.14).")
