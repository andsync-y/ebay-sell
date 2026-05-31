"""Command-line interface (§2.11).

Subcommands:
  setup                         interactive onboarding
  run    --user --source --dest run the listing pipeline
  sources                       list available source adapters
  sync   --user                 inventory/price sync (sample listings)
  orders --user                 fetch eBay orders & notify for approval

I/O lives here; business logic lives in the modules. A web layer could call
the same modules without going through this CLI.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .auth.credentials import get_default_store
from .auth.onboarding import setup as onboarding_setup
from .config import Config
from .models import ListingPlan
from .pipeline import Pipeline
from .sources import available_sources
from .sync import ActiveListing, InventorySync
from .orders import OrderManager
from .users import UserManager


def _plan_summary(plan: ListingPlan) -> dict:
    p = plan.pricing
    return {
        "sku": plan.item.sku,
        "title": plan.title or plan.item.title,
        "action": plan.action,
        "photo_mode": plan.photo_mode,
        "list_price_usd": p.list_price_usd if p else None,
        "profit_jpy": p.profit_jpy if p else None,
        "sell_through": plan.comps.sell_through_score if plan.comps else None,
        "warnings": plan.warnings,
    }


def cmd_setup(args: argparse.Namespace) -> int:
    onboarding_setup(open_browser=not args.no_browser)
    return 0


def cmd_sources(args: argparse.Namespace) -> int:
    print("Available sources:")
    for s in available_sources():
        print(f"  - {s}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    users = UserManager()
    if not users.get_user(args.user):
        # Auto-create a lightweight user so demos work without setup.
        users.create_user(args.user, args.user)
        print(f"(created user {args.user!r}; run `setup` to link accounts)", file=sys.stderr)

    config = Config.load(args.settings) if args.settings else Config.load()
    pipeline = Pipeline(
        user_id=args.user,
        config=config,
        store=get_default_store(),
        allow_live_fx=not args.offline,
    )
    plans = pipeline.run(
        source_name=args.source,
        dest_country=args.dest,
        keyword=args.keyword,
        limit=args.limit,
        publish=args.publish,
    )

    summaries = [_plan_summary(p) for p in plans]
    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    else:
        _print_plans(plans, pipeline)
    return 0


def _print_plans(plans: list[ListingPlan], pipeline: Pipeline) -> None:
    counts = {"auto_publish": 0, "draft_pending_photo": 0, "skip": 0}
    print(f"\nFX USD/JPY: {pipeline.usd_jpy:.2f} (source={pipeline.fx_source})\n")
    for p in plans:
        counts[p.action] = counts.get(p.action, 0) + 1
        price = f"${p.pricing.list_price_usd}" if p.pricing else "-"
        st = f"{p.comps.sell_through_score:.2f}" if p.comps else "-"
        icon = {"auto_publish": "✅", "draft_pending_photo": "📷", "skip": "⛔"}.get(p.action, "?")
        print(f"{icon} [{p.action}] {p.item.sku}")
        print(f"    {p.title or p.item.title}")
        print(f"    price={price} {p.currency}  photo={p.photo_mode}  sell_through={st}")
        for w in p.warnings:
            print(f"      ! {w}")
    print("\nSummary:")
    for k, v in counts.items():
        print(f"  {k}: {v}")


def cmd_sync(args: argparse.Namespace) -> int:
    # Demo: a couple of active listings tied to sample sources.
    listings = [
        ActiveListing(sku="rakuten:rk-1001", listed_price_usd=119.0, dest_country=args.dest),
        ActiveListing(sku="rakuten:rk-9999", listed_price_usd=50.0, dest_country=args.dest),
    ]
    sync = InventorySync(args.user, store=get_default_store(), allow_live_fx=not args.offline)
    decisions = sync.run(listings, apply=args.apply)
    for d in decisions:
        print(f"[{d.action}] {d.sku}: {d.reason}"
              + (f" -> ${d.new_price_usd}" if d.new_price_usd else ""))
    return 0


def cmd_orders(args: argparse.Namespace) -> int:
    mgr = OrderManager(args.user, store=get_default_store())
    orders = mgr.run()
    print(f"\n{len(orders)} order(s) processed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="yafu2ebay", description="Research & prepare eBay listings.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="interactive onboarding").add_argument(
        "--no-browser", action="store_true", help="don't auto-open the consent URL")

    sub.add_parser("sources", help="list available source adapters")

    pr = sub.add_parser("run", help="run the listing pipeline")
    pr.add_argument("--user", required=True)
    pr.add_argument("--source", default="rakuten")
    pr.add_argument("--dest", default="US", help="destination country (ISO-2)")
    pr.add_argument("--keyword", default=None)
    pr.add_argument("--limit", type=int, default=20)
    pr.add_argument("--publish", action="store_true", help="publish auto_publish plans")
    pr.add_argument("--json", action="store_true")
    pr.add_argument("--offline", action="store_true", help="skip live FX fetch")
    pr.add_argument("--settings", default=None)

    sy = sub.add_parser("sync", help="inventory/price sync (demo listings)")
    sy.add_argument("--user", required=True)
    sy.add_argument("--dest", default="US")
    sy.add_argument("--apply", action="store_true")
    sy.add_argument("--offline", action="store_true")

    oc = sub.add_parser("orders", help="fetch eBay orders & notify for approval")
    oc.add_argument("--user", required=True)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "setup": cmd_setup,
        "sources": cmd_sources,
        "run": cmd_run,
        "sync": cmd_sync,
        "orders": cmd_orders,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
