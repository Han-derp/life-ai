"""S3 人格可配置化。

对应 DEVELOPMENT_PLAN v2.0 · S3 的完成判据：

- 改 `personas/<name>/` 就能换一个人格，**不改任何代码**
- 空模板 + README 能让一个陌生人配出自己的她
- `behavior_rules.md`（通用骨架）独立于人格，不被覆盖
"""

from app.core import persona_loader
from app.core.exceptions import DomainValidationError, NotFoundError
from app.core.prompt_loader import load_prompt
from app.core.time_utils import to_utc_iso, utc_now
from app.database.connection import get_connection
from app.services import persona_service


def _set_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value, to_utc_iso(utc_now())),
        )


def _make_persona(root, name: str, text: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "persona.md").write_text(text, encoding="utf-8")


# ---------- 默认状态 ----------


def test_default_persona_is_the_action_partner():
    """默认人格是「行动搭档」✓ —— 不是人物 ✓（2026-10-05 换的 ✓）。"""
    assert persona_service.active_persona_name() == "companion"


def test_persona_content_comes_from_the_persona_file():
    """意图是"人格来自那个文件，而且是有内容的人格"。

    ⚠️ 原来还断言 ``text.endswith("直接承认。")`` ✗ —— 那是**锁死最后一句话**，
    于是用户改一改自己的人格（那是这份文件唯一的用途）测试就红 ✗。
    同一类错误在这个项目里犯过三次：**锁措辞，而不是锁含义**。
    """
    text = persona_loader.load_persona_text("companion")

    assert "行动搭档" in text
    assert len(text) > 200  # 空人格会让她退回客服腔
    assert not text.endswith("…")  # 没被截断


def test_list_personas_contains_default_and_template():
    names = persona_loader.list_personas()

    assert "companion" in names
    assert "template" in names


def test_persona_core_md_is_no_longer_a_persona():
    """人格只有一份：旧文件已改成迁移说明，不再是人格内容。

    ⚠️ 这里断言的是**意图**（"它已经不是人格正文了"）✓，
    不是某个名字 ✗ —— 我第一版改成 ``"行动搭档" not in old``，
    结果同一次提交里又往那份文件写了"默认是行动搭档" ✗ → 自己把自己测红 ✓。
    """
    old = load_prompt("persona_core.md")

    assert "已迁移" in old  # 它自己说"已迁移"
    assert "【身份】" not in old  # 人格正文的开头标记不在
    assert "personas" in old  # 指向正确的地方


# ---------- 不改代码就能换人格（完成判据 1） ----------


def test_switching_persona_needs_no_code_change(monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "another_her", "你是另一个人。")

    assert persona_loader.load_persona_text("another_her") == "你是另一个人。"

    persona_service.set_active_persona("another_her")
    assert persona_service.active_persona_name() == "another_her"


def test_active_persona_falls_back_when_stored_name_is_gone(monkeypatch, tmp_path):
    """数据库里存着一个已被删掉的人格名时，不能让应用起不来。"""
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。")

    _set_setting("persona", "deleted_one")

    assert persona_service.active_persona_name() == "companion"


def test_activate_unknown_persona_raises_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。")

    try:
        persona_service.set_active_persona("nope")
    except NotFoundError:
        pass
    else:  # pragma: no cover
        raise AssertionError("应抛出 NotFoundError")


# ---------- 新建 ----------


def test_create_persona_writes_file_and_activates(monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。")

    state = persona_service.create_persona("my_her", "你是我的她。", activate=True)

    assert state["active"] == "my_her"
    assert any(item["name"] == "my_her" for item in state["available"])
    assert (tmp_path / "my_her" / "persona.md").read_text(encoding="utf-8").strip() == "你是我的她。"
    assert state["content"] == "你是我的她。"


def test_create_persona_refuses_to_overwrite(monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。")

    try:
        persona_service.create_persona("companion", "覆盖掉她")
    except DomainValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("应拒绝覆盖已存在的人格")


def test_unsafe_persona_name_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)

    for bad in ("../evil", "a/b", "", "..", "with space"):
        assert persona_loader.is_valid_persona_name(bad) is False

    try:
        persona_loader.persona_path("../evil")
    except DomainValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("应拒绝路径穿越式的人格名")


# ---------- 通用骨架独立于人格（完成判据 3） ----------


def test_universal_skeleton_has_the_hard_bans():
    """通用骨架必须带着那些硬禁止项。

    ⚠️ 断言的是**规则内容**，不是标题。
    原来这里断言 "【事实规则】" / "【督促规则】" / "【禁止项" 这几个**标题** ——
    于是把提示词从"系统说明书"改写成"一个人的口吻"时（那是正当改动：
    模型会照着系统口吻复述"我是个虚拟助手"），这几条立刻红了，
    而它们想守住的**规则本身一条都没丢**。
    标题会变、规则不该变 —— 所以只锁规则。
    """
    rules = load_prompt("behavior_rules.md")

    for ban in ("你去哪了", "你欠我", "你几天没来", "不下命令", "状态下滑"):
        assert ban in rules, ban


def test_persona_files_do_not_duplicate_the_universal_skeleton():
    """人格文件不该重复写通用骨架的**禁止项**——否则两边会漂移。

    同样改成锁内容：那几条禁止项的原句不该出现在人格文件里。
    """
    for name in persona_loader.list_personas():
        text = persona_loader.load_persona_text(name)
        for ban in ("你欠我", "你去哪了", "你几天没来"):
            assert ban not in text, f"{name}: {ban}"


def test_context_persona_block_comes_from_the_active_persona(monkeypatch, tmp_path):
    from app.ai.context import assembler

    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。")
    _make_persona(tmp_path, "another_her", "你是另一个人。")

    persona_service.set_active_persona("another_her")
    blocks = {b.id: b for b in assembler.get_context_blocks()}

    assert blocks["persona"].content == "你是另一个人。"
    assert "another_her" in blocks["persona"].source
    assert "another_her" in blocks["persona"].label


def test_rules_block_is_the_same_for_every_persona(monkeypatch, tmp_path):
    from app.ai.context import assembler

    monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)
    _make_persona(tmp_path, "companion", "默认人格。")
    _make_persona(tmp_path, "another_her", "你是另一个人。")

    persona_service.set_active_persona("companion")
    first = {b.id: b for b in assembler.get_context_blocks()}["rules"].content

    persona_service.set_active_persona("another_her")
    second = {b.id: b for b in assembler.get_context_blocks()}["rules"].content

    assert first == second
