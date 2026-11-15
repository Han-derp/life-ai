from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatRequest, ChatResponse, MessageResponse
from app.schemas.chat_history import (
    EditMessageRequest,
    EditMessageResponse,
    MessageImpactResponse,
    MessageOperationResponse,
)
from app.services import chat_service
from app.services.chat_service import list_messages, send_chat, stream_chat
from app.workflows import chat_history

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/messages", response_model=list[MessageResponse])
def get_messages(limit: int = Query(default=100, ge=1, le=500)):
    return list_messages(limit)


@router.post("", response_model=ChatResponse)
def post_chat(data: ChatRequest):
    """一次性返回整段回复。

    保留它是**有意**的：测试不必解析 SSE，而且流式链路出问题时它是一条
    随时可回退的路（前端也是这么用的）。
    """
    return send_chat(data.content, image=data.image)


@router.post("/stream")
def post_chat_stream(data: ChatRequest):
    """流式对话（S6）：`text/event-stream`。

    每个事件是一行 JSON：

    - ``{"type": "delta", "text": "…"}`` 逐字增量
    - ``{"type": "done", "content": "…", "assistant_message_id": 12}`` 结束
    - ``{"type": "error", "detail": "…"}`` 出错

    ``X-Accel-Buffering: no`` 是给反向代理看的：不加的话 nginx 会把流缓冲成
    "全部生成完再一次性返回"，流式就白做了。
    """
    return StreamingResponse(
        stream_chat(data.content, image=data.image),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------- 聊天记录的编辑操作 ----------
#
# ⚠️ 这一组的设计决定：**删聊天记录 ≠ 让他忘掉。**
# 抽取时你的原话被复制进了 L1，所以删掉消息之后，他记住的事实
# 和那条事实的出处**都还在**（否则记忆会变成"没有出处的断言"，违反不变量 5）。
#
# 所以每个破坏性操作都是三步：①先能问影响范围 ②默认只删聊天 ③显式选择连带删记忆。
# 界面必须把第①步的结果摆给用户看 —— 不隐藏、也不偷偷保留。


@router.get("/messages/{message_id}/impact", response_model=MessageImpactResponse)
def get_message_impact(message_id: int):
    """删这条消息会影响哪些记忆 —— **删之前必须能问**。"""
    return chat_history.impact_of_deleting(message_id)


@router.get("/rollback/{message_id}/impact", response_model=MessageImpactResponse)
def get_rollback_impact(message_id: int):
    """回滚到这条消息会影响哪些记忆（它之后的一切）。"""
    return chat_history.impact_of_rollback(message_id)


@router.delete("/messages/{message_id}", response_model=MessageOperationResponse)
def delete_message(
    message_id: int,
    with_memory: bool = Query(default=False),
    purge_archive: bool = Query(default=False),
):
    """删一条消息。

    - 默认**只删聊天记录** —— 他还记得
    - ``with_memory=true`` → 连他记的事实一起删
    - ``+ purge_archive=true`` → 连**档案**（L1 原话）也抹掉。
      但只在那些原话已经**没有任何观察引用**时才抹得掉 ——
      有引用还抹，那条事实的出处就指向一句不存在的话（不变量 5）。
    """
    return chat_history.delete_message(
        message_id, with_memory=with_memory, purge_archive=purge_archive
    )


@router.patch("/messages/{message_id}", response_model=EditMessageResponse)
def patch_message(
    message_id: int,
    data: EditMessageRequest,
    with_memory: bool = Query(default=False),
    purge_archive: bool = Query(default=False),
):
    """编辑你自己发过的消息。

    - ``with_memory=false``（默认）：只改聊天记录 —— **他继续按旧版本记**
    - ``with_memory=true``：删掉这条消息衍生的事实 + 把它标回"待抽取"，
      调度器几分钟内用新版本重抽
    - ``+ purge_archive=true``：连**旧的那份原话**也从档案里抹掉
      （只在它已无任何引用时）
    """
    return chat_history.edit_message(
        message_id,
        data.content,
        with_memory=with_memory,
        purge_archive=purge_archive,
    )


@router.post("/messages/{message_id}/regenerate", response_model=MessageResponse)
def post_regenerate(message_id: int):
    """重新生成这条回复（只对 assistant 消息有效）。

    **替换**掉旧的那条。上下文由 `send_chat` 用的**同一个 helper** 重建，
    所以两次不同只可能来自模型自己。
    """
    return chat_service.regenerate_reply(message_id)


@router.post("/rollback/{message_id}", response_model=MessageOperationResponse)
def post_rollback(
    message_id: int,
    with_memory: bool = Query(default=False),
    purge_archive: bool = Query(default=False),
):
    """回滚到某条消息：保留它和它之前的一切，删掉之后的。

    ⚠️ 默认这**只动聊天记录**。他不会因此忘掉那段时间 ——
    L2/L3 是独立的，"遗忘"是 `with_memory=true` + `purge_archive=true`。
    """
    return chat_history.rollback(
        message_id, with_memory=with_memory, purge_archive=purge_archive
    )
