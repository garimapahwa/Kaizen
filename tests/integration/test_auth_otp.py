"""Email sign-in: only BD addresses, only with a code that was actually mailed, and only once.

Every test gets its own workspace and its own app, so the rate limit and the attempt counter start clean.
"""

import hashlib

import pytest
from fastapi.testclient import TestClient

import kaizen.api.app as app_module
from kaizen.api.app import create_app
from kaizen.review.sessions import OtpStore
from kaizen.workspace import Workspace

BD = "dharma.reddy@bd.com"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An app with the mailer stubbed, so the test can read the code the server generated."""
    sent: dict[str, str] = {}
    monkeypatch.setattr(app_module, "send_otp_email", lambda to, code: sent.__setitem__(to, code))
    monkeypatch.delenv("KAIZEN_OTP_DEV_MODE", raising=False)
    monkeypatch.delenv("KAIZEN_OTP_RATE_LIMIT", raising=False)
    monkeypatch.delenv("KAIZEN_ALLOWED_DOMAINS", raising=False)
    ws = Workspace(tmp_path / "ws")
    c = TestClient(create_app(ws))
    c.sent = sent
    c.ws = ws
    return c


def request_code(client, email: str = BD):
    r = client.post("/api/auth/request-otp", json={"email": email})
    assert r.status_code == 200, r.text
    return client.sent[email]


# ---- who may sign in ----------------------------------------------------------------------------


@pytest.mark.parametrize("email", ["user@gmail.com", "user@bd.com.evil.io", "user@notbd.com", "nobody", "@bd.com"])
def test_only_bd_addresses_may_request_a_code(client, email):
    r = client.post("/api/auth/request-otp", json={"email": email})
    assert r.status_code == 400 and r.json()["detail"] == "Only BD email addresses can sign in."
    assert email not in client.sent, "no code is generated for an address that cannot sign in"


def test_a_lookalike_domain_cannot_verify_either(client):
    """The check is on both routes: a code obtained for a BD address cannot be spent on another domain."""
    code = request_code(client)
    r = client.post("/api/auth/verify-otp", json={"email": "user@bd.com.evil.io", "code": code, "slot": 1})
    assert r.status_code == 400 and r.json()["detail"] == "Only BD email addresses can sign in."


def test_the_allowed_domain_set_is_configurable(client, monkeypatch):
    monkeypatch.setenv("KAIZEN_ALLOWED_DOMAINS", "bd.com, aad.example")
    assert client.post("/api/auth/request-otp", json={"email": "someone@aad.example"}).status_code == 200
    assert client.post("/api/auth/request-otp", json={"email": "someone@gmail.com"}).status_code == 400


# ---- the happy path -----------------------------------------------------------------------------


def test_a_mailed_code_opens_a_session(client):
    code = request_code(client)
    assert len(code) == 6 and code.isdigit()
    r = client.post("/api/auth/verify-otp", json={"email": BD, "code": code, "slot": 1})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["reviewer"] == BD and s["slot"] == 1 and s["blind"] is False
    assert "token" not in s, "the token belongs in the cookie, not the response body"
    assert client.get("/api/sessions/current").json()["session"]["reviewer"] == BD


def test_the_response_never_carries_the_code(client):
    r = client.post("/api/auth/request-otp", json={"email": BD})
    assert r.json() == {"ok": True}
    assert client.sent[BD] not in r.text


def test_a_code_works_once(client):
    code = request_code(client)
    assert client.post("/api/auth/verify-otp", json={"email": BD, "code": code, "slot": 1}).status_code == 200
    again = client.post("/api/auth/verify-otp", json={"email": BD, "code": code, "slot": 2})
    assert again.status_code == 401 and again.json()["detail"] == "Invalid or expired code."


def test_slot_two_is_blind_by_policy(client):
    assert client.post("/api/auth/verify-otp", json={"email": BD, "code": request_code(client), "slot": 2}).json()["blind"] is True


# ---- codes that must not work -------------------------------------------------------------------


def test_a_wrong_code_is_refused(client):
    code = request_code(client)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    r = client.post("/api/auth/verify-otp", json={"email": BD, "code": wrong, "slot": 1})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid or expired code."
    assert client.get("/api/sessions/current").json()["session"] is None


def test_an_expired_code_is_refused(client):
    """Stored straight through the store with a TTL already in the past."""
    otps = OtpStore(client.ws.db)
    otps.put(BD, hashlib.sha256(b"123456").hexdigest(), ttl_seconds=-5)
    r = client.post("/api/auth/verify-otp", json={"email": BD, "code": "123456", "slot": 1})
    assert r.status_code == 401 and r.json()["detail"] == "Invalid or expired code."


def test_a_code_dies_after_five_wrong_guesses(client):
    code = request_code(client)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for _ in range(OtpStore.MAX_ATTEMPTS):
        assert client.post("/api/auth/verify-otp", json={"email": BD, "code": wrong, "slot": 1}).status_code == 401
    assert client.post("/api/auth/verify-otp", json={"email": BD, "code": code, "slot": 1}).status_code == 401, "the right code no longer works once the attempts are spent"


def test_requests_are_rate_limited_per_address(client):
    for _ in range(3):
        assert client.post("/api/auth/request-otp", json={"email": BD}).status_code == 200
    r = client.post("/api/auth/request-otp", json={"email": BD})
    assert r.status_code == 429
    assert client.post("/api/auth/request-otp", json={"email": "someone.else@bd.com"}).status_code == 200, "the limit is per address"


# ---- the old door -------------------------------------------------------------------------------


def test_the_legacy_session_route_is_closed(client):
    r = client.post("/api/sessions", json={"reviewer": "Dharma", "slot": 1})
    assert r.status_code == 403 and r.json()["detail"] == "Use /api/auth/verify-otp"
    assert client.get("/api/sessions/current").json()["session"] is None


def test_dev_mode_reopens_the_legacy_route_and_skips_sending(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("KAIZEN_OTP_DEV_MODE", "1")
    monkeypatch.delenv("KAIZEN_SMTP_HOST", raising=False)
    c = TestClient(create_app(Workspace(tmp_path / "ws")))
    assert c.post("/api/sessions", json={"reviewer": "Dharma", "slot": 1}).status_code == 200
    with caplog.at_level("WARNING", logger="kaizen.api.mailer"):
        assert c.post("/api/auth/request-otp", json={"email": BD}).status_code == 200
    assert "sign-in code" in caplog.text, "dev mode logs the code instead of mailing it"


def test_unconfigured_smtp_is_reported_rather_than_silently_dropped(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIZEN_OTP_DEV_MODE", raising=False)
    monkeypatch.delenv("KAIZEN_SMTP_HOST", raising=False)
    monkeypatch.delenv("KAIZEN_SMTP_FROM", raising=False)
    c = TestClient(create_app(Workspace(tmp_path / "ws")), raise_server_exceptions=False)
    assert c.post("/api/auth/request-otp", json={"email": BD}).status_code == 502
