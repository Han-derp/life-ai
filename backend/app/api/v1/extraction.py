from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.schemas.extraction import (
    ExtractionBatchRequest,
    ExtractionPendingResponse,
    ExtractionResultResponse,
)
from app.services import chat_service
from app.workflows import extraction

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryImportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=30_000)


@router.post("/import")
def post_import(data: MemoryImportRequest):
    """导入一份事实清单（每行一条，空行与 `#` 开头的行跳过）。

    ⚠️ 它**不是**直接写进记忆表 ✗ —— 而是当成一条"你说过的话"存下来 ✓，
    之后由**正常抽取流程**变成事实 ✓
    （所以每条都自动有出处 ✓ —— 不变量 5 不破 ✓）。

    导入之后点一次「现在抽取」（或等调度器 ✓）就能在**关于TA** 页看到 ✓。

    ⚠️ 它**不会**出现在聊天记录里，也**不会**进每次请求的最近聊天 ✓ ——
    这是一批数据，不是一次对话 ✓。
    """
    return chat_service.import_notes(data.text)


@router.get("/extract/pending", response_model=ExtractionPendingResponse)
def get_extraction_pending():
    """还有多少条消息没被抽成记忆。"""
    return {"pending_messages": len(extraction.pending_messages(limit=500))}


@router.post("/extract", response_model=ExtractionResultResponse)
def post_extract(data: ExtractionBatchRequest):
    """立刻抽一批，不用等调度器；使用「硬核」模型档。"""
    return extraction.extract_pending(limit=data.limit)
