import base64
import time

from pydantic import SecretStr

from server.provider import ImageResult, OpenAIImageProvider, ProviderFailure, normalize_image

MESSAGES = {
    "prompt_too_long": "该生图模型只接受最多 2048 个字符的描述，请缩短提示词后创建新任务。",
    "credentials": "模型凭证被供应商拒绝或已过期。请在模型与订阅页面添加新的 API Key 连接，或重新进行账号授权。",
    "provider_access_denied": "供应商拒绝了此模型请求（HTTP 403）。连接验证成功不代表该模型有调用权限；请检查账号访问政策和模型权限，或联系供应商支持。",
    "provider_timeout": "连接模型服务超时，请检查后端网络及系统代理是否可用。修改代理设置后请重启 Lumina 后端。",
    "provider_network_error": "后端未能连接模型服务，请检查网络及系统代理。修改代理设置后请重启 Lumina 后端。",
    "rate_limit": "供应商额度不足或请求过多，请稍后再试。",
    "model_unavailable": "所选模型暂不可用，请重新选择模型。",
    "moderation_invalid": "内容检查没有返回有效结果，请调整描述或文本模型。",
    "enhancement_empty": "描述优化没有返回内容，请关闭优化或更换文本模型。",
    "edit_unsupported": "当前模型不支持图片编辑，请更换模型。",
    "reference_required": "当前模型必须上传参考图片；纯文字生图请选 Ming Image 0.1 Design 等文生图模型。",
    "provider_unknown": "供应商结果待确认，此任务不会自动重试。",
}

# Content checks are a small, non-billable request.  Keep a separate bound so
# a provider that never completes cannot leave the task card spinning for the
# full image-generation timeout.
CONTENT_CHECK_TIMEOUT_SECONDS = 90


def checked(result, *, billable=False, source=""):
    if not result.get("ok"):
        code = result.get("code", "provider_unavailable")
        uncertain = bool(result.get("uncertain")) or (
            billable and code in {"bridge_error", "bridge_unavailable", "invalid_image"}
        )
        message = MESSAGES.get(code, "暂时无法调用模型，请检查连接后重试。")
        if source:
            message = source + "：" + message
        raise ProviderFailure(code, message, uncertain=uncertain)
    return result


class JobProvider:
    def __init__(self, service, settings, row, fallback):
        self.service, self.settings, self.row, self.fallback = service, settings, row, fallback
        self.image_client = None

    def connection(self, role):
        identifier = self.row.get(role + "_connection_id")
        if not identifier:
            return None
        row = self.service.vault.owned(identifier, self.row["user_id"])
        if not row or row["version"] != self.row.get(role + "_connection_version"):
            raise ProviderFailure(
                "credentials", "该任务的模型连接已断开或变更，请重新配置后创建新任务。"
            )
        return row

    def native_image(self):
        connection = self.connection("image")
        if not connection:
            if not self.fallback:
                raise ProviderFailure("credentials", "服务器默认生图服务未配置。")
            return self.fallback
        if connection["provider"] != "openai" or connection["auth_method"] != "api_key":
            return None
        if not self.image_client:
            credential = self.service.vault.credential(connection)
            configured = self.settings.model_copy(
                update={
                    "openai_api_key": SecretStr(credential["key"]),
                    "openai_base_url": "https://api.openai.com/v1",
                    "text_model": self.row["text_model"] or self.settings.text_model,
                }
            )
            self.image_client = OpenAIImageProvider(configured)
        return self.image_client

    async def enhance(self, prompt):
        connection = self.connection("text")
        if not connection:
            return await self.native_image().enhance(prompt)
        reference = None
        model = next(
            (
                model
                for model in self.service.chats(connection)
                if model["id"] == self.row["text_model"]
            ),
            None,
        )
        if self.row.get("reference_id") and model and "image" in model["input"]:
            asset = self.service.db.one(
                "SELECT * FROM assets WHERE id=? AND user_id=?",
                (self.row["reference_id"], self.row["user_id"]),
            )
            if not asset or not self.service.asset_store.exists(asset["path"]):
                raise ProviderFailure("reference_missing", "参考图片已不可用，请重新上传。")
            reference = base64.b64encode(self.service.asset_store.read(asset["path"])).decode()
        result = checked(
            await self.service.bridge.request(
                "enhance",
                connection=await self.service.request_payload(connection),
                model=self.row["text_model"],
                prompt=prompt,
                reference=reference,
            ),
            source=f"描述优化失败（文本模型 {self.row['text_model']}）",
        )
        return result["text"]

    async def moderate(self, prompt):
        native = self.native_image()
        if native:
            return await native.moderate(prompt)
        connection = self.connection("text")
        if not connection:
            raise ProviderFailure("credentials", "请为内容检查选择文本模型。")
        result = checked(
            await self.service.bridge.request(
                "moderate",
                timeout=CONTENT_CHECK_TIMEOUT_SECONDS,
                connection=await self.service.request_payload(connection),
                model=self.row["text_model"],
                prompt=prompt,
            ),
            source=f"内容检查失败（文本模型 {self.row['text_model']}）",
        )
        return result["allowed"]

    async def generate(self, spec, prompt, reference, model):
        native = self.native_image()
        if native:
            return await native.generate(spec, prompt, reference, model)
        connection = self.connection("image")
        ratio = {"1024x1024": "1:1", "1536x1024": "3:2", "1024x1536": "2:3"}[spec.size]
        try:
            result = checked(
                await self.service.bridge.request(
                    "image",
                    connection=await self.service.request_payload(connection),
                    model=model,
                    prompt=prompt,
                    reference=base64.b64encode(reference.read_bytes()).decode()
                    if reference
                    else None,
                    ratio=ratio,
                    size=spec.size,
                ),
                billable=True,
            )
            if len(result.get("image", "")) > 36 * 1024 * 1024:
                raise ValueError("Image too large")
            content, _, _ = normalize_image(base64.b64decode(result["image"], validate=True))
            return ImageResult(content, result.get("request_id"))
        except (ValueError, KeyError) as exc:
            raise ProviderFailure(
                "invalid_image", "模型已响应，但图片无法归档，请核对该任务。", uncertain=True
            ) from exc
        except ProviderFailure as exc:
            if exc.code == "credentials" and connection["auth_method"] == "api_key":
                try:
                    self.service.db.execute(
                        "UPDATE model_connections SET status='needs_attention',checked_at=? "
                        "WHERE id=? AND user_id=? AND version=? AND revoked_at IS NULL",
                        (
                            time.time(),
                            connection["id"],
                            connection["user_id"],
                            connection["version"],
                        ),
                    )
                except self.service.db.database_errors:
                    pass  # Preserve the provider failure if status storage is unavailable.
            if exc.code in {"bridge_unavailable", "bridge_timeout"}:
                raise ProviderFailure(
                    "provider_unknown", MESSAGES["provider_unknown"], uncertain=True
                ) from exc
            raise

    async def close(self):
        if self.image_client:
            await self.image_client.close()


class ModelRouter:
    def __init__(self, service, settings, fallback=None):
        self.service, self.settings = service, settings
        self.fallback = fallback or (OpenAIImageProvider(settings) if settings.ready else None)
        self.jobs = {}

    def for_job(self, row):
        if row["id"] not in self.jobs:
            self.jobs[row["id"]] = JobProvider(self.service, self.settings, row, self.fallback)
        return self.jobs[row["id"]]

    async def cleanup_job(self, job_id):
        provider = self.jobs.pop(job_id, None)
        if provider:
            await provider.close()

    async def close(self):
        for identifier in list(self.jobs):
            await self.cleanup_job(identifier)
        if self.fallback and hasattr(self.fallback, "close"):
            await self.fallback.close()
