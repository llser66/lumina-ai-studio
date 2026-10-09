import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from server.db import QuotaExceeded
from server.schemas import GenerationSpec
from tests.conftest import register


def enqueue(client, prompt="A blue bottle on a mint pedestal", key=None, **extra):
    return client.post(
        "/api/jobs",
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
        json={"prompt": prompt, **extra},
    )


def test_guest_and_unconfigured_service(client, settings):
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/health").json()["provider_ready"]
    register(client)
    client.app.state.provider_ready = False
    assert enqueue(client).status_code == 503
    assert client.app.state.db.all("SELECT * FROM jobs") == []


def test_login_logout_session_and_cookie_security(client):
    user = register(client)
    session = next(cookie for cookie in client.cookies.jar if cookie.name == "lumina_session")
    assert "HttpOnly" in session._rest
    assert session._rest["SameSite"] == "strict"
    result = client.get("/api/auth/me")
    assert result.json()["user"]["id"] == user["id"]
    assert result.json()["quota"]["remaining"] == 2
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"email": user["email"], "password": "wrong-password"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login", json={"email": "missing@example.com", "password": "wrong-password"}
        ).status_code
        == 401
    )
    result = client.post(
        "/api/auth/login", json={"email": user["email"], "password": "test-only-password-123"}
    )
    assert result.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_password_not_echoed_and_registration_disabled(client, settings):
    result = client.post(
        "/api/auth/register", json={"name": "AA", "email": "test@example.com", "password": "XX"}
    )
    assert result.status_code == 422
    assert "XX" not in result.text
    settings.registration_enabled = False
    assert (
        client.post(
            "/api/auth/register",
            json={"name": "AA", "email": "test@example.com", "password": "valid-test-password"},
        ).status_code
        == 403
    )


def test_csrf_origin_and_custom_header(client, user):
    assert enqueue(client).status_code == 202
    assert (
        client.post(
            "/api/jobs",
            headers={"X-CSRF-Token": "", "Idempotency-Key": str(uuid.uuid4())},
            json={"prompt": "good valid prompt"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/jobs",
            headers={"Origin": "https://evil.example", "Idempotency-Key": str(uuid.uuid4())},
            json={"prompt": "good valid prompt"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/auth/login",
            headers={"X-Lumina-Client": ""},
            json={"email": user["email"], "password": "test-only-password-123"},
        ).status_code
        == 403
    )


def test_idempotency_conflict_and_quota(client, user):
    key = str(uuid.uuid4())
    first = enqueue(client, key=key)
    assert first.status_code == 202
    duplicate = enqueue(client, key=key)
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]
    assert enqueue(client, key=key, prompt="A different forest").status_code == 409
    assert enqueue(client).status_code == 202
    assert enqueue(client).status_code == 429
    assert client.get("/api/auth/me").json()["quota"]["used"] == 2
    assert first.json()["events"][0]["stage"] == "queued"
    assert "fingerprint" not in first.json()
    assert "user_id" not in first.json()


def test_atomic_budget_under_concurrent_enqueue(client, user):
    db = client.app.state.db

    def create(_):
        try:
            return db.create_job(
                user["id"],
                GenerationSpec(prompt="A real creative brief"),
                str(uuid.uuid4()),
                "test-model",
                2,
            )[0]["id"]
        except QuotaExceeded:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))
    assert len([result for result in results if result]) == 2
    assert db.quota(user["id"], 2)["used"] == 2


def test_queued_cancel_refunds_quota_and_active_cancel_rejected(client, user):
    job = enqueue(client).json()
    assert client.post("/api/jobs/" + job["id"] + "/cancel").status_code == 204
    assert client.get("/api/auth/me").json()["quota"]["used"] == 0
    assert client.post("/api/jobs/" + job["id"] + "/cancel").status_code == 409
    next_job = enqueue(client).json()
    assert client.app.state.db.claim("test-owner")["id"] == next_job["id"]
    assert client.post("/api/jobs/" + next_job["id"] + "/cancel").status_code == 409
    assert client.delete("/api/jobs/" + next_job["id"]).status_code == 409


def test_job_and_asset_ownership(client, user, png):
    asset = client.post("/api/assets", content=png, headers={"Content-Type": "image/png"}).json()
    assert client.get(asset["image_url"]).status_code == 200
    assert (
        "attachment"
        in client.get(asset["image_url"] + "?download=true").headers["content-disposition"]
    )
    job = enqueue(client, reference_id=asset["id"]).json()
    other = TestClient(client.app, headers={"X-Lumina-Client": "web"})
    register(other, email="other@example.com")
    assert other.get(asset["image_url"]).status_code == 404
    assert other.get("/api/jobs/" + job["id"]).status_code == 404
    assert other.get("/api/jobs/" + job["id"] + "/events").status_code == 404
    assert enqueue(other, reference_id=asset["id"]).status_code == 409
    assert other.patch("/api/jobs/" + job["id"], json={"favorite": True}).status_code == 404
    assert other.delete("/api/jobs/" + job["id"]).status_code == 404
    assert other.post("/api/jobs/" + job["id"] + "/cancel").status_code == 404
    other.close()


def test_upload_format_and_streaming_size_limits(client, user):
    assert (
        client.post(
            "/api/assets", content=b"<svg/>", headers={"Content-Type": "image/svg+xml"}
        ).status_code
        == 415
    )
    assert (
        client.post(
            "/api/assets", content=b"fake PNG", headers={"Content-Type": "image/png"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/assets",
            content=b"x" * (8 * 1024 * 1024 + 1),
            headers={"Content-Type": "image/png"},
        ).status_code
        == 413
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"size": "99999x99999"},
        {"quality": "ultra"},
        {"style": "evil"},
        {"n": 100},
        {"reference_id": "../../etc/passwd"},
        {"prompt": "  "},
    ],
)
def test_generation_input_validation(client, user, extra):
    assert enqueue(client, **extra).status_code == 422


def test_invalid_job_id_and_missing_request_key(client, user):
    assert client.get("/api/jobs/not-a-uuid").status_code == 422
    assert client.post("/api/jobs", json={"prompt": "A valid prompt"}).status_code == 422
    assert client.get("/api/jobs/" + str(uuid.uuid4())).status_code == 404
    assert client.get("/api/assets/" + str(uuid.uuid4())).status_code == 404


def test_favorites_pagination_archive_retains_budget_and_terminal_sse(client, user):
    first = enqueue(client).json()
    second = enqueue(client, prompt="A serene concrete garden").json()
    assert client.patch("/api/jobs/" + first["id"], json={"favorite": True}).status_code == 200
    assert len(client.get("/api/jobs?favorites=true").json()["items"]) == 1
    page = client.get("/api/jobs?limit=1").json()
    assert page["total"] == 2 and page["next_offset"] == 1
    assert len(client.get("/api/jobs?limit=1&offset=1").json()["items"]) == 1
    client.app.state.db.fail(first["id"], "provider_unknown", "Result is unknown", uncertain=True)
    events = client.get("/api/jobs/" + first["id"] + "/events")
    assert events.status_code == 200 and "event: job" in events.text and "uncertain" in events.text
    assert client.delete("/api/jobs/" + first["id"]).status_code == 204
    assert client.get("/api/jobs/" + first["id"]).status_code == 404
    assert client.get("/api/auth/me").json()["quota"]["used"] == 2
    assert client.get("/api/jobs").json()["items"][0]["id"] == second["id"]


def test_login_rate_limited(client):
    for _ in range(10):
        assert (
            client.post(
                "/api/auth/login",
                json={"email": "missing@example.com", "password": "wrong-password"},
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login", json={"email": "missing@example.com", "password": "wrong-password"}
        ).status_code
        == 429
    )


def test_security_headers_and_schema(client):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert uuid.UUID(response.headers["x-request-id"])
    assert "/api/jobs" in client.get("/api/openapi.json").json()["paths"]
    assert client.get("/api/this-does-not-exist").status_code == 404


def test_render_public_origin_can_register_but_other_origins_cannot(client, monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://lumina-example.onrender.com")
    payload = {"name": "Creator", "email": "render@example.com", "password": "test-only-password-123"}
    assert (
        client.post("/api/auth/register", json=payload, headers={"Origin": "https://other.example"}).status_code
        == 403
    )
    assert (
        client.post("/api/auth/register", json=payload, headers={"Origin": "https://lumina-example.onrender.com"}).status_code
        == 201
    )


def test_global_quota_applies_across_accounts(client, user, settings):
    settings.global_daily_generation_limit = 1
    assert enqueue(client).status_code == 202
    other = TestClient(client.app, headers={"X-Lumina-Client": "web"})
    register(other, email="global-budget@example.com")
    assert enqueue(other).status_code == 429
    other.close()


def test_large_json_and_chunked_body_are_bounded(client):
    payload = b'{"email":"' + b"x" * 33000 + b'"}'
    assert (
        client.post(
            "/api/auth/login", content=payload, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/api/auth/login",
            content=iter([payload[:10000], payload[10000:]]),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
