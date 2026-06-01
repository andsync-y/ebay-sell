"""Listing and sales history store.

Accumulates every listing plan and sale to progressively improve:
  - sell_through_score  : observed sell-through replaces generic estimates once
                          ≥3 brand samples (or ≥5 category samples) exist.
  - est_sell_price_usd  : median historical sold price replaces sample data.
  - early-skip signal   : brands with sell_through < 0.1 and ≥5 samples are
                          flagged so the pipeline can skip them cheaply.

Storage
-------
Local / CLI  → ~/.yafu2ebay/history.db   (SQLite)
Vercel /tmp  → /tmp/yafu2ebay/history.db (SQLite, ephemeral per cold start but
               persists across warm requests within the same Lambda instance)
Fallback     → in-memory SQLite (no persistence, but pipeline still runs)
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .auth.credentials import default_home

# Minimum samples required before historical data overrides generic estimates.
_MIN_BRAND_SAMPLES    = 3
_MIN_CATEGORY_SAMPLES = 5


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ListingRecord:
    sku: str
    source: str
    brand: Optional[str]
    category: Optional[str]
    title: str
    list_price_usd: float
    cost_jpy: int
    usd_jpy: float
    photo_mode: str
    action: str           # auto_publish | draft_pending_photo | skip
    listed_at: str        # ISO-8601 UTC


@dataclass
class SaleRecord:
    sku: str
    sold_price_usd: float
    usd_jpy: float
    sold_at: str          # ISO-8601 UTC


@dataclass
class BrandStats:
    brand: str
    total_listed: int
    total_sold: int
    sell_through_rate: float
    avg_profit_jpy: float
    avg_days_to_sell: Optional[float]
    is_reliable: bool     # True when sample size meets threshold


@dataclass
class CategoryStats:
    category: str
    total_listed: int
    total_sold: int
    sell_through_rate: float
    avg_profit_jpy: float
    is_reliable: bool


# ── Store ─────────────────────────────────────────────────────────────────────

class HistoryStore:
    """Thread-safe SQLite-backed listing/sales history."""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            home = default_home()
            try:
                home.mkdir(parents=True, exist_ok=True)
                db_path = home / "history.db"
            except OSError:
                db_path = None  # fall back to in-memory

        self._path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(db_path) if db_path else ":memory:",
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS listings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                sku           TEXT    NOT NULL,
                source        TEXT,
                brand         TEXT,
                category      TEXT,
                title         TEXT,
                list_price_usd REAL,
                cost_jpy      INTEGER,
                usd_jpy       REAL,
                photo_mode    TEXT,
                action        TEXT,
                listed_at     TEXT,
                status        TEXT    DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS sales (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                sku            TEXT    NOT NULL,
                sold_price_usd REAL,
                usd_jpy        REAL,
                sold_at        TEXT,
                days_to_sell   INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_l_brand    ON listings(brand);
            CREATE INDEX IF NOT EXISTS idx_l_category ON listings(category);
            CREATE INDEX IF NOT EXISTS idx_l_sku      ON listings(sku);
            CREATE INDEX IF NOT EXISTS idx_s_sku      ON sales(sku);
        """)
        self._conn.commit()

    # ── Write ────────────────────────────────────────────────────────────────

    def record_listing(self, rec: ListingRecord) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO listings
                   (sku, source, brand, category, title, list_price_usd,
                    cost_jpy, usd_jpy, photo_mode, action, listed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (rec.sku, rec.source, rec.brand, rec.category, rec.title,
                 rec.list_price_usd, rec.cost_jpy, rec.usd_jpy,
                 rec.photo_mode, rec.action, rec.listed_at),
            )
            self._conn.commit()

    def record_sale(self, rec: SaleRecord) -> None:
        with self._lock:
            # Calculate days_to_sell from listing date if available.
            row = self._conn.execute(
                "SELECT listed_at FROM listings WHERE sku=? AND status='active' "
                "ORDER BY id DESC LIMIT 1", (rec.sku,)
            ).fetchone()
            days = None
            if row:
                try:
                    listed = datetime.fromisoformat(row["listed_at"].replace("Z", "+00:00"))
                    sold   = datetime.fromisoformat(rec.sold_at.replace("Z", "+00:00"))
                    days   = max(0, (sold - listed).days)
                except Exception:
                    pass
            self._conn.execute(
                """INSERT INTO sales (sku, sold_price_usd, usd_jpy, sold_at, days_to_sell)
                   VALUES (?,?,?,?,?)""",
                (rec.sku, rec.sold_price_usd, rec.usd_jpy, rec.sold_at, days),
            )
            self._conn.execute(
                "UPDATE listings SET status='sold' WHERE sku=? AND status='active'",
                (rec.sku,),
            )
            self._conn.commit()

    # ── Read — per-item lookup (used by CompsProvider) ────────────────────────

    def historical_sell_through(
        self, brand: Optional[str], category: Optional[str]
    ) -> Optional[float]:
        """Returns a historically-grounded sell-through rate, or None if insufficient data."""
        if brand:
            row = self._conn.execute(
                """SELECT COUNT(*) total, SUM(CASE WHEN status='sold' THEN 1 ELSE 0 END) sold
                   FROM listings WHERE brand=? AND action != 'skip'""",
                (brand,),
            ).fetchone()
            if row and row["total"] >= _MIN_BRAND_SAMPLES:
                return (row["sold"] or 0) / row["total"]
        if category:
            row = self._conn.execute(
                """SELECT COUNT(*) total, SUM(CASE WHEN status='sold' THEN 1 ELSE 0 END) sold
                   FROM listings WHERE category=? AND action != 'skip'""",
                (category,),
            ).fetchone()
            if row and row["total"] >= _MIN_CATEGORY_SAMPLES:
                return (row["sold"] or 0) / row["total"]
        return None

    def historical_sold_price(self, brand: Optional[str]) -> Optional[float]:
        """Median historical sold price for brand, or None."""
        if not brand:
            return None
        rows = self._conn.execute(
            """SELECT s.sold_price_usd FROM sales s
               JOIN listings l ON l.sku = s.sku
               WHERE l.brand=? ORDER BY s.sold_price_usd""",
            (brand,),
        ).fetchall()
        prices = [r["sold_price_usd"] for r in rows if r["sold_price_usd"]]
        if len(prices) < 2:
            return None
        mid = len(prices) // 2
        return prices[mid] if len(prices) % 2 else (prices[mid - 1] + prices[mid]) / 2

    def is_low_performer(self, brand: Optional[str]) -> bool:
        """True when brand has ≥5 listings and sell-through < 0.10."""
        if not brand:
            return False
        row = self._conn.execute(
            """SELECT COUNT(*) total, SUM(CASE WHEN status='sold' THEN 1 ELSE 0 END) sold
               FROM listings WHERE brand=? AND action != 'skip'""",
            (brand,),
        ).fetchone()
        if row and row["total"] >= 5:
            st = (row["sold"] or 0) / row["total"]
            return st < 0.10
        return False

    # ── Read — aggregate stats ────────────────────────────────────────────────

    def summary(self) -> dict:
        row = self._conn.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN status='sold'   THEN 1 ELSE 0 END) sold,
                      SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active
               FROM listings"""
        ).fetchone()
        profit_row = self._conn.execute(
            """SELECT SUM(s.sold_price_usd * s.usd_jpy - l.cost_jpy) profit
               FROM sales s JOIN listings l ON l.sku = s.sku"""
        ).fetchone()
        total = row["total"] or 0
        sold  = row["sold"]  or 0
        return {
            "total_listings":    total,
            "total_sold":        sold,
            "active_listings":   row["active"] or 0,
            "sell_through_rate": round(sold / total, 3) if total else 0.0,
            "total_profit_jpy":  int(profit_row["profit"] or 0),
        }

    def all_brand_stats(self) -> list[BrandStats]:
        rows = self._conn.execute(
            """SELECT l.brand,
                      COUNT(*) total,
                      SUM(CASE WHEN l.status='sold' THEN 1 ELSE 0 END) sold,
                      AVG(CASE WHEN s.sold_price_usd IS NOT NULL
                               THEN s.sold_price_usd * s.usd_jpy - l.cost_jpy
                               ELSE NULL END) avg_profit,
                      AVG(s.days_to_sell) avg_days
               FROM listings l LEFT JOIN sales s ON l.sku = s.sku
               WHERE l.brand IS NOT NULL AND l.action != 'skip'
               GROUP BY l.brand ORDER BY sold DESC, total DESC"""
        ).fetchall()
        return [
            BrandStats(
                brand=r["brand"],
                total_listed=r["total"],
                total_sold=r["sold"] or 0,
                sell_through_rate=round((r["sold"] or 0) / r["total"], 3),
                avg_profit_jpy=round(r["avg_profit"] or 0),
                avg_days_to_sell=round(r["avg_days"], 1) if r["avg_days"] else None,
                is_reliable=r["total"] >= _MIN_BRAND_SAMPLES,
            )
            for r in rows
        ]

    def all_category_stats(self) -> list[CategoryStats]:
        rows = self._conn.execute(
            """SELECT l.category,
                      COUNT(*) total,
                      SUM(CASE WHEN l.status='sold' THEN 1 ELSE 0 END) sold,
                      AVG(CASE WHEN s.sold_price_usd IS NOT NULL
                               THEN s.sold_price_usd * s.usd_jpy - l.cost_jpy
                               ELSE NULL END) avg_profit
               FROM listings l LEFT JOIN sales s ON l.sku = s.sku
               WHERE l.category IS NOT NULL AND l.action != 'skip'
               GROUP BY l.category ORDER BY sold DESC, total DESC"""
        ).fetchall()
        return [
            CategoryStats(
                category=r["category"],
                total_listed=r["total"],
                total_sold=r["sold"] or 0,
                sell_through_rate=round((r["sold"] or 0) / r["total"], 3),
                avg_profit_jpy=round(r["avg_profit"] or 0),
                is_reliable=r["total"] >= _MIN_CATEGORY_SAMPLES,
            )
            for r in rows
        ]

    def recent_sales(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """SELECT s.sku, l.brand, l.category, l.title,
                      l.list_price_usd, s.sold_price_usd, l.cost_jpy,
                      s.usd_jpy, s.sold_at, s.days_to_sell
               FROM sales s JOIN listings l ON l.sku = s.sku
               ORDER BY s.sold_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
