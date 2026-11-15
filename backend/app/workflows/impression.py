"""印象归纳（L2 → L3）—— 记忆闭环的最后一块。

把若干条**具体事件**归纳成**一条判断**。三条约束：

1. **必须有依据**（不变量 5）。`create_impression()` 会拒绝空证据，
   所以这里也不接受空列表 —— 一条没有事件的判断不该存在。
2. **具体、不定性**（设计原则 R3）。归纳出的是"写作类任务容易拖到最后一晚"，
   不是"你是个拖延的人"。**判断不能变成标签。**
3. **证据越少，置信度越低**，不许因为"说得挺像"就给高分 —— 那是猜。

⚠️ **"同类"靠标签判断**（MEMORY_DESIGN §4.2：**同标签 + 同对象 = 同类**），
而 §6.4 规定**她只能建议新标签、不能自己创建**。
所以**全新安装（还没有任何标签）时，自动归纳永远不会触发** —— 这是设计接受的
（标签掌握在你手里）。那种情况下走手动路径：
`POST /api/v1/memory/impressions/generate` 直接指定用哪几条观察。

⚠️ 归纳使用独立的 `impression` 任务档，便于配置不同的推理模型，
而记忆库整个建立在"不许编造"上（MODEL_USAGE_SPEC §1.1）。
"""

import json

from app.core.exceptions import DomainValidationError, ExternalServiceError
from app.core.prompt_loader import load_prompt
from app.llm import factory
from app.services import memory_service

#: 一个标签下攒够这么多条，才算"同类事件累积"，够格归纳成印象。
#: 依据 MEMORY_DESIGN §4.4：「低于 3 次更像猜，高于 3 次她反应太慢」。
FROM_TAG_MIN_COUNT = memory_service.REVISION_THRESHOLD


def _parse(text: str) -> dict | None:
    """解析模型返回的判断。

    `json_mode` 只是**强约束**，不是保证：它可能返回 `{}`、
    缺字段，或者明确地告诉我们"没有共同点"（`content: null`）。
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    content = str(data.get("content") or "").strip()
    if not content:
        return None

    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return {"content": content, "confidence": min(1.0, max(0.0, confidence))}


def _render_observations(observations: list[dict]) -> str:
    lines = [f"[{o['id']}] ({o['local_date']}) {o['content']}" for o in observations]
    return (
        "以下是若干条具体事件（编号只给你对位用，不要写进判断）：\n\n"
        + "\n".join(lines)
    )


def generate_from(observation_ids: list[int]) -> dict | None:
    """把这几条观察归纳成一条印象。

    返回新建的印象；模型认为"没有共同点"时返回 ``None``（什么都不建）。
    """
    if not observation_ids:
        raise DomainValidationError("归纳至少要有一条依据（不变量 5）。")

    observations = [memory_service.get_observation(oid) for oid in observation_ids]

    text = factory.get_provider("impression").chat(
        [
            {"role": "system", "content": load_prompt("impression.md")},
            {"role": "user", "content": _render_observations(observations)},
        ],
        temperature=0.4,
        json_mode=True,
    )

    parsed = _parse(text)
    if parsed is None:
        return None

    return memory_service.create_impression(
        parsed["content"],
        observation_ids,
        confidence=parsed["confidence"],
    )


def ripe_candidates(min_count: int = FROM_TAG_MIN_COUNT) -> list[dict]:
    """够格归纳的「同类」分组：同标签下、**还没被用过**的观察 ≥ ``min_count`` 条。

    为什么要排掉"用过的"：否则同一个标签每跑一次就会多出一条重复印象。
    新来的事件攒够 ``min_count`` 条之后会形成**下一条**判断
    （"第二次归纳"和"修订旧印象"是两条不同的路，见 TODO.md）。
    """
    used = memory_service.used_evidence_ids()
    candidates: list[dict] = []

    for tag in memory_service.list_tags():
        observations = memory_service.list_observations(tag_id=tag["id"], limit=200)
        fresh = [o for o in observations if o["id"] not in used]
        if len(fresh) >= min_count:
            candidates.append(
                {
                    "tag_id": tag["id"],
                    "tag_name": tag["name"],
                    "observation_ids": [o["id"] for o in fresh],
                }
            )

    return candidates


def classify_direction(impression: dict, observation_ids: list[int]) -> dict[int, str]:
    """判断新事件相对**已有判断**是"同方向"还是"反方向"（设计裁决 R6）。

    拿不准时提示词要求判 `support` —— 反例的代价大得多
    （会下调把握，攒够 3 次还会让她改写判断）。

    ⚠️ 解析失败时返回空 dict，而调用方把**缺失一律当 support** ——
    方向判错的默认值必须是保守的那一侧。
    """
    observations = [memory_service.get_observation(oid) for oid in observation_ids]

    text = factory.get_provider("impression").chat(
        [
            {"role": "system", "content": load_prompt("evidence_direction.md")},
            {
                "role": "user",
                "content": (
                    f"已有判断：{impression['content']}\n\n新事件：\n"
                    + "\n".join(
                        f"[{o['id']}] ({o['local_date']}) {o['content']}"
                        for o in observations
                    )
                ),
            },
        ],
        temperature=0.2,
        json_mode=True,
    )

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}

    items = data.get("directions")
    if not isinstance(items, list):
        return {}

    valid = {o["id"] for o in observations}
    directions: dict[int, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        oid = item.get("observation_id")
        direction = item.get("direction")
        # 只接受出现在本次输入里的 id（不信任模型传回来的编号）
        if oid in valid and direction in ("support", "counter"):
            directions[oid] = direction
    return directions


def _render_revision(
    impression: dict,
    supporting: list[dict],
    countering: list[dict],
) -> str:
    lines = [
        f"她原来的判断：{impression['content']}"
        f"（置信度 {impression['confidence']:.2f}）",
        "",
        f"支持它的事件（{len(supporting)} 条）：",
    ]
    lines += [
        f"- ({item['local_date']}) {item['content']}" for item in supporting
    ] or ["- 无"]
    lines += [
        "",
        f"**反对它的事件（{len(countering)} 条）—— 这些必须被吸收进新判断：**",
    ]
    lines += [
        f"- ({item['local_date']}) {item['content']}" for item in countering
    ] or ["- 无"]
    return "\n".join(lines)


def revise_stale() -> list[dict]:
    """把攒够反例的判断**重新想一遍**（设计裁决 R6 第三条路：3 次 → 修订）。

    这条以前是**缺的**：`needs_revision` 会亮，界面上也写着
    "他该重新想一遍了（等下一次归纳时他会改写这条判断）"，
    但没有任何东西真的去改 —— 那句话是**空头承诺**。

    挂在 `generate_pending()` 里跑，也就是界面上说的"下一次归纳"。
    `needs_revision` 在新一代建成后才归零，所以这里天然幂等：
    跑失败了下一轮会重试，跑成功了就不会再跑。
    """
    revised: list[dict] = []

    for impression in memory_service.list_impressions(include_superseded=False):
        if not impression["needs_revision"]:
            continue

        evidence = impression["evidence"]
        supporting = [item for item in evidence if item.get("relation") != "counter"]
        countering = [item for item in evidence if item.get("relation") == "counter"]
        if not countering:
            # 计数和证据对不上（不该发生）—— 宁可不动，也不要凭空改写
            continue

        text = factory.get_provider("impression").chat(
            [
                {"role": "system", "content": load_prompt("impression_revise.md")},
                {
                    "role": "user",
                    "content": _render_revision(impression, supporting, countering),
                },
            ],
            temperature=0.4,
            json_mode=True,
        )

        parsed = _parse(text)
        if parsed is None:
            continue

        # 新一代的证据方向会统一成 support —— 这是对的：
        # 那些反例已经被**吸收进**新判断了，它们现在是新判断的支撑。
        revised.append(
            memory_service.revise_impression(
                impression["id"],
                parsed["content"],
                [item["id"] for item in evidence],
                confidence=parsed["confidence"],
            )
        )

    return revised


def generate_pending(min_count: int = FROM_TAG_MIN_COUNT) -> dict:
    """把够格的分组逐个处理，返回三类计数。

    设计裁决 R6 规定的三条路 —— **中间那条是关键修正**：

    ```
    新证据进来
       ├─ 没有印象 + 同类 ≥min_count 条 → 创建印象 #1
       ├─ 有印象 + 同方向               → 只记 support 证据，**不创建新一代**
       └─ 有印象 + 反方向               → 记 counter（1 次标记 / 2 次降置信 / 3 次修订）
    ```

    给调度器用 —— **异步**，不在聊天路径上。
    云端不可用时直接停下（不要把每个分组都试一遍、每次都超时）。
    """
    counts = {"created": 0, "supported": 0, "countered": 0, "revised": 0}

    for candidate in ripe_candidates(min_count):
        ids = candidate["observation_ids"]
        existing = memory_service.impressions_for_tag(candidate["tag_id"])

        try:
            if not existing:
                if generate_from(ids) is not None:
                    counts["created"] += 1
                continue

            # 最新一代（impressions_for_tag 已按 generation 降序）
            target = existing[0]
            directions = classify_direction(target, ids)

            supporting = [oid for oid in ids if directions.get(oid, "support") == "support"]
            countering = [oid for oid in ids if directions.get(oid) == "counter"]

            if supporting:
                # ★ 关键修正：同方向**不创建新一代**，只往已有判断上补证据
                memory_service.add_supporting_evidence(target["id"], supporting)
                counts["supported"] += 1

            for oid in countering:
                memory_service.record_counterexample(target["id"], oid)
                counts["countered"] += 1
        except ExternalServiceError:
            break

    # R6 第三条路：攒够反例的判断重新想一遍。
    # 放在最后 —— 这一轮刚记下的反例当场就能触发修订，不用等下一次 tick。
    try:
        counts["revised"] = len(revise_stale())
    except ExternalServiceError:
        # 改写失败不该把前面已经做完的事一起丢掉；
        # needs_revision 还在，下一轮会重试。
        pass

    return counts
