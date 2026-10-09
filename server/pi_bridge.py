import asyncio
import contextlib
import json
import os
import uuid
from urllib.request import getproxies

from server.config import ROOT
from server.provider import ProviderFailure


def bridge_environment():
    environment = os.environ.copy()
    # Node does not read Windows Internet Settings itself. Reuse the operator's
    # system proxy while keeping explicit environment settings authoritative.
    proxies = getproxies()
    for scheme in ("http", "https"):
        names = (scheme.upper() + "_PROXY", scheme + "_proxy")
        if proxies.get(scheme) and not any(name in environment for name in names):
            environment[names[0]] = proxies[scheme]
    if any(environment.get(name) for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")):
        environment.setdefault("NODE_USE_ENV_PROXY", "1")
    # No inherited user/provider credentials or CLI auth files are used.
    for name in list(environment):
        if (
            name.endswith(("_API_KEY", "_AUTH_TOKEN", "_TOKEN"))
            or name == "NODE_OPTIONS"
            or name.startswith(
                ("OPENAI_", "ANTHROPIC_", "GOOGLE_", "GEMINI_", "COPILOT_", "PI_OAUTH_")
            )
        ):
            environment.pop(name, None)
    return environment


class PiBridge:
    """A private subprocess, never a second network-facing API."""

    def __init__(self):
        self.process = None
        self.reader = None
        self.pending = {}
        self.start_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.on_event = None

    async def start(self):
        async with self.start_lock:
            if self.process and self.process.returncode is None:
                return
            self.process = await asyncio.create_subprocess_exec(
                "node",
                str(ROOT / "bridge" / "service.mjs"),
                cwd=ROOT,
                env=bridge_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=40 * 1024 * 1024,
            )
            initial = await asyncio.wait_for(self.process.stdout.readline(), 15)
            if not initial or json.loads(initial).get("kind") != "ready":
                raise ProviderFailure(
                    "bridge_unavailable", "模型连接服务未能启动，请检查服务端安装。"
                )
            self.reader = asyncio.create_task(self.listen())

    async def listen(self):
        try:
            while line := await self.process.stdout.readline():
                payload = json.loads(line)
                if "id" in payload and "result" in payload:
                    future = self.pending.pop(payload["id"], None)
                    if future and not future.done():
                        future.set_result(payload["result"])
                elif self.on_event:
                    self.on_event(payload)
        except (ValueError, asyncio.CancelledError):
            pass
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(
                        ProviderFailure("bridge_unavailable", "模型连接服务已中断。")
                    )
            self.pending.clear()

    async def request(self, op, *, timeout=280, request_id=None, **payload):
        await self.start()
        identifier = request_id or str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        async with self.write_lock:
            self.process.stdin.write(
                (json.dumps({"id": identifier, "op": op, **payload}) + "\n").encode()
            )
            await self.process.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            self.pending.pop(identifier, None)
            raise ProviderFailure(
                "provider_unknown" if op == "image" else "bridge_timeout",
                "模型服务响应超时，请检查连接。",
                uncertain=op == "image",
            ) from exc

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader
