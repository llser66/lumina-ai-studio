from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Style = Literal["natural", "editorial", "cinematic", "3d", "illustration", "anime"]


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=2, max_length=32)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError("请输入至少两个字符的名字")
        return value


class GenerationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=3, max_length=2000)
    negative_prompt: str = Field(default="", max_length=500)
    style: Style = "natural"
    size: Literal["1024x1024", "1536x1024", "1024x1536"] = "1024x1024"
    quality: Literal["low", "medium", "high"] = "medium"
    enhance: bool = True
    reference_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")

    @field_validator("prompt")
    @classmethod
    def clean_prompt(cls, value):
        value = value.strip()
        if len(value) < 3:
            raise ValueError("请至少用三个字符描述画面")
        return value


class FavoriteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    favorite: bool
