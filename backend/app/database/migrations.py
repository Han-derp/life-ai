from datetime import datetime, timezone

from app.database.connection import get_connection


MIGRATION_001 = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS schedule_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    title TEXT NOT NULL,
    description TEXT,

    start_at TEXT NOT NULL,
    end_at TEXT,

    completion_status TEXT NOT NULL
        DEFAULT 'pending'
        CHECK (
            completion_status IN (
                'pending',
                'completed',
                'cancelled'
            )
        ),

    source_type TEXT NOT NULL
        DEFAULT 'manual',

    note TEXT,

    completed_at TEXT,
    cancelled_at TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS deadlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    title TEXT NOT NULL,
    description TEXT,

    due_at TEXT NOT NULL,

    parent_id INTEGER,

    completion_status TEXT NOT NULL
        DEFAULT 'pending'
        CHECK (
            completion_status IN (
                'pending',
                'completed',
                'cancelled'
            )
        ),

    priority INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,

    completed_at TEXT,
    cancelled_at TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY(parent_id)
        REFERENCES deadlines(id)
        ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS habits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    name TEXT NOT NULL,

    tracking_type TEXT NOT NULL
        CHECK (
            tracking_type IN (
                'boolean',
                'numeric',
                'duration',
                'time'
            )
        ),

    aggregation_mode TEXT
        CHECK (
            aggregation_mode IS NULL
            OR aggregation_mode IN (
                'sum',
                'latest',
                'max',
                'min'
            )
        ),

    unit TEXT,

    target_value REAL,
    target_text TEXT,

    target_operator TEXT
        CHECK (
            target_operator IS NULL
            OR target_operator IN (
                '>=',
                '<=',
                '=',
                '>',
                '<'
            )
        ),

    time_anchor_minute INTEGER,

    active INTEGER NOT NULL DEFAULT 1
        CHECK (
            active IN (0, 1)
        ),

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS habit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    habit_id INTEGER NOT NULL,
    local_date TEXT NOT NULL,

    value_bool INTEGER
        CHECK (
            value_bool IS NULL
            OR value_bool IN (0, 1)
        ),

    value_number REAL,
    value_text TEXT,

    note TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE(habit_id, local_date),

    FOREIGN KEY(habit_id)
        REFERENCES habits(id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS pomodoro_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    started_at TEXT NOT NULL,
    ended_at TEXT,

    planned_minutes INTEGER NOT NULL,
    actual_minutes INTEGER,

    status TEXT NOT NULL
        DEFAULT 'running'
        CHECK (
            status IN (
                'running',
                'completed',
                'cancelled'
            )
        ),

    schedule_event_id INTEGER,
    deadline_id INTEGER,

    custom_label TEXT,
    note TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY(schedule_event_id)
        REFERENCES schedule_events(id)
        ON DELETE SET NULL,

    FOREIGN KEY(deadline_id)
        REFERENCES deadlines(id)
        ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS timeline_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    title TEXT NOT NULL,

    start_at TEXT NOT NULL,
    end_at TEXT,

    source_type TEXT NOT NULL
        DEFAULT 'manual',

    source_id INTEGER,

    note TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    title TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    conversation_id INTEGER NOT NULL,

    role TEXT NOT NULL
        CHECK (
            role IN (
                'user',
                'assistant'
            )
        ),

    content TEXT NOT NULL,

    model TEXT,
    metadata_json TEXT,

    created_at TEXT NOT NULL,

    FOREIGN KEY(conversation_id)
        REFERENCES conversations(id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS daily_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    local_date TEXT NOT NULL UNIQUE,

    stats_json TEXT NOT NULL,

    ai_summary TEXT,
    affirmation TEXT,
    main_issue TEXT,

    tomorrow_focus_json TEXT,

    raw_ai_json TEXT,

    status TEXT NOT NULL
        DEFAULT 'draft'
        CHECK (
            status IN (
                'draft',
                'confirmed'
            )
        ),

    confirmed_at TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE INDEX IF NOT EXISTS
    idx_schedule_start_at
ON schedule_events(start_at);

CREATE INDEX IF NOT EXISTS
    idx_schedule_status
ON schedule_events(completion_status);

CREATE INDEX IF NOT EXISTS
    idx_deadlines_due_at
ON deadlines(due_at);

CREATE INDEX IF NOT EXISTS
    idx_deadlines_parent
ON deadlines(parent_id);

CREATE INDEX IF NOT EXISTS
    idx_deadlines_status
ON deadlines(completion_status);

CREATE INDEX IF NOT EXISTS
    idx_habit_logs_date
ON habit_logs(local_date);

CREATE INDEX IF NOT EXISTS
    idx_habit_logs_habit
ON habit_logs(habit_id);

CREATE INDEX IF NOT EXISTS
    idx_pomodoro_started
ON pomodoro_sessions(started_at);

CREATE INDEX IF NOT EXISTS
    idx_pomodoro_deadline
ON pomodoro_sessions(deadline_id);

CREATE INDEX IF NOT EXISTS
    idx_pomodoro_schedule
ON pomodoro_sessions(schedule_event_id);

CREATE INDEX IF NOT EXISTS
    idx_timeline_start
ON timeline_events(start_at);

CREATE INDEX IF NOT EXISTS
    idx_messages_conversation_created
ON messages(
    conversation_id,
    created_at
);

CREATE INDEX IF NOT EXISTS
    idx_daily_reviews_date
ON daily_reviews(local_date);

CREATE UNIQUE INDEX IF NOT EXISTS
    idx_timeline_source_unique
ON timeline_events(
    source_type,
    source_id
)
WHERE source_id IS NOT NULL;
"""


MIGRATION_002 = """
CREATE TABLE IF NOT EXISTS context_exclusions (
    block_id TEXT PRIMARY KEY,
    excluded_at TEXT NOT NULL
);
"""


# S4：记忆库四层 + 标签（DEVELOPMENT_PLAN §S4 / MEMORY_DESIGN §4）
#
#   L1 raw_entries        你的原话/原文（只追加，AI 永不改写）
#   L2 observations       原子事实 + 时间戳 + 出处 + 置信度
#   L3 impressions        印象（代际 + 置信度 + 同类反例计数）
#      impression_evidence  印象 ↔ 事件（多对多）——不变量 5 的落点
#   LR relationship       称呼 / 梗 / 关系节点 / 分歧复盘
#      commitments        承诺（结构化、可验证守约）
#   tags / observation_tags 标签（用户可增删改；删标签只取消分类）
#
# 刻意不做的事：不加三层任务树（沿用 V1 的一层 milestone）；不上向量库。
MIGRATION_003 = """
CREATE TABLE IF NOT EXISTS raw_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    source_type TEXT NOT NULL
        DEFAULT 'chat'
        CHECK (
            source_type IN (
                'chat',
                'manual',
                'import'
            )
        ),

    content TEXT NOT NULL,

    occurred_at TEXT,

    created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    content TEXT NOT NULL,

    observed_at TEXT NOT NULL,
    local_date TEXT NOT NULL,

    raw_entry_id INTEGER,

    confidence REAL NOT NULL DEFAULT 1.0
        CHECK (
            confidence >= 0
            AND confidence <= 1
        ),

    excluded_at TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY(raw_entry_id)
        REFERENCES raw_entries(id)
        ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS impressions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    content TEXT NOT NULL,

    generation INTEGER NOT NULL DEFAULT 1,

    confidence REAL NOT NULL DEFAULT 0.5
        CHECK (
            confidence >= 0
            AND confidence <= 1
        ),

    status TEXT NOT NULL
        DEFAULT 'active'
        CHECK (
            status IN (
                'active',
                'disputed',
                'superseded'
            )
        ),

    counterexamples INTEGER NOT NULL DEFAULT 0,

    superseded_by INTEGER,

    excluded_at TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY(superseded_by)
        REFERENCES impressions(id)
        ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS impression_evidence (
    impression_id INTEGER NOT NULL,
    observation_id INTEGER NOT NULL,

    created_at TEXT NOT NULL,

    PRIMARY KEY (
        impression_id,
        observation_id
    ),

    FOREIGN KEY(impression_id)
        REFERENCES impressions(id)
        ON DELETE CASCADE,

    FOREIGN KEY(observation_id)
        REFERENCES observations(id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    name TEXT NOT NULL UNIQUE,

    dimension TEXT NOT NULL
        CHECK (
            dimension IN (
                'state',
                'domain',
                'object',
                'pattern',
                'time'
            )
        ),

    created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS observation_tags (
    observation_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,

    PRIMARY KEY (
        observation_id,
        tag_id
    ),

    FOREIGN KEY(observation_id)
        REFERENCES observations(id)
        ON DELETE CASCADE,

    FOREIGN KEY(tag_id)
        REFERENCES tags(id)
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS relationship (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    kind TEXT NOT NULL
        CHECK (
            kind IN (
                'address',
                'meme',
                'milestone',
                'dispute',
                'preference'
            )
        ),

    content TEXT NOT NULL,

    raw_entry_id INTEGER,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY(raw_entry_id)
        REFERENCES raw_entries(id)
        ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    content TEXT NOT NULL,
    source_text TEXT,

    structured_json TEXT,

    effective_from TEXT NOT NULL,
    expires_at TEXT,

    status TEXT NOT NULL
        DEFAULT 'active'
        CHECK (
            status IN (
                'active',
                'changed',
                'revoked'
            )
        ),

    created_via TEXT NOT NULL
        DEFAULT 'chat'
        CHECK (
            created_via IN (
                'chat',
                'manual'
            )
        ),

    superseded_by INTEGER,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY(superseded_by)
        REFERENCES commitments(id)
        ON DELETE SET NULL
);


CREATE INDEX IF NOT EXISTS
    idx_raw_entries_created
ON raw_entries(created_at);

CREATE INDEX IF NOT EXISTS
    idx_observations_local_date
ON observations(local_date);

CREATE INDEX IF NOT EXISTS
    idx_observations_observed_at
ON observations(observed_at);

CREATE INDEX IF NOT EXISTS
    idx_observations_raw_entry
ON observations(raw_entry_id);

CREATE INDEX IF NOT EXISTS
    idx_impressions_status
ON impressions(status);

CREATE INDEX IF NOT EXISTS
    idx_impression_evidence_observation
ON impression_evidence(observation_id);

CREATE INDEX IF NOT EXISTS
    idx_observation_tags_tag
ON observation_tags(tag_id);

CREATE INDEX IF NOT EXISTS
    idx_tags_dimension
ON tags(dimension);

CREATE INDEX IF NOT EXISTS
    idx_commitments_status
ON commitments(status);
"""


# S4 续：印象的态度标记（认同 / 异议）。
#
# 为什么不直接改 003：003 可能**已经被应用过**（用户可能已经启动过服务）。
# 已发布的脚本一旦被应用就不能再改，否则"已升级的库"和"新建的库"会长得不一样。
#
# 不在 ADD COLUMN 上写 CHECK：SQLite 对 ALTER TABLE ADD COLUMN 的约束有限制。
# 取值由 schemas 层的 Literal + service 层二次校验保证。
MIGRATION_004 = """
ALTER TABLE impressions
ADD COLUMN attitude TEXT NOT NULL DEFAULT 'none';
"""


# S7：她的规则（her_rules）。
#
# 规则与承诺是两张表（S7 原文："her_rules（规则）+ 复用 commitments（承诺）"）：
#   her_rules    约束**她**的行为（"23:00 之后不主动联系"）
#   commitments  她答应过**你**什么（S4 已建）
#
# 三条硬规则落在字段上（MEMORY_DESIGN §5.4）：
#   1. source_text —— 必须能显示**来源原话**，你才看得出她是不是理解错了
#   2. expires_at  —— 必须有**期限**："我今天不想被打扰" ≠ "以后都别打扰"
#   3. source      —— 创建方式；界面手改的（manual）**优先于**她从聊天理解的（chat）
MIGRATION_005 = """
CREATE TABLE IF NOT EXISTS her_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    content TEXT NOT NULL,
    source_text TEXT,

    source TEXT NOT NULL
        DEFAULT 'manual'
        CHECK (
            source IN (
                'manual',
                'chat'
            )
        ),

    effective_from TEXT NOT NULL,
    expires_at TEXT,

    status TEXT NOT NULL
        DEFAULT 'active'
        CHECK (
            status IN (
                'active',
                'paused',
                'revoked'
            )
        ),

    note TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);


CREATE INDEX IF NOT EXISTS
    idx_her_rules_status
ON her_rules(status);

CREATE INDEX IF NOT EXISTS
    idx_her_rules_expires
ON her_rules(expires_at);
"""


# S8：她主动联系过的记录（机制一：惦记 / 机制二：留存）。
#
# 三个设计要点落在这里：
#   1. ``delivered_at`` —— 机制二的小作文**不推送**，你回来时才展示。
#      所以"产生"和"送达"是两个时刻，必须分开存。
#   2. ``responded_at`` —— 你没回，她就降频；回了就恢复。
#   3. 判断"这次沉默期里已经发过什么"，靠 ``created_at > 最后一条用户消息时间``，
#      **不需要额外的状态表**：你回一句话，沉默期自然就结束了（这是 S8 最省事的地方）。
MIGRATION_006 = """
CREATE TABLE IF NOT EXISTS proactive_contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    kind TEXT NOT NULL
        CHECK (
            kind IN (
                'care',
                'silence_3d',
                'silence_7d',
                'essay'
            )
        ),

    content TEXT NOT NULL,

    slot TEXT,

    created_at TEXT NOT NULL,
    delivered_at TEXT,
    responded_at TEXT
);


CREATE INDEX IF NOT EXISTS
    idx_proactive_created
ON proactive_contacts(created_at);

CREATE INDEX IF NOT EXISTS
    idx_proactive_kind
ON proactive_contacts(kind);
"""


# S8 · B 类基线预警。
#
# **为什么单独一张表，而不是往 proactive_contacts 里加一种 kind**：
#
#   1. 它是"检测到的偏离"，不一定是"她找你说的话"——可能只被记下来，
#      也可能你哪天问起时才被说出来（方案 §5.1-B 的护栏）。
#   2. 同一段偏离不该反复提醒，所以需要 ``dismissed_at`` 这样的状态。
#   3. 更要紧的是：**"只有 2 项能触发预警"这条规则直接落在 CHECK 上**——
#      想加第三种，你得改这张表，而改的时候你会想起这条规矩。
#
# 另外：这张表不碰 ``proactive_contacts`` 已有的 CHECK 约束。
# 要往那张表里加 kind，SQLite 得整表重建（DROP + RENAME），
# 那是本次改动里唯一可能把已有数据库弄坏的操作，不值得为省一张表去冒。
MIGRATION_007 = """
CREATE TABLE IF NOT EXISTS baseline_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    kind TEXT NOT NULL
        CHECK (
            kind IN (
                'late_night',
                'deadline_stall'
            )
        ),

    detail_json TEXT NOT NULL,
    content TEXT,

    detected_at TEXT NOT NULL,
    voiced_at TEXT,
    dismissed_at TEXT,

    created_at TEXT NOT NULL
);


CREATE INDEX IF NOT EXISTS
    idx_baseline_kind
ON baseline_alerts(kind);

CREATE INDEX IF NOT EXISTS
    idx_baseline_detected
ON baseline_alerts(detected_at);
"""


# S4 修正（设计裁决 R6）：证据要区分**方向**。
#
# 没有它，"已经有印象之后又来 3 条同方向事件"只能形成**下一条印象** ——
# 1. 那是噪声，不是记忆（"你有点累"重复 47 次）
# 2. 会污染 §7.3 的演化史："她怎么改了看法"变成同一个结论的重复列表
#    而且**从第一天就开始污染，没法局部修复**
#
# ⚠️ 不在 ADD COLUMN 上写 CHECK：SQLite 对 ALTER TABLE ADD COLUMN 的约束有限制
# （MIGRATION_004 出于同样的原因也没写）。取值合法性由 `memory_service` 保证。
MIGRATION_008 = """
ALTER TABLE impression_evidence
ADD COLUMN relation TEXT NOT NULL DEFAULT 'support';
"""


# S4/R7+R8（设计裁决）：两件事一起落。
#
# 1. **预置标签** —— 解冷启动：全新安装没有任何标签，"同类"就凑不出来，
#    归纳永远不会触发。裁决 R7 明确这是**产品的一部分**，
#    不算"AI 自己造标签"。
# 2. **tag_suggestions** —— 抽取匹不上任何标签时，她**提建议**而不是硬塞（R7/R8）。
#
# 建议表**独立建**、不塞进 `app_settings`（裁决 R8）：
# 键值表在"同一个标签被建议多次"时会产生竞态（读出 → 解析 → 去重 → 写回）。
#
# `count` 是关键字段 —— 它让"她反复注意到同一件事"自己浮出来：
# 按次数降序排，这本身就是一种洞察，比按时间堆着有用得多。
MIGRATION_009 = """
CREATE TABLE IF NOT EXISTS tag_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    name TEXT NOT NULL,

    dimension TEXT NOT NULL
        CHECK (
            dimension IN (
                'state',
                'domain',
                'object',
                'pattern',
                'time'
            )
        ),

    reason TEXT,

    source_observation_id INTEGER,

    count INTEGER NOT NULL DEFAULT 1,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'approved',
                'rejected'
            )
        ),

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE (name, dimension),

    FOREIGN KEY(source_observation_id)
        REFERENCES observations(id)
        ON DELETE SET NULL
);


INSERT OR IGNORE INTO tags (name, dimension, created_at) VALUES
    ('疲惫',     'state',   strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('焦虑',     'state',   strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('低落',     'state',   strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('有精神',   'state',   strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    ('学习',     'domain',  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('工作',     'domain',  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('健康',     'domain',  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('关系',     'domain',  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    ('考试',     'object',  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('DDL',      'object',  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('项目',     'object',  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    ('熬夜',     'pattern', strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('拖延',     'pattern', strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('回避',     'pattern', strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('连续坚持', 'pattern', strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    ('早晨',     'time',    strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('下午',     'time',    strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('深夜',     'time',    strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
"""


# 聊天记录可编辑（产品需求）：L1 出处要能指回**它来自哪条消息**。
#
# 没有它，"删掉这句话的影响范围"根本查不出来 —— 也就无法在删除前告诉你
# "他还记着这件事"（设计决定：删聊天 ≠ 让他忘掉，所以必须说出来再让你选）。
#
# ⚠️ 外键是 ``ON DELETE SET NULL`` 而**不是** CASCADE：
# 删消息时 L1 出处必须**活着**。证据链断了，"他还记得"就变成没有出处的断言，
# 违反不变量 5。要连带删记忆，走显式的那条路（用户点"一起删掉"）。
#
# SQLite 允许 ADD COLUMN 带 REFERENCES，前提是默认值为 NULL（这里正是）。
MIGRATION_010 = """
ALTER TABLE raw_entries
ADD COLUMN message_id INTEGER
    REFERENCES messages(id)
    ON DELETE SET NULL;
"""


# 记忆检索（DEVELOPMENT_PLAN 关键约束：检索用 SQLite FTS5，不上向量库）。
#
# 没有它，记忆只能**按时间取最近 N 条** —— 你今天说的话他能检索到，
# 三个月前说的那句永远找不到。而"他会记得你说过的事"这个承诺，
# 用三个月尺度去看，就是这个功能。
#
# ⚠️ **索引里存的是"切好的"文本，不是原文。** 两个中文方案都不能用：
#
# - 默认的 `unicode61` 会把一整串中文当成**一个词** → "我今天有点累" 完全不可检索
# - `trigram` 要求命中**连续三个字符** → "考试"这种两字词换个上下文就匹配不上
#   （「下个月要考试」与「考试的事情」不共享任何三字窗口）——
#   而这恰恰是最该命中的场景
#
# 所以：**CJK 一字一个 token**（由 Python `memory_service._segment()` 切好），
# 查询用**相邻两字的短语**匹配。中文词大多是两个字，于是任意长度的词都能命中。
#
# 切分在 Python 里做，所以索引的写入也在 service 层（`index_memory()`），
# **不靠触发器** —— SQL 做不了分词。已有数据的补索引见 `backfill_search_index()`。
#
# `kind` / `row_id` 用 UNINDEXED：**不能**拿我们自己的 id 当 FTS 的 rowid ——
# observations 和 impressions 的 id 会撞车。
MIGRATION_011 = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    search_text,
    kind UNINDEXED,
    row_id UNINDEXED
);
"""


# 012：「行动计划」—— 三层计划（完整 / **最低** / **备用**）+ 步骤 ✓
#
# ⚠️ `steps_json` 存的是步骤数组的 JSON ✓（`[{title, minutes, priority}]` ✓）——
# 不单独建子表 ✓：这里**不做进度追踪** ✓，一份计划就是一个整体 ✓。
MIGRATION_012 = """
CREATE TABLE IF NOT EXISTS action_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    reason TEXT,
    minimum_plan TEXT,
    fallback_plan TEXT,
    steps_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'confirmed', 'active', 'completed', 'adjusted', 'cancelled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


# 013：「卡在哪」✓ —— 七种阻塞类型（见 `schemas/action_plan.BLOCKING_TYPES` ✓）。
#
# ⚠️ 用 `ALTER TABLE ... ADD COLUMN` ✓ —— SQLite 支持 ✓，
# 而且**不动** 012 已发布的脚本 ✓（append-only 规矩 ✓）。
# `NULL` 是合法的 ✓（模型没判断出来 / 手工建的计划 ✓）。
MIGRATION_013 = """
ALTER TABLE action_plans ADD COLUMN blocking_type TEXT;
"""


# 014：把「行动步骤」从 JSON 拆成**独立表** ✓ + 接到真实执行 ✓（③ ⑤ 的地基）
#
# 为什么必须拆（2026-10-05 ✓）：
# - 步骤要**有自己的状态** ✗（待做 / 进行中 / 已完成 / 已跳过）
# - 要记**实际耗时** / 开始 / 完成 / 中断次数 / 跳过原因 ✓
# - 番茄钟要**绑定到具体步骤** ✓（`pomodoro_sessions.action_step_id` ✓）
# - 复盘要**按步骤统计** ✓ —— 这些在 JSON 列上做不到 ✗
#
# ⚠️ `action_plans.steps_json` **保留** ✓：旧数据、旧接口还在读它 ✓。
# 新写入时会**两处都写** ✓ —— JSON 是"原始草案"✓，表是"可执行步骤"✓。
# 这里顺手把**已有**的 JSON 步骤搬进表 ✓（用 SQLite 的 json_each ✓）。
#
# ⚠️ 所有"关联列"（deadline_id / schedule_id / action_step_id）**故意不加外键** ✗：
# 它们指向的表在别的迁移里 ✓，加 FK 会让这个迁移和那些迁移**耦合** ✓，
# 以后想改就难了 ✓。**引用完整性由 service 层保证** ✓（这个项目一贯如此 ✓）。
MIGRATION_014 = """
CREATE TABLE IF NOT EXISTS action_plan_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES action_plans(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 25,
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK (priority IN ('low', 'medium', 'high')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'completed', 'skipped')),
    estimated_minutes INTEGER,
    actual_minutes INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    interruption_count INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT,
    pomodoro_count INTEGER NOT NULL DEFAULT 0,
    deadline_id INTEGER,
    schedule_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_plan_steps_plan
    ON action_plan_steps(plan_id, position);

ALTER TABLE pomodoro_sessions ADD COLUMN action_step_id INTEGER;
"""


# ⚠️ 把 012 之前存下来的 JSON 步骤搬进 014 的新表 ✓。
# 单独一条迁移 ✓ —— 因为 `ALTER TABLE` 和"数据搬运"是两件事 ✓，
# 以后回头看也清楚哪一步在干什么 ✓。
#
# `json_each` 是 SQLite 的内建函数 ✓（项目已经在用 FTS5 ✓，版本足够 ✓）。
# `json_valid` 是兜底 ✓ —— 坏掉的 JSON 直接跳过 ✗，不要让整个迁移挂掉 ✅。
MIGRATION_015 = """
INSERT INTO action_plan_steps (
    plan_id, position, title, minutes, priority,
    status, estimated_minutes, actual_minutes,
    created_at, updated_at
)
SELECT
    p.id,
    CAST(s.key AS INTEGER),
    COALESCE(json_extract(s.value, '$.title'), '（未命名步骤）'),
    COALESCE(json_extract(s.value, '$.minutes'), 25),
    COALESCE(json_extract(s.value, '$.priority'), 'medium'),
    'pending',
    COALESCE(json_extract(s.value, '$.minutes'), 25),
    0,
    p.created_at,
    p.updated_at
FROM action_plans AS p, json_each(p.steps_json) AS s
WHERE json_valid(p.steps_json)
  AND NOT EXISTS (SELECT 1 FROM action_plan_steps WHERE plan_id = p.id);
"""


# 016：保存计划确认所需信息，并为落地操作提供幂等键。
MIGRATION_016 = """
ALTER TABLE action_plans ADD COLUMN assumptions_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE action_plans ADD COLUMN needs_confirmation_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE action_plan_steps ADD COLUMN materialize_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_action_step_materialize_key
    ON action_plan_steps(materialize_key) WHERE materialize_key IS NOT NULL;
"""


# 018：**重要** 与 **困难** 分开 ✓（2026-10-05 第三轮 ✓）。
#
# ⚠️ 这两件事**不是一回事** ✗：
# - `importance` ✓ = **用户的价值判断**（"这件事对我有多重要"）
# - `difficulty` ✓ = **执行负担**（"做起来有多费劲"）
#
# 混成一个"优先级"就废了 ✗ ——
# "很重要但很容易"和"不重要但很难"是两种完全不同的东西 ✓，
# 它们的**处理方式**也不同 ✓（前者值得花时间 ✓，后者要么拆小要么推迟 ✓）。
#
# ⚠️ 已有的 `priority`（low / medium / high）**保留** ✓ ——
# 那是**模型给的粗排** ✓，不是用户的判断 ✓。两者并存 ✓，各归各的 ✓。
#
# `NULL` = **还没标记** ✓（不是 0 ✓ —— "没想过"和"一点也不重要"是两回事 ✗）。
MIGRATION_018 = """
ALTER TABLE action_plan_steps ADD COLUMN importance INTEGER;
ALTER TABLE action_plan_steps ADD COLUMN difficulty INTEGER;
"""


# 019：**回填**计划状态 ✓（2026-10-05 ✓）。
#
# ⚠️ 为什么需要这一步（真实 bug ✓）：
# `refresh_plan_status()` 只在**步骤发生变化**时才跑 ✓ ——
# 所以**在它上线之前**就已经做完的计划 ✗，状态会**永远停在 `confirmed`** ✗
#（用户实测：7 步全部完成/跳过，界面还写着"已确认" ✓）。
#
# 这里用**和 `refresh_plan_status()` 完全一样的规则**重算一遍 ✓ ——
# 如果哪天规则改了 ✓，记得**再写一条新迁移** ✗（不要回来改这条 ✓）。
#
# `draft` / `cancelled` **不动** ✓（草稿不该被步骤推着走 ✓，已取消的更不该复活 ✓）。
MIGRATION_019 = """
UPDATE action_plans
SET status = CASE
    WHEN NOT EXISTS (
        SELECT 1 FROM action_plan_steps WHERE plan_id = action_plans.id
    ) THEN 'confirmed'
    WHEN NOT EXISTS (
        SELECT 1 FROM action_plan_steps
        WHERE plan_id = action_plans.id AND status NOT IN ('completed', 'skipped')
    ) THEN CASE
        WHEN EXISTS (
            SELECT 1 FROM action_plan_steps
            WHERE plan_id = action_plans.id AND status = 'skipped'
        ) THEN 'adjusted'
        ELSE 'completed'
    END
    WHEN EXISTS (
        SELECT 1 FROM action_plan_steps
        WHERE plan_id = action_plans.id AND status = 'active'
    ) THEN 'active'
    ELSE 'confirmed'
END
WHERE status NOT IN ('draft', 'cancelled');
"""


# 017：番茄的「暂停」是**正交**于旧 session 状态的一维 ✓ —— 不重建表 ✓。
#
# 为什么加 `focus_seconds` / `elapsed_seconds`（2026-10-05 第二轮 ✓）：
# 暂停期间**不算专注** ✓，但**墙钟照样走** ✓ —— 两个数必须分开 ✓，
# 否则"我坐了 3 小时、真专注 40 分钟"这种事实就没了 ✗。
#
# `action_plan_steps.actual_seconds` 是**秒级**的 ✓ ——
# 分钟级会把 25 分 40 秒记成 25 ✗，偏差分析就失真了 ✓。
# 迁移里那句 `UPDATE` 把老数据按分钟换算回来 ✓。
#
# `confirmed_minutes` 记的是**确认时**的预计时长 ✓ ——
# 后来再 `adjust` 也不会改它 ✓（否则"计划偏差"会被自己的调整抹平 ✗）。
MIGRATION_017 = """
ALTER TABLE pomodoro_sessions ADD COLUMN paused_at TEXT;
ALTER TABLE pomodoro_sessions ADD COLUMN paused_seconds INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pomodoro_sessions ADD COLUMN focus_seconds INTEGER;
ALTER TABLE pomodoro_sessions ADD COLUMN elapsed_seconds INTEGER;
ALTER TABLE action_plan_steps ADD COLUMN confirmed_minutes INTEGER;
ALTER TABLE action_plan_steps ADD COLUMN actual_seconds INTEGER NOT NULL DEFAULT 0;
UPDATE action_plan_steps SET actual_seconds = actual_minutes * 60;
ALTER TABLE action_plan_steps ADD COLUMN materialize_payload TEXT;
"""

# 顺序执行的迁移：(版本号, SQL)。新增迁移只能追加；
# 已发布的脚本不可修改，否则"已升级的库"与"新建的库"会不一致。
_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, MIGRATION_001),
    (2, MIGRATION_002),
    (3, MIGRATION_003),
    (4, MIGRATION_004),
    (5, MIGRATION_005),
    (6, MIGRATION_006),
    (7, MIGRATION_007),
    (8, MIGRATION_008),
    (9, MIGRATION_009),
    (10, MIGRATION_010),
    (11, MIGRATION_011),
    (12, MIGRATION_012),
    (13, MIGRATION_013),
    (14, MIGRATION_014),
    (15, MIGRATION_015),
    (16, MIGRATION_016),
    (17, MIGRATION_017),
    (18, MIGRATION_018),
    (19, MIGRATION_019),
)


def _utc_iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def migrate() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
                schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
            """
        )

        applied = {
            row["version"]
            for row in conn.execute(
                """
                SELECT version
                FROM schema_migrations
                """
            ).fetchall()
        }

        for version, script in _MIGRATIONS:
            if version in applied:
                continue

            conn.executescript(script)

            conn.execute(
                """
                INSERT INTO schema_migrations (
                    version,
                    applied_at
                )
                VALUES (?, ?)
                """,
                (
                    version,
                    _utc_iso_now(),
                ),
            )