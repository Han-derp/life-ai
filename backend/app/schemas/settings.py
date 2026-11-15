"""运行时设置的 API contract。"""

from pydantic import BaseModel, Field


class SettingsResponse(BaseModel):
    api_key_configured: bool
    api_key_hint: str
    model: str
    base_url: str
    recommended_models: list[str]
    providers: dict[str, str]
    tiers: dict[str, dict] = Field(default_factory=dict)
    configuration_warnings: list[str] = Field(default_factory=list)
    password_enabled: bool = False
    env_path: str


class SettingsUpdate(BaseModel):
    """留空 = 不改。"""

    api_key: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=100)
    tiers: dict[str, dict] | None = None
    app_password: str | None = Field(default=None, max_length=200)


class SettingsTestResponse(BaseModel):
    ok: bool
    detail: str | None = None
    reply: str | None = None
    model: str | None = None
    usage: dict | None = None
