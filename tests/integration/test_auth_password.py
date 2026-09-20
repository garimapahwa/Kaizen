"""Sign-up and sign-in: only BD addresses, passwords stored as scrypt, one door into a session.

Every test gets its own workspace and its own app, so the lockout counter starts clean.
"""

import pytest
from fastapi.testclient import TestClient

from kaizen.api.app import create_app
from kaizen.review.sessions import MAX_FAILED, MIN_PASSWORD, UserStore, hash_password, verify_password
from kaizen.workspace import Workspace

BD = "dharma.reddy@bd.com"
PW = "correct-horse-battery"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("KAIZEN_ALLOWED_DOMAINS", raising=False)
    ws = Workspace(tmp_path / "ws")
    c = TestClient(create_app(ws))
    c.ws = ws
    return c


def signup(client, email=BD, password=PW):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def signin(client, email=BD, password=PW, slot=1, **extra):
    return client.post("/api/auth/signin", json={"email": email, "password": password, "slot": slot, **extra})


# ---- who may sign up ----------------------------------------------------------------------------


@pytest.mark.parametrize("email", ["user@gmail.com", "user@bd.com.evil.io", "user@notbd.com", "nobody", "@bd.com"])
def test_only_bd_addresses_may_sign_up(client, email):
    r = signup(client, email)
    assert r.status_code == 400 and r.json()["detail"] == "Only BD email addresses can sign in."


def test_a_lookalike_domain_cannot_sign_in_either(client):
    signup(client)
    r = signin(client, "user@bd.com.evil.io")
    assert r.status_code == 400 and r.json()["detail"] == "Only BD email addresses can sign in."


def test_the_allowed_domain_set_is_configurable(client, monkeypatch):
    monkeypatch.setenv("KAIZEN_ALLOWED_DOMAINS", "bd.com, aad.example")
    assert signup(client, "someone@aad.example").status_code == 200
    assert signup(client, "someone@gmail.com").status_code == 400


def test_a_short_password_is_refused(client):
    r = signup(client, BD, "short")
    assert r.status_code == 400 and str(MIN_PASSWORD) in r.json()["detail"]
    assert signin(client, BD, "short").status_code == 401, "nothing was created"


def test_an_address_cannot_be_registered_twice(client):
    assert signup(client).status_code == 200
    again = signup(client, BD, "a-different-password")
    assert again.status_code == 409
    assert signin(client, BD, PW).status_code == 200, "the original password still works"


# ---- the happy path -----------------------------------------------------------------------------


def test_sign_up_then_sign_in_opens_a_session(client):
    assert signup(client).json() == {"ok": True, "email": BD}
    r = signin(client)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["reviewer"] == BD and s["slot"] == 1 and s["blind"] is False
    assert "token" not in s, "the token belongs in the cookie, not the response body"
    assert client.get("/api/sessions/current").json()["session"]["reviewer"] == BD


def test_the_address_is_normalised(client):
    signup(client, "Dharma.Reddy@BD.com  ")
    assert signin(client, "DHARMA.REDDY@bd.com").json()["reviewer"] == BD


def test_slot_two_is_blind_by_policy(client):
    signup(client)
    assert signin(client, slot=2).json()["blind"] is True


def test_an_invalid_slot_is_refused(client):
    signup(client)
    assert signin(client, slot=7).status_code == 400


# ---- credentials that must not work ---------------------------------------------------------------


def test_a_wrong_password_is_refused(client):
    signup(client)
    r = signin(client, password="not-the-password")
    assert r.status_code == 401 and r.json()["detail"] == "That email address and password do not match an account."
    assert client.get("/api/sessions/current").json()["session"] is None


def test_an_unknown_account_gives_the_same_answer_as_a_wrong_password(client):
    """Otherwise the endpoint reports which BD addresses have accounts here."""
    signup(client)
    unknown = signin(client, "someone.else@bd.com")
    wrong = signin(client, BD, "not-the-password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_repeated_failures_lock_the_account_briefly(client):
    signup(client)
    for _ in range(MAX_FAILED):
        assert signin(client, password="wrong").status_code == 401
    locked = signin(client, password=PW)
    assert locked.status_code == 429, "the right password is refused while locked out"
    assert "minute" in locked.json()["detail"]


def test_a_successful_sign_in_clears_the_failure_count(client):
    signup(client)
    for _ in range(MAX_FAILED - 1):
        signin(client, password="wrong")
    assert signin(client).status_code == 200
    for _ in range(MAX_FAILED - 1):
        assert signin(client, password="wrong").status_code == 401
    assert signin(client).status_code == 200, "the counter restarted after the good sign-in"


# ---- password storage ---------------------------------------------------------------------------


def test_passwords_are_stored_as_salted_scrypt_not_plaintext(client):
    signup(client)
    row = client.ws.db.conn.execute("SELECT password_hash FROM users WHERE email = ?", (BD,)).fetchone()
    stored = row["password_hash"]
    assert PW not in stored
    assert stored.startswith("scrypt$")
    assert verify_password(PW, stored) and not verify_password("wrong", stored)


def test_the_same_password_hashes_differently_each_time(client):
    """A per-account salt: two reviewers with the same password must not share a hash."""
    assert hash_password(PW) != hash_password(PW)


def test_a_corrupt_hash_never_authenticates():
    for junk in ("", "plaintext", "scrypt$bad", "md5$1$1$1$aa$bb"):
        assert verify_password(PW, junk) is False


# ---- the reset path -----------------------------------------------------------------------------


def test_clearing_an_account_lets_the_reviewer_sign_up_again(client):
    """The forgotten-password path: an admin clears the account, the reviewer chooses a new password."""
    signup(client)
    assert UserStore(client.ws.db).delete(BD) is True
    assert signin(client, password=PW).status_code == 401, "the old password stops working"
    assert signup(client, BD, "a-brand-new-password").status_code == 200
    assert signin(client, BD, "a-brand-new-password").status_code == 200


def test_clearing_an_account_keeps_decisions(client):
    """Decisions are recorded against the address, not against a row in the users table."""
    signup(client)
    client.ws.db.conn.execute(
        "INSERT INTO decisions (run_id, row_id, reviewer_slot, reviewer_name, decision, comment, decided_at, blind) VALUES (?,?,?,?,?,?,?,0)",
        ("run-x", "row-1", 1, BD, "ACCEPT", "", "2026-01-01T00:00:00+00:00"),
    )
    client.ws.db.conn.commit()
    UserStore(client.ws.db).delete(BD)
    kept = client.ws.db.conn.execute("SELECT reviewer_name FROM decisions WHERE row_id = 'row-1'").fetchone()
    assert kept["reviewer_name"] == BD


# ---- the old door -------------------------------------------------------------------------------


def test_the_legacy_session_route_is_closed(client):
    r = client.post("/api/sessions", json={"reviewer": "Dharma", "slot": 1})
    assert r.status_code == 403 and r.json()["detail"] == "Use /api/auth/signin"
    assert client.get("/api/sessions/current").json()["session"] is None


def test_review_endpoints_still_refuse_an_anonymous_caller(client):
    assert client.get("/api/runs/run-nope/results").status_code == 401
