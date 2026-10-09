import asyncio
import json
import time
import uuid
from urllib.parse import urlparse

from fastapi import HTTPException

from server.config import ROOT
from server.custom_endpoint import resolve_custom_endpoint
from server.model_schemas import OAuthAnswer, PreferenceUpdate
from server.provider import ProviderFailure
from server.vault import CredentialVault

IMAGE_MODELS = [
    {"id": name, "name": name, "input": ["text", "image"], "type": "image"}
    for name in ["gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2", "gpt-image-1.5"]
]
# These OpenRouter models require an input image and cannot serve Lumina's
# default text-to-image route. The bridge also validates live API capabilities.
REFERENCE_ONLY_IMAGE_MODELS = {
    "inclusionai/ming-image-0.1-design-layer",
    "recraft/recraft-v4-styles",
    "recraft/recraft-v4-styles-pro",
    "recraft/recraft-v4-styles-vector",
    "recraft/recraft-v4-styles-pro-vector",
}
AUTH_HOSTS = {
    "auth.openai.com",
    "github.com",
    "openrouter.ai",
    "claude.ai",
    "console.anthropic.com",
    "platform.claude.com",
}

OAUTH_MESSAGES = {
    "oauth_region_unsupported": "OpenAI 判定 Lumina 当前网络出口来自暂不支持的地区。若你实际位于支持地区，请联系 OpenAI 支持核查网络定位；否则可改用所在地可用的模型供应商 API Key。",
    "oauth_token_rejected": "已收到登录回调，但供应商未接受令牌交换。请重新授权；不要重复使用旧回调地址。",
    "oauth_scope_missing": "已收到登录回调，但该授权未开放 API 直用权限。可选择 OpenAI Codex 订阅入口或 API Key。",
    "oauth_network_error": "已收到登录回调，但服务端连接授权服务器失败。请检查服务端网络后重新授权。",
    "oauth_callback_busy": "本机登录回调端口正在使用，请完成或关闭其他登录流程后重试。",
    "oauth_state_mismatch": "回调不属于本次授权。请重新开始，并只使用新生成的登录链接。",
    "oauth_callback_invalid": "回调信息不完整，请重新授权并粘贴本次完整回调地址。",
    "oauth_token_invalid": "供应商没有返回完整的登录凭证，请重新授权。",
    "oauth_timeout": "本次授权已过期，请重新开始授权。",
    "oauth_storage_error": "授权已完成，但连接保存失败。请检查本机存储后重新授权。",
    "oauth_failed": "账号授权未完成，请重新开始；失败阶段显示在下方。",
}
OAUTH_STAGES = {
    "starting": "正在准备账号授权…",
    "waiting_user": "请打开本次登录页面，或提交本次授权码／完整回调地址。",
    "exchanging_token": "已收到登录回调，正在交换账号凭证…",
    "saving": "账号凭证已返回，正在保存连接…",
}


class ModelService:
    def __init__(self, db, settings, bridge):
        self.db, self.settings, self.bridge = db, settings, bridge
        self.vault = CredentialVault(db, settings.data_dir)
        self.catalog = json.loads((ROOT / "bridge" / "catalog.json").read_text(encoding="utf-8"))
        self.providers = {provider["id"]: provider for provider in self.catalog["providers"]}
        self.flows = {}
        self.bridge.on_event = self.on_event
        device = settings.data_dir / "model-device-id"
        if not device.exists():
            device.write_text(str(uuid.uuid4()), encoding="utf-8")
        self.device_id = device.read_text(encoding="utf-8").strip()

    def owned(self, connection_id, user_id):
        row = self.vault.owned(connection_id, user_id)
        if not row:
            raise HTTPException(404, "模型连接不存在")
        return row

    def images(self, row):
        if row["provider"] == "openai" and row["auth_method"] == "api_key":
            return IMAGE_MODELS
        models = self.providers[row["provider"]]["image_models"]
        if row["provider"] == "openrouter":
            return [model for model in models if model["id"] not in REFERENCE_ONLY_IMAGE_MODELS]
        return models

    def chats(self, row):
        if row["provider"] == "custom":
            return [
                {
                    "id": row["model_id"],
                    "name": row["model_name"] or row["model_id"],
                    "input": ["text", "image"] if row["supports_image"] else ["text"],
                    "type": "chat",
                }
            ]
        return self.providers[row["provider"]]["chat_models"]

    def preferred(self, provider, model_id):
        if model_id and not any(
            model["id"] == model_id for model in self.providers[provider]["chat_models"]
        ):
            raise HTTPException(422, "所选视觉／文本模型不在该供应商目录中")

    def apply_preferred(self, user_id, row, model_id):
        if model_id:
            existing = self.preferences(user_id)
            self.save_preferences(
                user_id,
                PreferenceUpdate(
                    image_connection_id=existing["image_connection_id"],
                    image_model=existing["image_model"],
                    text_connection_id=row["id"],
                    text_model=model_id,
                ),
            )

    def preferences(self, user_id):
        return self.db.one("SELECT * FROM model_preferences WHERE user_id=?", (user_id,)) or {
            "image_connection_id": None,
            "image_model": "",
            "text_connection_id": None,
            "text_model": "",
        }

    def configuration(self, user_id, fallback_ready):
        connections = self.db.all(
            "SELECT * FROM model_connections WHERE user_id=? AND revoked_at IS NULL ORDER BY created_at DESC",
            (user_id,),
        )
        visible = []
        for row in connections:
            item = self.vault.public(row)
            item["image_models"] = self.images(row)
            item["chat_models"] = self.chats(row)
            item.update(
                preferred_model=row["model_id"],
                base_url=row["base_url"],
                model_id=row["model_id"],
                model_name=row["model_name"],
                supports_image=bool(row["supports_image"]),
            )
            item["subscription"] = (
                row["auth_method"] == "oauth" and self.providers[row["provider"]]["subscription"]
            )
            visible.append(item)
        preference = self.preferences(user_id)
        image = (
            self.vault.owned(preference["image_connection_id"], user_id)
            if preference["image_connection_id"]
            else None
        )
        selected = (
            next(
                (model for model in self.images(image) if model["id"] == preference["image_model"]),
                None,
            )
            if image
            else None
        )
        ready = bool(selected) if preference["image_connection_id"] else fallback_ready
        model = preference["image_model"] if image else self.settings.image_model
        return {
            "owner_id": user_id,
            "sdk": self.catalog["sdk"],
            "sdk_version": self.catalog["version"],
            "providers": list(self.providers.values()),
            "connections": visible,
            "preferences": {
                key: preference[key]
                for key in (
                    "image_connection_id",
                    "image_model",
                    "text_connection_id",
                    "text_model",
                )
            },
            "image_ready": ready,
            "active_image_model": model,
            "server_default_available": fallback_ready,
            "image_controls": {
                "quality": not image or image["provider"] == "openai",
                "exact_size": not image or image["provider"] == "openai",
                "edit": bool(selected and "image" in selected["input"]) if image else True,
            },
        }

    def create_key(self, user_id, body):
        if not self.providers[body.provider]["api_key"]:
            raise HTTPException(422, "该供应商需要账号授权，不能使用 API Key 连接")
        total = self.db.one(
            "SELECT COUNT(*) AS count FROM model_connections WHERE user_id=? AND revoked_at IS NULL",
            (user_id,),
        )["count"]
        if total >= 20:
            raise HTTPException(409, "最多保留 20 个模型连接，请先移除不使用的连接")
        base_url = ""
        model_id = body.preferred_model
        if body.provider == "custom":
            if body.account_id:
                raise HTTPException(422, "Account ID 仅用于 Cloudflare")
            base_url, _ = resolve_custom_endpoint(body.base_url)
            model_id = body.model_id.strip()
            if not model_id or any(ord(char) < 32 for char in model_id):
                raise HTTPException(422, "自定义接口需要填写模型 ID")
        elif body.provider == "qwen":
            if body.model_id or body.model_name or body.supports_image or body.account_id:
                raise HTTPException(422, "自定义模型字段仅用于自定义接口")
            if body.base_url:
                base_url, _ = resolve_custom_endpoint(body.base_url)
            self.preferred(body.provider, model_id)
        elif body.provider == "cloudflare":
            if not body.account_id:
                raise HTTPException(422, "Cloudflare 连接需要填写 32 位 Account ID")
            if body.base_url or body.model_id or body.model_name or body.supports_image or model_id:
                raise HTTPException(422, "Cloudflare 生图连接只需 Account ID 和 API Token")
        else:
            if (
                body.base_url
                or body.model_id
                or body.model_name
                or body.supports_image
                or body.account_id
            ):
                raise HTTPException(422, "自定义地址和模型字段仅用于自定义接口")
            self.preferred(body.provider, model_id)
        row = self.vault.save(
            user_id,
            body.provider,
            body.name,
            "api_key",
            {
                "type": "api_key",
                "key": body.api_key.get_secret_value(),
                **(
                    {"account_id": body.account_id.lower()} if body.provider == "cloudflare" else {}
                ),
            },
            base_url=base_url,
            model_id=model_id,
            model_name=body.model_name,
            supports_image=body.supports_image,
        )
        self.apply_preferred(user_id, row, model_id)
        return self.vault.public(row)

    def save_preferences(self, user_id, body):
        if body.image_connection_id and not body.text_connection_id:
            image_connection = self.owned(body.image_connection_id, user_id)
            if not self.chats(image_connection):
                raise HTTPException(422, "该生图连接不提供文本模型，请另选一个文本连接")
        for connection_id, model_id, role in [
            (body.image_connection_id, body.image_model, "image"),
            (body.text_connection_id, body.text_model, "chat"),
        ]:
            if not connection_id:
                if model_id:
                    raise HTTPException(422, "请先选择模型连接")
                continue
            row = self.owned(connection_id, user_id)
            models = self.images(row) if role == "image" else self.chats(row)
            if not any(model["id"] == model_id for model in models):
                raise HTTPException(422, "当前连接不支持所选模型或用途")
        with self.db.connect(write=True) as connection:
            for identifier in [body.image_connection_id, body.text_connection_id]:
                if (
                    identifier
                    and not connection.execute(
                        "SELECT id FROM model_connections WHERE id=? AND user_id=? AND revoked_at IS NULL",
                        (identifier, user_id),
                    ).fetchone()
                ):
                    raise HTTPException(409, "连接已经变更，请重新选择")
            connection.execute(
                "INSERT INTO model_preferences(user_id,image_connection_id,image_model,text_connection_id,text_model,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET image_connection_id=excluded.image_connection_id,image_model=excluded.image_model,text_connection_id=excluded.text_connection_id,text_model=excluded.text_model,updated_at=excluded.updated_at",
                (
                    user_id,
                    body.image_connection_id,
                    body.image_model,
                    body.text_connection_id,
                    body.text_model,
                    time.time(),
                ),
            )

    def route(self, user_id, spec, fallback_ready):
        preference = self.preferences(user_id)
        bindings = {
            "image_connection_id": None,
            "image_connection_version": None,
            "text_connection_id": None,
            "text_connection_version": None,
            "text_model": self.settings.text_model,
        }
        if preference["image_connection_id"]:
            image = self.owned(preference["image_connection_id"], user_id)
            model = next(
                (model for model in self.images(image) if model["id"] == preference["image_model"]),
                None,
            )
            if not model:
                raise HTTPException(422, "请重新选择可用的生图模型")
            if spec.reference_id and "image" not in model["input"]:
                raise HTTPException(422, "当前模型不支持参考图编辑，请切换模型或移除参考图")
            bindings.update(
                image_connection_id=image["id"], image_connection_version=image["version"]
            )
            image_model = model["id"]
        else:
            if not fallback_ready:
                raise HTTPException(503, "请在模型与订阅页面连接生图服务，并保存默认生图模型")
            image_model = self.settings.image_model
        if preference["text_connection_id"]:
            text = self.owned(preference["text_connection_id"], user_id)
            bindings.update(
                text_connection_id=text["id"],
                text_connection_version=text["version"],
                text_model=preference["text_model"],
            )
        elif preference["image_connection_id"]:
            choices = self.providers[image["provider"]]["chat_models"]
            preferred = ["gpt-5-mini", "openai/gpt-4o-mini", "openai/gpt-5-mini"]
            model = next(
                (
                    model
                    for identifier in preferred
                    for model in choices
                    if model["id"] == identifier
                ),
                choices[0] if choices else None,
            )
            if not model:
                raise HTTPException(422, "请为优化和内容检查选择文本模型")
            bindings.update(
                text_connection_id=image["id"],
                text_connection_version=image["version"],
                text_model=model["id"],
            )
        return image_model, bindings

    def payload(self, row):
        return {key: row[key] for key in ("id", "user_id", "provider", "version")} | {
            "credential": self.vault.credential(row),
            **{key: row[key] for key in ("base_url", "model_id", "model_name", "supports_image")},
        }

    async def request_payload(self, row):
        payload = self.payload(row)
        if row["provider"] == "custom" or (row["provider"] == "qwen" and row["base_url"]):
            normalized, addresses = await asyncio.to_thread(
                resolve_custom_endpoint, row["base_url"]
            )
            payload.update(base_url=normalized, endpoint_ips=addresses)
        return payload

    async def check(self, connection_id, user_id):
        row = self.owned(connection_id, user_id)
        try:
            result = await self.bridge.request(
                "check", timeout=25, connection=await self.request_payload(row)
            )
        except ProviderFailure:
            result = {"ok": False, "code": "provider_unavailable"}
        status = "verified" if result.get("ok") else "needs_attention"
        self.db.execute(
            "UPDATE model_connections SET status=?,checked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
            (status, time.time(), connection_id, user_id),
        )
        return {
            "ok": bool(result.get("ok")),
            "status": status,
            "message": (
                "授权凭证可读取；模型权限需在实际调用中确认。"
                if result.get("verification") == "auth_resolved"
                else "Cloudflare Token 有效；Account ID、生图权限与免费额度需在实际生成中确认。"
                if result.get("verification") == "token_only"
                else "凭证验证通过，实际模型权限以调用结果为准。"
            )
            if result.get("ok")
            else (
                "OpenRouter API Key 无效或已过期，请在供应商后台创建新 Key 并添加新连接。"
                if row["provider"] == "openrouter" and result.get("code") == "credentials"
                else "验证未完成，请检查密钥、订阅权限或网络。"
            ),
        }

    async def remove(self, connection_id, user_id):
        self.owned(connection_id, user_id)
        self.db.execute(
            "UPDATE model_connections SET credential='',revoked_at=?,status='disconnected',version=version+1 WHERE id=? AND user_id=?",
            (time.time(), connection_id, user_id),
        )
        if getattr(self.bridge, "process", None):
            await self.bridge.request("forget", user_id=user_id, connection_id=connection_id)

    def flow(self, flow_id, user_id):
        flow = self.flows.get(flow_id)
        if not flow or flow["user_id"] != user_id:
            raise HTTPException(404, "授权会话不存在")
        return flow

    @staticmethod
    def public_flow(flow):
        return {
            key: flow.get(key)
            for key in (
                "id",
                "provider",
                "status",
                "message",
                "auth_url",
                "verification_url",
                "user_code",
                "prompt",
                "connection_id",
                "expires_at",
                "error_code",
                "stage",
                "http_status",
                "retryable",
                "manual_callback",
                "manual_input_kind",
            )
        }

    async def start_oauth(self, user_id, session_hash, body):
        if not self.providers[body.provider]["oauth"]:
            raise HTTPException(422, "该供应商不支持账号授权")
        self.preferred(body.provider, body.preferred_model)
        active = [flow for flow in self.flows.values() if flow["status"] == "pending"]
        if any(flow["user_id"] == user_id for flow in active) or len(active) >= 16:
            raise HTTPException(409, "已有授权正在进行，请完成或取消后再试")
        if body.provider in {"openai", "openai-codex"} and any(
            flow["provider"] in {"openai", "openai-codex"} for flow in active
        ):
            raise HTTPException(409, "ChatGPT 授权通道正在使用，请稍后再试")
        # Remove expired metadata; credentials never live in this flow dictionary.
        self.flows = {
            identifier: flow
            for identifier, flow in self.flows.items()
            if flow["expires_at"] > time.time() - 300
        }
        identifier = str(uuid.uuid4())
        flow = {
            "id": identifier,
            "user_id": user_id,
            "session_hash": session_hash,
            "provider": body.provider,
            "name": body.name,
            "status": "pending",
            "message": "正在准备账号授权…",
            "expires_at": time.time() + 600,
            "command_id": str(uuid.uuid4()),
            "preferred_model": body.preferred_model,
            "stage": "starting",
            "error_code": None,
            "retryable": False,
            "manual_callback": False,
            "manual_input_kind": "url" if body.provider == "openai" else "code_or_url",
        }
        self.flows[identifier] = flow
        flow["task"] = asyncio.create_task(self.run_oauth(flow))
        return self.public_flow(flow)

    async def run_oauth(self, flow):
        try:
            result = await self.bridge.request(
                "oauth",
                timeout=610,
                request_id=flow["command_id"],
                flow_id=flow["id"],
                provider=flow["provider"],
                device_id=self.device_id,
            )
            session = self.db.one(
                "SELECT user_id FROM sessions WHERE token_hash=? AND expires_at>?",
                (flow["session_hash"], time.time()),
            )
            if flow["status"] != "pending" or not session or session["user_id"] != flow["user_id"]:
                flow.update(
                    status="cancelled",
                    prompt=None,
                    manual_callback=False,
                    auth_url=None,
                    verification_url=None,
                    user_code=None,
                )
                return
            if not result.get("ok"):
                self.fail_flow(
                    flow,
                    result.get("code", "oauth_failed"),
                    result.get("stage", flow["stage"]),
                    result.get("http_status"),
                )
                return
            row = self.vault.save(
                flow["user_id"],
                flow["provider"],
                flow["name"],
                "oauth",
                result["credential"],
                model_id=flow["preferred_model"],
            )
            self.apply_preferred(flow["user_id"], row, flow["preferred_model"])
            flow.update(
                status="connected",
                message="账号已连接，可以选择默认文本模型。",
                connection_id=row["id"],
                prompt=None,
                auth_url=None,
                user_code=None,
                verification_url=None,
                stage="connected",
                manual_callback=False,
                retryable=False,
            )
        except (ProviderFailure, asyncio.CancelledError):
            if flow["status"] == "pending":
                self.fail_flow(flow, "oauth_failed", flow["stage"])
        except Exception:
            self.fail_flow(
                flow,
                "oauth_storage_error" if flow["stage"] == "saving" else "oauth_failed",
                flow["stage"],
            )

    @staticmethod
    def fail_flow(flow, code, stage, http_status=None):
        flow.update(
            status="failed",
            message=OAUTH_MESSAGES.get(code, OAUTH_MESSAGES["oauth_failed"]),
            error_code=code,
            stage=stage,
            http_status=http_status,
            retryable=True,
            prompt=None,
            manual_callback=False,
            auth_url=None,
            verification_url=None,
            user_code=None,
        )

    def on_event(self, event):
        if event.get("kind") == "credential_update":
            self.vault.refresh(event["id"], event["user_id"], event["version"], event["credential"])
            return
        flow = self.flows.get(event.get("flow_id"))
        if not flow or flow["status"] != "pending":
            return
        if event["kind"] == "oauth_stage":
            stage = event.get("stage")
            if stage in OAUTH_STAGES:
                flow.update(stage=stage, message=OAUTH_STAGES[stage])
                if stage in {"exchanging_token", "saving"}:
                    flow["manual_callback"] = False
        elif event["kind"] == "oauth_prompt":
            prompt = event["prompt"]
            flow["prompt"] = {
                "id": event["prompt_id"],
                "type": prompt["type"],
                "message": prompt["message"],
                "placeholder": prompt.get("placeholder", ""),
                "options": prompt.get("options", []),
            }
            flow["manual_callback"] = prompt["type"] == "manual_code"
        elif event["kind"] == "oauth_prompt_cancel":
            if (flow.get("prompt") or {}).get("id") == event["prompt_id"]:
                flow["prompt"] = None
                flow["manual_callback"] = False
        elif event["kind"] == "oauth_event":
            detail = event["event"]
            if detail["type"] in {"auth_url", "device_code"}:
                url = detail.get("url") or detail.get("verificationUri")
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.hostname not in AUTH_HOSTS:
                    self.fail_flow(flow, "oauth_callback_invalid", flow["stage"])
                    return
                if detail["type"] == "auth_url":
                    flow["auth_url"] = url
                else:
                    flow.update(verification_url=url, user_code=detail["userCode"])
                flow.update(stage="waiting_user", message=OAUTH_STAGES["waiting_user"])
            else:
                flow["message"] = "正在确认账号授权…"

    async def answer(self, flow_id, user_id, body):
        flow = self.flow(flow_id, user_id)
        prompt = flow.get("prompt")
        if (
            flow["status"] != "pending"
            or flow["stage"] in {"exchanging_token", "saving"}
            or not prompt
            or prompt["id"] != body.prompt_id
        ):
            raise HTTPException(409, "该授权步骤已结束，请刷新授权状态")
        answer = body.answer.get_secret_value()
        if (
            flow["provider"] == "github-copilot"
            and prompt["type"] == "text"
            and answer.strip().lower() != "github.com"
        ):
            raise HTTPException(422, "当前支持 github.com 账号，请填写 github.com")
        result = await self.bridge.request(
            "answer", flow_id=flow_id, prompt_id=body.prompt_id, answer=answer
        )
        if not result.get("ok"):
            raise HTTPException(409, "授权步骤已失效")
        flow["prompt"] = None
        flow["manual_callback"] = False

    async def callback(self, flow_id, user_id, body):
        flow = self.flow(flow_id, user_id)
        prompt = flow.get("prompt")
        if not prompt or prompt["type"] != "manual_code" or flow["status"] != "pending":
            raise HTTPException(409, "本次手动授权步骤尚未就绪或已结束，请使用当前授权页面")
        await self.answer(flow_id, user_id, OAuthAnswer(prompt_id=prompt["id"], answer=body.answer))

    async def cancel(self, flow_id, user_id):
        flow = self.flow(flow_id, user_id)
        if flow["status"] == "pending":
            flow.update(
                status="cancelled",
                message="授权已取消。",
                prompt=None,
                manual_callback=False,
                auth_url=None,
                verification_url=None,
                user_code=None,
                retryable=True,
            )
            await self.bridge.request("cancel", target=flow["command_id"])
            flow["task"].cancel()

    async def cancel_session(self, session_hash):
        for flow in list(self.flows.values()):
            if flow["session_hash"] == session_hash and flow["status"] == "pending":
                await self.cancel(flow["id"], flow["user_id"])

    async def close(self):
        for flow in list(self.flows.values()):
            if flow["status"] == "pending":
                flow["task"].cancel()
        await asyncio.gather(
            *(flow["task"] for flow in self.flows.values()), return_exceptions=True
        )
        await self.bridge.close()
