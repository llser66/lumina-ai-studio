"""Isolated browser-test service; cannot call the real image provider."""
import os
import io
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

with TemporaryDirectory(prefix="lumina-e2e-") as directory:
    os.environ["DATA_DIR"] = directory
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["COOKIE_SECURE"] = "false"
    os.environ["REGISTRATION_ENABLED"] = "true"
    os.environ["APP_ORIGINS"] = "http://127.0.0.1:8091"
    fixture_mode = "--provider-fixture" in sys.argv
    port = 8092 if fixture_mode else 8091
    os.environ["APP_ORIGINS"] = "http://127.0.0.1:" + str(port)
    if fixture_mode:
        os.environ["REGISTRATION_LIMIT"] = "100"
        from PIL import Image
        from server.main import create_app
        from tests.conftest import TestImageProvider
        from tests.model_bridge_fixture import ModelBridgeFixture

        output = io.BytesIO()
        Image.new("RGB", (64, 64), (32, 100, 170)).save(output, "PNG")
        application = create_app(provider=TestImageProvider(output.getvalue()), bridge=ModelBridgeFixture(output.getvalue()))
        uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning")
    else:
        uvicorn.run("server.main:app", host="127.0.0.1", port=port, log_level="warning")
