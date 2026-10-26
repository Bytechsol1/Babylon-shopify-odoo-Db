"""Logins (AUTH=supabase): only signed-in, allowed users reach the API, and the audit log says who acted."""
import time
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from app import auth, db
from app.config import settings
from app.main import app

URL = "https://example.supabase.co"
KEY = ec.generate_private_key(ec.SECP256R1())


class FakeJWKS:
    """Stands in for Supabase's public signing keys."""
    def get_signing_key_from_jwt(self, token):
        return type("K", (), {"key": KEY.public_key()})()


def token(email="packer@babylonllc.com", exp=3600, key=KEY, aud="authenticated", iss=URL + "/auth/v1"):
    now = int(time.time())
    return jwt.encode({"sub": "u1", "email": email, "aud": aud, "iss": iss, "iat": now, "exp": now + exp},
                      key, algorithm="ES256", headers={"kid": "k1"})


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "auth", "supabase")
    monkeypatch.setattr(settings, "supabase_url", URL)
    monkeypatch.setattr(settings, "supabase_anon_key", "anon")
    monkeypatch.setattr(settings, "allowed_emails", {"@babylonllc.com", "cody@example.com"})
    monkeypatch.setattr(auth, "_jwks", FakeJWKS())
    return TestClient(app)


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def test_the_api_needs_a_sign_in(client):
    assert client.get("/api/summary").status_code == 401
    assert client.get("/api/summary", headers=bearer("not-a-token")).status_code == 401


def test_a_signed_in_allowed_user_gets_in(client):
    assert client.get("/api/summary", headers=bearer(token())).status_code == 200
    assert client.get("/api/summary", headers=bearer(token("cody@example.com"))).status_code == 200


def test_an_account_not_on_the_list_is_refused(client):
    r = client.get("/api/summary", headers=bearer(token("stranger@gmail.com")))
    assert r.status_code == 401 and "not allowed" in r.json()["detail"]


def test_expired_forged_or_foreign_tokens_are_refused(client):
    other = ec.generate_private_key(ec.SECP256R1())
    for bad in (token(exp=-120), token(key=other), token(aud="anon"), token(iss="https://evil.supabase.co/auth/v1")):
        assert client.get("/api/summary", headers=bearer(bad)).status_code == 401


def test_the_page_and_its_config_stay_public(client):
    assert client.get("/").status_code == 200
    cfg = client.get("/api/public-config").json()
    assert cfg == {"auth": "supabase", "supabase_url": URL, "supabase_anon_key": "anon"}


def test_the_health_check_needs_no_login(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_the_audit_log_records_who_acted(client):
    h = bearer(token("packer@babylonllc.com"))
    o = client.post("/api/dev/order", json={"items": [{"sku": "BAB-LIP-01", "qty": 1}]}, headers=h).json()
    client.post("/api/orders/" + o["id"].replace("/", "%2F") + "/scan", json={"code": "L2609-L"}, headers=h)
    actors = {e["kind"]: e["actor"] for e in db.events(o["id"])}
    assert actors["scan"] == "packer@babylonllc.com"


def test_auth_cannot_be_switched_on_without_an_allow_list(monkeypatch):
    from app.config import Settings
    monkeypatch.setenv("AUTH", "supabase")
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    with pytest.raises(ValueError, match="ALLOWED_EMAILS"):
        Settings()
