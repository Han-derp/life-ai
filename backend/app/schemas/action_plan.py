from pydantic import BaseModel, Field

#: 七种「卡在哪」✓ —— 和 `AI_COMPANION_REDESIGN.md` / `ACTION_CENTER_MVP.md` 一致 ✓。
#: 模型必须从这七个里选一个 ✓（选不出来就留着 null ✗，不要瞎猜 ✓）。
BLOCKING_TYPES = (
    "unclear",
    "too_large",
    "low_energy",
    "time_pressure",
    "stalled",
    "blocked",
    "review",
)

#: 步骤的四个状态 ✓ —— `ACTION_CENTER_MVP.md` 第 2 条 ✓。
STEP_STATUSES = ("pending", "active", "completed", "skipped")


class ActionStep(BaseModel):
    """草案里的**一步** ✓（AI 返回的 / 用户手填的 ✓）。"""

    title: str = Field(min_length=1, max_length=200)
    minutes: int = Field(default=25, ge=1, le=480)
    priority: str = Field(default="medium", pattern="^(low|medium|high)$")


class ActionPlanCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)
    minimum_plan: str | None = Field(default=None, max_length=500)
    fallback_plan: str | None = Field(default=None, max_length=500)
    steps: list[ActionStep] = Field(min_length=1, max_length=20)

    # ↓ 2026-10-05 加（`ACTION_CENTER_MVP.md` 的草案结构 ✓）——
    # 三个字段**全部可选** ✓，旧数据、手工建的计划都不受影响 ✓
    # ⚠️ `pattern` 是 `re.search` ✓，**必须加锚点** ✗ —— 否则 "xlow_energyx" 也算合法 ✓
    blocking_type: str | None = Field(
        default=None, pattern="^(" + "|".join(BLOCKING_TYPES) + ")$"
    )
    assumptions: list[str] = Field(default_factory=list, max_length=10)
    needs_confirmation: list[str] = Field(default_factory=list, max_length=10)


class ActionPlanResponse(ActionPlanCreate):
    id: int
    status: str
    created_at: str
    updated_at: str
    #: `action_plan_steps` 里的可执行步骤 ✓（`steps` 那份是**原始草案** ✓）
    executable_steps: list["ActionPlanStepOut"] = Field(default_factory=list)


class ActionPlanStepOut(BaseModel):
    """一条**可执行**的步骤 ✓（`action_plan_steps` 表 ✓）。"""

    id: int
    plan_id: int
    position: int
    title: str
    minutes: int
    priority: str
    status: str
    estimated_minutes: int | None = None
    actual_minutes: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    interruption_count: int = 0
    skip_reason: str | None = None
    pomodoro_count: int = 0
    deadline_id: int | None = None
    schedule_id: int | None = None
    #: 用户自己的两个判断 ✓（2026-10-05 第三轮 ✓）—— **分开** ✗：
    #: `importance` = 价值（0–3）/ `difficulty` = 执行负担（0–3）。
    #: `None` = **还没标记** ✓（不是 0 —— "没想过"和"一点也不重要"是两回事 ✗）。
    importance: int | None = None
    difficulty: int | None = None


class StepAdjustRequest(BaseModel):
    """调整一步 ✓ —— **只改传进来的字段** ✓。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    minutes: int | None = Field(default=None, ge=1, le=480)
    priority: str | None = Field(default=None, pattern="^(low|medium|high)$")
    #: **重要 / 困难分开** ✓ —— 见 `ActionPlanStepOut` 的说明 ✓。
    importance: int | None = Field(default=None, ge=0, le=3)
    difficulty: int | None = Field(default=None, ge=0, le=3)


class StepSkipRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class StepMaterializeRequest(BaseModel):
    """把一步**落地**成真实任务 ✓（③）。

    ⚠️ **不默认塞进日历** ✗（用户 2026-10-05 明确要求 ✓）——
    必须**明确指定**要哪种 ✓：

    - `deadline` ✓：有截止时间 → 建 DDL（要传 `due_at` ✓）
    - `schedule` ✓：有具体时间 → 建日程（要传 `start_at` + `end_at` ✓）
    - `none` ✓：**默认** —— 只留作行动步骤 ✗「不要制造大量无效安排」
    """

    kind: str = Field(default="none", pattern="^(none|deadline|schedule)$")
    due_at: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    materialize_key: str | None = Field(default=None, min_length=1, max_length=120)


class ActionPlanDraftRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ActionPlanDraftResponse(ActionPlanCreate):
    """AI 出的**草稿** ✓ —— 还没入库 ✓，用户确认后才保存 ✓。

    ⚠️ `source` 是**给用户看的实话** ✓：

    - `"ai"` ✓ —— 它真的生成成功了
    - `"template"` ✗ —— **AI 这次没成功**，这是**保底模板** ✓

    有了它，界面才能写清"这是模板，不是 AI 想的" ✓
    —— 否则用户会以为自己在看 AI 的方案 ✗（2026-10-05 特意加的 ✓）。
    """

    source: str = Field(default="ai", pattern="^(ai|template)$")
