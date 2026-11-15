from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    #: 允许空 —— 可以只发一张图（"要么有字，要么有图"由 service 校验并给出人话报错）
    content: str = Field(default="", max_length=12000)
    #: ``data:image/jpeg;base64,...``。图片**会发给云端** —— 这是它唯一能被看见的方式。
    image: str | None = None


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    created_at: str
    model: str | None = None
    metadata: dict | None = None


class ChatResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse
