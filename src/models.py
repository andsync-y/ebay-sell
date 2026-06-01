"""Data models (§2.3).

Plain dataclasses with light (de)serialisation helpers. No I/O, no business
logic — these are the contracts passed between pipeline stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Sourcing
# --------------------------------------------------------------------------- #
@dataclass
class SourceItem:
    """A purchase candidate fetched from an EC source.

    price_jpy is the acquisition cost (仕入C). dims_cm is (L, W, H) in cm.
    """

    source: str
    source_id: str
    url: str
    title: str
    price_jpy: int
    condition: str = "new"            # "new" | "used"
    brand: Optional[str] = None
    category: Optional[str] = None
    upc: Optional[str] = None
    ean: Optional[str] = None
    mpn: Optional[str] = None
    weight_g: Optional[int] = None
    dims_cm: Optional[tuple[float, float, float]] = None
    market_price_usd: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceItem":
        dims = d.get("dims_cm")
        if dims is not None:
            dims = tuple(dims)  # JSON gives a list
        return cls(
            source=d["source"],
            source_id=d["source_id"],
            url=d["url"],
            title=d["title"],
            price_jpy=int(d["price_jpy"]),
            condition=d.get("condition", "new"),
            brand=d.get("brand"),
            category=d.get("category"),
            upc=d.get("upc"),
            ean=d.get("ean"),
            mpn=d.get("mpn"),
            weight_g=d.get("weight_g"),
            dims_cm=dims,
            market_price_usd=d.get("market_price_usd"),
            raw=d.get("raw", {}),
        )

    @property
    def sku(self) -> str:
        """Stable SKU used to tie an eBay listing back to its source."""
        return f"{self.source}:{self.source_id}"


# --------------------------------------------------------------------------- #
# Pricing & shipping
# --------------------------------------------------------------------------- #
@dataclass
class ShippingQuote:
    carrier: str
    zone: str
    charged_weight_g: int
    cost_jpy: int
    note: str = ""


@dataclass
class PricingResult:
    cost_jpy: int
    shipping: Optional[ShippingQuote]
    ebay_fee_rate: float
    payment_fx_rate: float
    target_margin: float
    profit_jpy: int
    list_price_jpy: int
    list_price_usd: float
    ok: bool
    reason: str = ""


@dataclass
class Comps:
    """Sold-based market signal (§2.13-A)."""

    est_sell_price_usd: Optional[float] = None
    sell_through_score: float = 0.0          # 0..1
    median_days_to_sell: Optional[int] = None


# --------------------------------------------------------------------------- #
# Listing plan (pipeline output)
# --------------------------------------------------------------------------- #
@dataclass
class ListingPlan:
    item: SourceItem
    catalog_match: bool
    photo_mode: str                  # "catalog" | "manual"
    action: str                      # "auto_publish" | "draft_pending_photo" | "skip"
    title: str
    description: str
    pricing: Optional[PricingResult]
    comps: Optional[Comps]
    warnings: list[str] = field(default_factory=list)
    marketplace: str = "EBAY_US"
    currency: str = "USD"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # tuples -> lists already handled by asdict; keep as-is for JSON.
        return d


# --------------------------------------------------------------------------- #
# Users & credentials (§2.11)
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class User:
    user_id: str
    display_name: str = ""
    created_at: str = field(default_factory=_now_iso)


@dataclass
class Credential:
    """A stored credential. ``data`` is the *decrypted* payload in memory;
    the CredentialStore is responsible for encrypting it at rest (§2.12).
    """

    user_id: str
    service: str                     # "ebay" | "rakuten" | "yahoo" | "anthropic"
    kind: str                        # "oauth_token" | "app_id" | "session"
    data: dict[str, Any]
    expires_at: Optional[str] = None  # ISO8601; for oauth access tokens

    def is_expired(self, skew_seconds: int = 120) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc).timestamp() >= (exp.timestamp() - skew_seconds)


@dataclass
class UserProfile:
    """Per-user overrides layered on top of settings.yaml."""

    user_id: str
    target_margin: Optional[float] = None
    payment_fx_rate: Optional[float] = None
    shipping_overrides: dict[str, Any] = field(default_factory=dict)
    brand_exclusions_extra: list[str] = field(default_factory=list)
    default_dest: Optional[str] = None
    default_marketplace: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UserProfile":
        return cls(
            user_id=d["user_id"],
            target_margin=d.get("target_margin"),
            payment_fx_rate=d.get("payment_fx_rate"),
            shipping_overrides=d.get("shipping_overrides", {}),
            brand_exclusions_extra=d.get("brand_exclusions_extra", []),
            default_dest=d.get("default_dest"),
            default_marketplace=d.get("default_marketplace"),
        )
