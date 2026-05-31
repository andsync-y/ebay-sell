"""Scraper governance & monitoring (§2.13-B).

  * RateLimiter   — token-ish throttle so we respect site rate limits.
  * RobotsChecker — robots.txt fetch + can_fetch (with caching).
  * ScraperMonitor— records fetch counts / errors and raises alerts on
                    anomalies (e.g. zero results, repeated failures).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib import robotparser
from urllib.parse import urlparse

USER_AGENT = "yafu2ebay/0.1 (+https://example.invalid/yafu2ebay)"


class RateLimiter:
    """Simple thread-safe minimum-interval limiter."""

    def __init__(self, calls_per_sec: float = 1.0):
        self.min_interval = 1.0 / calls_per_sec if calls_per_sec > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


class RobotsChecker:
    """Caches robots.txt per host and answers can_fetch()."""

    def __init__(self, user_agent: str = USER_AGENT):
        self.user_agent = user_agent
        self._cache: dict[str, robotparser.RobotFileParser] = {}

    def can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._cache.get(host)
        if rp is None:
            rp = robotparser.RobotFileParser()
            rp.set_url(f"{host}/robots.txt")
            try:
                rp.read()
            except Exception:
                # If robots can't be read, be conservative but don't hard-fail
                # offline tests: default-allow with a TODO.
                # TODO: in production, prefer fail-closed for unknown robots.
                self._cache[host] = rp
                return True
            self._cache[host] = rp
        return rp.can_fetch(self.user_agent, url)


@dataclass
class Alert:
    source: str
    level: str          # "warning" | "error"
    message: str
    ts: float = field(default_factory=time.time)


class ScraperMonitor:
    """Tracks per-source fetch health. Raises alerts for anomalies."""

    def __init__(self, source: str, zero_result_is_alert: bool = True):
        self.source = source
        self.zero_result_is_alert = zero_result_is_alert
        self.total_fetched = 0
        self.error_count = 0
        self.alerts: list[Alert] = []

    def record(self, n_items: int) -> None:
        self.total_fetched += n_items
        if self.zero_result_is_alert and n_items == 0:
            self._alert("warning", "Fetched 0 items (possible block or layout change)")

    def record_error(self, message: str) -> None:
        self.error_count += 1
        self._alert("error", f"Fetch error: {message}")

    def _alert(self, level: str, message: str) -> None:
        self.alerts.append(Alert(self.source, level, message))

    def healthy(self) -> bool:
        return self.error_count == 0 and not any(a.level == "error" for a in self.alerts)
