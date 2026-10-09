from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

ProviderId = Literal[
    "openai",
    "openrouter",
    "cloudflare",
    "anthropic",
    "google",
    "github-copilot",
    "openai-codex",
    "deepseek",
    "qwen",
    "custom",
]


class ConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: ProviderId
    name: str = Field(min_length=2, max_length=60)
    api_key: SecretStr
    preferred_model: str = Field(default="", max_length=200)
    base_url: str = Field(default="", max_length=2000)
    model_id: str = Field(default="", max_length=200)
    model_name: str = Field(default="", max_length=120)
    supports_image: bool = False
    account_id: str = Field(default="", pattern=r"^(?:[0-9a-fA-F]{32})?$", max_length=32)

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        key = value.get_secret_value().strip()
        if not 8 <= len(key) <= 8192 or any(ord(char) < 32 for char in key):
            raise ValueError("请输入有效的 API Key")
        return SecretStr(key)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("请输入连接名称")
        return value.strip()


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_connection_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    image_model: str = Field(default="", max_length=200)
    text_connection_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    text_model: str = Field(default="", max_length=200)


class OAuthStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: ProviderId
    name: str = Field(min_length=2, max_length=60)
    preferred_model: str = Field(default="", max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("请输入连接名称")
        return value.strip()


class OAuthAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    answer: SecretStr

    @field_validator("answer")
    @classmethod
    def bounded(cls, value):
        if not 1 <= len(value.get_secret_value()) <= 8192:
            raise ValueError("授权信息长度无效")
        return value


class OAuthCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: SecretStr

    @field_validator("answer")
    @classmethod
    def bounded(cls, value):
        if not 1 <= len(value.get_secret_value()) <= 8192:
            raise ValueError("授权信息长度无效")
        return value
