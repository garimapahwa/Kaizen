"""Reviewer sessions: who is reviewing, in which slot, and whether they are blind.

Identity used to be a name typed into the browser and blind mode a query parameter, so a reviewer could
switch either of them client-side. Both now live here. The server issues an opaque token, stores the
slot and the blind flag against it, and derives blind mode from the workspace policy rather than from
anything the caller sends. Changing the policy or opening a session is an audit event.

A session is only opened after the caller signs in: `UserStore` below holds the accounts, and
`POST /api/auth/signin` is the only route that calls `SessionStore.open`. Identity is therefore a
BD email address backed by a password, and the blind flag stays a server-side decision derived from
the workspace policy rather than anything the caller sends.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kaizen.storage.db import Database

BLIND_REVIEW_KEY = "blind_review"
POLICY_REQUIRED = "required"
POLICY_OPTIONAL = "optional"
POLICIES = (POLICY_REQUIRED, POLICY_OPTIONAL)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ReviewSession:
    """An open review session. `blind` is decided by the server, never by the client."""

    token: str
    reviewer: str
    slot: int
    blind: bool
    created_at: str

    def to_dict(self, policy: str) -> dict:
        """Session as the API returns it. The token is never included; it travels in a cookie."""
        return {"reviewer": self.reviewer, "slot": self.slot, "blind": self.blind, "created_at": self.created_at, "blind_review_policy": policy}


class SessionStore:
    def __init__(self, db: Database):
        self.db = db
        self.conn = db.conn

    # ---- policy ---------------------------------------------------------------------------------

    def policy(self) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (BLIND_REVIEW_KEY,)).fetchone()
        return row["value"] if row and row["value"] in POLICIES else POLICY_REQUIRED

    def set_policy(self, value: str, by: str) -> str:
        if value not in POLICIES:
            raise ValueError(f"blind review policy must be one of {', '.join(POLICIES)}")
        with self.db.lock:
            self.conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (BLIND_REVIEW_KEY, value, _now(), by),
            )
            self.db.audit(by, "session.policy", f"blind review policy set to {value}")
            self.conn.commit()
        return value

    def blind_for(self, slot: int, requested: bool | None = None) -> bool:
        """Blind mode for a slot. Reviewer 1 is never blind; reviewer 2 is blind whenever the policy
        requires it, and may only be unblinded when the policy is optional."""
        if slot != 2:
            return False
        if self.policy() == POLICY_REQUIRED:
            return True
        return True if requested is None else bool(requested)

    # ---- sessions -------------------------------------------------------------------------------

    def open(self, reviewer: str, slot: int, blind: bool | None = None) -> ReviewSession:
        name = (reviewer or "").strip()
        if not name:
            raise ValueError("a reviewer name is required: decisions are recorded against it")
        if slot not in (1, 2):
            raise ValueError("reviewer slot must be 1 (facilitator) or 2 (independent reviewer)")
        s = ReviewSession(secrets.token_urlsafe(32), name, slot, self.blind_for(slot, blind), _now())
        with self.db.lock:
            self.conn.execute(
                "INSERT INTO sessions (token, reviewer, slot, blind, created_at, last_seen_at, ended_at) VALUES (?,?,?,?,?,?,'')",
                (s.token, s.reviewer, s.slot, int(s.blind), s.created_at, s.created_at),
            )
            self.db.audit(s.reviewer, "session.open", f"slot {s.slot}, blind {'on' if s.blind else 'off'} (policy {self.policy()})")
            self.conn.commit()
        return s

    def resolve(self, token: str | None) -> ReviewSession | None:
        if not token:
            return None
        row = self.conn.execute("SELECT * FROM sessions WHERE token = ? AND ended_at = ''", (token,)).fetchone()
        if row is None:
            return None
        with self.db.lock:
            self.conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token = ?", (_now(), token))
            self.conn.commit()
        return ReviewSession(row["token"], row["reviewer"], int(row["slot"]), bool(row["blind"]), row["created_at"])

    def end(self, token: str | None) -> bool:
        s = self.resolve(token)
        if s is None:
            return False
        with self.db.lock:
            self.conn.execute("UPDATE sessions SET ended_at = ? WHERE token = ?", (_now(), token))
            self.db.audit(s.reviewer, "session.end", f"slot {s.slot}")
            self.conn.commit()
        return True

    def active(self) -> list[ReviewSession]:
        rows = self.conn.execute("SELECT * FROM sessions WHERE ended_at = '' ORDER BY created_at").fetchall()
        return [ReviewSession(r["token"], r["reviewer"], int(r["slot"]), bool(r["blind"]), r["created_at"]) for r in rows]


# ---- accounts -----------------------------------------------------------------------------------

SCRYPT_N = 2**14  # ~100ms per hash on a laptop: slow enough to make offline cracking expensive
SCRYPT_R = 8
SCRYPT_P = 1
MIN_PASSWORD = 10
MAX_FAILED = 10
LOCKOUT_SECONDS = 900


def hash_password(password: str) -> str:
    """scrypt with a per-account random salt, stored as one self-describing string.

    Deliberately not a plain SHA-256: a fast hash of a human-chosen password is cracked in bulk if the
    workspace file ever leaks. The parameters travel with the hash so they can be raised later without
    invalidating existing accounts.
    """
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


@dataclass(frozen=True)
class User:
    email: str
    created_at: str
    updated_at: str
    locked_until: str


class UserStore:
    """Reviewer accounts: a BD email address and a password, in the workspace database.

    There is no password-reset email, because this tool has no mail server to send one from. An account
    is cleared by an administrator from the command line (`kaizen users reset`), after which the person
    signs up again and chooses a new password themselves — so a reset never puts their password in
    anyone else's hands.
    """

    def __init__(self, db: Database):
        self.db = db
        self.conn = db.conn

    def _row(self, email: str):
        return self.conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    def exists(self, email: str) -> bool:
        return self._row(email) is not None

    def create(self, email: str, password: str) -> User:
        if len(password or "") < MIN_PASSWORD:
            raise ValueError(f"the password must be at least {MIN_PASSWORD} characters")
        if self.exists(email):
            raise KeyError(email)
        now = _now()
        with self.db.lock:
            self.conn.execute(
                "INSERT INTO users (email, password_hash, created_at, updated_at, failed_attempts, locked_until) VALUES (?,?,?,?,0,'')",
                (email, hash_password(password), now, now),
            )
            self.db.audit(email, "auth.signup", "account created")
            self.conn.commit()
        return User(email, now, now, "")

    def locked_seconds(self, email: str) -> int:
        """Seconds until sign-in is allowed again, or 0. Lockouts expire on their own: nobody has to
        unlock an account, which matters when there is no help desk behind this tool."""
        row = self._row(email)
        if row is None or not row["locked_until"]:
            return 0
        remaining = (datetime.fromisoformat(row["locked_until"]) - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(remaining))

    def verify(self, email: str, password: str) -> bool:
        """True for the right password on an unlocked account. Wrong guesses count towards a lockout."""
        row = self._row(email)
        if row is None or self.locked_seconds(email) > 0:
            return False
        if verify_password(password or "", row["password_hash"]):
            if row["failed_attempts"] or row["locked_until"]:
                with self.db.lock:
                    self.conn.execute("UPDATE users SET failed_attempts = 0, locked_until = '' WHERE email = ?", (email,))
                    self.conn.commit()
            return True
        failed = int(row["failed_attempts"]) + 1
        locked = (datetime.now(timezone.utc) + timedelta(seconds=LOCKOUT_SECONDS)).isoformat(timespec="seconds") if failed >= MAX_FAILED else ""
        with self.db.lock:
            self.conn.execute("UPDATE users SET failed_attempts = ?, locked_until = ? WHERE email = ?", (failed, locked, email))
            if locked:
                self.db.audit(email, "auth.locked", f"{failed} failed sign-ins; locked for {LOCKOUT_SECONDS // 60} minutes")
            self.conn.commit()
        return False

    def delete(self, email: str, by: str = "admin") -> bool:
        """Clear the account so the address can sign up again. Decisions keep their reviewer name: they
        are recorded against the address, not against a row in this table."""
        if not self.exists(email):
            return False
        with self.db.lock:
            self.conn.execute("DELETE FROM users WHERE email = ?", (email,))
            self.db.audit(by, "auth.reset", f"account cleared for {email}; they can sign up again")
            self.conn.commit()
        return True

    def list(self) -> list[User]:
        rows = self.conn.execute("SELECT * FROM users ORDER BY email").fetchall()
        return [User(r["email"], r["created_at"], r["updated_at"], r["locked_until"]) for r in rows]


