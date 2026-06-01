"""Credential storage (§2.11 / §2.12).

Hard rules enforced here:
  * Secrets are NEVER written in plaintext to disk, config, or logs.
  * Credentials are isolated per ``user_id`` — one user can never read
    another user's data.
  * eBay uses OAuth tokens; passwords are never stored.

Backends
--------
``EncryptedFileStore``  : default. Fernet-encrypted JSON on local disk. The
                          symmetric key comes from $YAFU2EBAY_KEY, or is
                          generated and stored at ~/.yafu2ebay/key (0600).
``KeyringStore``        : optional. Uses the OS keychain via the ``keyring``
                          package, if installed.

Both implement the ``CredentialStore`` interface:
    get(user_id, service) -> Credential | None
    set(user_id, service, cred) -> None
    delete(user_id, service) -> None
    list_services(user_id) -> list[str]
"""

from __future__ import annotations

import abc
import base64
import json
import os
import stat
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from ..models import Credential

def default_home() -> Path:
    """Resolve the storage root at call time (so tests/env overrides apply).

    Falls back to /tmp/yafu2ebay when the home directory is not writable
    (e.g. Vercel / read-only serverless environments).
    """
    env = os.environ.get("YAFU2EBAY_HOME")
    if env:
        return Path(env)
    candidate = Path.home() / ".yafu2ebay"
    # Quick write-access check: if home is read-only, use /tmp instead.
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        return Path("/tmp/yafu2ebay")


# Backwards-compatible module constant (do NOT use as a default-arg value;
# it is captured at import time before env overrides). Prefer default_home().
DEFAULT_HOME = default_home()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, stat.S_IRWXU)  # 0700
    except OSError:
        pass  # best-effort on platforms without POSIX perms


# --------------------------------------------------------------------------- #
# Key management
# --------------------------------------------------------------------------- #
def load_or_create_key(home: Optional[Path] = None) -> bytes:
    """Return the Fernet key.

    Priority: $YAFU2EBAY_KEY (base64 urlsafe) > key file > newly generated.
    The key itself is the only secret; treat it like a master password.
    TODO: for distributed/multi-user deployments derive a per-user key
    (e.g. from the user's own passphrase) so operators never hold plaintext.
    """
    env_key = os.environ.get("YAFU2EBAY_KEY")
    if env_key:
        return env_key.encode() if isinstance(env_key, str) else env_key

    home = home or default_home()
    _ensure_dir(home)
    key_path = home / "key"
    if key_path.exists():
        return key_path.read_bytes().strip()

    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass
    return key


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
class CredentialStore(abc.ABC):
    @abc.abstractmethod
    def get(self, user_id: str, service: str) -> Optional[Credential]: ...

    @abc.abstractmethod
    def set(self, user_id: str, service: str, cred: Credential) -> None: ...

    @abc.abstractmethod
    def delete(self, user_id: str, service: str) -> None: ...

    @abc.abstractmethod
    def list_services(self, user_id: str) -> list[str]: ...


# --------------------------------------------------------------------------- #
# Encrypted-file backend (default)
# --------------------------------------------------------------------------- #
class EncryptedFileStore(CredentialStore):
    """One encrypted blob per user at ``<home>/users/<user_id>/credentials.enc``.

    Per-user files give hard filesystem-level isolation; nothing about user B
    is ever loaded when operating as user A.
    """

    def __init__(self, home: Optional[Path] = None, key: Optional[bytes] = None):
        self.home = Path(home) if home else default_home()
        self._fernet = Fernet(key or load_or_create_key(self.home))

    def _path(self, user_id: str) -> Path:
        safe = _safe_user_id(user_id)
        return self.home / "users" / safe / "credentials.enc"

    def _read_all(self, user_id: str) -> dict[str, dict]:
        path = self._path(user_id)
        if not path.exists():
            return {}
        token = path.read_bytes()
        try:
            raw = self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise RuntimeError(
                f"Cannot decrypt credentials for {user_id!r}: wrong key?"
            ) from exc
        return json.loads(raw.decode("utf-8"))

    def _write_all(self, user_id: str, blob: dict[str, dict]) -> None:
        path = self._path(user_id)
        _ensure_dir(path.parent)
        token = self._fernet.encrypt(json.dumps(blob).encode("utf-8"))
        path.write_bytes(token)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass

    def get(self, user_id: str, service: str) -> Optional[Credential]:
        blob = self._read_all(user_id)
        rec = blob.get(service)
        if rec is None:
            return None
        return Credential(
            user_id=user_id,
            service=service,
            kind=rec["kind"],
            data=rec["data"],
            expires_at=rec.get("expires_at"),
        )

    def set(self, user_id: str, service: str, cred: Credential) -> None:
        if cred.user_id != user_id or cred.service != service:
            raise ValueError("Credential user_id/service mismatch")
        blob = self._read_all(user_id)
        blob[service] = {
            "kind": cred.kind,
            "data": cred.data,
            "expires_at": cred.expires_at,
        }
        self._write_all(user_id, blob)

    def delete(self, user_id: str, service: str) -> None:
        blob = self._read_all(user_id)
        if service in blob:
            del blob[service]
            self._write_all(user_id, blob)

    def list_services(self, user_id: str) -> list[str]:
        return sorted(self._read_all(user_id).keys())


# --------------------------------------------------------------------------- #
# OS keychain backend (optional)
# --------------------------------------------------------------------------- #
class KeyringStore(CredentialStore):
    """Stores each credential in the OS keychain under a namespaced service.

    Requires the ``keyring`` package. Falls back is the caller's responsibility.
    """

    _NS = "yafu2ebay"

    def __init__(self) -> None:
        try:
            import keyring  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "KeyringStore requires the 'keyring' package (pip install keyring)"
            ) from exc
        import keyring

        self._kr = keyring
        # Track which services exist per user (keyring has no enumeration API).
        self._index_service = f"{self._NS}:index"

    def _svc(self, user_id: str) -> str:
        return f"{self._NS}:{_safe_user_id(user_id)}"

    def get(self, user_id: str, service: str) -> Optional[Credential]:
        raw = self._kr.get_password(self._svc(user_id), service)
        if raw is None:
            return None
        rec = json.loads(raw)
        return Credential(
            user_id=user_id,
            service=service,
            kind=rec["kind"],
            data=rec["data"],
            expires_at=rec.get("expires_at"),
        )

    def set(self, user_id: str, service: str, cred: Credential) -> None:
        rec = {"kind": cred.kind, "data": cred.data, "expires_at": cred.expires_at}
        self._kr.set_password(self._svc(user_id), service, json.dumps(rec))
        self._add_to_index(user_id, service)

    def delete(self, user_id: str, service: str) -> None:
        try:
            self._kr.delete_password(self._svc(user_id), service)
        except Exception:  # pragma: no cover - backend-specific
            pass
        self._remove_from_index(user_id, service)

    def list_services(self, user_id: str) -> list[str]:
        raw = self._kr.get_password(self._index_service, _safe_user_id(user_id))
        return sorted(json.loads(raw)) if raw else []

    def _add_to_index(self, user_id: str, service: str) -> None:
        cur = set(self.list_services(user_id))
        cur.add(service)
        self._kr.set_password(
            self._index_service, _safe_user_id(user_id), json.dumps(sorted(cur))
        )

    def _remove_from_index(self, user_id: str, service: str) -> None:
        cur = set(self.list_services(user_id))
        cur.discard(service)
        self._kr.set_password(
            self._index_service, _safe_user_id(user_id), json.dumps(sorted(cur))
        )


# --------------------------------------------------------------------------- #
# Helpers / factory
# --------------------------------------------------------------------------- #
def _safe_user_id(user_id: str) -> str:
    """Filesystem/keychain-safe encoding of a user id (no traversal)."""
    return base64.urlsafe_b64encode(user_id.encode("utf-8")).decode("ascii").rstrip("=")


def get_default_store(backend: Optional[str] = None) -> CredentialStore:
    """Factory. ``backend`` env override: $YAFU2EBAY_CRED_BACKEND ∈ {file,keyring}."""
    backend = backend or os.environ.get("YAFU2EBAY_CRED_BACKEND", "file")
    if backend == "keyring":
        return KeyringStore()
    return EncryptedFileStore()
