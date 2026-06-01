"""Source adapters (§2.8, §2.9).

Add a new EC site by subclassing ``BaseSource`` and decorating with
``@register("name")``. Adapters receive ``user_id`` + ``CredentialStore`` so
they run with that user's credentials; if keys are missing they fall back to
the bundled sample data and the whole app still works offline.

COMPLIANCE: adapters return product *attributes only*. We never copy the
source's description text into a listing, and we never fetch/transform/re-host
the source's images (eBay image policy / VeRO). Listing copy is generated
fresh from attributes in generation.py; photos come only from the eBay
catalog (ebay.py).
"""

from __future__ import annotations

import abc
import json
from pathlib import Path
from typing import Callable, Optional

from .auth.credentials import CredentialStore
from .config import SAMPLE_DATA_DIR
from .models import Credential, SourceItem
from .monitoring import RateLimiter, ScraperMonitor

# Registry of source name -> class.
_REGISTRY: dict[str, type["BaseSource"]] = {}


def register(name: str) -> Callable[[type["BaseSource"]], type["BaseSource"]]:
    def deco(cls: type["BaseSource"]) -> type["BaseSource"]:
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return deco


def available_sources() -> list[str]:
    return sorted(_REGISTRY.keys())


def get_source(
    name: str,
    user_id: str = "",
    store: Optional[CredentialStore] = None,
    monitor: Optional[ScraperMonitor] = None,
) -> "BaseSource":
    if name not in _REGISTRY:
        raise KeyError(f"Unknown source {name!r}. Available: {available_sources()}")
    return _REGISTRY[name](user_id=user_id, store=store, monitor=monitor)


class BaseSource(abc.ABC):
    name: str = "base"

    def __init__(
        self,
        user_id: str = "",
        store: Optional[CredentialStore] = None,
        monitor: Optional[ScraperMonitor] = None,
    ):
        self.user_id = user_id
        self.store = store
        self.monitor = monitor or ScraperMonitor(self.name)

    def _credential(self, service: str) -> Optional[Credential]:
        if not self.store or not self.user_id:
            return None
        return self.store.get(self.user_id, service)

    @abc.abstractmethod
    def search(self, keyword: Optional[str] = None, limit: int = 20) -> list[SourceItem]:
        """Return purchase candidates as SourceItem (attributes only)."""

    def get_item(self, source_id: str) -> Optional[SourceItem]:
        """Fetch a single item (used by sync.py). Default: scan search()."""
        for it in self.search(limit=100):
            if it.source_id == source_id:
                return it
        return None

    # -- helpers ----------------------------------------------------------- #
    def _load_sample(self, filename: str) -> list[SourceItem]:
        path = Path(SAMPLE_DATA_DIR) / filename
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [SourceItem.from_dict(d) for d in data]


@register("rakuten")
class RakutenSource(BaseSource):
    """Rakuten Web Service Ichiba Item Search API.

    Uses an Application ID (app-wide; affiliate id optional per user). Falls
    back to sample data when no app id credential is present.
    TODO (§2.14): wire the real endpoint + response mapping.
    """

    name = "rakuten"
    API_URL = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"

    def search(self, keyword: Optional[str] = None, limit: int = 20) -> list[SourceItem]:
        cred = self._credential("rakuten")
        app_id = cred.data.get("app_id") if cred else None

        if not app_id:
            # Offline fallback.
            items = self._load_sample("rakuten_items.json")
            return self._filter_kw(items, keyword)[:limit]

        # --- Live path (best-effort; respects rate limits) --------------- #
        limiter = RateLimiter(calls_per_sec=1.0)  # Rakuten free tier is modest
        limiter.wait()
        try:
            import requests

            params = {
                "applicationId": app_id,
                "format": "json",
                "keyword": keyword or "",
                "hits": min(limit, 30),
            }
            if cred and cred.data.get("affiliate_id"):
                params["affiliateId"] = cred.data["affiliate_id"]
            resp = requests.get(self.API_URL, params=params, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
            items = [self._map_item(e["Item"]) for e in payload.get("Items", [])]
            self.monitor.record(len(items))
            return items[:limit]
        except Exception as exc:  # network/parse error -> alert + fallback
            self.monitor.record_error(str(exc))
            items = self._load_sample("rakuten_items.json")
            return self._filter_kw(items, keyword)[:limit]

    def _map_item(self, e: dict) -> SourceItem:
        # TODO: refine condition/brand/upc extraction from real fields.
        return SourceItem(
            source=self.name,
            source_id=str(e.get("itemCode", e.get("itemUrl", ""))),
            url=e.get("itemUrl", ""),
            title=e.get("itemName", ""),
            price_jpy=int(e.get("itemPrice", 0)),
            condition="new",
            brand=None,
            category=None,
            upc=None,
            raw=e,
        )

    @staticmethod
    def _filter_kw(items: list[SourceItem], keyword: Optional[str]) -> list[SourceItem]:
        if not keyword:
            return items
        kw = keyword.lower()
        return [i for i in items if kw in i.title.lower()]


@register("yahoo")
class YahooAuctionsSource(BaseSource):
    """Yahoo! Auctions (Japan).

    The official API requires Yahoo Japan Developer credentials. Falls back
    to sample data when no client_id credential is configured.
    TODO (§2.14): implement live RSS/API integration.
    """

    name = "yahoo"

    def search(self, keyword: Optional[str] = None, limit: int = 20) -> list[SourceItem]:
        items = self._load_sample("yahoo_items.json")
        if keyword:
            kw = keyword.lower()
            items = [i for i in items if kw in i.title.lower()]
        return items[:limit]


@register("yahoo_shopping")
class YahooShoppingSource(BaseSource):
    """Yahoo! Shopping Japan.

    Uses the Yahoo Shopping Item Search API when a client_id credential is
    present. Falls back to sample data otherwise.
    TODO (§2.14): wire the live endpoint + response mapping.
    """

    name = "yahoo_shopping"
    API_URL = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"

    def search(self, keyword: Optional[str] = None, limit: int = 20) -> list[SourceItem]:
        cred = self._credential("yahoo_shopping")
        client_id = cred.data.get("client_id") if cred else None

        if not client_id:
            items = self._load_sample("yahoo_shopping_items.json")
            if keyword:
                kw = keyword.lower()
                items = [i for i in items if kw in i.title.lower()]
            return items[:limit]

        limiter = RateLimiter(calls_per_sec=1.0)
        limiter.wait()
        try:
            import requests

            params = {
                "appid": client_id,
                "query": keyword or "",
                "results": min(limit, 50),
                "sort": "-sold",
            }
            resp = requests.get(self.API_URL, params=params, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
            items = [self._map_item(e) for e in payload.get("hits", [])]
            self.monitor.record(len(items))
            return items[:limit]
        except Exception as exc:
            self.monitor.record_error(str(exc))
            items = self._load_sample("yahoo_shopping_items.json")
            if keyword:
                kw = keyword.lower()
                items = [i for i in items if kw in i.title.lower()]
            return items[:limit]

    def _map_item(self, e: dict) -> SourceItem:
        brand_info = e.get("brand")
        brand = brand_info.get("name") if isinstance(brand_info, dict) else None
        return SourceItem(
            source=self.name,
            source_id=str(e.get("code", "")),
            url=e.get("url", ""),
            title=e.get("name", ""),
            price_jpy=int(e.get("price", 0)),
            condition="new",
            brand=brand,
            category=None,
            upc=e.get("janCode"),
            raw=e,
        )
