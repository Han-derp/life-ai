"""人格的 API contract（DEVELOPMENT_PLAN v2.0 · S3）。"""

from pydantic import BaseModel, Field


class PersonaSummary(BaseModel):
    name: str  # 目录名
    display_name: str  # 界面显示名（meta.json，缺省为目录名）


class PersonaResponse(BaseModel):
    active: str
    active_display_name: str
    available: list[PersonaSummary]
    content: str


class PersonaActivateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class PersonaCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str | None = Field(default=None, max_length=64)
    content: str = Field(min_length=1, max_length=8000)
    activate: bool = True


class PersonaContentUpdate(BaseModel):
    """编辑**当前人格**的正文（界面上的"编辑人格"）。

    只能改激活的那个 —— 名字不进这个 contract，见 `persona_service`。
    """

    content: str = Field(min_length=10, max_length=8000)
