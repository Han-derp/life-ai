from fastapi import APIRouter

from app.schemas.persona import (
    PersonaActivateRequest,
    PersonaContentUpdate,
    PersonaCreateRequest,
    PersonaResponse,
)
from app.services.persona_service import (
    create_persona,
    get_persona_state,
    save_persona_content,
    set_active_persona,
)

router = APIRouter(prefix="/persona", tags=["persona"])


@router.get("", response_model=PersonaResponse)
def get_persona():
    """当前人格 + 可用人格列表 + 人格全文（S3）。"""
    return get_persona_state()


@router.put("/content", response_model=PersonaResponse)
def put_persona_content(data: PersonaContentUpdate):
    """编辑**当前人格**的正文。

    ⚠️ 和 `POST /persona`（新建，拒绝覆盖）不同：**这里允许覆盖** ——
    用户在界面上点的是"编辑"，那是明确的意思表示 ✓。
    写之前会留一份 `persona.md.bak` ✓，所以改坏了能捞回来。

    改完**不用重启**：人格文件是按修改时间缓存的 ✓。
    """
    return save_persona_content(data.content)


@router.post("/activate", response_model=PersonaResponse)
def post_activate_persona(data: PersonaActivateRequest):
    """切换当前人格。写 app_settings，不需要重启。"""
    set_active_persona(data.name)
    return get_persona_state()


@router.post("", response_model=PersonaResponse, status_code=201)
def post_persona(data: PersonaCreateRequest):
    """新建一个人格。

    已存在则返回 400——**编辑**请直接改 `personas/<name>/persona.md`，
    避免应用内误覆盖现有的人格。
    """
    return create_persona(data.name, data.content, data.activate, data.display_name)
