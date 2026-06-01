"""Compliance & account protection (§2.13-B).

  * ComplianceChecker.check(item)      — prohibited/regulated/VeRO screening.
  * AccountHealthMonitor.evaluate(...) — pause listings when health degrades.

Rules are seeded from sample_data/compliance_rules.json. TODO (§2.14): expand
from the official eBay prohibited-items policy, export-control lists, and the
VeRO participant directory; refresh on a schedule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import SAMPLE_DATA_DIR
from .models import SourceItem

_RULES_PATH = Path(SAMPLE_DATA_DIR) / "compliance_rules.json"


@dataclass
class ComplianceResult:
    allowed: bool                       # False -> must skip entirely
    is_vero: bool = False               # True -> force manual photo or skip
    is_restricted_brand: bool = False
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ComplianceChecker:
    def __init__(self, rules: Optional[dict] = None):
        if rules is None:
            rules = json.loads(_RULES_PATH.read_text(encoding="utf-8")) if _RULES_PATH.exists() else {}
        self.prohibited_keywords = [k.lower() for k in rules.get("prohibited_keywords", [])]
        self.prohibited_categories = [c.lower() for c in rules.get("prohibited_categories", [])]
        self.vero_brands = {b.lower() for b in rules.get("vero_brands", [])}
        self.restricted_brands = {b.lower() for b in rules.get("restricted_brands", [])}

    def is_vero_brand(self, brand: Optional[str]) -> bool:
        return bool(brand) and brand.lower() in self.vero_brands

    def check(self, item: SourceItem) -> ComplianceResult:
        reasons: list[str] = []
        warnings: list[str] = []
        title = (item.title or "").lower()
        brand = (item.brand or "").lower()
        category = (item.category or "").lower()

        # Prohibited / regulated -> hard block.
        for kw in self.prohibited_keywords:
            if kw in title or (brand and kw == brand):
                reasons.append(f"prohibited/regulated keyword: {kw!r}")
        if category and category in self.prohibited_categories:
            reasons.append(f"prohibited category: {category!r}")

        allowed = not reasons

        # VeRO brand -> not a hard block, but force manual review/photo.
        is_vero = self.is_vero_brand(item.brand)
        if is_vero:
            warnings.append(
                f"VeRO-participant brand {item.brand!r}: route to manual photo / review"
            )

        is_restricted = bool(brand) and brand in self.restricted_brands
        if is_restricted:
            warnings.append(f"restricted brand {item.brand!r}: extra scrutiny required")

        return ComplianceResult(
            allowed=allowed,
            is_vero=is_vero,
            is_restricted_brand=is_restricted,
            reasons=reasons,
            warnings=warnings,
        )


@dataclass
class AccountHealth:
    defect_rate: float = 0.0
    late_shipment_rate: float = 0.0
    open_vero_cases: int = 0


@dataclass
class HealthVerdict:
    safe_to_list: bool
    breaches: list[str] = field(default_factory=list)


class AccountHealthMonitor:
    """Evaluates seller metrics against configured thresholds (§2.13-B)."""

    def __init__(self, config):
        self.max_defect = float(config.get("account_health.max_defect_rate", 0.02))
        self.max_late = float(config.get("account_health.max_late_shipment_rate", 0.05))
        self.max_vero = int(config.get("account_health.max_open_vero_cases", 0))

    def evaluate(self, health: AccountHealth) -> HealthVerdict:
        breaches: list[str] = []
        if health.defect_rate > self.max_defect:
            breaches.append(
                f"defect_rate {health.defect_rate:.3f} > {self.max_defect:.3f}"
            )
        if health.late_shipment_rate > self.max_late:
            breaches.append(
                f"late_shipment_rate {health.late_shipment_rate:.3f} > {self.max_late:.3f}"
            )
        if health.open_vero_cases > self.max_vero:
            breaches.append(
                f"open_vero_cases {health.open_vero_cases} > {self.max_vero}"
            )
        return HealthVerdict(safe_to_list=not breaches, breaches=breaches)
