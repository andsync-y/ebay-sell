"""Foreign exchange (§2.13-C).

Provides USD/JPY (and other pairs) with a safety buffer. Tries a live source
when network + config allow, else uses the configured fallback from
settings.yaml. The buffer guards against yen-strengthening between listing and
payout.

TODO (§2.14): choose a production FX data source and add an API key path.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .config import Config

# Free, key-less endpoint used opportunistically. If unreachable we fall back.
_LIVE_URL = "https://api.exchangerate.host/latest"


@dataclass
class FxRate:
    pair: str            # e.g. "USD/JPY"
    rate: float          # raw market rate (1 USD = rate JPY)
    buffered_rate: float  # rate after applying safety buffer
    source: str          # "live" | "config"
    ts: float


class FxProvider:
    def __init__(self, config: Config):
        self.config = config
        self._cache: dict[str, FxRate] = {}

    def usd_jpy(self, allow_live: bool = True) -> FxRate:
        return self.get("USD", "JPY", allow_live=allow_live)

    def get(self, base: str, quote: str, allow_live: bool = True) -> FxRate:
        pair = f"{base}/{quote}"
        buffer_pct = float(self.config.get("fx.buffer_pct", 0.0))

        if allow_live:
            live = self._fetch_live(base, quote)
            if live is not None:
                # Buffer makes JPY proceeds *more conservative*: a listing
                # priced with a slightly weaker assumed JPY protects margin.
                buffered = live * (1.0 - buffer_pct)
                rate = FxRate(pair, live, buffered, "live", time.time())
                self._cache[pair] = rate
                return rate

        # Fallback to configured rate.
        cfg_rate = float(self.config.get("fx.usd_jpy", 150.0))
        buffered = cfg_rate * (1.0 - buffer_pct)
        return FxRate(pair, cfg_rate, buffered, "config", time.time())

    def _fetch_live(self, base: str, quote: str) -> Optional[float]:
        try:
            import requests

            resp = requests.get(
                _LIVE_URL, params={"base": base, "symbols": quote}, timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            return float(data["rates"][quote])
        except Exception:
            # Offline / blocked / parse error -> caller uses config fallback.
            return None
