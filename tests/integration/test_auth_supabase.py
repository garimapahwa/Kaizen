"""Accounts checked by Supabase: only email + password leave the workspace; sessions stay local.

A fake Supabase stands in for the network and answers the way Supabase Auth's HTTP API does. The real
project runs with "Confirm email" off, because BD mail blocks external senders: sign-up works at once and
no email is ever sent. The fake can also behave as if that setting were left on, to check the warning.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kaizen.api.app import create_app
from kaizen.cli.main import app as cli
from kaizen.review.auth import AuthError, LocalAccounts, SupabaseAccounts, accounts_for, is_secret_key, save_config, validate
from kaizen.workspace import Workspace

BD = "dharma.reddy@bd.com"
PW = "correct-horse-battery"
URL = "https://abcdefgh.supabase.co"
KEY = "sb_publishable_test"


class FakeSupabase:
    """Just enough of Supabase Auth: /signup and /token."""

    def __init__(self, confirm_email: bool = False):
        self.confirm_email = confirm_email
        self.users: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None, dict]] = []
        self.down = False

    def confirm(self, email: str) -> None:
        self.users[email]["confirmed"] = True

    def __call__(self, method, url, headers, body):
        if self.down:
            raise AuthError(503, "Cannot reach the sign-in service (Supabase). Check the internet connection and try again.")
        self.calls.append((method, url, body, headers))
        assert headers["apikey"] == KEY
        path = url.split("/auth/v1/", 1)[1].split("?", 1)[0]
        if path == "signup":
            email = body["email"]
            if email in self.users:
                if self.confirm_email:
                    return 200, {"id": "fake", "email": email}  # Supabase hides that the account exists
                return 422, {"code": 422, "error_code": "user_already_exists", "msg": "User already registered"}
            self.users[email] = {"password": body["password"], "confirmed": not self.confirm_email}
            return (200, {"id": "u1", "email": email, "confirmation_sent_at": "now"}) if self.confirm_email else (200, {"access_token": "t", "user": {"email": email}})
        if path == "token":
            u = self.users.get(body["email"])
            if u is None or u["password"] != body["password"]:
                return 400, {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"}
            if not u["confirmed"]:
                return 400, {"code": 400, "error_code": "email_not_confirmed", "msg": "Email not confirmed"}
            return 200, {"access_token": "t", "user": {"email": body["email"]}}
        return 404, {}


@pytest.fixture
def fake():
    return FakeSupabase()


@pytest.fixture
def client(tmp_path, fake, monkeypatch):
    monkeypatch.delenv("KAIZEN_ALLOWED_DOMAINS", raising=False)
    ws = Workspace(tmp_path / "ws")
    c = TestClient(create_app(ws, accounts=SupabaseAccounts(URL, KEY, http=fake)))
    c.ws = ws
    return c


def signup(client, email=BD, password=PW):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def signin(client, email=BD, password=PW, slot=1):
    return client.post("/api/auth/signin", json={"email": email, "password": password, "slot": slot})


# ---- sign-up ------------------------------------------------------------------------------------


def test_sign_up_stores_the_account_in_supabase_not_the_workspace(client, fake):
    r = signup(client)
    assert r.status_code == 200 and r.json() == {"ok": True, "email": BD}
    assert BD in fake.users
    assert client.ws.db.conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0, "no password is kept locally"


def test_only_bd_addresses_reach_supabase(client, fake):
    assert signup(client, "someone@gmail.com").status_code == 400
    assert signin(client, "someone@bd.com.evil.io").status_code == 400
    assert fake.calls == [], "a non-BD address is refused before any call to Supabase"


def test_a_short_password_is_refused_without_calling_supabase(client, fake):
    assert signup(client, BD, "short").status_code == 400
    assert fake.calls == []


def test_an_address_cannot_be_registered_twice(client):
    signup(client)
    again = signup(client, BD, "another-password-1")
    assert again.status_code == 409 and "Kaizen admin" in again.json()["detail"]
    assert signin(client, BD, PW).status_code == 200, "the original password is untouched"


def test_confirm_email_left_on_in_supabase_is_explained(tmp_path):
    """BD mail never receives Supabase's confirmation link, so the admin must switch the setting off."""
    fake = FakeSupabase(confirm_email=True)
    c = TestClient(create_app(Workspace(tmp_path / "ws"), accounts=SupabaseAccounts(URL, KEY, http=fake)))
    r = signup(c)
    assert r.status_code == 503 and "Confirm email" in r.json()["detail"]
    s = signin(c)
    assert s.status_code == 403 and "Kaizen admin" in s.json()["detail"], "the stuck account is explained too"
    fake.confirm(BD)
    assert signin(c).status_code == 200


# ---- signing in ---------------------------------------------------------------------------------


def test_sign_in_opens_a_local_session_with_the_slot(client, fake):
    signup(client)
    s = signin(client, slot=2).json()
    assert s["reviewer"] == BD and s["slot"] == 2 and s["blind"] is True
    assert client.ws.db.conn.execute("SELECT reviewer FROM sessions").fetchone()["reviewer"] == BD


def test_the_same_account_works_from_a_second_workspace(tmp_path, fake):
    """Two laptops, one Supabase project: sign up on one, sign in on the other."""
    laptop_a = TestClient(create_app(Workspace(tmp_path / "a"), accounts=SupabaseAccounts(URL, KEY, http=fake)))
    laptop_b = TestClient(create_app(Workspace(tmp_path / "b"), accounts=SupabaseAccounts(URL, KEY, http=fake)))
    signup(laptop_a)
    assert signin(laptop_b).status_code == 200


def test_wrong_password_and_unknown_account_look_the_same(client, fake):
    signup(client)
    wrong, unknown = signin(client, BD, "not-the-password"), signin(client, "nobody@bd.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_the_ui_is_told_where_accounts_live(client, tmp_path):
    assert client.get("/api/sessions/current").json()["accounts"] == "supabase"
    local = TestClient(create_app(Workspace(tmp_path / "local")))
    assert local.get("/api/sessions/current").json()["accounts"] == "local"


def test_no_password_reset_routes_exist(client):
    """Nothing in Kaizen sends email: a forgotten password is set by an administrator in Supabase."""
    assert client.post("/api/auth/forgot", json={"email": BD}).status_code in (404, 405)
    assert client.post("/api/auth/reset", json={"access_token": "x", "password": PW}).status_code in (404, 405)


def test_supabase_unreachable_is_a_clear_503(client, fake):
    fake.down = True
    r = signin(client)
    assert r.status_code == 503 and "internet" in r.json()["detail"]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (429, {"error_code": "over_request_rate_limit", "msg": "slow down"}, 429),
        (400, {"error_code": "email_address_not_authorized", "msg": "Email address not authorized"}, 503),  # tried to send an email
        (401, {"message": "Invalid API key"}, 503),
        (500, {"msg": "boom"}, 503),
    ],
)
def test_supabase_failures_become_readable_errors(tmp_path, status, body, expected):
    backend = SupabaseAccounts(URL, KEY, http=lambda *a: (status, body))
    c = TestClient(create_app(Workspace(tmp_path / "ws"), accounts=backend))
    r = signup(c)
    assert r.status_code == expected and r.json()["detail"]


# ---- configuration ------------------------------------------------------------------------------


def _jwt(role: str) -> str:
    part = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJIUzI1NiJ9.{part}.sig"


def test_secret_keys_are_refused():
    assert is_secret_key("sb_secret_abc") and is_secret_key(_jwt("service_role"))
    assert not is_secret_key("sb_publishable_abc") and not is_secret_key(_jwt("anon"))
    with pytest.raises(ValueError, match="service_role"):
        validate(URL, _jwt("service_role"))


@pytest.mark.parametrize("url", ["abcdefgh.supabase.co", "http://abcdefgh.supabase.co", "https://"])
def test_the_url_must_be_https(url):
    with pytest.raises(ValueError):
        validate(url, KEY)


def test_a_local_supabase_over_http_is_allowed():
    assert validate("http://127.0.0.1:54321/", KEY) == ("http://127.0.0.1:54321", KEY)


def test_backend_selection(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIZEN_SUPABASE_URL", raising=False)
    monkeypatch.delenv("KAIZEN_SUPABASE_KEY", raising=False)
    ws = Workspace(tmp_path / "ws")
    assert isinstance(accounts_for(ws.db), LocalAccounts), "local by default, so the offline demo keeps working"
    save_config(ws.db, URL + "/", KEY, by="test")
    chosen = accounts_for(ws.db)
    assert isinstance(chosen, SupabaseAccounts) and chosen.url == URL
    monkeypatch.setenv("KAIZEN_SUPABASE_URL", "https://other.supabase.co")
    monkeypatch.setenv("KAIZEN_SUPABASE_KEY", KEY)
    assert accounts_for(ws.db).url == "https://other.supabase.co", "the environment wins over the saved setting"
    monkeypatch.delenv("KAIZEN_SUPABASE_KEY")
    with pytest.raises(ValueError, match="both"):
        accounts_for(ws.db)


def test_cli_switches_between_supabase_and_local(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIZEN_SUPABASE_URL", raising=False)
    monkeypatch.delenv("KAIZEN_SUPABASE_KEY", raising=False)
    w = ["--workspace", str(tmp_path / "ws")]
    runner = CliRunner()
    assert "local" in runner.invoke(cli, [*w, "auth", "show"]).output
    bad = runner.invoke(cli, [*w, "auth", "supabase", URL, "sb_secret_nope"])
    assert bad.exit_code != 0 and "service_role" in bad.output
    assert runner.invoke(cli, [*w, "auth", "supabase", URL, KEY]).exit_code == 0
    assert URL in runner.invoke(cli, [*w, "auth", "show"]).output
    users = runner.invoke(cli, [*w, "users", "list"])
    assert users.exit_code == 1 and "Supabase dashboard" in users.output
    assert runner.invoke(cli, [*w, "auth", "local"]).exit_code == 0
    assert "local" in runner.invoke(cli, [*w, "auth", "show"]).output
