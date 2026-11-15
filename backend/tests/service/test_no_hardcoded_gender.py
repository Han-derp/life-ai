"""设计裁决 R3：**代码和 prompt 里不许写死性别代词 / 人格名字。**

这个项目是**空模板分发**的 —— 每个人自己配人格，可能是女朋友、也可能是男朋友。
所以凡是需要称呼"对方"的地方，都要**从人格配置读**，不能写死。

⚠️ 一个例外：`personas/<name>/persona.md` **里面**写"你是某某"是
正确的 —— 那是人格自己的定义，不是通用代码。

⚠️ 另一个我犯过的错，记在这里免得重犯：
`_render_history()`（面板上的「最近聊天」）**不发给模型**，
所以它里面的代词是纯展示问题、没有行为影响。
当时我误以为它是"唯一告诉模型性别的线索"，判断错了。
"""

from app.ai.context import assembler
from app.schemas.memory import ObservationCreate
from app.services import memory_service


def _an_impression() -> dict:
    observation = memory_service.create_observation(
        ObservationCreate(content="她说下个月要考试", source_text="我下个月要考试")
    )
    return memory_service.create_impression(
        "压力大",
        [observation["id"]],
        confidence=0.8,
    )


# ---------- 面板上的说话人：读人格名，不写死 ----------


def test_the_panel_uses_the_current_persona_name(client):
    rendered = assembler._render_history(
        [{"role": "assistant", "content": "在。"}]
    )

    assert "行动搭档：" in rendered
    assert "他：" not in rendered
    assert "她：" not in rendered


def test_the_label_follows_the_active_persona(client, monkeypatch):
    """换成**别人格**之后说话人跟着变 —— 这才叫"不写死"。

    这条是真正的回归护栏：谁把名字换回写死的代词，它就会红。
    `template` 的 meta.json 里 display_name 是「（他的名字）」。
    """
    monkeypatch.setattr(assembler, "active_persona_name", lambda: "template")

    rendered = assembler._render_history(
        [{"role": "assistant", "content": "在。"}]
    )

    assert "（他的名字）" in rendered


def test_the_user_side_stays_you(client):
    """你自己那一侧永远是「你」，不受人格影响。"""
    rendered = assembler._render_history([{"role": "user", "content": "在吗"}])

    assert "你：" in rendered


# ---------- 进 prompt 的那一块：也必须没有代词 ----------


def test_the_impressions_block_has_no_hardcoded_pronoun(client):
    """记忆块的内容**是会进 prompt 的**，所以这里写死代词有真实影响。"""
    _an_impression()

    block = next(
        b for b in assembler.get_context_blocks() if b.id == "memory:impressions"
    )

    assert "他对你的判断" not in block.content
    assert "她对你的判断" not in block.content
    assert "关于你的判断" in block.content


def test_the_impressions_block_label_has_no_pronoun(client):
    """块标题会显示在 S2 面板上，同样是使用者看得见的东西。"""
    _an_impression()

    block = next(
        b for b in assembler.get_context_blocks() if b.id == "memory:impressions"
    )

    assert block.label == "关于你的判断"
