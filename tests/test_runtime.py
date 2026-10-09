import time

from fastapi.testclient import TestClient

from server.main import create_app
from tests.conftest import register
from tests.test_api import enqueue


def test_background_worker_lifespan_processes_real_queue(settings, provider):
    application = create_app(settings, provider=provider)
    with TestClient(application, headers={"X-Lumina-Client": "web"}) as client:
        register(client)
        job = enqueue(client).json()
        deadline = time.monotonic() + 5
        result = None
        while time.monotonic() < deadline:
            result = client.get("/api/jobs/" + job["id"]).json()
            if result["status"] == "succeeded":
                break
            time.sleep(0.05)
        assert result["status"] == "succeeded"
        assert provider.calls == 1
        assert client.get(result["image_url"]).status_code == 200
