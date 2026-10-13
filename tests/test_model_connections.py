import base64
import socket
import time

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from server.main import create_app
from server.model_provider import ModelRouter
from server.provider import ProviderFailure
from server.vault import CredentialVault
from server.worker import Worker
from server.workflow import build_graph
from tests.conftest import register
from tests.model_bridge_fixture import ModelBridgeFixture
from tests.test_api import enqueue


@pytest.fixture
def model_client(settings, png):
    bridge = ModelBridgeFixture(png)
    app = create_app(settings, start_worker=False, bridge=bridge)
    with TestClient(app, headers={"X-Lumina-Client": "web"}) as client:
        register(client)
        yield client, bridge


def connect(client, provider="openrouter", name="My model account"):
    response = client.post(
        "/api/models/connections",
        json={"provider": provider, "name": name, "api_key": "test-only-model-key-1234"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def set_image(client, connection):
    configuration = client.get("/api/models/config").json()
    row = next(row for row in configuration["connections"] if row["id"] == connection["id"])
    model = row["image_models"][0]["id"]
    response = client.put(
        "/api/models/preferences", json={"image_connection_id": row["id"], "image_model": model}
    )
    assert response.status_code == 200, response.text
    return model


def test_config_catalog_is_private_and_does_not_expose_credentials(model_client):
    client, _ = model_client
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/models/config").status_code == 401
    connection = connect(client)
    config = client.get("/api/models/config")
    assert config.status_code == 200
    assert config.json()["sdk"] == "@earendil-works/pi-ai"
    assert "test-only-model-key" not in config.text
    assert "credential" not in connection
    row = client.app.state.db.one("SELECT * FROM model_connections WHERE id=?", (connection["id"],))
    assert "test-only-model-key" not in row["credential"]
    assert client.app.state.models.vault.credential(row)["key"] == "test-only-model-key-1234"
    assert config.json()["connections"][0]["image_models"]
    anonymous.close()


def test_reference_only_image_models_cannot_be_saved_as_default(model_client):
    client, _ = model_client
    connection = connect(client)
    config = client.get("/api/models/config").json()
    models = next(row for row in config["connections"] if row["id"] == connection["id"])[
        "image_models"
    ]
    assert any(model["id"] == "inclusionai/ming-image-0.1-design" for model in models)
    assert not any(model["id"] == "inclusionai/ming-image-0.1-design-layer" for model in models)
    response = client.put(
        "/api/models/preferences",
        json={
            "image_connection_id": connection["id"],
            "image_model": "inclusionai/ming-image-0.1-design-layer",
        },
    )
    assert response.status_code == 422


def test_cloudflare_image_connection_is_private_and_requires_account_id(model_client):
    client, _ = model_client
    account_id = "0123456789abcdef0123456789abcdef"
    invalid = client.post(
        "/api/models/connections",
        json={
            "provider": "cloudflare",
            "name": "Cloudflare Free",
            "api_key": "test-only-cloudflare-token",
        },
    )
    assert invalid.status_code == 422
    response = client.post(
        "/api/models/connections",
        json={
            "provider": "cloudflare",
            "name": "Cloudflare Free",
            "api_key": "test-only-cloudflare-token",
            "account_id": account_id,
        },
    )
    assert response.status_code == 201, response.text
    config = client.get("/api/models/config").json()
    row = next(item for item in config["connections"] if item["id"] == response.json()["id"])
    assert [model["id"] for model in row["image_models"]] == [
        "@cf/black-forest-labs/flux-1-schnell"
    ]
    assert row["chat_models"] == []
    assert account_id not in str(config)
    assert "test-only-cloudflare-token" not in str(config)
    stored = client.app.state.db.one("SELECT * FROM model_connections WHERE id=?", (row["id"],))
    assert account_id not in stored["credential"]
    assert client.app.state.models.vault.credential(stored)["account_id"] == account_id
    missing_text = client.put(
        "/api/models/preferences",
        json={"image_connection_id": row["id"], "image_model": row["image_models"][0]["id"]},
    )
    assert missing_text.status_code == 422
    text_connection = connect(client, "deepseek")
    text_row = next(
        item
        for item in client.get("/api/models/config").json()["connections"]
        if item["id"] == text_connection["id"]
    )
    selected = client.put(
        "/api/models/preferences",
        json={
            "image_connection_id": row["id"],
            "image_model": row["image_models"][0]["id"],
            "text_connection_id": text_row["id"],
            "text_model": text_row["chat_models"][0]["id"],
        },
    )
    assert selected.status_code == 200
    assert selected.json()["image_controls"] == {
        "quality": False,
        "exact_size": False,
        "edit": False,
    }


def test_connection_checks_are_explicit_and_read_only(model_client):
    client, bridge = model_client
    connection = connect(client)
    assert bridge.calls == []
    result = client.post("/api/models/connections/" + connection["id"] + "/check")
    assert result.json()["ok"]
    assert [call[0] for call in bridge.calls] == ["check"]
    assert client.get("/api/models/config").json()["connections"][0]["status"] == "verified"
    bridge.fail = {"ok": False, "code": "credentials"}
    assert not client.post("/api/models/connections/" + connection["id"] + "/check").json()["ok"]
    assert client.get("/api/models/config").json()["connections"][0]["status"] == "needs_attention"


def test_connections_preferences_and_oauth_are_account_owned(model_client):
    client, _ = model_client
    connection = connect(client)
    other = TestClient(client.app, headers={"X-Lumina-Client": "web"})
    register(other, email="config-other@example.com")
    assert other.get("/api/models/config").json()["connections"] == []
    assert other.post("/api/models/connections/" + connection["id"] + "/check").status_code == 404
    assert other.delete("/api/models/connections/" + connection["id"]).status_code == 404
    assert (
        other.put(
            "/api/models/preferences",
            json={"image_connection_id": connection["id"], "image_model": "anything"},
        ).status_code
        == 404
    )
    other.close()


def test_model_role_validation_and_revocation_never_falls_back_to_shared_key(model_client):
    client, _ = model_client
    connection = connect(client, "anthropic")
    assert (
        client.put(
            "/api/models/preferences",
            json={"image_connection_id": connection["id"], "image_model": "claude-sonnet"},
        ).status_code
        == 422
    )
    image_connection = connect(client)
    set_image(client, image_connection)
    assert client.get("/api/models/config").json()["image_ready"]
    assert client.delete("/api/models/connections/" + image_connection["id"]).status_code == 204
    assert not client.get("/api/models/config").json()["image_ready"]
    assert enqueue(client).status_code == 404
    raw = client.app.state.db.one(
        "SELECT credential FROM model_connections WHERE id=?", (image_connection["id"],)
    )
    assert raw["credential"] == ""


@pytest.mark.parametrize(
    "body",
    [
        {"provider": "unknown", "name": "AA", "api_key": "valid-test-key"},
        {"provider": "openai", "name": "AA", "api_key": "short"},
        {"provider": "openai", "name": "AA", "api_key": "abc\n123456"},
        {
            "provider": "openai",
            "name": "AA",
            "api_key": "valid-test-key",
            "base_url": "https://127.0.0.1",
        },
    ],
)
def test_configuration_boundary_validation_never_echoes_keys(model_client, body):
    client, _ = model_client
    response = client.post("/api/models/connections", json=body)
    assert response.status_code == 422
    assert body["api_key"] not in response.text


def test_connection_csrf_and_preference_model_ids(model_client):
    client, _ = model_client
    assert (
        client.post(
            "/api/models/connections",
            headers={"X-CSRF-Token": ""},
            json={"provider": "openai", "name": "AA", "api_key": "test-key-valid"},
        ).status_code
        == 403
    )
    connection = connect(client)
    assert (
        client.put(
            "/api/models/preferences",
            json={"image_connection_id": connection["id"], "image_model": "not-in-catalog"},
        ).status_code
        == 422
    )
    assert (
        client.put("/api/models/preferences", json={"image_model": "no-connection"}).status_code
        == 422
    )


def test_encrypted_credentials_cannot_be_transplanted_between_connections(model_client):
    client, _ = model_client
    first = connect(client, name="First account")
    second = connect(client, name="Second account")
    db = client.app.state.db
    first_row = db.one("SELECT * FROM model_connections WHERE id=?", (first["id"],))
    second_row = db.one("SELECT * FROM model_connections WHERE id=?", (second["id"],))
    second_row["credential"] = first_row["credential"]
    with pytest.raises(ProviderFailure):
        client.app.state.models.vault.credential(second_row)


def test_refresh_cannot_restore_revoked_or_wrong_owner_credentials(model_client):
    client, _ = model_client
    connection = connect(client)
    service = client.app.state.models
    row = service.vault.owned(connection["id"], client.get("/api/auth/me").json()["user"]["id"])
    new_credential = {"type": "api_key", "key": "test-only-rotated-key"}
    service.on_event(
        {
            "kind": "credential_update",
            "id": row["id"],
            "user_id": row["user_id"],
            "version": row["version"],
            "credential": new_credential,
        }
    )
    assert (
        service.vault.credential(service.vault.owned(row["id"], row["user_id"])) == new_credential
    )
    service.vault.refresh(
        row["id"], "another-owner", row["version"], {"type": "api_key", "key": "wrong"}
    )
    client.delete("/api/models/connections/" + row["id"])
    service.vault.refresh(row["id"], row["user_id"], row["version"], new_credential)
    assert service.vault.owned(row["id"], row["user_id"]) is None


def test_missing_encryption_key_does_not_silently_replace_existing_store(model_client, settings):
    client, _ = model_client
    connect(client)
    (settings.data_dir / "credentials.key").unlink()
    with pytest.raises(RuntimeError):
        CredentialVault(client.app.state.db, settings.data_dir)
    assert not (settings.data_dir / "credentials.key").exists()


async def test_selected_pi_image_model_is_pinned_and_runs_real_graph(model_client, settings):
    client, bridge = model_client
    connection = connect(client)
    model = set_image(client, connection)
    response = enqueue(client)
    assert response.status_code == 202
    job = response.json()
    assert job["model"] == model
    db = client.app.state.db
    stored = db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    assert stored["image_connection_id"] == connection["id"]
    assert stored["text_connection_id"] == connection["id"]
    router = ModelRouter(client.app.state.models, settings)
    async with AsyncSqliteSaver.from_conn_string(
        str(settings.data_dir / "route-checkpoints.sqlite3")
    ) as saver:
        graph = build_graph(db, router, settings.data_dir / "outputs", saver)
        worker = Worker(db, graph, settings.data_dir / "outputs", router)
        await worker.process(db.claim(worker.owner))
    assert client.get("/api/jobs/" + job["id"]).json()["status"] == "succeeded"
    assert [call[0] for call in bridge.calls] == ["enhance", "moderate", "image"]
    moderate_call = next(call for call in bridge.calls if call[0] == "moderate")
    assert moderate_call[1]["timeout"] == 90
    assert router.jobs == {}
    assert client.get("/api/jobs/" + job["id"]).json()["image_url"]


async def test_rejected_image_key_marks_connection_as_needing_attention(model_client, settings):
    client, bridge = model_client
    connection = connect(client)
    set_image(client, connection)
    client.app.state.db.execute(
        "UPDATE model_connections SET status='verified' WHERE id=?", (connection["id"],)
    )
    original_request = bridge.request

    async def reject_generation(op, request_id=None, **payload):
        if op == "image":
            return {"ok": False, "code": "credentials"}
        return await original_request(op, request_id=request_id, **payload)

    bridge.request = reject_generation
    job = enqueue(client).json()
    db = client.app.state.db
    router = ModelRouter(client.app.state.models, settings)
    async with AsyncSqliteSaver.from_conn_string(
        str(settings.data_dir / "rejected-key-checkpoints.sqlite3")
    ) as saver:
        graph = build_graph(db, router, settings.data_dir / "outputs", saver)
        worker = Worker(db, graph, settings.data_dir / "outputs", router)
        await worker.process(db.claim(worker.owner))
    assert client.get("/api/jobs/" + job["id"]).json()["error_code"] == "credentials"
    assert (
        db.one("SELECT status FROM model_connections WHERE id=?", (connection["id"],))["status"]
        == "needs_attention"
    )


def wait_flow(client, flow_id, expected):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        flow = client.get("/api/models/oauth/" + flow_id).json()
        if expected(flow):
            return flow
        time.sleep(0.03)
    raise AssertionError("OAuth state did not reach expected state")


def test_web_oauth_prompts_complete_and_store_only_redacted_metadata(model_client):
    client, _ = model_client
    start = client.post(
        "/api/models/oauth", json={"provider": "github-copilot", "name": "My subscription"}
    )
    assert start.status_code == 202
    flow = wait_flow(client, start.json()["id"], lambda value: value.get("prompt"))
    assert flow["verification_url"] == "https://github.com/login/device"
    assert flow["user_code"] == "TEST-1234"
    answer = client.post(
        "/api/models/oauth/" + flow["id"] + "/answer",
        json={"prompt_id": flow["prompt"]["id"], "answer": "github.com"},
    )
    assert answer.status_code == 200
    completed = wait_flow(client, flow["id"], lambda value: value["status"] == "connected")
    config = client.get("/api/models/config")
    assert "test-only-access-token" not in config.text
    assert config.json()["connections"][0]["subscription"]
    assert config.json()["connections"][0]["id"] == completed["connection_id"]


def test_oauth_cancellation_owner_and_enterprise_domain_boundary(model_client):
    client, _ = model_client
    start = client.post(
        "/api/models/oauth", json={"provider": "github-copilot", "name": "Cancel subscription"}
    ).json()
    flow = wait_flow(client, start["id"], lambda value: value.get("prompt"))
    other = TestClient(client.app, headers={"X-Lumina-Client": "web"})
    register(other, email="oauth-other@example.com")
    assert other.get("/api/models/oauth/" + flow["id"]).status_code == 404
    assert other.post("/api/models/oauth/" + flow["id"] + "/cancel").status_code == 404
    other.close()
    assert (
        client.post(
            "/api/models/oauth/" + flow["id"] + "/answer",
            json={"prompt_id": flow["prompt"]["id"], "answer": "internal.example"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/models/oauth", json={"provider": "github-copilot", "name": "Second flow"}
        ).status_code
        == 409
    )
    assert client.post("/api/models/oauth/" + flow["id"] + "/cancel").status_code == 204
    assert client.get("/api/models/oauth/" + flow["id"]).json()["status"] == "cancelled"
    assert client.get("/api/models/config").json()["connections"] == []


def test_oauth_cancel_releases_state_when_bridge_abort_fails(model_client):
    client, bridge = model_client
    start = client.post(
        "/api/models/oauth", json={"provider": "github-copilot", "name": "Bridge failure"}
    ).json()
    wait_flow(client, start["id"], lambda value: value.get("prompt"))
    original_request = bridge.request

    async def fail_cancel(op, request_id=None, **payload):
        if op == "cancel":
            raise RuntimeError("bridge unavailable")
        return await original_request(op, request_id=request_id, **payload)

    bridge.request = fail_cancel
    assert client.post("/api/models/oauth/" + start["id"] + "/cancel").status_code == 204
    assert client.get("/api/models/oauth/" + start["id"]).json()["status"] == "cancelled"
    retry = client.post(
        "/api/models/oauth", json={"provider": "github-copilot", "name": "Retry after cancel"}
    )
    assert retry.status_code == 202


def test_oauth_reset_clears_all_current_user_flows(model_client):
    client, _ = model_client
    start = client.post(
        "/api/models/oauth", json={"provider": "github-copilot", "name": "Reset flow"}
    ).json()
    wait_flow(client, start["id"], lambda value: value.get("prompt"))
    reset = client.post("/api/models/oauth/reset")
    assert reset.status_code == 200 and reset.json() == {"cancelled": 1}
    assert client.get("/api/models/oauth/" + start["id"]).json()["status"] == "cancelled"
    retry = client.post(
        "/api/models/oauth", json={"provider": "github-copilot", "name": "After reset"}
    )
    assert retry.status_code == 202


def test_expired_oauth_flow_is_reaped_before_concurrency_check(model_client):
    client, _ = model_client
    start = client.post(
        "/api/models/oauth", json={"provider": "github-copilot", "name": "Expired flow"}
    ).json()
    wait_flow(client, start["id"], lambda value: value.get("prompt"))
    client.app.state.models.flows[start["id"]]["expires_at"] = time.time() - 1
    retry = client.post(
        "/api/models/oauth", json={"provider": "github-copilot", "name": "After expiry"}
    )
    assert retry.status_code == 202


def test_logout_cancels_pending_authorization_and_unsupported_provider(model_client):
    client, _ = model_client
    assert (
        client.post(
            "/api/models/oauth", json={"provider": "google", "name": "No OAuth"}
        ).status_code
        == 422
    )
    start = client.post(
        "/api/models/oauth", json={"provider": "github-copilot", "name": "Logout test"}
    ).json()
    wait_flow(client, start["id"], lambda value: value.get("prompt"))
    assert client.post("/api/auth/logout").status_code == 204
    assert client.app.state.models.flows[start["id"]]["status"] == "cancelled"


@pytest.mark.parametrize(
    "provider,model", [("deepseek", "deepseek-flash"), ("qwen", "qwen-vl-plus")]
)
def test_new_api_key_providers_save_preferred_vision_model(model_client, provider, model):
    client, bridge = model_client
    response = client.post(
        "/api/models/connections",
        json={
            "provider": provider,
            "name": "Vision account",
            "api_key": "test-only-vision-key",
            "preferred_model": model,
        },
    )
    assert response.status_code == 201, response.text
    config = client.get("/api/models/config").json()
    assert config["preferences"]["text_connection_id"] == response.json()["id"]
    assert config["preferences"]["text_model"] == model
    assert config["connections"][0]["image_models"] == []
    assert "test-only-vision-key" not in str(config)
    assert bridge.calls == []


def test_custom_model_saved_and_endpoint_resolved_again_before_use(model_client, monkeypatch):
    client, bridge = model_client
    addresses = ["8.8.8.8"]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses
        ],
    )
    body = {
        "provider": "custom",
        "name": "Custom vision",
        "api_key": "test-only-custom-key",
        "base_url": "https://model.example.com/v1/",
        "model_id": "my-vision",
        "model_name": "My Vision",
        "supports_image": True,
    }
    created = client.post("/api/models/connections", json=body)
    assert created.status_code == 201, created.text
    config = client.get("/api/models/config").json()
    assert config["connections"][0]["chat_models"][0]["input"] == ["text", "image"]
    assert config["preferences"]["text_model"] == "my-vision"
    assert (
        client.post("/api/models/connections/" + created.json()["id"] + "/check").status_code == 200
    )
    assert bridge.calls[-1][1]["connection"]["endpoint_ips"] == ["8.8.8.8"]
    assert bridge.calls[-1][1]["connection"]["base_url"] == "https://model.example.com/v1"
    addresses[:] = ["127.0.0.1"]
    count = len(bridge.calls)
    assert (
        client.post("/api/models/connections/" + created.json()["id"] + "/check").status_code == 422
    )
    assert len(bridge.calls) == count


def test_qwen_workspace_endpoint_is_pinned_and_revalidated(model_client, monkeypatch):
    client, bridge = model_client
    addresses = ["8.8.4.4"]
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses
        ],
    )
    response = client.post(
        "/api/models/connections",
        json={
            "provider": "qwen",
            "name": "Qwen workspace",
            "api_key": "test-only-qwen-key",
            "base_url": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/",
            "preferred_model": "qwen-vl-plus",
        },
    )
    assert response.status_code == 201, response.text
    result = client.post("/api/models/connections/" + response.json()["id"] + "/check")
    assert result.status_code == 200
    connection = bridge.calls[-1][1]["connection"]
    assert (
        connection["base_url"]
        == "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert connection["endpoint_ips"] == ["8.8.4.4"]
    addresses[:] = ["10.0.0.1"]
    count = len(bridge.calls)
    assert (
        client.post("/api/models/connections/" + response.json()["id"] + "/check").status_code
        == 422
    )
    assert len(bridge.calls) == count


def test_current_manual_callback_completes_codex_model_and_rejects_other_owner(model_client):
    client, _ = model_client
    start = client.post(
        "/api/models/oauth",
        json={
            "provider": "openai-codex",
            "name": "Codex subscription",
            "preferred_model": "gpt-6-sol",
        },
    )
    assert start.status_code == 202
    flow = wait_flow(client, start.json()["id"], lambda value: value.get("manual_callback"))
    assert flow["auth_url"].startswith("https://auth.openai.com/")
    other = TestClient(client.app, headers={"X-Lumina-Client": "web"})
    register(other, email="manual-other@example.com")
    assert (
        other.post(
            "/api/models/oauth/" + flow["id"] + "/callback", json={"answer": "test-only-callback"}
        ).status_code
        == 404
    )
    assert (
        other.post(
            "/api/models/oauth", json={"provider": "openai", "name": "Shared callback port"}
        ).status_code
        == 409
    )
    other.close()
    assert (
        client.post(
            "/api/models/oauth/" + flow["id"] + "/callback",
            json={"answer": "test-only-current-callback"},
        ).status_code
        == 200
    )
    done = wait_flow(client, flow["id"], lambda value: value["status"] == "connected")
    assert done["prompt"] is None and done["auth_url"] is None
    assert (
        client.post(
            "/api/models/oauth/" + flow["id"] + "/callback",
            json={"answer": "test-only-stale-callback"},
        ).status_code
        == 409
    )
    config = client.get("/api/models/config").json()
    assert config["preferences"]["text_model"] == "gpt-6-sol"
    assert config["preferences"]["text_connection_id"] == done["connection_id"]
    assert "test-only-access" not in str(config)


def test_failure_after_callback_exposes_safe_stage_and_discards_stale_link(model_client):
    client, bridge = model_client
    bridge.oauth_failure = {
        "ok": False,
        "code": "oauth_token_rejected",
        "stage": "exchanging_token",
        "http_status": 401,
    }
    start = client.post(
        "/api/models/oauth", json={"provider": "openai", "name": "Direct subscription"}
    ).json()
    flow = wait_flow(client, start["id"], lambda value: value.get("prompt"))
    assert (
        client.post(
            "/api/models/oauth/" + flow["id"] + "/callback",
            json={"answer": "test-only-sensitive-code"},
        ).status_code
        == 200
    )
    failed = wait_flow(client, flow["id"], lambda value: value["status"] == "failed")
    assert failed["error_code"] == "oauth_token_rejected"
    assert failed["stage"] == "exchanging_token" and failed["http_status"] == 401
    assert "令牌交换" in failed["message"]
    assert failed["auth_url"] is None and failed["prompt"] is None and not failed["manual_callback"]
    assert "test-only-sensitive-code" not in str(failed)
    assert client.get("/api/models/config").json()["connections"] == []
    retry = client.post(
        "/api/models/oauth", json={"provider": "openai-codex", "name": "Fresh subscription"}
    )
    assert retry.status_code == 202 and retry.json()["id"] != flow["id"]


def test_unsupported_region_rejection_shows_actionable_safe_message(model_client):
    client, bridge = model_client
    bridge.oauth_failure = {
        "ok": False,
        "code": "oauth_region_unsupported",
        "stage": "exchanging_token",
        "http_status": 403,
    }
    start = client.post(
        "/api/models/oauth", json={"provider": "openai-codex", "name": "Codex subscription"}
    ).json()
    flow = wait_flow(client, start["id"], lambda value: value.get("prompt"))
    assert (
        client.post(
            "/api/models/oauth/" + flow["id"] + "/callback",
            json={"answer": "test-only-code"},
        ).status_code
        == 200
    )
    failed = wait_flow(client, flow["id"], lambda value: value["status"] == "failed")
    assert failed["error_code"] == "oauth_region_unsupported"
    assert failed["stage"] == "exchanging_token" and failed["http_status"] == 403
    assert "不支持的地区" in failed["message"]
    assert "test-only-code" not in str(failed)
    assert "unsupported_country_region_territory" not in str(failed)


def test_unknown_preferred_model_rejected_before_oauth_or_key_is_saved(model_client):
    client, bridge = model_client
    assert (
        client.post(
            "/api/models/oauth",
            json={
                "provider": "openai-codex",
                "name": "Invalid model",
                "preferred_model": "unknown-test-model",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/models/connections",
            json={
                "provider": "qwen",
                "name": "Invalid model",
                "api_key": "test-only-key",
                "preferred_model": "unknown-test-model",
            },
        ).status_code
        == 422
    )
    assert bridge.calls == []
    assert client.get("/api/models/config").json()["connections"] == []


async def test_vision_enhancement_uses_owned_reference_and_text_only_omits_it(
    model_client, settings, png
):
    client, bridge = model_client
    image_connection = connect(client)
    set_image(client, image_connection)
    response = client.post(
        "/api/models/connections",
        json={
            "provider": "qwen",
            "name": "Vision analysis",
            "api_key": "test-only-vision-key",
            "preferred_model": "qwen-vl-plus",
        },
    )
    assert response.status_code == 201
    asset = client.post("/api/assets", content=png, headers={"Content-Type": "image/png"}).json()
    job = enqueue(client, reference_id=asset["id"]).json()
    row = client.app.state.db.one("SELECT * FROM jobs WHERE id=?", (job["id"],))
    router = ModelRouter(client.app.state.models, settings)
    provider = router.for_job(row)
    await provider.enhance("Keep the reference subject")
    assert base64.b64decode(bridge.calls[-1][1]["reference"]) == png
    row["text_model"] = "qwen-plus"
    await provider.enhance("Text only")
    assert bridge.calls[-1][1]["reference"] is None
    row["text_model"] = "qwen-vl-plus"
    row["user_id"] = "other-owner"
    with pytest.raises(ProviderFailure):
        await provider.enhance("Cannot access another account")
    await router.close()
