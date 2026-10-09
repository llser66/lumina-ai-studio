import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.config import Settings
from server.main import create_app
from server.provider import ImageResult


@pytest.fixture
def png():
    image = Image.new("RGB", (64, 64), (32, 100, 170))
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


class TestImageProvider:
    """Test-only dependency injection. The application has no fake-provider switch."""

    def __init__(self, png):
        self.png = png
        self.calls = 0
        self.allowed = True
        self.failure = None
        self.reference = None

    async def enhance(self, prompt):
        return "Refined: " + prompt

    async def moderate(self, prompt):
        return self.allowed

    async def generate(self, spec, prompt, reference, model):
        self.calls += 1
        self.reference = reference
        if self.failure:
            raise self.failure
        return ImageResult(self.png, "request-test-only")


@pytest.fixture
def provider(png):
    return TestImageProvider(png)


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, openai_api_key="", data_dir=tmp_path, daily_generation_limit=2)


@pytest.fixture
def client(settings, provider):
    application = create_app(settings, start_worker=False, provider=provider)
    with TestClient(application, headers={"X-Lumina-Client": "web"}) as session:
        yield session


def register(client, *, email="creator@example.com"):
    result = client.post(
        "/api/auth/register",
        json={"name": "创作者", "email": email, "password": "test-only-password-123"},
    )
    assert result.status_code == 201, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return result.json()["user"]


@pytest.fixture
def user(client):
    return register(client)
