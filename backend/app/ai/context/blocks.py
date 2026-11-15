"""上下文块数据结构（DEVELOPMENT_PLAN v2.0 · S1）。

把发给模型的 system prompt 从"一个不可分割的字符串"改成
"一组可独立开关、可整块丢弃的块"。

为什么需要它：

1. 逐块开关（S2「这次会发送什么」面板）要求上下文先结构化。
2. 整块丢弃（而不是从句子中间硬切），避免把中文句子切成一半。
3. 按"变化频率"排列块，让 DeepSeek KV 缓存的前缀稳定命中。
"""

from dataclasses import dataclass

# kind：块的来源类别
KIND_PERSONA = "persona"
KIND_RULES = "rules"
KIND_MEMORY = "memory"
KIND_TODAY = "today"
KIND_HISTORY = "history"
KIND_MESSAGE = "message"

# state：块是否发送
STATE_INCLUDE = "include"  # 正常发送（默认）
STATE_EXCLUDE_ONCE = "exclude_once"  # 只这次不发，下次恢复
STATE_EXCLUDE_ALWAYS = "exclude_always"  # 永久不发

# sensitivity：给面板做视觉分组用（不参与发送决策）
SENSITIVITY_NORMAL = "normal"
SENSITIVITY_SENSITIVE = "sensitive"


@dataclass
class ContextBlock:
    """一个可独立开关、可整块丢弃的上下文块。"""

    id: str  # "persona" / "today:schedule" / "memory:42"
    kind: str  # persona|rules|memory|today|history|message
    label: str  # 给人看的标签，例如"今日日程"
    content: str  # 实际文本
    tokens: int = 0  # 估算 token（由 assembler 填充）
    source: str = ""  # 出处："L3 印象 #42" / "10/12 你说的话"
    sensitivity: str = SENSITIVITY_NORMAL
    state: str = STATE_INCLUDE
    priority: int = 0  # 越大越先保留（丢弃时按升序丢）

    @property
    def is_excluded(self) -> bool:
        return self.state in (STATE_EXCLUDE_ONCE, STATE_EXCLUDE_ALWAYS)
