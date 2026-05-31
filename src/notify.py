"""Notifications (§2.13-A).

Pluggable notifier with console default and optional Slack/LINE/email webhook
delivery. Used by orders.py (one-tap purchase alerts), monitoring, and account
health. Channel config/secrets come from env or the CredentialStore, never YAML.

TODO (§2.14): wire real webhook URLs / SMTP and per-user channel preferences.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Notification:
    title: str
    body: str
    actions: list[dict] = field(default_factory=list)  # e.g. [{"label","url"}]
    level: str = "info"   # info | warning | error


class BaseNotifier:
    def send(self, n: Notification) -> bool:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleNotifier(BaseNotifier):
    def send(self, n: Notification) -> bool:
        print(f"\n🔔 [{n.level.upper()}] {n.title}")
        print(n.body)
        for a in n.actions:
            print(f"   → {a.get('label', 'action')}: {a.get('url', '')}")
        return True


class WebhookNotifier(BaseNotifier):
    """POSTs a JSON payload to a Slack/LINE-compatible incoming webhook."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, n: Notification) -> bool:
        try:
            import requests

            text = f"*{n.title}*\n{n.body}"
            for a in n.actions:
                text += f"\n- {a.get('label')}: {a.get('url')}"
            resp = requests.post(self.webhook_url, json={"text": text}, timeout=15)
            return resp.ok
        except Exception:
            return False


def get_notifier(webhook_url: Optional[str] = None) -> BaseNotifier:
    url = webhook_url or os.environ.get("YAFU2EBAY_WEBHOOK_URL")
    if url:
        return WebhookNotifier(url)
    return ConsoleNotifier()
