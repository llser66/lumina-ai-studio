import asyncio
import base64


class ModelBridgeFixture:
    """Isolated app testing; never used by the production entrypoint."""

    def __init__(self, png):
        self.png = png
        self.on_event = None
        self.calls = []
        self.flows = {}
        self.process = None
        self.fail = None
        self.oauth_failure = None

    async def request(self, op, request_id=None, **payload):
        self.calls.append((op, payload))
        if self.fail:
            return self.fail
        if op == "oauth":
            future = asyncio.get_running_loop().create_future()
            prompt_id = "00000000-0000-4000-8000-000000000001"
            self.flows[payload["flow_id"]] = (future, request_id)
            if payload["provider"] in {"openai", "openai-codex"}:
                self.on_event(
                    {
                        "kind": "oauth_event",
                        "flow_id": payload["flow_id"],
                        "event": {
                            "type": "auth_url",
                            "url": "https://auth.openai.com/oauth/authorize?state=test-only-current-flow",
                        },
                    }
                )
                prompt = {
                    "type": "manual_code",
                    "message": "Paste current callback URL",
                    "placeholder": "Current authorization code or redirect URL",
                }
            else:
                self.on_event(
                    {
                        "kind": "oauth_event",
                        "flow_id": payload["flow_id"],
                        "event": {
                            "type": "device_code",
                            "verificationUri": "https://github.com/login/device",
                            "userCode": "TEST-1234",
                        },
                    }
                )
                prompt = {"type": "text", "message": "GitHub domain", "placeholder": "github.com"}
            self.on_event(
                {
                    "kind": "oauth_prompt",
                    "flow_id": payload["flow_id"],
                    "prompt_id": prompt_id,
                    "prompt": prompt,
                }
            )
            await future
            self.on_event(
                {"kind": "oauth_stage", "flow_id": payload["flow_id"], "stage": "exchanging_token"}
            )
            if self.oauth_failure:
                return self.oauth_failure
            self.on_event({"kind": "oauth_stage", "flow_id": payload["flow_id"], "stage": "saving"})
            return {
                "ok": True,
                "credential": {
                    "type": "oauth",
                    "access": "test-only-access-token",
                    "refresh": "test-only-refresh-token",
                    "expires": 9999999999999,
                },
            }
        if op == "answer":
            self.flows[payload["flow_id"]][0].set_result(True)
            return {"ok": True}
        if op == "cancel":
            for future, identifier in self.flows.values():
                if identifier == payload["target"] and not future.done():
                    future.cancel()
            return {"ok": True}
        if op == "image":
            return {
                "ok": True,
                "image": base64.b64encode(self.png).decode(),
                "mime_type": "image/png",
                "request_id": "pi-test-request",
            }
        if op == "moderate":
            return {"ok": True, "allowed": True}
        if op == "enhance":
            return {"ok": True, "text": "Refined via pi: " + payload["prompt"]}
        return {"ok": True}

    async def close(self):
        for future, _ in self.flows.values():
            if not future.done():
                future.cancel()
