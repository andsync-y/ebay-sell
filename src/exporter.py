"""Export records for consumption-tax refund evidence (§2.13-E).

Produces a CSV of shipment/sale records in a form usable as export evidence
for Japanese consumption-tax (輸出免税) refunds. The exact required fields and
treatment must be confirmed with a tax professional (TODO §2.14) — this is a
structured starting point, not tax advice.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExportRecord:
    sale_date: str            # YYYY-MM-DD
    order_id: str
    sku: str
    description: str
    dest_country: str
    sold_price_jpy: int
    purchase_price_jpy: int   # includes JP consumption tax paid on sourcing
    carrier: str = ""
    tracking_no: str = ""
    export_date: str = ""


FIELDS = [
    "sale_date", "export_date", "order_id", "sku", "description",
    "dest_country", "carrier", "tracking_no",
    "sold_price_jpy", "purchase_price_jpy",
]


def to_csv(records: list[ExportRecord]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS)
    writer.writeheader()
    for r in records:
        writer.writerow({
            "sale_date": r.sale_date,
            "export_date": r.export_date,
            "order_id": r.order_id,
            "sku": r.sku,
            "description": r.description,
            "dest_country": r.dest_country,
            "carrier": r.carrier,
            "tracking_no": r.tracking_no,
            "sold_price_jpy": r.sold_price_jpy,
            "purchase_price_jpy": r.purchase_price_jpy,
        })
    return buf.getvalue()


def write_csv(records: list[ExportRecord], path: str) -> str:
    data = to_csv(records)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(data)
    return path
