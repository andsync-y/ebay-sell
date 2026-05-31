"""User & profile management (§2.11).

Non-secret user records (id, display name) and per-user overrides
(UserProfile) live as plain JSON under <home>/users/<user_id>/. Secrets do
NOT live here — those go through the CredentialStore.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .auth.credentials import default_home, _safe_user_id, _ensure_dir
from .models import User, UserProfile


class UserManager:
    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else default_home()

    # -- paths ------------------------------------------------------------- #
    def _user_dir(self, user_id: str) -> Path:
        return self.home / "users" / _safe_user_id(user_id)

    def _user_file(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "user.json"

    def _profile_file(self, user_id: str) -> Path:
        return self._user_dir(user_id) / "profile.json"

    # -- users ------------------------------------------------------------- #
    def create_user(self, user_id: str, display_name: str = "") -> User:
        if self.get_user(user_id):
            raise ValueError(f"User {user_id!r} already exists")
        user = User(user_id=user_id, display_name=display_name or user_id)
        _ensure_dir(self._user_dir(user_id))
        self._user_file(user_id).write_text(
            json.dumps(asdict(user), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        path = self._user_file(user_id)
        if not path.exists():
            return None
        d = json.loads(path.read_text(encoding="utf-8"))
        return User(**d)

    def get_or_create(self, user_id: str, display_name: str = "") -> User:
        return self.get_user(user_id) or self.create_user(user_id, display_name)

    def list_users(self) -> list[User]:
        base = self.home / "users"
        if not base.exists():
            return []
        out: list[User] = []
        for d in base.iterdir():
            uf = d / "user.json"
            if uf.exists():
                out.append(User(**json.loads(uf.read_text(encoding="utf-8"))))
        return sorted(out, key=lambda u: u.user_id)

    # -- profiles ---------------------------------------------------------- #
    def get_profile(self, user_id: str) -> Optional[UserProfile]:
        path = self._profile_file(user_id)
        if not path.exists():
            return None
        return UserProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_profile(self, profile: UserProfile) -> None:
        _ensure_dir(self._user_dir(profile.user_id))
        self._profile_file(profile.user_id).write_text(
            json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8"
        )
