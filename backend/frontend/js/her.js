import { get, patch, post, put, remove } from "./api.js";
import { showToast } from "./utils.js";

const el = (id) => document.getElementById(id);

const ATTITUDE_LABELS = {
    none: "还没表态",
    agreed: "你认同",
    disputed: "你有异议",
};

function humanDate(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso).slice(0, 10);
    return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" }).format(date);
}

function badge(text, type) {
    const span = document.createElement("span");
    span.className = "badge";
    if (type) span.classList.add(type);
    span.textContent = text;
    return span;
}

// 叙事视角，不是版本列表（MEMORY_DESIGN §7.3）
function narrative(lineage) {
    const past = lineage.history.slice(0, -1);
    const now = lineage.current;

    if (!past.length) return `我觉得：${now.content}。`;

    const parts = past.map((entry, index) => {
        const when = humanDate(entry.created_at);
        return index === 0
            ? `${when} 我一开始觉得：${entry.content}`
            : `${when} 我改了看法：${entry.content}`;
    });
    parts.push(`现在我觉得：${now.content}`);
    return `${parts.join("。")}。`;
}

// ---------- 标签：归纳的依据 ----------

const DIMENSION_LABELS = {
    state: "状态",
    domain: "领域",
    object: "对象",
    pattern: "行为模式",
    time: "时间",
};

// 取一次，给"选标签"的下拉用
let allTags = [];
// 事件只绑一次（loadTags 每次刷新都会跑到这里）
let tagsBound = false;

function tagChip(tag, { removable = false } = {}) {
    const chip = document.createElement("span");
    chip.className = "tag-chip";
    chip.append(document.createTextNode(`${tag.name} · ${tag.observation_count ?? 0}`));

    if (removable) {
        const drop = document.createElement("button");
        drop.type = "button";
        drop.className = "tag-chip-drop";
        drop.textContent = "×";
        drop.title = "删掉这个标签。它只取消分类，事实还在。";
        drop.addEventListener("click", async () => {
            try {
                await remove(`/memory/tags/${tag.id}`);
                showToast("标签已删除。事实还在，只是不再分类。", 4000);
                await loadHer();
            } catch (error) {
                showToast(error.message, 5000);
            }
        });
        chip.append(drop);
    }
    return chip;
}

function bindTagsOnce() {
    if (tagsBound) return;
    tagsBound = true;

    el("tag-new-toggle").addEventListener("click", () => {
        const form = el("tag-form");
        form.hidden = !form.hidden;
    });

    el("tag-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = el("tag-name").value.trim();
        if (!name) return;

        try {
            await post("/memory/tags", { name, dimension: el("tag-dimension").value });
            el("tag-form").hidden = true;
            el("tag-name").value = "";
            showToast("标签已建好。同类的事实会归到它下面。", 4000);
            await loadHer();
        } catch (error) {
            showToast(error.message, 5000);
        }
    });
}

async function loadTags() {
    const container = el("tag-list");
    bindTagsOnce();

    // ⚠️ 建议必须在**提前 return 之前**加载：
    // "一个标签都没有"正是最需要看到她建议的时候（冷启动）。
    await loadTagSuggestions();

    try {
        allTags = await get("/memory/tags");
        container.replaceChildren();

        if (!allTags.length) {
            const empty = document.createElement("div");
            empty.className = "empty-state";
            empty.textContent =
                "还没有标签。归纳靠它判断「同类」—— 没有标签就凑不出同类，" +
                "也就永远形成不了判断。先建一个你真正关心的（例如「熬夜」）。";
            container.append(empty);
            return;
        }

        // 按维度分组：设计建议同一维度保留 3–5 个，分组能让你看出有没有长歪
        const grouped = new Map();
        for (const tag of allTags) {
            if (!grouped.has(tag.dimension)) grouped.set(tag.dimension, []);
            grouped.get(tag.dimension).push(tag);
        }

        for (const [dimension, items] of grouped) {
            const group = document.createElement("div");
            group.className = "tag-group";

            const title = document.createElement("div");
            title.className = "tag-group-title";
            title.textContent = `${DIMENSION_LABELS[dimension] || dimension}（${items.length}）`;

            const chips = document.createElement("div");
            chips.className = "tag-chips";
            for (const tag of items) chips.append(tagChip(tag, { removable: true }));

            group.append(title, chips);
            container.append(group);
        }
    } catch (error) {
        showToast(error.message, 3800);
    }
}

// ---------- 她的标签建议（她只能建议，不能自己创建） ----------

async function loadTagSuggestions() {
    const box = el("tag-suggestion-box");
    const container = el("tag-suggestions");

    try {
        const suggestions = await get("/memory/tag-suggestions");
        container.replaceChildren();
        box.hidden = suggestions.length === 0;

        for (const suggestion of suggestions) {
            container.append(suggestionRow(suggestion));
        }
    } catch (error) {
        showToast(error.message, 3800);
    }
}

function suggestionRow(suggestion) {
    const row = document.createElement("div");
    row.className = "evidence-row";

    const body = document.createElement("div");

    const title = document.createElement("div");
    title.className = "item-title";
    // 次数 > 1 才显示 —— "她反复注意到同一件事"本身就是一条信息
    title.textContent =
        suggestion.count > 1
            ? `${suggestion.name} · 提了 ${suggestion.count} 次`
            : suggestion.name;

    const reason = document.createElement("div");
    reason.className = "evidence-meta muted";
    reason.textContent = suggestion.reason || "（没有说明理由）";

    body.append(title, reason);

    const actions = document.createElement("div");
    actions.className = "tag-chips";

    const yes = document.createElement("button");
    yes.type = "button";
    yes.className = "mini-button";
    yes.textContent = "批准";
    yes.title = "批准后它就是一个真标签，以后同类的事实用它分类。";
    yes.addEventListener("click", () => decideSuggestion(suggestion.id, "approve"));

    const no = document.createElement("button");
    no.type = "button";
    no.className = "mini-button secondary";
    no.textContent = "不要";
    no.title = "拒绝之后，这个名字 TA 永远不会再提。";
    no.addEventListener("click", () => decideSuggestion(suggestion.id, "reject"));

    actions.append(yes, no);
    row.append(body, actions);
    return row;
}

async function decideSuggestion(suggestionId, decision) {
    try {
        await post(`/memory/tag-suggestions/${suggestionId}/${decision}`, {});
        showToast(
            decision === "approve" ? "标签已建好。" : "不会再提这个名字了。",
            4000,
        );
        await loadHer();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

async function setObservationTags(observation, tagIds) {
    try {
        await put(`/memory/observations/${observation.id}/tags`, { tag_ids: tagIds });
        await loadHer();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

// ---------- 一条事实 ----------

function observationRow(observation) {
    const row = document.createElement("div");
    row.className = "evidence-row";

    const body = document.createElement("div");

    // 事实在前，原话在后 —— 和"依据"那张卡相反：
    // 那里重点是"他说的话"，这里重点是"记住的那件事"。
    const fact = document.createElement("div");
    fact.className = "item-title";
    fact.textContent = observation.content;

    const meta = document.createElement("div");
    meta.className = "evidence-meta muted";
    const confidence = Math.round((observation.confidence ?? 1) * 100);
    meta.textContent = `${humanDate(observation.observed_at)} · 置信度 ${confidence}%`;

    const quote = document.createElement("div");
    quote.className = "evidence-quote";
    quote.textContent = `原话：「${observation.source_text || "（没有留下原话）"}」`;

    body.append(fact, meta, quote);

    // 标签：一条事实得挂上标签，才可能和别人凑成「同类」、被归纳成判断。
    const own = observation.tags || [];
    const rest = allTags.filter((tag) => !own.some((t) => t.id === tag.id));

    if (own.length || rest.length) {
        const tags = document.createElement("div");
        tags.className = "tag-chips";

        for (const tag of own) {
            const chip = document.createElement("span");
            chip.className = "tag-chip";
            chip.append(document.createTextNode(tag.name));

            const off = document.createElement("button");
            off.type = "button";
            off.className = "tag-chip-drop";
            off.textContent = "×";
            off.title = "取消这条分类。事实本身还在。";
            off.addEventListener("click", () =>
                setObservationTags(
                    observation,
                    own.filter((t) => t.id !== tag.id).map((t) => t.id),
                ),
            );
            chip.append(off);
            tags.append(chip);
        }

        if (rest.length) {
            const picker = document.createElement("select");
            picker.className = "tag-picker";
            picker.append(new Option("+ 标签", ""));
            for (const tag of rest) picker.append(new Option(tag.name, tag.id));
            picker.addEventListener("change", () => {
                if (!picker.value) return;
                setObservationTags(observation, [
                    ...own.map((t) => t.id),
                    Number(picker.value),
                ]);
            });
            tags.append(picker);
        }

        body.append(tags);
    }

    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "mini-button secondary";
    drop.textContent = "删掉";
    drop.title =
        "删掉这条事实。判断仍然保留，只是少了一条依据。" +
        "想改成别的内容就删掉它、再重新说一遍 —— " +
        "事实的文字不给手改（改了它就对不上原话了）。";
    // 复用依据卡的删除逻辑：它已经处理了"这是某条判断的最后一条依据"那种拒绝
    drop.addEventListener("click", () => dropEvidence(observation.id));

    row.append(body, drop);
    return row;
}

async function loadObservations() {
    const container = el("observation-list");
    try {
        const observations = await get("/memory/observations?limit=30");
        container.replaceChildren();

        if (!observations.length) {
            const empty = document.createElement("div");
            empty.className = "empty-state";
            empty.textContent =
                "他还没记住什么。在聊天里说一件具体的事（例如「今天背了两小时单词」），" +
                "几分钟后它会自己出现在这里 —— 也可以在「设置 → 记忆抽取」里立刻抽一次。";
            container.append(empty);
            return;
        }
        for (const observation of observations) {
            container.append(observationRow(observation));
        }
    } catch (error) {
        showToast(error.message, 3800);
    }
}


function evidenceRow(observation) {
    const row = document.createElement("div");
    row.className = "evidence-row";

    const body = document.createElement("div");
    const quote = document.createElement("div");
    quote.className = "evidence-quote";
    quote.textContent = `「${observation.source_text || "（没有留下原话）"}」`;

    const meta = document.createElement("div");
    meta.className = "evidence-meta muted";
    meta.textContent = `${humanDate(observation.observed_at)} · ${observation.content}`;

    body.append(quote, meta);

    const fix = document.createElement("button");
    fix.type = "button";
    fix.className = "mini-button secondary";
    fix.textContent = "这条记错了";
    fix.title = "删掉这条事实。他仍然保留判断，只是少了一条依据。";
    fix.addEventListener("click", () => dropEvidence(observation.id));

    row.append(body, fix);
    return row;
}

function lineageCard(lineage) {
    const current = lineage.current;
    const card = document.createElement("article");
    card.className = "panel her-card";

    const narrativeText = document.createElement("p");
    narrativeText.className = "her-narrative";
    narrativeText.textContent = narrative(lineage);
    card.append(narrativeText);

    const badges = document.createElement("div");
    badges.className = "her-badges";
    badges.append(
        badge(`第 ${lineage.generation} 代`, ""),
        badge(
            ATTITUDE_LABELS[current.attitude] || current.attitude,
            current.attitude === "disputed" ? "overdue" : "",
        ),
        badge(`置信度 ${Math.round(current.confidence * 100)}%`, ""),
    );
    if (current.counterexamples > 0) {
        badges.append(
            badge(`反例 ${current.counterexamples} 次`, current.needs_revision ? "overdue" : ""),
        );
    }
    card.append(badges);

    if (current.needs_revision) {
        const hint = document.createElement("p");
        hint.className = "her-hint";
        hint.textContent =
            "反例已经攒够 3 次——他该重新想一遍了（等下一次归纳时他会改写这条判断）。";
        card.append(hint);
    }

    const evidenceTitle = document.createElement("h4");
    evidenceTitle.className = "her-section-title";
    evidenceTitle.textContent = `依据（${current.evidence.length} 条）`;
    card.append(evidenceTitle);

    const evidenceList = document.createElement("div");
    evidenceList.className = "evidence-list";
    for (const observation of current.evidence) {
        evidenceList.append(evidenceRow(observation));
    }
    if (!current.evidence.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "这条判断现在没有可用依据了。";
        evidenceList.append(empty);
    }
    card.append(evidenceList);

    const past = lineage.history.slice(0, -1);
    if (past.length) {
        const details = document.createElement("details");
        details.className = "her-history";
        const summary = document.createElement("summary");
        summary.textContent = `他改过 ${past.length} 次看法`;
        details.append(summary);
        for (const entry of past.slice().reverse()) {
            const line = document.createElement("div");
            line.className = "her-history-row";
            line.textContent = `${humanDate(entry.created_at)}：${entry.content}`;
            details.append(line);
        }
        card.append(details);
    }

    const actions = document.createElement("div");
    actions.className = "button-row her-actions";

    const agree = document.createElement("button");
    agree.type = "button";
    agree.className = "mini-button";
    agree.textContent = current.attitude === "agreed" ? "已认同" : "认同";
    agree.disabled = current.attitude === "agreed";
    agree.addEventListener("click", () => setAttitude(current.id, "agreed"));

    const dispute = document.createElement("button");
    dispute.type = "button";
    dispute.className = "mini-button secondary";
    dispute.textContent = current.attitude === "disputed" ? "已标异议" : "异议";
    dispute.disabled = current.attitude === "disputed";
    dispute.title = "他不会因此删掉这条判断，但以后引用时会带上「你不同意」。";
    dispute.addEventListener("click", () => setAttitude(current.id, "disputed"));

    actions.append(agree, dispute);
    card.append(actions);

    return card;
}

async function loadImpressions() {
    const container = el("her-impressions");
    try {
        const lineages = await get("/memory/impression-lineages");
        container.replaceChildren();

        if (!lineages.length) {
            const empty = document.createElement("div");
            empty.className = "empty-state";
            empty.textContent =
                "他还没有对你形成判断。等他攒够观察，这里会出现他的看法，以及每条看法的依据。";
            container.append(empty);
            return;
        }
        for (const lineage of lineages) container.append(lineageCard(lineage));
    } catch (error) {
        showToast(error.message, 3800);
    }
}

// ── 你说过的话（全量）：他记下的一切 ✓ ────────────────────────
//
// ⚠️ **这一层和 L1 不是一回事** ✗（用户 2026-10-05 提的 ✓）：
//   · `raw_entries`（L1）= **只有成了事实的那些原话** ✓ —— 是个**子集** ✗
//   · **这里** = `messages` 里的**每一句你说的话** ✓ —— 才是"他记下的一切" ✓✓
//
// 每句下面写着**从它抽出了几条事实** ✓（没抽到很正常 ✓，不是丢了 ✓）。
//
// 只读 ✓ —— 原话只追加、永不改写 ✓（它是事实源 ✓）。

function herEmptyNote(container, message) {
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = message;
    container.append(note);
}

function quoteRow(quote) {
    const row = document.createElement("div");
    row.className = "evidence-row";

    const body = document.createElement("div");

    const text = document.createElement("div");
    text.className = "item-title";
    text.textContent = quote.content;

    const meta = document.createElement("div");
    meta.className = "evidence-meta muted";
    const facts = (quote.fact_ids || []).length;
    const from = quote.imported ? "导入的清单" : "聊天里说的";
    meta.textContent = facts
        ? `${humanDate(quote.created_at)} · ${from} · 抽出 ${facts} 条事实`
        : `${humanDate(quote.created_at)} · ${from} · 还没抽到事实`;

    body.append(text, meta);
    row.append(body);
    return row;
}

async function loadQuotes() {
    const container = el("quote-list");
    if (!container) return;
    container.replaceChildren();
    try {
        const quotes = await get("/memory/quotes?limit=100");
        if (!quotes.length) {
            herEmptyNote(
                container,
                "还没有。你先在聊天里说点什么 —— 从你说第一句开始，这里就会全都在。",
            );
            return;
        }
        for (const quote of quotes) container.append(quoteRow(quote));
    } catch (error) {
        herEmptyNote(container, error.message);
    }
}

// ── 页内分区导航 ──────────────────────────────────────────────

const HER_PANES = ["quotes", "facts", "impressions", "tags", "promises", "rules"];
const HER_PANE_KEY = "life-ai-her-pane";

function showHerPane(name) {
    const target = HER_PANES.includes(name) ? name : "impressions";
    for (const pane of document.querySelectorAll("[data-her-pane]")) {
        pane.hidden = pane.dataset.herPane !== target;
    }
    for (const button of document.querySelectorAll("[data-her-goto]")) {
        button.classList.toggle("active", button.dataset.herGoto === target);
    }
    try {
        localStorage.setItem(HER_PANE_KEY, target);
    } catch (error) {
        /* 隐私模式下 localStorage 会抛 —— 记不住就记不住 ✓ */
    }
}

function bindHerNav() {
    for (const button of document.querySelectorAll("[data-her-goto]")) {
        button.addEventListener("click", () => showHerPane(button.dataset.herGoto));
    }
    let saved = "impressions";
    try {
        saved = localStorage.getItem(HER_PANE_KEY) || "impressions";
    } catch (error) {
        /* 同上 ✓ */
    }
    showHerPane(saved);
}

export async function loadHer() {
    // ⚠️ 标签必须**先**拿到（await 而不是并发）：
    // 事实那一栏的「+ 标签」下拉靠 allTags 渲染，并发会因为竞态而渲染出空下拉。
    await loadTags();

    // 其余各自独立加载：印象为空不该连带其他块也不显示
    await Promise.all([
        loadImpressions(),
        loadObservations(),
        loadQuotes(),
        loadRules(),
        loadCommitments(),
    ]);
}

async function setAttitude(impressionId, attitude) {
    try {
        await post(`/memory/impressions/${impressionId}/attitude`, { attitude });
        await loadHer();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

async function dropEvidence(observationId) {
    try {
        await remove(`/memory/observations/${observationId}`);
        showToast("已删掉那条事实。他的判断还在，只是少了一条依据。", 4000);
        await loadHer();
    } catch (error) {
        showToast(error.message, 6000);
    }
}

// ---------- 规则（S7） ----------

const RULE_SOURCE_LABELS = {
    manual: "你手改的",
    chat: "他从聊天理解的",
};

function ruleRow(rule) {
    const card = document.createElement("div");
    card.className = "item-card rule-card";
    if (!rule.in_effect) card.classList.add("done");

    const body = document.createElement("div");
    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = rule.content;

    const meta = document.createElement("div");
    meta.className = "item-meta";
    const bits = [RULE_SOURCE_LABELS[rule.source] || rule.source];
    bits.push(rule.is_permanent ? "永久" : `到 ${rule.expires_at_local} 为止`);
    if (rule.status === "paused") bits.push("已暂停");
    if (rule.status === "revoked") bits.push("已撤销");
    if (!rule.in_effect) bits.push("当前不生效");
    meta.textContent = bits.join(" · ");

    body.append(title, meta);

    if (rule.source_text) {
        // 硬规则 1：聊天改的规则必须能显示来源原话
        const quote = document.createElement("div");
        quote.className = "rule-quote";
        quote.textContent = `来源：「${rule.source_text}」`;
        body.append(quote);
    }

    const actions = document.createElement("div");
    actions.className = "item-actions";

    const pause = document.createElement("button");
    pause.type = "button";
    pause.className = "mini-button secondary";
    pause.textContent = rule.status === "active" ? "暂停" : "恢复";
    pause.addEventListener("click", () =>
        patchRule(rule.id, { status: rule.status === "active" ? "paused" : "active" }),
    );

    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "mini-button";
    edit.textContent = "改";
    edit.addEventListener("click", () => {
        // 就地编辑，不用弹窗（U2：不喜欢弹窗 / 抽屉）
        const editor = document.createElement("div");
        editor.className = "rule-editor";

        const input = document.createElement("input");
        input.value = rule.content;
        input.maxLength = 300;

        const save = document.createElement("button");
        save.type = "button";
        save.className = "mini-button";
        save.textContent = "保存";
        save.addEventListener("click", () => {
            const text = input.value.trim();
            if (!text) return;
            patchRule(rule.id, { content: text });
        });

        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "mini-button secondary";
        cancel.textContent = "取消";
        cancel.addEventListener("click", () => editor.remove());

        editor.append(input, save, cancel);
        card.append(editor);
    });

    const drop = document.createElement("button");
    drop.type = "button";
    drop.className = "mini-button secondary";
    drop.textContent = "删";
    drop.addEventListener("click", () => deleteRule(rule.id));

    actions.append(pause, edit, drop);
    card.append(body, actions);
    return card;
}

async function loadRules() {
    const container = el("rule-list");
    try {
        const rules = await get("/rules");
        container.replaceChildren();

        if (!rules.length) {
            const empty = document.createElement("div");
            empty.className = "empty-state";
            empty.textContent =
                "还没有任何规则。你说一句「以后十一点以后别找我」，或者点右上角自己加一条。";
            container.append(empty);
            return;
        }
        for (const rule of rules) container.append(ruleRow(rule));
    } catch (error) {
        showToast(error.message, 3800);
    }
}

async function patchRule(ruleId, payload) {
    try {
        await patch(`/rules/${ruleId}`, payload);
        await loadRules();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

async function deleteRule(ruleId) {
    try {
        await remove(`/rules/${ruleId}`);
        await loadRules();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

function bindRules() {
    el("rule-new-toggle").addEventListener("click", () => {
        const form = el("rule-form");
        form.hidden = !form.hidden;
    });

    el("rule-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const content = el("rule-content").value.trim();
        if (!content) return;

        const expires = el("rule-expires").value.trim();
        const sourceText = el("rule-source").value.trim();

        try {
            await post("/rules", {
                content,
                expires_at_local: expires || null,
                source_text: sourceText || null,
            });
            el("rule-form").hidden = true;
            el("rule-content").value = "";
            el("rule-expires").value = "";
            el("rule-source").value = "";
            showToast("规则已保存。界面手改的优先于他从聊天理解的。", 4000);
            await loadRules();
        } catch (error) {
            showToast(error.message, 5000);
        }
    });
}

export function initHer() {
    // 记忆分区导航 ✓ —— 一次只显示一块（要的是"可翻看 + 简洁漂亮" ✓）
    bindHerNav();
    bindRules();
    bindCommitments();
    el("her-refresh").addEventListener("click", loadHer);
}


// ---------- 承诺（S7 · 关系记忆 LR） ----------

function promiseRow(commitment) {
    const card = document.createElement("div");
    card.className = "item-card rule-card";
    if (!commitment.in_effect) card.classList.add("done");

    const body = document.createElement("div");
    const title = document.createElement("div");
    title.className = "item-title";
    title.textContent = commitment.content;

    const meta = document.createElement("div");
    meta.className = "item-meta";
    const bits = [commitment.created_via === "chat" ? "他从聊天答应的" : "你手记的"];
    if (commitment.window_text) bits.push(commitment.window_text);
    bits.push(commitment.is_permanent ? "永久" : `到 ${commitment.expires_at_local} 为止`);
    if (commitment.status === "changed") bits.push("已改口");
    if (commitment.status === "revoked") bits.push("已撤销");
    if (!commitment.in_effect) bits.push("当前不生效");
    meta.textContent = bits.join(" · ");

    body.append(title, meta);

    if (commitment.source_text) {
        const quote = document.createElement("div");
        quote.className = "rule-quote";
        quote.textContent = `你的原话：「${commitment.source_text}」`;
        body.append(quote);
    }

    const actions = document.createElement("div");
    actions.className = "item-actions";

    const revise = document.createElement("button");
    revise.type = "button";
    revise.className = "mini-button";
    revise.textContent = "改口";
    revise.title = "生成新版本；旧版本会保留下来，这样「他改过口」本身也留得住。";
    revise.addEventListener("click", () => {
        const editor = document.createElement("div");
        editor.className = "rule-editor";

        const input = document.createElement("input");
        input.value = commitment.content;
        input.maxLength = 600;

        const save = document.createElement("button");
        save.type = "button";
        save.className = "mini-button";
        save.textContent = "保存";
        save.addEventListener("click", () => {
            const text = input.value.trim();
            if (!text) return;
            revisePromise(commitment.id, text);
        });

        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.className = "mini-button secondary";
        cancel.textContent = "取消";
        cancel.addEventListener("click", () => editor.remove());

        editor.append(input, save, cancel);
        card.append(editor);
    });

    const revoke = document.createElement("button");
    revoke.type = "button";
    revoke.className = "mini-button secondary";
    revoke.textContent = "撤销";
    revoke.addEventListener("click", () => revokePromise(commitment.id));

    actions.append(revise, revoke);
    card.append(body, actions);
    return card;
}

async function loadCommitments() {
    const container = el("promise-list");
    try {
        const commitments = await get("/commitments");
        container.replaceChildren();

        if (!commitments.length) {
            const empty = document.createElement("div");
            empty.className = "empty-state";
            empty.textContent =
                "他还没答应过什么。他答应你的时候，这里会记下来——包括他的回应和你当时的原话。";
            container.append(empty);
            return;
        }
        for (const commitment of commitments) container.append(promiseRow(commitment));
    } catch (error) {
        showToast(error.message, 3800);
    }
}

async function revisePromise(commitmentId, content) {
    try {
        await post(`/commitments/${commitmentId}/revise`, { content });
        showToast("记下了。旧版本还留着。", 3500);
        await loadCommitments();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

async function revokePromise(commitmentId) {
    try {
        await post(`/commitments/${commitmentId}/revoke`, {});
        await loadCommitments();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

function bindCommitments() {
    el("promise-new-toggle").addEventListener("click", () => {
        const form = el("promise-form");
        form.hidden = !form.hidden;
    });

    el("promise-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const content = el("promise-content").value.trim();
        if (!content) return;

        const sourceText = el("promise-source").value.trim();
        const quietStart = el("promise-quiet-start").value.trim();
        const quietEnd = el("promise-quiet-end").value.trim();

        // 只填开始时间也算数：表示"从那时起，当天剩下的时间别找我"
        let structured = null;
        if (quietStart) {
            structured = { quiet_hours: { start: quietStart, end: quietEnd || null } };
        }

        try {
            await post("/commitments", {
                content,
                source_text: sourceText || null,
                structured,
            });
            el("promise-form").hidden = true;
            el("promise-content").value = "";
            el("promise-source").value = "";
            el("promise-quiet-start").value = "";
            el("promise-quiet-end").value = "";
            showToast("承诺已记下。", 3500);
            await loadCommitments();
        } catch (error) {
            showToast(error.message, 5000);
        }
    });
}
