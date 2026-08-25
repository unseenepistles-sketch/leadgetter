"""Sign-in, so that "authorised by" means something.

The requirements say *authorized users* may edit records and override renewal
dates. Without a login, an authoriser's name is just text somebody typed — the
audit trail records a claim rather than a fact. This closes that: when sign-in is
on, the person editing is the person the session says they are, and the audit
trail is filled in from the session rather than from a form field.

Deliberately small and swappable. The department will most likely end up signing
in with their work accounts once the app is hosted; this exists so the system is
not wide open in the meantime, and so the permission checks are already in the
right places when that swap happens.

Off by default, so a local demo needs no setup.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("uniforms.auth")

COOKIE = "uniform_session"
_PBKDF2_ROUNDS = 240_000


@dataclass(frozen=True, slots=True)
class User:
    name: str
    #: Only admins may edit records or override renewal dates.
    admin: bool = False


def hash_password(password: str, *, salt: Optional[bytes] = None) -> str:
    """Produce a storable ``pbkdf2$<rounds>$<salt>$<hash>`` string."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored.startswith("pbkdf2$"):
        # Plaintext is tolerated so a pilot can be stood up quickly, but it is
        # called out loudly at startup.
        return hmac.compare_digest(password, stored)
    try:
        _, rounds, salt_b64, digest_b64 = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(rounds)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def parse_users(raw: str) -> dict[str, tuple[str, bool]]:
    """``name:secret:role,name:secret`` -> {name: (secret, is_admin)}."""
    users: dict[str, tuple[str, bool]] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) < 2:
            log.warning("ignoring malformed AUTH_USERS entry %r", entry)
            continue
        name, secret = parts[0].strip(), parts[1].strip()
        role = parts[2].strip().lower() if len(parts) > 2 else "admin"
        if not name or not secret:
            continue
        users[name] = (secret, role == "admin")
    return users


class Authenticator:
    def __init__(self, users: dict[str, tuple[str, bool]], secret_key: str,
                 *, enabled: bool = True, max_age_seconds: int = 12 * 3600) -> None:
        self.users = users
        self.secret_key = secret_key
        self.enabled = enabled and bool(users)
        self.max_age_seconds = max_age_seconds

        if enabled and not users:
            log.error("AUTH_ENABLED is on but AUTH_USERS is empty — sign-in cannot work, "
                      "so it stays off. Set AUTH_USERS to switch it on.")
        if self.enabled and any(not s.startswith("pbkdf2$") for s, _ in users.values()):
            log.warning("some AUTH_USERS entries hold plaintext passwords; "
                        "run scripts/hash_password.py and store the hash instead")

    def authenticate(self, name: str, password: str) -> Optional[User]:
        entry = self.users.get((name or "").strip())
        if entry is None:
            # Spend the same work on an unknown user so timing does not reveal
            # which names exist.
            verify_password(password or "", hash_password("no-such-user"))
            return None
        secret, admin = entry
        if not verify_password(password or "", secret):
            return None
        return User(name=name.strip(), admin=admin)

    # ------------------------------------------------------------- session

    def issue(self, user: User) -> str:
        payload = f"{user.name}|{int(user.admin)}|{int(time.time())}"
        signature = hmac.new(
            self.secret_key.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return base64.urlsafe_b64encode(f"{payload}|{signature}".encode()).decode()

    def read(self, token: Optional[str]) -> Optional[User]:
        if not token:
            return None
        try:
            raw = base64.urlsafe_b64decode(token.encode()).decode()
            name, admin, issued, signature = raw.rsplit("|", 3)
        except (ValueError, TypeError):
            return None

        expected = hmac.new(
            self.secret_key.encode(), f"{name}|{admin}|{issued}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if time.time() - int(issued) > self.max_age_seconds:
            return None
        # A user removed from the config loses access on their next request.
        if name not in self.users:
            return None
        return User(name=name, admin=admin == "1")


def build_authenticator() -> Authenticator:
    enabled = os.getenv("AUTH_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    secret = os.getenv("SECRET_KEY", "")
    if enabled and not secret:
        # A per-process key still signs correctly; it just logs everyone out on
        # restart, which is safer than shipping a predictable default.
        secret = secrets.token_hex(32)
        log.warning("SECRET_KEY is unset — sessions will not survive a restart")
    return Authenticator(
        parse_users(os.getenv("AUTH_USERS", "")),
        secret or "development-only",
        enabled=enabled,
    )
