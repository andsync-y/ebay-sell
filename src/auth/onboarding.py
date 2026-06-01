"""Initial setup / account-linking flow (§2.11).

Drives the `setup` subcommand: create/select a user, link eBay via OAuth
(browser consent -> auth code -> tokens), register Rakuten App ID, optionally
store a Yahoo session, and capture per-user parameters into a UserProfile.

All secrets go through the CredentialStore (encrypted). Passwords are never
stored; eBay uses OAuth tokens only. Functions accept ``input_fn``/``print_fn``
so the flow is testable without a live terminal.
"""

from __future__ import annotations

import webbrowser
from typing import Callable, Optional

from ..models import Credential, UserProfile
from ..users import UserManager
from .credentials import CredentialStore, get_default_store
from .ebay_oauth import EbayOAuthConfig, build_authorize_url, exchange_code


def setup(
    store: Optional[CredentialStore] = None,
    users: Optional[UserManager] = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    open_browser: bool = True,
) -> str:
    """Run interactive onboarding. Returns the user_id."""
    store = store or get_default_store()
    users = users or UserManager()

    print_fn("=== yafu2ebay setup ===")
    user_id = input_fn("User id (new or existing): ").strip()
    if not user_id:
        raise ValueError("user_id is required")
    display = input_fn("Display name [optional]: ").strip()
    user = users.get_or_create(user_id, display)
    print_fn(f"User ready: {user.user_id} ({user.display_name})")

    # --- eBay OAuth ------------------------------------------------------- #
    if _yesno(input_fn, "Link eBay account now? [y/N] "):
        link_ebay(store, user_id, input_fn=input_fn, print_fn=print_fn,
                  open_browser=open_browser)

    # --- Rakuten ---------------------------------------------------------- #
    if _yesno(input_fn, "Register Rakuten Application ID? [y/N] "):
        app_id = input_fn("Rakuten Application ID: ").strip()
        affiliate = input_fn("Rakuten Affiliate ID [optional]: ").strip()
        data = {"app_id": app_id}
        if affiliate:
            data["affiliate_id"] = affiliate
        store.set(user_id, "rakuten",
                  Credential(user_id, "rakuten", "app_id", data))
        print_fn("Rakuten credentials saved (encrypted).")

    # --- Yahoo (optional, purchase-time only) ----------------------------- #
    if _yesno(input_fn, "Store a Yahoo session for manual purchasing? [y/N] "):
        token = input_fn("Yahoo session token (will be encrypted): ").strip()
        if token:
            store.set(user_id, "yahoo",
                      Credential(user_id, "yahoo", "session", {"session": token}))
            print_fn("Yahoo session saved (encrypted).")

    # --- Anthropic (optional) --------------------------------------------- #
    if _yesno(input_fn, "Store an Anthropic API key for listing text? [y/N] "):
        key = input_fn("Anthropic API key (will be encrypted): ").strip()
        if key:
            store.set(user_id, "anthropic",
                      Credential(user_id, "anthropic", "app_id", {"api_key": key}))
            print_fn("Anthropic key saved (encrypted).")

    # --- Profile ---------------------------------------------------------- #
    profile = users.get_profile(user_id) or UserProfile(user_id=user_id)
    margin = input_fn("Target margin (e.g. 0.08) [blank=keep default]: ").strip()
    if margin:
        profile.target_margin = float(margin)
    dest = input_fn("Default destination country (e.g. US) [optional]: ").strip()
    if dest:
        profile.default_dest = dest.upper()
    mk = input_fn("Default eBay marketplace (e.g. EBAY_US) [optional]: ").strip()
    if mk:
        profile.default_marketplace = mk
    users.save_profile(profile)
    print_fn("Profile saved.")

    print_fn(f"\nSetup complete for {user_id}. Linked services: {store.list_services(user_id)}")
    return user_id


def link_ebay(
    store: CredentialStore,
    user_id: str,
    oauth_cfg: Optional[EbayOAuthConfig] = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    open_browser: bool = True,
) -> Credential:
    """Run the eBay OAuth consent + code exchange and store tokens."""
    cfg = oauth_cfg or EbayOAuthConfig()
    url = build_authorize_url(cfg, state=user_id)
    print_fn("\nOpen this URL, log in to eBay, and approve access:")
    print_fn(f"  {url}")
    if open_browser and cfg.configured:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    if not cfg.configured:
        print_fn("(No eBay app configured — storing a STUB token so the app runs offline.)")
    code = input_fn("Paste the authorization code from the redirect (blank=stub): ").strip()
    cred = exchange_code(cfg, code)
    cred.user_id = user_id
    store.set(user_id, "ebay", cred)
    print_fn("eBay tokens saved (encrypted). Password was never requested or stored.")
    return cred


def _yesno(input_fn: Callable[[str], str], prompt: str) -> bool:
    return input_fn(prompt).strip().lower() in {"y", "yes"}
