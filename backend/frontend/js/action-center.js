import { get, post } from "./api.js";
import { showDialog, showToast } from "./utils.js";
import { refreshToday } from "./today.js";

const el = (id) => document.getElementById(id);

/** 「卡在哪」的**中文标签** ✓ —— 和后端 `BLOCKING_TYPES` 一一对应 ✓。 */
const BLOCKING_LABELS = {
    unclear: "不知道先做什么",
    too_large: "启动困难",
    low_energy: "精力不够",
    time_pressure: "时间压力",
    stalled: "连续延期",
    blocked: "卡住了",
    review: "该复盘了",
};

const STATUS_LABELS = {
    draft: "草稿",
    adjusted: "调整过",
    confirmed: "已确认",
    active: "进行中",
    completed: "已完成",
    cancelled: "已取消",
};

const STEP_LABELS = {
    pending: "待做",
    active: "进行中",
    completed: "已完成",
    skipped: "已跳过",
};

function statePill(type) {
    if (!type) return null;
    const pill = document.createElement("span");
    pill.className = "plan-state";
    pill.textContent = BLOCKING_LABELS[type] || type;
    return pill;
}

function smallButton(text, onClick, danger = false) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = danger ? "mini-button danger" : "mini-button";
    button.textContent = text;
    button.addEventListener("click", onClick);
    return button;
}

/** 一步的按钮 ✓ —— **按状态出现** ✓（不是五个全堆上去 ✗）。 */
function stepButtons(plan, step) {
    const box = document.createElement("span");
    box.className = "action-step-actions";

    const call = (path, body, ok) => async () => {
        try {
            await post(path, body || {});
            if (ok) showToast(ok, 3000);
            await loadPlans();
        } catch (error) {
            showToast(error.message, 5000);
        }
    };

    const base = `/action-plans/${plan.id}/steps/${step.id}`;

    if (step.status === "pending") {
        box.append(smallButton("开始", call(`${base}/start`)));
        box.append(smallButton("开始番茄", () => startPomodoro(plan, step)));
        if (!step.deadline_id && !step.schedule_id) {
            box.append(smallButton("放日程", () => materializeStep(plan, step)));
        }
        box.append(smallButton("跳过", () => skipStep(plan, step), true));
    } else if (step.status === "active") {
        box.append(smallButton("暂停", call(`${base}/pause`)));
        box.append(smallButton("完成", call(`${base}/complete`, null, "已完成。")));
    } else if (step.status === "completed") {
        const note = document.createElement("span");
        note.className = "action-step-meta";
        note.textContent = "✓";
        box.append(note);
    }

    return box;
}

/**
 * ⑤ 从这一步**直接起一个番茄** ✓ —— 番茄绑到 `action_step_id` ✓。
 *
 * ⚠️ **不重写计时** ✗：走的是原本的 `POST /pomodoro/start` ✓，
 * 只是多带一个 `action_step_id` ✓。
 * 结束番茄时后端会把**实际耗时写回这一步** ✓（`record_pomodoro()` ✓）。
 *
 * ⚠️ 为什么这里是 `location.reload()` ✗：番茄面板在 `today.js` 里 ✓，
 * 而它没导出刷新函数 ✓ —— 重载比"偷偷只刷一半"诚实 ✓。
 */
async function startPomodoro(plan, step) {
    try {
        await post(`/action-plans/${plan.id}/steps/${step.id}/start`, {});
        await post("/pomodoro/start", {
            planned_minutes: step.estimated_minutes ?? step.minutes,
            action_step_id: step.id,
        });
        showToast("番茄已开始 —— 结束时用时会自动记到这一步。", 5000);
        setTimeout(() => location.reload(), 900);
    } catch (error) {
        showToast(error.message, 6000);
    }
}

async function skipStep(plan, step) {
    const ask = await showDialog({
        title: "跳过这一步",
        body: "今天不做了？写一句原因就行（以后复盘会用到，不写也可以）。",
        input: { value: "" },
        options: [{ value: "skip", label: "跳过" }],
    });
    if (!ask || ask.value !== "skip") return;
    try {
        await post(`/action-plans/${plan.id}/steps/${step.id}/skip`, {
            reason: (ask.text || "").trim() || null,
        });
        showToast("已跳过。", 3000);
        await loadPlans();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

/**
 * ③ 落地 ✓ —— **永远不会自动帮你塞进日历** ✗。
 *
 * 用户 2026-10-05 明确说："不要让系统默认把所有步骤都塞进日历，
 * 否则容易制造大量无效安排。"
 */
async function materializeStep(plan, step) {
    const kindPick = await showDialog({
        title: `「${step.title}」要放进哪儿？`,
        body:
            "这一句会被**同时**写成任务和行动步骤：\n\n" +
            "· 建成 DDL —— 适合有截止时间的（考试、交付）\n" +
            "· 建成日程 —— 适合有具体时间块的（今天 20:00 学习）\n\n" +
            "⚠️ 什么都不选 = 就当成行动步骤，不进日历。",
        options: [
            { value: "deadline", label: "建成 DDL" },
            { value: "schedule", label: "建成日程" },
            { value: "none", label: "不进日历" },
        ],
    });
    if (!kindPick) return;
    if (kindPick.value === "none") {
        showToast("好，它只留在行动步骤里。", 3500);
        return;
    }

    const isDeadline = kindPick.value === "deadline";
    const timeAsk = await showDialog({
        title: isDeadline ? "截止时间是什么时候？" : "开始时间是什么时候？",
        body: "按 2026-10-07 23:00 这样的写法填。",
        input: { value: "" },
        options: [{ value: "ok", label: "确定" }],
    });
    if (!timeAsk || timeAsk.value !== "ok") return;

    const when = (timeAsk.text || "").trim();
    if (!when) {
        showToast("时间不能空着。", 4000);
        return;
    }

    try {
        await post(`/action-plans/${plan.id}/steps/${step.id}/materialize`, {
            kind: isDeadline ? "deadline" : "schedule",
            due_at: isDeadline ? when : null,
            start_at: isDeadline ? null : when,
            // ⚠️ **幂等键** ✓（`MIGRATION_016` ✓）—— 同一次选择的重试复用同一个值 ✓，
            // 换个时间就是新的一次选择 ✓。这样网络抖动不会建出两个 DDL ✗。
            materialize_key: `step-${step.id}-${isDeadline ? "deadline" : "schedule"}-${when}`,
        });
        showToast(
            isDeadline
                ? "已建成 DDL ✓ —— 今天页的「DDL」卡片只显示最紧的那一条，点卡片能看全部。"
                : "已建成日程 ✓ —— 今天页的「日程」卡片只显示最近的那一条，点卡片能看全部。",
            6000,
        );
        await loadPlans();
        // ⚠️ 今天页的 **DDL / 日程卡片**也要刷新 ✗ ——
        // 不然 DDL 真的建好了 ✓，列表里却看不到 ✗（要手动刷新才知道 ✓）。
        await refreshToday();
    } catch (error) {
        showToast(error.message, 6000);
    }
}

/**
 * 「重要」/「困难」—— **两排独立的点** ✓（2026-10-05 第三轮 ✓）。
 *
 * ⚠️ 为什么不合并成一个"优先级" ✗（用户原话 ✓）：
 * **重要是价值判断，困难是执行负担** ✓ ——
 * "很重要但很容易"和"不重要但很难"必须能分别表达 ✓。
 *
 * ⚠️ 四档 0–3 ✓；**不传 = 没标记** ✓（和"标成 0"不是一回事 ✓）。
 */
function markControl(plan, step, key, label) {
    const wrap = document.createElement("span");
    wrap.className = "action-step-mark";

    const name = document.createElement("span");
    name.className = "action-step-mark-label";
    name.textContent = label;
    wrap.append(name);

    for (let level = 0; level <= 3; level += 1) {
        const dot = document.createElement("button");
        dot.type = "button";
        const value = step[key];
        const on = value !== null && value !== undefined && level <= value;
        dot.className = on ? "mark-dot on" : "mark-dot";
        dot.title = `${label} ${level} / 3`;
        dot.setAttribute("aria-label", `${label} ${level} 档`);
        dot.addEventListener("click", async () => {
            try {
                await post(`/action-plans/${plan.id}/steps/${step.id}/adjust`, {
                    [key]: level,
                });
                await loadPlans();
            } catch (error) {
                showToast(error.message, 5000);
            }
        });
        wrap.append(dot);
    }
    return wrap;
}

/** 一条**可执行**步骤 ✓（`executable_steps` ✓，不是 JSON 草案 ✓）。 */
function stepRow(plan, step) {
    const row = document.createElement("div");
    row.className = "action-step-row";

    const dot = document.createElement("span");
    dot.className = `action-step-dot ${step.status}`;

    const label = document.createElement("span");
    label.className = step.status === "completed" ? "action-step-title done" : "action-step-title";
    label.textContent = step.title;

    const meta = document.createElement("span");
    meta.className = "action-step-meta";
    const planned = step.estimated_minutes ?? step.minutes;
    const bits = [STEP_LABELS[step.status] || step.status];
    bits.push(
        step.actual_minutes
            ? `${step.actual_minutes} / ${planned} 分钟`
            : `${planned} 分钟`,
    );
    if (step.interruption_count) bits.push(`中断 ${step.interruption_count} 次`);
    if (step.pomodoro_count) bits.push(`${step.pomodoro_count} 个番茄`);
    // ⚠️ **落地了要看得见** ✗（2026-10-05 用户反馈 ✓）——
    // 不然点完「放日程」之后，步骤上一点痕迹都没有 ✓，
    // 而 DDL 卡片只显示"最紧的那一条" ✗ → 看起来就像**什么都没发生** ✗。
    if (step.deadline_id) bits.push("已进 DDL");
    if (step.schedule_id) bits.push("已进日程");
    meta.textContent = bits.join(" · ");

    row.append(dot, label, meta, stepButtons(plan, step));

    // ⚠️ **标记只给还没结束的步骤** ✗（2026-10-05 用户反馈 ✓）——
    // 做完 / 跳过的东西不用你再评分 ✓，两排点只会把已经很长的一张卡撑得更长 ✓。
    if (step.status === "pending" || step.status === "active") {
        const marks = document.createElement("div");
        marks.className = "action-step-marks";
        marks.append(markControl(plan, step, "importance", "重要"));
        marks.append(markControl(plan, step, "difficulty", "困难"));

        const wrapper = document.createElement("div");
        wrapper.append(row, marks);
        return wrapper;
    }

    return row;
}

function planProgress(plan) {
    const steps = plan.executable_steps || [];
    if (!steps.length) return "";
    const done = steps.filter((s) => s.status === "completed").length;
    const actual = steps.reduce((sum, s) => sum + (s.actual_minutes || 0), 0);
    return `${done} / ${steps.length} 步${actual ? ` · 实际 ${actual} 分钟` : ""}`;
}

/** 所有步骤都结束了 ✓（完成或跳过）—— **不依赖计划状态** ✗。 */
function allStepsSettled(plan) {
    const steps = plan.executable_steps || [];
    return (
        steps.length > 0 &&
        steps.every((step) => step.status === "completed" || step.status === "skipped")
    );
}

function planRow(plan) {
    // ⚠️ **同时看状态和步骤** ✗ —— 只看 `status` 是不够的 ✓：
    // 老数据的状态可能还是落后的 `confirmed` ✗（在 `refresh_plan_status()` 上线前做完的 ✓），
    // 那时候卡片就收不起来 ✓（2026-10-05 用户实测发现 ✓）。
    // `MIGRATION_019` 回填了历史数据 ✓，但界面这里也**不信任单一信号** ✓。
    const settled =
        ["completed", "adjusted", "cancelled"].includes(plan.status) || allStepsSettled(plan);
    // ⚠️ **收尾的计划默认收起** ✗（2026-10-05 用户反馈："处理之后就一直这么放着，有点丑" ✓）——
    // 一个 7 步的计划全做完之后，它占了整屏 ✓，但你**再也不会看它第二眼** ✗。
    const expanded = expandedPlans.get(plan.id) ?? !settled;

    const row = document.createElement("div");
    row.className = "evidence-row";

    const body = document.createElement("div");

    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = plan.title;
    body.append(title);

    const meta = document.createElement("div");
    meta.className = "evidence-meta muted";
    const bits = [STATUS_LABELS[plan.status] || plan.status];
    if (plan.blocking_type) {
        bits.push(BLOCKING_LABELS[plan.blocking_type] || plan.blocking_type);
    }
    const progress = planProgress(plan);
    if (progress) bits.push(progress);
    meta.textContent = bits.join(" · ");
    body.append(meta);

    if (!expanded) {
        // 收起状态只留：摘要 + 「展开」✓（最低完成版本也收着 ✓，它已经在 meta 里体现过了 ✓）
        const actions = document.createElement("div");
        actions.className = "item-actions";
        const more = document.createElement("button");
        more.type = "button";
        more.className = "mini-button";
        more.textContent = "展开";
        more.addEventListener("click", () => {
            expandedPlans.set(plan.id, true);
            loadPlans();
        });
        actions.append(more);
        row.append(body, actions);
        return row;
    }

    if (plan.minimum_plan) {
        const minimum = document.createElement("div");
        minimum.className = "action-plan-minimum";
        minimum.textContent = `最低完成版本：${plan.minimum_plan}`;
        body.append(minimum);
    }

    const steps = document.createElement("div");
    steps.className = "action-step-list";
    for (const step of plan.executable_steps || []) {
        steps.append(stepRow(plan, step));
    }
    body.append(steps);

    const actions = document.createElement("div");
    actions.className = "item-actions";
    if (plan.status === "draft" || plan.status === "adjusted") {
        actions.append(
            smallButton("确认这个计划", async () => {
                try {
                    await post(`/action-plans/${plan.id}/confirm`, {});
                    showToast("已确认 —— 现在可以一步步开始了。", 4000);
                    await loadPlans();
                } catch (error) {
                    showToast(error.message, 5000);
                }
            }),
        );
    }
    if (settled) {
        actions.append(
            smallButton("收起", () => {
                expandedPlans.set(plan.id, false);
                loadPlans();
            }),
        );
    }

    row.append(body, actions);
    return row;
}

function renderDraft(plan) {
    const box = el("action-plan-draft");
    if (!box) return;
    box.replaceChildren();

    if (plan.source === "template") {
        const warn = document.createElement("p");
        warn.className = "muted";
        warn.textContent =
            "⚠️ AI 这次没有生成成功 —— 下面是保底模板，你可以直接保存，或者重试一次。";
        box.append(warn);
    }

    const pill = statePill(plan.blocking_type);
    if (pill) box.append(pill);

    const headline = document.createElement("h3");
    const first = (plan.steps || [])[0];
    headline.textContent = first ? `现在先做：${first.title}` : plan.title;
    box.append(headline);

    const reason = document.createElement("p");
    reason.className = "muted";
    reason.textContent = plan.reason || "根据当前状态生成的初步方案。";
    box.append(reason);

    const list = document.createElement("div");
    list.className = "action-step-list";
    for (const step of plan.steps || []) {
        const row = document.createElement("div");
        row.className = "action-step-row";

        const dot = document.createElement("span");
        dot.className = "action-step-dot";

        const label = document.createElement("span");
        label.textContent = step.title;

        const meta = document.createElement("span");
        meta.className = "action-step-meta";
        meta.textContent = `${step.minutes} 分钟`;

        row.append(dot, label, meta);
        list.append(row);
    }
    box.append(list);

    if (plan.minimum_plan) {
        const minimum = document.createElement("p");
        minimum.className = "action-plan-minimum";
        minimum.textContent = `最低完成版本：${plan.minimum_plan}`;
        box.append(minimum);
    }
    if (plan.fallback_plan) {
        const fallback = document.createElement("p");
        fallback.className = "muted";
        fallback.textContent = `做不动时：${plan.fallback_plan}`;
        box.append(fallback);
    }

    const actions = document.createElement("div");
    actions.className = "item-actions";

    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary-button";
    save.textContent = "保存这个方案";
    save.addEventListener("click", () => saveDraft(plan, save));
    actions.append(save);

    const discard = document.createElement("button");
    discard.type = "button";
    discard.className = "ghost-button";
    discard.textContent = "先不要";
    discard.addEventListener("click", () => {
        box.hidden = true;
        box.replaceChildren();
    });
    actions.append(discard);

    box.append(actions);
    box.hidden = false;
}

async function saveDraft(plan, button) {
    button.disabled = true;
    button.textContent = "保存中…";
    try {
        await post("/action-plans", {
            title: plan.title,
            reason: plan.reason,
            minimum_plan: plan.minimum_plan,
            fallback_plan: plan.fallback_plan,
            steps: plan.steps,
            blocking_type: plan.blocking_type,
        });
        showToast("已保存 —— 在下面点「确认」它才算定下来。", 5000);
        const box = el("action-plan-draft");
        box.hidden = true;
        box.replaceChildren();
        await loadPlans();
    } catch (error) {
        showToast(error.message, 5000);
    } finally {
        button.disabled = false;
        button.textContent = "保存这个方案";
    }
}

/** 计划列表默认只显示最近 5 个 ✓ —— 想看**全部**就切一下 ✓（这就是「历史计划入口」✓）。 */
let showAllPlans = false;

/** 哪些计划被**手动展开**了 ✓（收尾的默认收起 ✓ —— 见 `planRow()` 的说明）。 */
const expandedPlans = new Map();

async function loadPlans() {
    const container = el("action-plan-list");
    if (!container) return;
    container.replaceChildren();
    try {
        const plans = await get("/action-plans");
        if (!plans.length) return;

        // ⚠️ **没收尾的排在前面** ✗ —— 接口是按 id 倒序的 ✓，
        // 但一个"全部完成"的计划不该压在一个"还在跑"的计划上面 ✓。
        const settled = new Set(["completed", "adjusted", "cancelled"]);
        const ordered = [...plans].sort(
            (a, b) => Number(settled.has(a.status)) - Number(settled.has(b.status)),
        );
        const shown = showAllPlans ? ordered : ordered.slice(0, 5);
        for (const plan of shown) container.append(planRow(plan));

        if (ordered.length > 5) {
            const more = document.createElement("button");
            more.type = "button";
            more.className = "ghost-button";
            more.textContent = showAllPlans
                ? "只看最近的"
                : `查看全部（${plans.length} 个）`;
            more.addEventListener("click", () => {
                showAllPlans = !showAllPlans;
                loadPlans();
            });
            container.append(more);
        }
    } catch (error) {
        showToast(error.message, 5000);
    }
}

export function initActionCenter() {
    const form = el("action-plan-form");
    if (!form) return;

    const submit = async (message) => {
        if (!message.trim()) return;
        const button = form.querySelector("button");
        button.disabled = true;
        button.textContent = "正在整理…";
        try {
            renderDraft(await post("/action-plans/draft", { message }));
        } catch (error) {
            showToast(error.message, 3800);
        } finally {
            button.disabled = false;
            button.textContent = "帮我安排";
        }
    };

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        submit(el("action-plan-input").value);
    });

    document
        .querySelectorAll(".action-quick")
        .forEach((button) =>
            button.addEventListener("click", () => {
                el("action-plan-input").value = button.dataset.prompt;
                submit(button.dataset.prompt);
            }),
        );

    // ⚠️ 番茄钟面板在 `today.js` 里 ✓ —— 它结束/取消后，**实际耗时**会写回步骤 ✓，
    // 但这边显示的是**上一次拉取的**数据 ✗ → 听它的刷新事件，重新拉一次 ✓。
    window.addEventListener("today:refreshed", () => loadPlans());

    loadPlans();
}
