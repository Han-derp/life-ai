import { renderExecutionSummary } from "./execution-summary.js";
import { get, post, remove } from "./api.js";
import { deadlineGroups, timeOf } from "./plan-tree.js";
import {
    localDisplay,
    setBusy,
    showDialog,
    showToast,
    toDatetimeLocalValue,
} from "./utils.js";

let currentContext = null;
let runningPomodoro = null;
let timerHandle = null;
let currentReview = null;

const el = (id) => document.getElementById(id);

function empty(container, text) {
    container.replaceChildren();
    const node = document.createElement("div");
    node.className = "empty-state";
    node.textContent = text;
    container.appendChild(node);
}

function actionButton(text, action, secondary = false) {
    const button = document.createElement("button");
    button.className = `mini-button${secondary ? " secondary" : ""}`;
    button.textContent = text;
    button.addEventListener("click", action);
    return button;
}

function badge(text, type = "") {
    const span = document.createElement("span");
    span.className = `badge ${type}`.trim();
    span.textContent = text;
    return span;
}

// ── 「计划」：三张摘要卡 + 三个「查看全部」列表页 ────────────────
//
// 用户 2026-10-05 的原话：
//   "这些卡片，加多了事项后会变得很长，而且左右并排的卡片内容数不一样后
//    长度不一样，很丑。换一种方式 —— 显示时间最近的一条日程，时间最紧的一条 ddl
//    （拆解过的话给一个下拉框，里面是拆解出来的子 ddl），习惯显示一条最新的习惯；
//    然后点击卡片，就有一个界面显示全部，分三类可以选择看哪一类。"
//
// 所以每张卡**只放一条** ✓ —— 这是"长度不一样很丑"的**结构性**解药 ✓，
// 不是靠调内边距去凑 ✓。全部内容在点开之后的列表页里 ✓。
//
// ⚠️ 一次只拉一份数据（`planCache`）✓：摘要卡和列表页**读同一份** ✓。
// 分成两处各拉一次的话，"卡上写的和列表里看到的不是一回事"迟早会出现 ✗
//（S2 的上下文面板当初就是这么修的 ✓ —— 面板不许撒谎 ✓）。

const PLAN_LISTS = {
    schedule: {
        dialog: "schedule-list-dialog", container: "schedule-all-list",
        card: "plan-card-schedule", key: "life-ai-plan-schedule",
    },
    deadline: {
        dialog: "deadline-list-dialog", container: "deadline-all-list",
        card: "plan-card-deadline", key: "life-ai-plan-deadline",
    },
    habit: {
        dialog: "habit-list-dialog", container: "habit-all-list",
        card: "plan-card-habit", key: "life-ai-plan-habit",
    },
};

const PLAN_FILTERS = {
    schedule: ["all", "completed", "pending"],
    deadline: ["all", "completed", "pending"],
    habit: ["all", "active", "archived"],
};

const PLAN_EMPTY = {
    schedule: { all: "还没有日程。", completed: "还没有完成的日程。", pending: "没有未完成的日程。" },
    deadline: { all: "还没有 DDL。", completed: "还没有完成的 DDL。", pending: "没有未完成的 DDL。" },
    habit: { all: "还没有习惯。", active: "没有养成中的习惯。", archived: "还没有结束的习惯。" },
};

// 状态原来是直接显示英文的（`pending` / `upcoming` / `overdue`）✗ ——
// 用户在中文界面里看到一堆英文状态，属于"功能不清晰" ✓。
const TIME_STATUS_LABEL = {
    pending: "未完成", completed: "已完成", cancelled: "已取消",
    upcoming: "待开始", ongoing: "进行中", overdue: "已过期",
};

const planFilter = { schedule: "all", deadline: "all", habit: "all" };
let planCache = { schedule: [], deadline: [], habit: [] };

function badgeType(status) {
    if (status === "overdue") return "overdue";
    if (status === "ongoing" || status === "completed") return "ongoing";
    return "";
}

function statusLabel(status) {
    return TIME_STATUS_LABEL[status] || status;
}

// 卡片上要能一眼看出"是不是今天" ✓ —— 所以今天只说时间，别的日子带上月日 ✓。
function planWhen(startLocal, endLocal) {
    if (!startLocal) return "";
    const day = startLocal.slice(0, 10);
    const time = startLocal.slice(11, 16);
    const sameDay = endLocal && endLocal.slice(0, 10) === day;
    const end = sameDay ? `–${endLocal.slice(11, 16)}` : "";
    const prefix = day === toDatetimeLocalValue().slice(0, 10) ? "今天" : day.slice(5).replace("-", "/");
    return `${prefix} ${time}${end}`;
}

function planNote(text) {
    const node = document.createElement("div");
    node.className = "plan-item-meta";
    node.textContent = text;
    return node;
}

// 列表行的统一外壳（三张列表共用 ✓，省得三处各写一遍 DOM ✓）。
function itemRow({ done = false, title, badges = [], meta = "", actions = [], extra = null, extraClass = "", leading = null }) {
    const card = document.createElement("article");
    card.className = `item-card${done ? " done" : ""}${extraClass ? ` ${extraClass}` : ""}`;
    const body = document.createElement("div");
    const heading = document.createElement("div");
    heading.className = "item-title";
    // ⚠️ 小三角要排在标题**前面** ✓（树形的展开箭头在那儿 ✓），
    // 所以标题用 `createTextNode` 而不是 `textContent` ✗ ——
    // 后者会把 `leading` 一起冲掉 ✓。
    if (leading) heading.appendChild(leading);
    heading.appendChild(document.createTextNode(title));
    for (const [text, type] of badges) heading.appendChild(badge(text, type));
    body.appendChild(heading);
    if (meta) {
        const metaNode = document.createElement("div");
        metaNode.className = "item-meta";
        metaNode.textContent = meta;
        body.appendChild(metaNode);
    }
    if (extra) body.appendChild(extra);
    const actionsNode = document.createElement("div");
    actionsNode.className = "item-actions";
    for (const node of actions) actionsNode.appendChild(node);
    card.append(body, actionsNode);
    return card;
}

// ---------- 摘要卡选哪一条 ----------

// 「时间最近的一条日程」✓
// ⚠️ 用服务端给的 `time_status` 判断先后 ✓ —— 不在前端重算时区 ✗。
function nearestSchedule(items) {
    const pending = items.filter((item) => item.completion_status === "pending");
    if (!pending.length) return null;
    const upcoming = pending.filter((item) => item.time_status === "upcoming");
    if (upcoming.length) {
        return [...upcoming].sort((a, b) => timeOf(a.start_at) - timeOf(b.start_at))[0];
    }
    const ongoing = pending.filter((item) => item.time_status === "ongoing");
    if (ongoing.length) return ongoing[0];
    // 今天的都过去了还没做 → 显示**最近过去的那一条** ✓（那是"没做"的信号 ✓）
    return pending.filter((item) => item.time_status === "overdue")[0] || null;
}

// 「时间最紧的一条 DDL」✓
// ⚠️ 只看**顶层**（`parent_id === null`）✓ —— 里程碑显示在它父任务的下拉框里 ✓，
// 否则拆解过的 DDL 会让子任务跑到卡片上来，反而看不出"最紧的那件事"是什么 ✗。
function urgentDeadline(items) {
    const pending = items.filter(
        (item) => item.completion_status === "pending" && !item.parent_id,
    );
    if (!pending.length) return null;
    return [...pending].sort((a, b) => timeOf(a.due_at) - timeOf(b.due_at))[0];
}

// 「一条最新的习惯」✓（= 最近建的那个还在养成中的 ✓）
function newestHabit(items) {
    const active = items.filter((item) => item.active);
    if (!active.length) return null;
    return active.reduce((newest, item) => (item.id > newest.id ? item : newest));
}

// ---------- 摘要卡 ----------

function renderScheduleSummary() {
    const container = el("schedule-summary");
    container.replaceChildren();
    const item = nearestSchedule(planCache.schedule);
    if (!item) {
        container.appendChild(planNote("还没有待办的日程。"));
        return;
    }

    const title = document.createElement("div");
    title.className = "plan-item-title";
    title.textContent = item.title;

    const meta = document.createElement("div");
    meta.className = "plan-item-meta";
    meta.textContent = `${planWhen(item.start_at_local, item.end_at_local)} · ${statusLabel(item.time_status)}`;

    const actions = document.createElement("div");
    actions.className = "plan-item-actions";
    actions.append(
        actionButton("完成", async () => {
            await post(`/schedule/${item.id}/complete`, {});
            await refreshToday();
        }),
        actionButton("取消", async () => {
            await post(`/schedule/${item.id}/cancel`, {});
            await refreshToday();
        }, true),
    );

    container.append(title, meta, actions);
}

function renderDeadlineSummary() {
    const container = el("deadline-summary");
    container.replaceChildren();
    const item = urgentDeadline(planCache.deadline);
    if (!item) {
        container.appendChild(planNote("没有待办的 DDL。"));
        return;
    }

    const title = document.createElement("div");
    title.className = "plan-item-title";
    title.textContent = item.title;
    title.appendChild(badge(statusLabel(item.time_status), badgeType(item.time_status)));

    const meta = document.createElement("div");
    meta.className = "plan-item-meta";
    meta.textContent = `截止 ${localDisplay(item.due_at_local)}`;

    const actions = document.createElement("div");
    actions.className = "plan-item-actions";
    actions.append(
        actionButton("完成", async () => {
            await post(`/deadlines/${item.id}/complete`, {});
            await refreshToday();
        }),
        actionButton("取消", async () => {
            await post(`/deadlines/${item.id}/cancel`, {});
            await refreshToday();
        }, true),
    );

    container.append(title, meta, actions);

    const children = planCache.deadline.filter((child) => child.parent_id === item.id);
    if (children.length) container.appendChild(childrenDropdown(children));
}

// 拆解出来的子 DDL —— 原生的 `<details>` 下拉框 ✓（一行 JS 都不用 ✓）。
//
// ⚠️ 用户 2026-10-06 要求这里**能操作** ✓，把我上一版的判断推翻了：
//   "ddl 被拆解为里程碑过的话要显示拆解过这种状态，子任务可以收起来，
//    然后点一下原 ddl 卡片上加一个下拉小三角，就可以显示出来操作。"
//
// 所以：折叠时就写着「已拆解 · …」✓（状态一眼可见 ✓，不展开也知道拆过 ✓）；
// 展开之后每条子任务带自己的「完成」✓ —— 不用再跑去列表页 ✓。
function childrenDropdown(children) {
    const details = document.createElement("details");
    details.className = "plan-sub";

    const pending = children.filter((child) => child.completion_status === "pending").length;
    const summary = document.createElement("summary");
    summary.textContent = pending
        ? `已拆解 · 还有 ${pending} 个没做完`
        : `已拆解 · ${children.length} 个都做完了`;
    details.appendChild(summary);

    const list = document.createElement("div");
    list.className = "plan-sub-list";
    for (const child of children) {
        const row = document.createElement("div");
        row.className = "plan-sub-row";

        const name = document.createElement("span");
        name.textContent = child.title;

        const when = document.createElement("span");
        when.className = "muted";
        when.textContent = localDisplay(child.due_at_local);

        const actions = document.createElement("span");
        actions.className = "plan-sub-actions";
        if (child.completion_status === "pending") {
            actions.appendChild(actionButton("完成", async () => {
                await post(`/deadlines/${child.id}/complete`, {});
                await refreshToday();
            }));
        } else {
            const done = document.createElement("span");
            done.className = "muted";
            done.textContent = statusLabel(child.completion_status);
            actions.appendChild(done);
        }

        row.append(name, when, actions);
        list.appendChild(row);
    }
    details.appendChild(list);
    return details;
}

function habitCurrentText(item) {
    if (item.tracking_type === "boolean") return item.current_bool ? "已完成" : "未完成";
    if (item.tracking_type === "time") return item.current_text || "未记录";
    if (item.current_number === null || item.current_number === undefined) return "未记录";
    return `${item.current_number}${item.unit ? ` ${item.unit}` : ""}`;
}

function habitTargetText(item) {
    if (item.target_text) return item.target_text;
    if (item.target_value === null || item.target_value === undefined) return "";
    return `${item.target_operator || ""} ${item.target_value}${item.unit ? ` ${item.unit}` : ""}`;
}

function habitProgress(item) {
    if (item.progress === null || item.progress === undefined) return null;
    const progress = document.createElement("div");
    progress.className = "progress";
    const fill = document.createElement("span");
    fill.style.width = `${Math.max(0, Math.min(1, item.progress)) * 100}%`;
    progress.appendChild(fill);
    return progress;
}

function habitControl(item) {
    const control = document.createElement("div");
    control.className = "habit-control";
    if (item.tracking_type === "boolean") {
        control.appendChild(actionButton(item.current_bool ? "取消打卡" : "打卡", async () => {
            await post(`/habits/${item.id}/log`, { value_bool: !item.current_bool });
            await refreshToday();
        }));
        return control;
    }
    const input = document.createElement("input");
    input.type = item.tracking_type === "time" ? "time" : "number";
    if (item.tracking_type !== "time") input.step = "any";
    input.placeholder = item.tracking_type === "duration" ? "分钟" : (item.unit || "数值");
    const button = actionButton(item.aggregation_mode === "sum" ? "增加" : "记录", async () => {
        if (!input.value) return;
        const payload = item.tracking_type === "time"
            ? { value_text: input.value }
            : { value_number: Number(input.value) };
        await post(`/habits/${item.id}/log`, payload);
        input.value = "";
        await refreshToday();
    });
    control.append(input, button);
    return control;
}

function renderHabitSummary() {
    const container = el("habit-summary");
    container.replaceChildren();
    const item = newestHabit(planCache.habit);
    if (!item) {
        container.appendChild(planNote("还没有习惯。先建一个真正想追踪的。"));
        return;
    }

    const title = document.createElement("div");
    title.className = "plan-item-title";
    title.textContent = item.name;
    if (item.met === true) title.appendChild(badge("达标", "ongoing"));

    const meta = document.createElement("div");
    meta.className = "plan-item-meta";
    const target = habitTargetText(item);
    meta.textContent = `${habitCurrentText(item)}${target ? ` · 目标 ${target}` : ""}`;

    container.append(title, meta);
    const progress = habitProgress(item);
    if (progress) container.appendChild(progress);
    container.appendChild(habitControl(item));
}

function renderPlanSummaries() {
    renderScheduleSummary();
    renderDeadlineSummary();
    renderHabitSummary();
}

// ---------- 「查看全部」列表页 ----------

// 「未完成」是**待办** ✓ → 最急的在上 ✓（接口给的是 DESC，那是给翻看用的 ✓）。
function scheduleListItems(filter) {
    const items = planCache.schedule.filter(
        (item) => filter === "all" || item.completion_status === filter,
    );
    if (filter === "pending") {
        return [...items].sort((a, b) => timeOf(a.start_at) - timeOf(b.start_at));
    }
    return items;
}

// DDL 排序。三种筛选口径不一样 ✓：
//   · 未完成 → **最急的在上** ✓（那是待办 ✓）
//   · 已完成 → 最近的在上 ✓
//   · 全部   → 保持接口顺序 ✓（未完成在前、组内按截止升序 ✓ —— "还剩什么"最该在最上面 ✓）
//
// ⚠️ `timeOf` / `sortDeadlines` / `deadlineGroups` 都搬去了 `plan-tree.js` ✓ ——
// 那边没有任何 DOM 依赖 ✓，所以能在 Node 里**直接跑** ✓
//（见 `tests/test_frontend_plan_tree.py`：这是这个项目第一条行为级的前端测试 ✓）。

function habitListItems(filter) {
    const items = planCache.habit.filter((item) => {
        if (filter === "all") return true;
        return filter === "active" ? item.active : !item.active;
    });
    // 「已结束」把最近建的放前面 ✓（id 越大越新 ✓）
    return filter === "archived" ? [...items].sort((a, b) => b.id - a.id) : items;
}

function scheduleRow(item) {
    const pending = item.completion_status === "pending";
    const actions = [];
    if (pending) {
        actions.push(
            actionButton("完成", async () => {
                await post(`/schedule/${item.id}/complete`, {});
                await refreshToday();
            }),
            actionButton("取消", async () => {
                await post(`/schedule/${item.id}/cancel`, {});
                await refreshToday();
            }, true),
        );
    }
    return itemRow({
        done: !pending,
        title: item.title,
        badges: [[statusLabel(item.time_status), badgeType(item.time_status)]],
        meta: `${planWhen(item.start_at_local, item.end_at_local)}${item.note ? ` · ${item.note}` : ""}`,
        actions,
    });
}

// ⚠️ 哪些父任务被展开了，必须记在**模块里** ✓ —— 不能只存在 DOM 上 ✗：
// 勾掉一条里程碑会 `refreshToday()` 重画整个列表 ✓，
// 不记的话那棵树会**当场收回去** ✓（你刚要勾下一个，就得重新展开一次 ✓）。
const expandedDeadlines = new Set();

function disclosureButton(expanded, label, onToggle) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "disclosure";
    button.textContent = expanded ? "▾" : "▸";
    button.setAttribute("aria-expanded", String(expanded));
    button.title = label;
    button.addEventListener("click", onToggle);
    return button;
}

// ⚠️ `children` 是**当前筛选下**要显示的子任务 ✓，`total` 是全部 ✓（见 `deadlineGroups`）。
// 里程碑自己走同一条路 ✓（`children` 空、`total` 0 → 没有三角、没有徽标 ✓）。
function deadlineRow(item, children = [], total = 0) {
    const pending = item.completion_status === "pending";
    const actions = [];
    // 「拆解」只对**顶层且未完成**的 DDL 有意义 ✓（里程碑不用再拆 ✓）
    if (pending && !item.parent_id) actions.push(actionButton("拆解", () => suggestBreakdown(item)));
    // ⚠️ 「删除」是**真的从库里删掉** ✓ —— 和「取消」不是一回事 ✗
    actions.push(actionButton("删除", () => deleteDeadline(item), true));
    // ⚠️ 只有 pending 能改状态 ✓（服务端会拒绝 ✓）——
    // 列表里现在**混着已完成 / 已取消的**，所以这个判断必须有 ✗。
    if (pending) {
        actions.push(
            actionButton("完成", async () => {
                await post(`/deadlines/${item.id}/complete`, {});
                await refreshToday();
            }),
            actionButton("取消", async () => {
                await post(`/deadlines/${item.id}/cancel`, {});
                await refreshToday();
            }, true),
        );
    }
    const badges = [[statusLabel(item.time_status), badgeType(item.time_status)]];
    if (item.parent_id) badges.push(["里程碑", ""]);
    // ⚠️ 用户 2026-10-06 要求：**拆解过这件事本身要看得出来** ✓。
    // 卡上那个下拉框只在"最紧的那一条"上出现 ✓，
    // 所以在列表里也得标出来 —— 否则你翻到一条 DDL 时无法知道它下面还有里程碑 ✗。
    if (total) badges.push([`已拆解 ${total}`, ""]);

    const expanded = expandedDeadlines.has(item.id);
    const leading = children.length
        ? disclosureButton(
            expanded,
            `展开 / 收起这 ${children.length} 个子任务`,
            () => {
                if (expandedDeadlines.has(item.id)) expandedDeadlines.delete(item.id);
                else expandedDeadlines.add(item.id);
                renderPlanList("deadline");
            },
        )
        : null;

    const row = itemRow({
        done: !pending,
        title: item.title,
        badges,
        meta: `截止 ${localDisplay(item.due_at_local)}`,
        actions,
        extraClass: item.parent_id ? "milestone" : "",
        leading,
    });

    if (!children.length) return row;

    // ⚠️ 默认**收起来** ✓（用 `hidden` 属性 ✓ —— 全局那条 `[hidden]{display:none!important}` 兜着 ✓）。
    const group = document.createElement("div");
    group.className = "milestone-group";
    group.hidden = !expanded;
    for (const child of children) group.appendChild(deadlineRow(child, [], 0));

    const tree = document.createElement("div");
    tree.className = "deadline-tree";
    tree.append(row, group);
    return tree;
}

function habitRow(item) {
    const actions = [];
    if (item.active) {
        actions.push(actionButton("归档", async () => {
            const answer = await showDialog({
                title: "归档这个习惯？",
                body:
                    `「${item.name}」会从今天起收起来，不再出现在打卡列表里。\n\n` +
                    "打卡记录**全部保留** —— 以后在「已结束」里可以恢复。",
                options: [{ value: "archive", label: "归档" }],
            });
            if (!answer || answer.value !== "archive") return;
            await remove(`/habits/${item.id}`);
            showToast("已归档，记录保留。", 4000);
            await refreshToday();
        }, true));
    } else {
        // ⚠️ 「归档」的反操作 ✓ —— 没有它，归档就是单向的 ✗
        //（点错一次就只能去改数据库 ✓）。
        actions.push(actionButton("恢复", async () => {
            await post(`/habits/${item.id}/restore`, {});
            showToast("已放回养成中。", 4000);
            await refreshToday();
        }));
    }
    // ⚠️ **彻底删除** ✓ —— 连它**全部打卡记录**一起删 ✗，不可恢复 ✓。
    // 和「归档」是两件事：归档只是收起来（记录**留着** ✓）。
    actions.push(actionButton("删除", async () => {
        const answer = await showDialog({
            title: "彻底删掉这个习惯？",
            body:
                `「${item.name}」和它**全部打卡记录**都会被删掉，找不回来。\n\n` +
                "只是想让它从今天消失的话，用「归档」—— 那个会保留历史记录。",
            options: [{ value: "purge", label: "连记录一起删", danger: true }],
        });
        if (!answer || answer.value !== "purge") return;
        try {
            const result = await remove(`/habits/${item.id}/purge`);
            showToast(`已删掉（含 ${result?.deleted_logs ?? 0} 条记录）。`, 5000);
            await refreshToday();
        } catch (error) {
            showToast(error.message, 6000);
        }
    }, true));

    // ⚠️ 已结束的习惯**不给打卡控件** ✗ —— 它今天不参与追踪 ✓
    //（`active = 0` 时服务端也会拒绝写入 ✓）。
    let extra = null;
    if (item.active) {
        extra = document.createDocumentFragment();
        const progress = habitProgress(item);
        if (progress) extra.appendChild(progress);
        extra.appendChild(habitControl(item));
    }

    const target = habitTargetText(item);
    return itemRow({
        done: !item.active,
        title: item.name,
        badges: item.active
            ? [["养成中", "ongoing"], ...(item.met === true ? [["今日达标", "ongoing"]] : [])]
            : [["已结束", ""]],
        meta: item.active
            ? `${habitCurrentText(item)}${target ? ` · 目标 ${target}` : ""}`
            : `${target ? `目标 ${target} · ` : ""}已收起，打卡记录保留`,
        actions,
        extra,
    });
}

function renderPlanList(kind) {
    const container = el(PLAN_LISTS[kind].container);
    if (!container) return;
    const filter = planFilter[kind];

    // ⚠️ DDL 走**树**（父任务 + 收起来的里程碑 ✓），另外两张走平铺 ✓。
    if (kind === "deadline") {
        const groups = deadlineGroups(planCache.deadline, filter);
        if (!groups.length) {
            empty(container, PLAN_EMPTY.deadline[filter] || "这里还是空的。");
            return;
        }
        container.replaceChildren();
        for (const group of groups) {
            container.appendChild(deadlineRow(group.item, group.children, group.total));
        }
        return;
    }

    const items = kind === "schedule" ? scheduleListItems(filter) : habitListItems(filter);
    if (!items.length) {
        empty(container, PLAN_EMPTY[kind][filter] || "这里还是空的。");
        return;
    }
    container.replaceChildren();
    for (const item of items) {
        container.appendChild(kind === "schedule" ? scheduleRow(item) : habitRow(item));
    }
}

function syncFilterButtons(kind) {
    const dialog = el(PLAN_LISTS[kind].dialog);
    if (!dialog) return;
    for (const button of dialog.querySelectorAll("[data-filter-value]")) {
        button.classList.toggle("active", button.dataset.filterValue === planFilter[kind]);
    }
}

function renderOpenPlanLists() {
    for (const kind of Object.keys(PLAN_LISTS)) {
        if (el(PLAN_LISTS[kind].dialog)?.open) renderPlanList(kind);
    }
}

async function loadPlanData() {
    const [schedule, deadline, habit] = await Promise.all([
        get("/schedule"),
        get("/deadlines"),
        get("/habits"),
    ]);
    planCache = { schedule, deadline, habit };
}

async function refreshPlan() {
    await loadPlanData();
    renderPlanSummaries();
    renderOpenPlanLists();
}

function readFilter(kind) {
    try {
        const saved = localStorage.getItem(PLAN_LISTS[kind].key);
        if (saved && PLAN_FILTERS[kind].includes(saved)) return saved;
    } catch (error) {
        /* 隐私模式下 localStorage 会抛 —— 记不住就记不住 ✓ */
    }
    return "all";
}

function rememberFilter(kind, value) {
    planFilter[kind] = value;
    try {
        localStorage.setItem(PLAN_LISTS[kind].key, value);
    } catch (error) {
        /* 同上 ✓ */
    }
}

async function openPlanList(kind) {
    const dialog = el(PLAN_LISTS[kind].dialog);
    if (!dialog) return;
    planFilter[kind] = readFilter(kind);
    syncFilterButtons(kind);
    // 先用缓存画一遍（点开立刻有内容 ✓），再去拉最新的 ✓
    renderPlanList(kind);
    dialog.showModal();
    await refreshPlan();
}

// ⚠️ 卡片整体是**可点的** ✓，但里面的按钮 / 输入框 / 打卡控件**不能**连带打开列表 ✗
//（不然"点一下完成"会顺手弹出列表页 ✓ —— 那是这类"整卡可点"最常见的事故 ✗）。
function isCardControl(target) {
    return Boolean(target.closest("button, input, select, textarea, a, details, summary"));
}

function bindPlanCards() {
    for (const [kind, config] of Object.entries(PLAN_LISTS)) {
        const card = el(config.card);
        if (!card) continue;
        card.addEventListener("click", (event) => {
            if (event.target !== card && isCardControl(event.target)) return;
            openPlanList(kind);
        });
        card.addEventListener("keydown", (event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            if (event.target !== card && isCardControl(event.target)) return;
            event.preventDefault();
            openPlanList(kind);
        });
    }
}

function bindPlanFilters() {
    for (const [kind, config] of Object.entries(PLAN_LISTS)) {
        planFilter[kind] = readFilter(kind);
        syncFilterButtons(kind);
        const dialog = el(config.dialog);
        if (!dialog) continue;
        for (const button of dialog.querySelectorAll("[data-filter-value]")) {
            button.addEventListener("click", () => {
                rememberFilter(kind, button.dataset.filterValue);
                syncFilterButtons(kind);
                renderPlanList(kind);
            });
        }
    }
    bindPlanCards();
}

// 真的删掉一条 DDL（**从库里删** ✓，和「取消」不是一回事 ✗）。
//
// ⚠️ 它会连**子任务一起删** ✓ —— 所以必须让你确认一次 ✓（用 danger 样式 ✓）。
async function deleteDeadline(item) {
    const answer = await showDialog({
        title: "真的删掉？",
        body:
            `「${item.title}」会从库里消失，它下面的子任务也会一起删。\n\n` +
            "只是不想做了的话，用「取消」—— 那个会留下记录。",
        options: [{ value: "delete", label: "删掉", danger: true }],
    });
    if (!answer || answer.value !== "delete") return;

    try {
        await remove(`/deadlines/${item.id}`);
        showToast("已删掉。", 4000);
        await refreshToday();
    } catch (error) {
        showToast(error.message, 6000);
    }
}

// DDL 的「AI 拆解」（不变量 2 的形状：**AI 只建议，你确认了才落地** ✓）。
//
// ⚠️ 所以它是**两步**，绝不允许"点一下就改数据" ✗：
//    ① suggest → 拿到 AI 建议的子任务清单，**只显示给你看**
//    ② 你点确认 → apply → **才**真的建出那几条 ✓
async function suggestBreakdown(item) {
    let result;
    try {
        result = await post(`/deadlines/${item.id}/breakdown/suggest`, {});
    } catch (error) {
        showToast(error.message, 6000);
        return;
    }

    const subtasks = result.subtasks || [];
    if (!subtasks.length) {
        showToast(
            result.ai_available
                ? "他没能把这件事拆开 —— 也许它本来就不该拆。"
                : "AI 现在用不了（没填 Key，或模型不可用）。",
            6000,
        );
        return;
    }

    const body = subtasks
        .map((task, index) => {
            const due = task.due_at_local ? `（${localDisplay(task.due_at_local)}）` : "";
            return `${index + 1}. ${task.title}${due}`;
        })
        .join("\n");

    // ⚠️ `showDialog` 自己会补一个「取消」✓，所以这里只传确认那一个 ✓
    const answer = await showDialog({
        title: "他建议这样拆",
        body: `${body}\n\n确认后会真的建出这几条 —— 建错了可以再删。`,
        options: [{ value: "apply", label: "就按这个建" }],
    });
    if (!answer || answer.value !== "apply") return;

    try {
        await post(`/deadlines/${item.id}/breakdown/apply`, { subtasks });
        showToast(`已建出 ${subtasks.length} 条。`, 5000);
        await refreshToday();
    } catch (error) {
        showToast(error.message, 6000);
    }
}

async function renderTimeline() {
    const container = el("timeline-list");
    const items = await get("/timeline/today");
    if (!items.length) return empty(container, "今天的实际时间轴还是空的。番茄结束后也会自动写到这里。 ");
    container.replaceChildren();
    for (const item of items) {
        const row = document.createElement("div");
        row.className = "timeline-row";
        const time = document.createElement("div");
        time.className = "timeline-time";
        time.textContent = `${localDisplay(item.start_at_local, false)}${item.end_at_local ? `–${localDisplay(item.end_at_local, false)}` : ""}`;
        const mark = document.createElement("div");
        mark.className = "timeline-mark";
        const body = document.createElement("div");
        const title = document.createElement("div");
        title.className = "item-title";
        title.textContent = item.title;
        const meta = document.createElement("div");
        meta.className = "item-meta";
        meta.textContent = item.source_type === "pomodoro" ? "番茄自动记录" : "手动记录";
        body.append(title, meta);
        row.append(time, mark, body);
        container.appendChild(row);
    }
}

function renderSnapshot(context) {
    const pendingDeadlines = context.deadlines.length;
    const met = context.habits.filter((x) => x.met === true).length;
    el("metric-deadlines").textContent = pendingDeadlines;
    el("metric-pomodoro").textContent = context.pomodoro.count;
    el("metric-pomodoro-minutes").textContent = `${context.pomodoro.minutes} min`;
    el("metric-habits").textContent = `${met}/${context.habits.length}`;

    const overdue = context.deadlines.filter((x) => x.time_status === "overdue");
    if (overdue.length) {
        el("snapshot-title").textContent = `有 ${overdue.length} 个 DDL 已经过期。`;
        el("snapshot-text").textContent = `先别排满今天，优先处理「${overdue[0].title}」这种真实约束。`;
    } else if (context.deadlines.length) {
        el("snapshot-title").textContent = "今天不用安排得太死。";
        el("snapshot-text").textContent = `最近的约束是「${context.deadlines[0].title}」，截止 ${localDisplay(context.deadlines[0].due_at_local)}。`;
    } else {
        el("snapshot-title").textContent = "今天暂时没有逼近的 DDL。";
        el("snapshot-text").textContent = "留一点余地，也留一点真实记录。";
    }
}

function rebuildPomodoroTargets(context) {
    const select = el("pomodoro-target");
    const current = select.value;
    select.replaceChildren();
    const custom = new Option("临时事项…", "custom");
    select.appendChild(custom);
    for (const item of context.schedule.filter((x) => x.completion_status === "pending")) {
        select.appendChild(new Option(`日程 · ${item.title}`, `schedule:${item.id}`));
    }
    for (const item of context.deadlines) {
        select.appendChild(new Option(`DDL · ${item.title}`, `deadline:${item.id}`));
    }
    if ([...select.options].some((x) => x.value === current)) select.value = current;
    el("pomodoro-custom").hidden = select.value !== "custom";
}

function renderPomodoroRunning(session) {
    runningPomodoro = session;
    el("pomodoro-idle").hidden = Boolean(session);
    el("pomodoro-running").hidden = !session;
    clearInterval(timerHandle);
    if (!session) return;
    el("pomodoro-label").textContent = session.target_label;

    const received = performance.now();
    let toggle = el("toggle-pomodoro-pause");
    if (!toggle) {
        toggle = document.createElement("button");
        toggle.id = "toggle-pomodoro-pause";
        el("finish-pomodoro").before(toggle);
    }
    toggle.textContent = session.paused ? "继续专注" : "暂停";
    toggle.onclick = async () => {
        toggle.disabled = true;
        try {
            await post(`/pomodoro/${session.id}/${session.paused ? "resume" : "pause"}`, {});
            await refreshPomodoro();
        } catch (error) { showToast(error.message); }
        finally { toggle.disabled = false; }
    };
    const update = () => {
        const planned = session.planned_minutes * 60;
        const elapsed = session.focus_seconds + (session.paused ? 0 : Math.floor((performance.now() - received) / 1000));
        const remaining = Math.max(0, planned - elapsed);
        const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
        const ss = String(remaining % 60).padStart(2, "0");
        el("pomodoro-clock").textContent = `${mm}:${ss}`;
    };
    update();
    timerHandle = setInterval(update, 1000);
}

async function refreshPomodoro() {
    const session = await get("/pomodoro/current");
    renderPomodoroRunning(session);
}

export async function refreshToday() {
    try {
        const [context] = await Promise.all([
            get("/context/today"),
            refreshPlan(),
            renderTimeline(),
            refreshPomodoro(),
        ]);
        currentContext = context;
        renderSnapshot(context);
        rebuildPomodoroTargets(context);
        // ⚠️ 通知行动中心 ✓ —— 番茄结束 / 取消 / 暂停都会走到这里 ✓，
        // 而行动中心显示的"实际耗时"是**同一份数据** ✓（`action_plan_steps` ✓）。
        // 不通知的话，那边会一直显示**上一次**的数字 ✗ 直到手动刷新 ✓。
        window.dispatchEvent(new CustomEvent("today:refreshed"));
    } catch (error) {
        showToast(error.message, 3800);
        throw error;
    }
}

function bindForms() {
    el("schedule-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        try {
            await post("/schedule", {
                title: data.get("title"),
                description: null,
                start_at_local: data.get("start"),
                end_at_local: data.get("end") || null,
                note: data.get("note") || null,
            });
            form.closest("dialog").close();
            form.reset();
            showToast("日程已保存");
            await refreshToday();
        } catch (error) { showToast(error.message, 3800); }
    });

    el("deadline-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        try {
            await post("/deadlines", {
                title: data.get("title"),
                description: null,
                due_at_local: data.get("due"),
                parent_id: null,
                priority: Number(data.get("priority") || 0),
            });
            form.closest("dialog").close();
            form.reset();
            showToast("DDL 已保存");
            await refreshToday();
        } catch (error) { showToast(error.message, 3800); }
    });

    const habitType = el("habit-type");
    const syncHabitFields = () => {
        const type = habitType.value;
        el("habit-number-fields").hidden = !["numeric", "duration"].includes(type);
        el("habit-time-fields").hidden = type !== "time";
    };
    habitType.addEventListener("change", syncHabitFields);
    syncHabitFields();

    el("habit-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        const type = data.get("tracking_type");
        const payload = {
            name: data.get("name"),
            tracking_type: type,
            aggregation_mode: ["numeric", "duration"].includes(type) ? "sum" : "latest",
            unit: type === "duration" ? "min" : (data.get("unit") || null),
            target_value: ["numeric", "duration"].includes(type) && data.get("target_value") ? Number(data.get("target_value")) : null,
            target_text: type === "time" ? data.get("target_text") : null,
            target_operator: type === "time" ? "<=" : (type === "boolean" ? null : ">="),
            time_anchor: type === "time" ? data.get("time_anchor") : null,
        };
        try {
            await post("/habits", payload);
            form.closest("dialog").close();
            form.reset();
            syncHabitFields();
            showToast("习惯已创建");
            await refreshToday();
        } catch (error) { showToast(error.message, 3800); }
    });

    el("timeline-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = new FormData(form);
        try {
            await post("/timeline", {
                title: data.get("title"),
                start_at_local: data.get("start"),
                end_at_local: data.get("end") || null,
                note: null,
            });
            form.closest("dialog").close();
            form.reset();
            showToast("已记入实际时间轴");
            await refreshToday();
        } catch (error) { showToast(error.message, 3800); }
    });
}

function bindPomodoro() {
    el("pomodoro-target").addEventListener("change", (event) => {
        el("pomodoro-custom").hidden = event.target.value !== "custom";
    });

    el("start-pomodoro").addEventListener("click", async () => {
        const select = el("pomodoro-target").value;
        const payload = {
            planned_minutes: Number(el("pomodoro-minutes").value || 25),
            schedule_event_id: null,
            deadline_id: null,
            custom_label: null,
        };
        if (select.startsWith("schedule:")) payload.schedule_event_id = Number(select.split(":")[1]);
        else if (select.startsWith("deadline:")) payload.deadline_id = Number(select.split(":")[1]);
        else payload.custom_label = el("pomodoro-custom").value.trim();
        if (select === "custom" && !payload.custom_label) {
            showToast("先写一个临时专注事项。");
            return;
        }
        try {
            const session = await post("/pomodoro/start", payload);
            renderPomodoroRunning(session);
            showToast("番茄开始");
        } catch (error) { showToast(error.message, 3800); }
    });

    el("finish-pomodoro").addEventListener("click", async () => {
        if (!runningPomodoro) return;
        try {
            await post(`/pomodoro/${runningPomodoro.id}/finish`, {});
            showToast("番茄已写入实际时间轴");
            await refreshToday();
        } catch (error) { showToast(error.message, 3800); }
    });

    el("cancel-pomodoro").addEventListener("click", async () => {
        if (!runningPomodoro) return;
        try {
            await post(`/pomodoro/${runningPomodoro.id}/cancel`, {});
            showToast("番茄已取消");
            await refreshToday();
        } catch (error) { showToast(error.message, 3800); }
    });
}

function renderReview(review) {
    currentReview = review;
    el("review-loading").hidden = true;
    el("review-content").hidden = false;
    const stats = review.stats;
    const statItems = [
        ["日程完成", `${stats.schedule.completed}/${stats.schedule.total}`],
        ["番茄", `${stats.pomodoro.count} · ${stats.pomodoro.minutes}m`],
        ["习惯", `${stats.habits.met}/${stats.habits.tracked}`],
        ["逾期DDL", stats.deadlines.overdue],
        ["时间轴", stats.timeline_events],
    ];
    const statsBox = el("review-stats");
    statsBox.replaceChildren();
    for (const [label, value] of statItems) {
        const box = document.createElement("div");
        box.className = "review-stat";
        const span = document.createElement("span"); span.textContent = label;
        const strong = document.createElement("strong"); strong.textContent = value;
        box.append(span, strong); statsBox.appendChild(box);
    }
    renderExecutionSummary(statsBox, stats.execution);
    el("review-summary").textContent = review.ai_summary || "—";
    el("review-affirmation").textContent = review.affirmation || "—";
    el("review-issue").textContent = review.main_issue || "—";
    const focusBox = el("review-focus");
    focusBox.replaceChildren();
    for (const item of review.tomorrow_focus) {
        const row = document.createElement("div"); row.className = "focus-row";
        const input = document.createElement("input");
        input.value = item.text;
        input.dataset.deadlineId = item.deadline_id ?? "";
        row.appendChild(input); focusBox.appendChild(row);
    }
    if (!review.tomorrow_focus.length) {
        const p = document.createElement("div"); p.className = "muted"; p.textContent = "没有自动生成重点；你可以先保持明天空白。"; focusBox.appendChild(p);
    }
    if (!review.ai_available) {
        el("review-summary").textContent += "（当前为无 API Key 的本地回退结果）";
    }
}

async function generateReview() {
    el("review-loading").hidden = false;
    el("review-content").hidden = true;
    const review = await post("/review/today/generate", {});
    renderReview(review);
}

function bindReview() {
    el("open-review").addEventListener("click", async () => {
        const dialog = el("review-dialog"); dialog.showModal();
        try {
            const existing = await get("/review/today");
            if (existing) renderReview(existing); else await generateReview();
        } catch (error) { showToast(error.message, 3800); }
    });
    el("regenerate-review").addEventListener("click", async () => {
        try { await generateReview(); } catch (error) { showToast(error.message, 3800); }
    });
    el("confirm-review").addEventListener("click", async () => {
        if (!currentReview) return;
        const inputs = [...el("review-focus").querySelectorAll("input")];
        const focus = inputs.filter((x) => x.value.trim()).map((x) => ({
            text: x.value.trim(),
            deadline_id: x.dataset.deadlineId ? Number(x.dataset.deadlineId) : null,
        }));
        try {
            const result = await post("/review/today/confirm", { tomorrow_focus: focus });
            renderReview(result);
            showToast("今天已归档");
            setTimeout(() => el("review-dialog").close(), 500);
        } catch (error) { showToast(error.message, 3800); }
    });
}

function bindDialogs() {
    document.querySelectorAll("[data-open-dialog]").forEach((button) => {
        button.addEventListener("click", () => {
            const dialog = el(button.dataset.openDialog);
            const now = toDatetimeLocalValue();
            dialog.querySelectorAll('input[type="datetime-local"]').forEach((input) => {
                if (!input.value) input.value = now;
            });
            dialog.showModal();
            el("fab-menu").hidden = true;
        });
    });
    document.querySelectorAll(".dialog-close").forEach((button) => {
        button.addEventListener("click", () => button.closest("dialog").close());
    });
    document.querySelectorAll("dialog").forEach((dialog) => {
        dialog.addEventListener("click", (event) => {
            if (event.target === dialog) dialog.close();
        });
    });
}

export function initToday() {
    bindForms();
    bindPomodoro();
    bindReview();
    bindDialogs();
    bindPlanFilters();
}
