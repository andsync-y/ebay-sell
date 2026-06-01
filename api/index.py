"""Vercel entry point — Flask web API wrapping the yafu2ebay pipeline.

Fully stateless: uses MemoryStore (zero filesystem writes) so it works in
Vercel's read-only sandbox. Credentials are not needed — falls back to
sample data automatically.
"""

from __future__ import annotations

# ── Must be the very first executable lines ─────────────────────────────────
import os
import sys

# Force writable path for any lib that touches home (Vercel home is read-only)
os.environ["YAFU2EBAY_HOME"] = "/tmp/yafu2ebay"
os.environ.setdefault("HOME", "/tmp")         # extra safety for any lib using HOME
# ────────────────────────────────────────────────────────────────────────────

import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request

from src.auth.credentials import MemoryStore
from src.config import Config
from src.orders import OrderManager
from src.pipeline import Pipeline
from src.sources import available_sources
from src.sync import ActiveListing, InventorySync

app = Flask(__name__, static_folder="static", static_url_path="/static")

_store = MemoryStore()
_config = Config.load()


def _make_pipeline() -> Pipeline:
    return Pipeline(
        user_id="web-user",
        config=_config,
        store=_store,
        allow_live_fx=True,
    )


def _plan_to_dict(plan) -> dict:
    p = plan.pricing
    c = plan.comps
    return {
        "sku": plan.item.sku,
        "source": plan.item.source,
        "title": plan.title or plan.item.title,
        "action": plan.action,
        "photo_mode": plan.photo_mode,
        "list_price_usd": p.list_price_usd if p else None,
        "profit_jpy": p.profit_jpy if p else None,
        "cost_jpy": plan.item.price_jpy,
        "sell_through": round(c.sell_through_score, 2) if c else None,
        "est_sell_price_usd": c.est_sell_price_usd if c else None,
        "marketplace": plan.marketplace,
        "currency": plan.currency,
        "warnings": plan.warnings,
        "description": plan.description,
    }


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/sources")
def sources():
    return jsonify({"sources": available_sources()})


@app.route("/api/run", methods=["POST"])
def run():
    data = request.get_json(silent=True) or {}
    source  = data.get("source", "rakuten")
    dest    = data.get("dest", "US")
    keyword = data.get("keyword") or None
    limit   = int(data.get("limit", 20))
    try:
        pipeline = _make_pipeline()
        plans = pipeline.run(source_name=source, dest_country=dest,
                             keyword=keyword, limit=limit, publish=False)
        counts = {"auto_publish": 0, "draft_pending_photo": 0, "skip": 0}
        for p in plans:
            counts[p.action] = counts.get(p.action, 0) + 1
        return jsonify({
            "ok": True,
            "usd_jpy": pipeline.usd_jpy,
            "fx_source": pipeline.fx_source,
            "counts": counts,
            "plans": [_plan_to_dict(p) for p in plans],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc),
                        "trace": traceback.format_exc()}), 500


@app.route("/api/orders")
def orders():
    try:
        mgr = OrderManager(user_id="web-user", config=_config, store=_store)
        order_list = mgr.fetch_new_orders()
        return jsonify({
            "ok": True,
            "orders": [
                {
                    "order_id": o.order_id,
                    "title": o.title,
                    "sold_price_usd": o.sold_price_usd,
                    "buyer_country": o.buyer_country,
                    "source_url": o.source_url,
                    "source_price_jpy": o.source_price_jpy,
                    "max_price_jpy": mgr.max_purchase_price_jpy(o),
                    "status": o.status,
                }
                for o in order_list
            ],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc),
                        "trace": traceback.format_exc()}), 500


@app.route("/api/sync")
def sync():
    dest = request.args.get("dest", "US")
    listings = [
        ActiveListing(sku="rakuten:rk-1001", listed_price_usd=119.0, dest_country=dest),
        ActiveListing(sku="rakuten:rk-9999", listed_price_usd=50.0,  dest_country=dest),
        ActiveListing(sku="yahoo:yf-2002",   listed_price_usd=94.0,  dest_country=dest),
    ]
    try:
        inv = InventorySync(user_id="web-user", config=_config,
                            store=_store, allow_live_fx=True)
        decisions = inv.run(listings, apply=False)
        return jsonify({
            "ok": True,
            "decisions": [
                {"sku": d.sku, "action": d.action, "reason": d.reason,
                 "new_price_usd": d.new_price_usd}
                for d in decisions
            ],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc),
                        "trace": traceback.format_exc()}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
