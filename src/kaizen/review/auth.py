"""Where reviewer accounts live: in this workspace's database, or in a Supabase project.

Only the email address and password are ever held by Supabase. Sessions, the reviewer slot, blind mode,
decisions and everything else stay in the local workspace, exactly as before: a successful Supabase
sign-in opens the same local `SessionStore` session a local sign-in does.

No email is ever sent: BD mail blocks external senders, so the Supabase project runs with "Confirm email"
off, and a forgotten password is set by an administrator in the Supabase dashboard.

Which backend is used is decided once, when the app starts:

1. `KAIZEN_SUPABASE_URL` and `KAIZEN_SUPABASE_KEY` in the environment, if both are set;
2. otherwise the values saved in the workspace with `kaizen auth supabase <url> <key>`;
3. otherwise local accounts (the default, and the only option offline).

The key must be the project's anon / publishable key. It is designed to be public, so it may be stored in
the workspace database. A service_role / secret key bypasses every protection in Supabase and is refused.

Supabase is called over its plain HTTP API with the standard library, so no extra dependency is needed.
Proxies set in `HTTPS_PROXY` are honoured.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone

from kaizen.review.sessions import MIN_PASSWORD, UserStore
from kaizen.storage.db import Database

URL_KEY = "supabase_url"
KEY_KEY = "supabase_key"
TIMEOUT_SECONDS = 10

BAD_CREDENTIALS = "That email address and password do not match an account."
UNREACHABLE = "Cannot reach the sign-in service (Supabase). Check the internet connection and try again."
TURN_OFF_CONFIRM = "Supabase is set to email a confirmation link, which BD mail cannot receive. In Supabase, turn off “Confirm email” (Authentication → Sign In / Providers)."


class AuthError(Exception):
    """A sign-in problem the API turns into an HTTP error with this status and message."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---- local accounts -----------------------------------------------------------------------------


class LocalAccounts:
    """Accounts in the workspace database (scrypt, lockout). A forgotten password is cleared by an admin."""

    name = "local"

    def __init__(self, db: Database):
        self.users = UserStore(db)

    def sign_up(self, email: str, password: str) -> None:
        try:
            self.users.create(email, password)
        except KeyError:
            raise AuthError(409, "An account already exists for that address. Sign in instead, or ask an administrator to reset it.")
        except ValueError as e:
            raise AuthError(400, str(e))

    def sign_in(self, email: str, password: str) -> None:
        locked = self.users.locked_seconds(email)
        if locked:
            raise AuthError(429, f"Too many failed attempts. Try again in {max(1, locked // 60)} minute(s).")
        if not self.users.verify(email, password):
            # One message for "no such account" and "wrong password": the difference would tell an
            # outsider which BD addresses have accounts here.
            raise AuthError(401, BAD_CREDENTIALS)


# ---- Supabase accounts --------------------------------------------------------------------------

Http = Callable[[str, str, dict, dict | None], tuple[int, dict]]


def _urllib_http(method: str, url: str, headers: dict, body: dict | None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.status, _json(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _json(e.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AuthError(503, UNREACHABLE)


def _json(raw: bytes) -> dict:
    try:
        out = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _message(body: dict) -> str:
    return str(body.get("msg") or body.get("error_description") or body.get("message") or body.get("error") or "")


def _code(body: dict) -> str:
    return str(body.get("error_code") or body.get("code") or "")


class SupabaseAccounts:
    """Accounts in a Supabase project (Supabase Auth), shared by every laptop configured with it."""

    name = "supabase"

    def __init__(self, url: str, key: str, http: Http | None = None):
        self.url = url.rstrip("/")
        self.key = key
        self._http = http or _urllib_http

    def _call(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> tuple[int, dict]:
        headers = {"apikey": self.key, "Content-Type": "application/json", "Accept": "application/json"}
        url = f"{self.url}/auth/v1/{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return self._http(method, url, headers, body)

    def _common(self, status: int, body: dict) -> None:
        """Failures every Supabase call can return."""
        code = _code(body)
        if status == 429 or code.startswith("over_"):
            raise AuthError(429, "Too many attempts in a short time. Wait a few minutes and try again.")
        if code == "email_address_not_authorized":
            raise AuthError(503, TURN_OFF_CONFIRM)
        if status == 401 and "api key" in _message(body).lower():
            raise AuthError(503, "The Supabase key is not valid for this project. Check it with `kaizen auth show`.")
        if status >= 500:
            raise AuthError(503, "Supabase had a problem answering. Try again in a moment.")

    def sign_up(self, email: str, password: str) -> None:
        if len(password or "") < MIN_PASSWORD:
            raise AuthError(400, f"the password must be at least {MIN_PASSWORD} characters")
        status, body = self._call("POST", "signup", {"email": email, "password": password})
        if status in (200, 201):
            if "access_token" not in body:
                # Supabase answered without a session: it has emailed a confirmation link instead, and
                # the account cannot sign in until it is clicked. BD mail never delivers it.
                raise AuthError(503, TURN_OFF_CONFIRM)
            return
        code = _code(body)
        if code in ("user_already_exists", "email_exists"):
            raise AuthError(409, "An account already exists for that address. Sign in instead, or ask the Kaizen admin to set a new password.")
        if code == "signup_disabled":
            raise AuthError(403, "New accounts are switched off in Supabase. Ask whoever runs the Supabase project to add you.")
        self._common(status, body)
        raise AuthError(400, _message(body) or "Supabase refused the sign-up.")

    def sign_in(self, email: str, password: str) -> None:
        status, body = self._call("POST", "token", {"email": email, "password": password}, {"grant_type": "password"})
        if status == 200 and body.get("access_token"):
            returned = str((body.get("user") or {}).get("email") or "").lower()
            if returned and returned != email:
                raise AuthError(401, BAD_CREDENTIALS)
            return
        code = _code(body)
        if code == "email_not_confirmed":
            raise AuthError(403, "This account is waiting for email confirmation in Supabase. Ask the Kaizen admin to confirm it (Authentication → Users) and to turn off “Confirm email”.")
        self._common(status, body)
        raise AuthError(401, BAD_CREDENTIALS)


# ---- configuration ------------------------------------------------------------------------------


def is_secret_key(key: str) -> bool:
    """True for keys that must never be stored or used here: they bypass Supabase's own protections."""
    if key.startswith("sb_secret_"):
        return True
    parts = key.split(".")
    if len(parts) == 3:  # a legacy JWT key: the role is in the payload
        try:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        except ValueError:
            return False
        return isinstance(payload, dict) and payload.get("role") == "service_role"
    return False


def validate(url: str, key: str) -> tuple[str, str]:
    """Normalised (url, key), or ValueError explaining what is wrong."""
    url, key = (url or "").strip().rstrip("/"), (key or "").strip()
    parsed = urllib.parse.urlparse(url)
    local = parsed.hostname in ("localhost", "127.0.0.1")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("the Supabase URL must start with https://, e.g. https://abcdefgh.supabase.co")
    if not parsed.hostname:
        raise ValueError("the Supabase URL has no host name")
    if not key:
        raise ValueError("the Supabase key is empty")
    if is_secret_key(key):
        raise ValueError("that is a service_role / secret key, which bypasses all of Supabase's protections. Use the anon / publishable key instead (Project Settings → API Keys).")
    return url, key


def saved_config(db: Database) -> tuple[str, str] | None:
    rows = {r["key"]: r["value"] for r in db.conn.execute("SELECT key, value FROM settings WHERE key IN (?, ?)", (URL_KEY, KEY_KEY))}
    if rows.get(URL_KEY) and rows.get(KEY_KEY):
        return rows[URL_KEY], rows[KEY_KEY]
    return None


def save_config(db: Database, url: str, key: str, by: str) -> tuple[str, str]:
    url, key = validate(url, key)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.lock:
        for k, v in ((URL_KEY, url), (KEY_KEY, key)):
            db.conn.execute(
                "INSERT INTO settings (key, value, updated_at, updated_by) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (k, v, now, by),
            )
        db.audit(by, "auth.config", f"accounts moved to Supabase at {url}")
        db.conn.commit()
    return url, key


def clear_config(db: Database, by: str) -> None:
    with db.lock:
        db.conn.execute("DELETE FROM settings WHERE key IN (?, ?)", (URL_KEY, KEY_KEY))
        db.audit(by, "auth.config", "accounts moved back to this workspace (local)")
        db.conn.commit()


def env_config() -> tuple[str, str] | None:
    """Both environment variables, or None. Setting only one of them is a mistake worth stopping on."""
    url, key = os.environ.get("KAIZEN_SUPABASE_URL", "").strip(), os.environ.get("KAIZEN_SUPABASE_KEY", "").strip()
    if bool(url) != bool(key):
        raise ValueError("set both KAIZEN_SUPABASE_URL and KAIZEN_SUPABASE_KEY, or neither")
    return (url, key) if url else None


def accounts_for(db: Database, http: Http | None = None) -> LocalAccounts | SupabaseAccounts:
    """The account backend this workspace uses. Raises ValueError for a broken configuration rather than
    silently falling back to local accounts, which would split the team across two sets of passwords."""
    cfg = env_config() or saved_config(db)
    if cfg is None:
        return LocalAccounts(db)
    url, key = validate(*cfg)
    return SupabaseAccounts(url, key, http)
