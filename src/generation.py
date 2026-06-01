"""Original listing-text generation (§2.8, §2.13-D).

HARD RULE: we generate original English copy from product *attributes only*.
We never copy the source listing's description text. The Anthropic path is
prompted with structured attributes (never the source's prose); the template
path is fully deterministic. Both append standardised policy boilerplate.

Falls back to template generation when no Anthropic key / package is present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .config import Config
from .models import SourceItem

# Standard policy boilerplate appended to every description (§2.13-D).
# TODO (§2.14): align exact wording with the seller's actual return/shipping
# policies and per-marketplace requirements.
POLICY_TEMPLATE = (
    "\n\nShipping: Ships from Japan with tracking. Carefully packaged.\n"
    "Returns: 30-day returns accepted; buyer pays return shipping unless the "
    "item is not as described.\n"
    "Customs/Import: Import duties and taxes (if any) are the buyer's "
    "responsibility and are not included in the item price or shipping."
)


@dataclass
class ListingText:
    title: str
    description: str
    source: str   # "template" | "anthropic"


def _condition_phrase(item: SourceItem) -> str:
    return "Brand New" if item.condition == "new" else "Pre-Owned"


def make_seo_title(item: SourceItem, max_chars: int) -> str:
    """Deterministic SEO-ish title from attributes (brand + mpn + key words).

    Built from attributes only — never the source description.
    """
    parts: list[str] = []
    if item.brand:
        parts.append(item.brand)
    if item.mpn:
        parts.append(item.mpn)
    # Pull a few latin keywords from the (attribute) title; skip CJK so we
    # don't transplant source prose verbatim.
    ascii_words = [w for w in (item.title or "").replace("/", " ").split() if w.isascii()]
    for w in ascii_words:
        candidate = " ".join(parts + [w])
        if w not in parts and len(candidate) <= max_chars:
            parts.append(w)
    parts.append(_condition_phrase(item))
    title = " ".join(parts)
    if len(title) > max_chars:
        title = title[:max_chars].rstrip()
    return title or (item.title[:max_chars] if item.title else "Item")


class ListingGenerator:
    def __init__(self, config: Config, user_anthropic_key: Optional[str] = None):
        self.config = config
        self.use_api = bool(config.get("generation.use_api", False))
        self.model = config.get("generation.anthropic_model", "claude-sonnet-4-6")
        self.max_title = int(config.get("generation.max_title_chars", 80))
        self.api_key = user_anthropic_key or os.environ.get("ANTHROPIC_API_KEY")

    def generate(self, item: SourceItem) -> ListingText:
        title = make_seo_title(item, self.max_title)
        if self.use_api and self.api_key:
            try:
                return self._generate_api(item, title)
            except Exception:
                # Any failure -> deterministic template (never blocks pipeline).
                pass
        return self._generate_template(item, title)

    # -- template (default, offline) -------------------------------------- #
    def _generate_template(self, item: SourceItem, title: str) -> ListingText:
        cond = _condition_phrase(item)
        lines = [f"{title}", ""]
        intro_bits = []
        if item.brand:
            intro_bits.append(f"by {item.brand}")
        intro = " ".join(intro_bits)
        lines.append(
            f"This is a {cond.lower()} {item.category or 'item'} {intro}.".replace("  ", " ").strip()
        )
        lines.append("")
        lines.append("Specifications:")
        for label, val in [
            ("Brand", item.brand),
            ("Model (MPN)", item.mpn),
            ("UPC", item.upc),
            ("EAN", item.ean),
            ("Condition", cond),
        ]:
            if val:
                lines.append(f"  - {label}: {val}")
        desc = "\n".join(lines) + POLICY_TEMPLATE
        return ListingText(title=title, description=desc, source="template")

    # -- Anthropic API ---------------------------------------------------- #
    def _generate_api(self, item: SourceItem, title: str) -> ListingText:
        import anthropic  # imported lazily; optional dependency

        client = anthropic.Anthropic(api_key=self.api_key)
        # Pass ONLY structured attributes — never the source description prose.
        attrs = {
            "brand": item.brand,
            "category": item.category,
            "mpn": item.mpn,
            "upc": item.upc,
            "ean": item.ean,
            "condition": item.condition,
        }
        prompt = (
            "Write an original eBay listing description in English for a product "
            "with these attributes. Do NOT invent specs you aren't given. Be "
            "concise, factual, and buyer-friendly. Return only the description "
            "body (no title).\n\n"
            f"Attributes: {attrs}"
        )
        msg = client.messages.create(
            model=self.model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        body = "".join(
            block.text for block in msg.content if getattr(block, "type", "") == "text"
        ).strip()
        return ListingText(
            title=title, description=body + POLICY_TEMPLATE, source="anthropic"
        )
