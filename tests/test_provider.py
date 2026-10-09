import base64
import io

import httpx
import pytest
from openai import AsyncOpenAI
from PIL import Image
from pydantic import SecretStr

from server.config import Settings
from server.provider import OpenAIImageProvider, ProviderFailure, normalize_image
from server.schemas import GenerationSpec


async def mock_provider(settings, handler):
    settings.openai_api_key = SecretStr("test-only-placeholder")
    provider = OpenAIImageProvider(settings)
    assert provider.client.max_retries == 0
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="test-only-placeholder",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return provider


async def test_images_generate_and_edit_sdk_contract(settings, png, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"x-request-id": "sdk-request-test"},
            json={"created": 1, "data": [{"b64_json": base64.b64encode(png).decode()}]},
        )

    provider = await mock_provider(settings, handler)
    spec = GenerationSpec(prompt="A clean product photo", enhance=False)
    result = await provider.generate(spec, spec.prompt, None, settings.image_model)
    assert result.request_id == "sdk-request-test"
    assert normalize_image(result.content)[1:] == (64, 64)
    assert requests[0].url.path == "/v1/images/generations"
    reference = tmp_path / "reference.png"
    reference.write_bytes(png)
    await provider.generate(spec, "Change only the background", reference, settings.image_model)
    assert requests[1].url.path == "/v1/images/edits"
    assert "multipart/form-data" in requests[1].headers["content-type"]
    await provider.close()


async def test_enhance_and_moderation_sdk_contract(settings):
    def handler(request):
        if request.url.path.endswith("/moderations"):
            return httpx.Response(
                200,
                json={
                    "id": "modr-test",
                    "model": "omni-moderation-latest",
                    "results": [{"flagged": False, "categories": {}, "category_scores": {}}],
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "resp-test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": settings.text_model,
                "output": [
                    {
                        "id": "msg-test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "A carefully lit product photograph",
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    provider = await mock_provider(settings, handler)
    assert (await provider.enhance("A product")).startswith("A carefully")
    assert await provider.moderate("A product")
    await provider.close()


@pytest.mark.parametrize(
    "status,code,uncertain",
    [
        (400, "rejected", False),
        (401, "credentials", False),
        (429, "rate_limit", False),
        (500, "provider_unknown", True),
    ],
)
async def test_provider_errors_are_classified_without_sdk_retry(settings, status, code, uncertain):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            json={
                "error": {
                    "message": "test-only provider detail",
                    "type": "test_failure",
                    "code": "test_failure",
                }
            },
        )

    provider = await mock_provider(settings, handler)
    with pytest.raises(ProviderFailure) as error:
        await provider.generate(
            GenerationSpec(prompt="A landscape"), "A landscape", None, settings.image_model
        )
    assert error.value.code == code and error.value.uncertain is uncertain
    assert "test-only provider detail" not in error.value.message
    assert calls == 1
    await provider.close()


async def test_network_timeout_is_uncertain_for_images_only(settings):
    def handler(request):
        raise httpx.ReadTimeout("test-only timeout", request=request)

    provider = await mock_provider(settings, handler)
    with pytest.raises(ProviderFailure) as result:
        await provider.generate(
            GenerationSpec(prompt="A landscape"), "A landscape", None, settings.image_model
        )
    assert result.value.uncertain
    with pytest.raises(ProviderFailure) as result:
        await provider.moderate("A landscape")
    assert not result.value.uncertain
    with pytest.raises(ProviderFailure) as result:
        await provider.enhance("A landscape")
    assert not result.value.uncertain
    await provider.close()


async def test_invalid_provider_image_keeps_charge_uncertain(settings):
    provider = await mock_provider(
        settings,
        lambda request: httpx.Response(
            200, json={"created": 1, "data": [{"b64_json": "not-base64!!"}]}
        ),
    )
    with pytest.raises(ProviderFailure) as result:
        await provider.generate(
            GenerationSpec(prompt="A landscape"), "A landscape", None, settings.image_model
        )
    assert result.value.uncertain
    await provider.close()


def test_decoder_rejects_wrong_formats_and_huge_dimensions():
    for format_name, dimensions in [("GIF", (12, 12)), ("PNG", (5000, 20))]:
        output = io.BytesIO()
        Image.new("RGB", dimensions).save(output, format_name)
        with pytest.raises(ValueError):
            normalize_image(output.getvalue())
    with pytest.raises(ValueError):
        normalize_image(b"x" * (25 * 1024 * 1024 + 1))


def test_provider_url_must_be_https(tmp_path):
    with pytest.raises(ValueError):
        Settings(_env_file=None, data_dir=tmp_path, openai_base_url="http://example.com")
