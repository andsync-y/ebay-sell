"""eBay orders -> one-tap sourcing approval (§2.13-A).

When an item sells on eBay we fetch the order, find the matching source URL +
the maximum price we can pay (the cost baked into our listing price), and
notify the operator. The human approves; only then do we proceed to purchase.
Spending cash always stays behind a human gate.

  * Yahoo Auctions: manual / semi-automatic (open URL, bid/buy by hand).
  * Rakuten: cart-assisted (the buy step is still operator-approved).

Offline it reads sample_data/ebay_orders.json.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .auth.credentials import CredentialStore
from .auth.ebay_oauth import get_valid_access_token
from .config import SAMPLE_DATA_DIR, Config
from .notify import BaseNotifier, Notification, get_notifier


@dataclass
class Order:
    order_id: str
    sku: str
    title: str
    sold_price_usd: float
    buyer_country: str
    source_url: str
    source_price_jpy: int
    status: str = "awaiting_purchase"

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        return cls(
            order_id=d["order_id"],
            sku=d.get("sku", ""),
            title=d.get("title", ""),
            sold_price_usd=float(d.get("sold_price_usd", 0)),
            buyer_country=d.get("buyer_country", ""),
            source_url=d.get("source_url", ""),
            source_price_jpy=int(d.get("source_price_jpy", 0)),
            status=d.get("status", "awaiting_purchase"),
        )


class OrderManager:
    def __init__(
        self,
        user_id: str,
        config: Optional[Config] = None,
        store: Optional[CredentialStore] = None,
        notifier: Optional[BaseNotifier] = None,
    ):
        self.user_id = user_id
        self.config = config or Config.load()
        self.store = store
        self.notifier = notifier or get_notifier()

    def _is_live(self) -> bool:
        if not self.store:
            return False
        tok = get_valid_access_token(self.store, self.user_id)
        return bool(tok) and not tok.startswith("STUB-")

    def fetch_new_orders(self) -> list[Order]:
        if self._is_live():
            # TODO (§2.14): call Sell Fulfillment getOrders for new paid orders.
            raise NotImplementedError("Live order fetch not yet implemented (TODO §2.14).")
        path = Path(SAMPLE_DATA_DIR) / "ebay_orders.json"
        if not path.exists():
            return []
        return [Order.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]

    def max_purchase_price_jpy(self, order: Order) -> int:
        """Upper bound we may pay at the source = the cost baked into our price.

        Here we surface the known source price; if the source has moved, the
        operator sees both numbers and decides. A small buffer is allowed.
        """
        buffer = float(self.config.get("orders.purchase_buffer_pct", 0.0) or 0.0)
        return int(math.ceil(order.source_price_jpy * (1.0 + buffer)))

    def notify_for_approval(self, order: Order) -> Notification:
        cap = self.max_purchase_price_jpy(order)
        n = Notification(
            title=f"🟢 SOLD: {order.title}",
            body=(
                f"Order {order.order_id} sold for ${order.sold_price_usd} "
                f"to {order.buyer_country}.\n"
                f"Source price: ¥{order.source_price_jpy} (max pay: ¥{cap}).\n"
                f"Approve to purchase from the source."
            ),
            actions=[{"label": "Open source to purchase", "url": order.source_url}],
            level="info",
        )
        self.notifier.send(n)
        return n

    def approve_purchase(self, order: Order) -> dict:
        """Operator-approved purchase step.

        We never auto-spend: this records the approval and hands off to the
        manual/cart flow. TODO (§2.14): Rakuten cart automation; Yahoo manual.
        """
        return {
            "status": "approved_for_manual_purchase",
            "order_id": order.order_id,
            "source_url": order.source_url,
            "max_price_jpy": self.max_purchase_price_jpy(order),
        }

    def run(self) -> list[Order]:
        orders = self.fetch_new_orders()
        for o in orders:
            if o.status == "awaiting_purchase":
                self.notify_for_approval(o)
        return orders
