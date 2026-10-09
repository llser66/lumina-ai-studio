import pytest

from server.ops import settle
from tests.test_api import enqueue


def test_operator_reconciliation_requires_uncertain_and_an_explicit_outcome(client, user, settings):
    job = enqueue(client).json()
    with pytest.raises(ValueError):
        settle(settings, job["id"], "test audit", confirmed_no_image=True)
    client.app.state.db.fail(job["id"], "provider_unknown", "test uncertainty", uncertain=True)
    with pytest.raises(ValueError):
        settle(settings, job["id"], "test audit")
    settle(
        settings, job["id"], "Confirmed provider did not return an image", confirmed_no_image=True
    )
    assert client.get("/api/jobs/" + job["id"]).json()["status"] == "failed"
    assert client.get("/api/auth/me").json()["quota"]["remaining"] == 2


def test_operator_can_attach_verified_provider_output(client, user, settings, png, tmp_path):
    job = enqueue(client).json()
    client.app.state.db.fail(job["id"], "provider_unknown", "test uncertainty", uncertain=True)
    image = tmp_path / "verified.png"
    image.write_bytes(png)
    settle(settings, job["id"], "Verified provider output", image=image)
    result = client.get("/api/jobs/" + job["id"]).json()
    assert result["status"] == "succeeded"
    assert result["error_code"] is None
    assert client.get(result["image_url"]).status_code == 200
