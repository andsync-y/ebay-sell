"""Configuration loading (§2.10).

Loads settings.yaml and exposes a small ``Config`` wrapper with dotted-path
access plus per-user override merging (UserProfile).
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Optional

import yaml

from .models import UserProfile

# Repo root = parent of this file's package dir.
_PKG_DIR = Path(__file__).resolve().parent
ROOT_DIR = _PKG_DIR.parent
DEFAULT_SETTINGS_PATH = ROOT_DIR / "config" / "settings.yaml"
SAMPLE_DATA_DIR = ROOT_DIR / "sample_data"


class Config:
    def __init__(self, data: dict[str, Any], path: Optional[Path] = None):
        self._data = data
        self.path = path

    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "Config":
        p = Path(path) if path else DEFAULT_SETTINGS_PATH
        # Allow override via env for tests / deployments.
        env_path = os.environ.get("YAFU2EBAY_SETTINGS")
        if path is None and env_path:
            p = Path(env_path)
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data, p)

    def get(self, dotted: str, default: Any = None) -> Any:
        """Fetch by dotted path, e.g. ``config.get("pricing.target_margin")``."""
        node: Any = self._data
        for key in dotted.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def for_user(self, profile: Optional[UserProfile]) -> "Config":
        """Return a new Config with the user's overrides merged in.

        Keeps the pipeline pure: callers pass a per-user view rather than
        mutating global config.
        """
        if profile is None:
            return self
        merged = copy.deepcopy(self._data)

        if profile.target_margin is not None:
            merged.setdefault("pricing", {})["target_margin"] = profile.target_margin
        if profile.payment_fx_rate is not None:
            merged.setdefault("pricing", {})["payment_fx_rate"] = profile.payment_fx_rate
        if profile.shipping_overrides:
            # Shallow-merge shipping overrides (e.g. user's own carrier rates).
            merged.setdefault("shipping", {}).update(profile.shipping_overrides)
        if profile.brand_exclusions_extra:
            existing = merged.setdefault("ebay", {}).get("brand_image_exclusions", [])
            merged["ebay"]["brand_image_exclusions"] = list(
                {*existing, *(b.lower() for b in profile.brand_exclusions_extra)}
            )
        if profile.default_marketplace:
            merged.setdefault("ebay", {})["default_marketplace"] = profile.default_marketplace

        return Config(merged, self.path)
