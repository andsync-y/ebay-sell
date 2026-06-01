"""eBay OAuth 2.0 authorization-code flow + automatic token refresh (§2.11).

We request only the minimal Sell scopes needed. The user's *password is never
seen or stored* — the browser flow returns an authorization code which we
exchange for access + refresh tokens. Tokens live in the CredentialStore,
encrypted at rest.

Offline / no-keys behaviour: ``build_authorize_url`` and the exchange/refresh
calls degrade to clearly-marked stub tokens so the rest of the app runs
end-to-end without real eBay app credentials. Stub tokens are tagged
``"stub": true`` so downstream code (ebay.py) knows to use the console stub.

TODO (§2.14): register an eBay app to obtain App ID / Cert ID / RuName, and
confirm the exact scope list for your selling features.
"""

from __future__ import annotations

import base64
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import requests

from ..models import Credential
from .credentials import CredentialStore

# Minimal Sell scopes. TODO: trim/extend to match enabled features.
DEFAULT_SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
]

# Production vs sandbox endpoints.
ENDPOINTS = {
    "production": {
        "authorize": "https://auth.ebay.com/oauth2/authorize",
        "token": "https://api.ebay.com/identity/v1/oauth2/token",
    },
    "sandbox": {
        "authorize": "https://auth.sandbox.ebay.com/oauth2/authorize",
        "token": "https://api.sandbox.ebay.com/identity/v1/oauth2/token",
    },
}


class EbayOAuthConfig:
    """App-level (not per-user) OAuth config, sourced from env (never YAML)."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        ru_name: Optional[str] = None,
        environment: str = "production",
        scopes: Optional[list[str]] = None,
    ):
        self.client_id = client_id or os.environ.get("EBAY_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("EBAY_CLIENT_SECRET")
        self.ru_name = ru_name or os.environ.get("EBAY_RU_NAME")
        self.environment = os.environ.get("EBAY_ENV", environment)
        self.scopes = scopes or DEFAULT_SCOPES

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.ru_name)

    @property
    def endpoints(self) -> dict[str, str]:
        return ENDPOINTS.get(self.environment, ENDPOINTS["production"])


def build_authorize_url(cfg: EbayOAuthConfig, state: str = "") -> str:
    """URL the user opens in a browser to grant consent."""
    if not cfg.configured:
        # Offline stub so onboarding can be demonstrated without an eBay app.
        return f"[STUB] eBay authorize URL (set EBAY_CLIENT_ID/SECRET/RU_NAME). state={state}"
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": cfg.ru_name,
        "response_type": "code",
        "scope": " ".join(cfg.scopes),
    }
    if state:
        params["state"] = state
    return f"{cfg.endpoints['authorize']}?{urlencode(params)}"


def _basic_auth_header(cfg: EbayOAuthConfig) -> str:
    raw = f"{cfg.client_id}:{cfg.client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _expiry_iso(expires_in: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).isoformat()


def exchange_code(cfg: EbayOAuthConfig, code: str) -> Credential:
    """Exchange an authorization code for access + refresh tokens."""
    if not cfg.configured:
        return _stub_credential()
    resp = requests.post(
        cfg.endpoints["token"],
        headers={
            "Authorization": _basic_auth_header(cfg),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cfg.ru_name,
        },
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    return Credential(
        user_id="",  # filled in by caller before storing
        service="ebay",
        kind="oauth_token",
        data={
            "access_token": tok["access_token"],
            "refresh_token": tok.get("refresh_token"),
            "token_type": tok.get("token_type", "User Access Token"),
            "scopes": cfg.scopes,
            "stub": False,
        },
        expires_at=_expiry_iso(tok.get("expires_in", 7200)),
    )


def refresh(cfg: EbayOAuthConfig, cred: Credential) -> Credential:
    """Refresh an access token using the stored refresh_token."""
    if cred.data.get("stub"):
        # Keep the stub usable across runs.
        cred.expires_at = _expiry_iso(7200)
        return cred
    refresh_token = cred.data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh_token; user must re-authorize eBay.")
    resp = requests.post(
        cfg.endpoints["token"],
        headers={
            "Authorization": _basic_auth_header(cfg),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(cfg.scopes),
        },
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    cred.data["access_token"] = tok["access_token"]
    cred.expires_at = _expiry_iso(tok.get("expires_in", 7200))
    return cred


def get_valid_access_token(
    store: CredentialStore, user_id: str, cfg: Optional[EbayOAuthConfig] = None
) -> Optional[str]:
    """Return a non-expired access token, refreshing transparently if needed.

    Returns None if the user hasn't linked eBay (caller falls back to stub).
    """
    cfg = cfg or EbayOAuthConfig()
    cred = store.get(user_id, "ebay")
    if cred is None:
        return None
    if cred.is_expired():
        try:
            cred = refresh(cfg, cred)
            store.set(user_id, "ebay", cred)
        except Exception:
            # Refresh failed -> signal re-link needed.
            return None
    return cred.data.get("access_token")


def _stub_credential() -> Credential:
    """A clearly-marked offline token so the app runs without an eBay app."""
    return Credential(
        user_id="",
        service="ebay",
        kind="oauth_token",
        data={
            "access_token": f"STUB-EBAY-TOKEN-{int(time.time())}",
            "refresh_token": "STUB-EBAY-REFRESH",
            "token_type": "User Access Token",
            "scopes": DEFAULT_SCOPES,
            "stub": True,
        },
        expires_at=_expiry_iso(7200),
    )
