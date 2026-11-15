import { get, post } from "./api.js";
import { showToast } from "./utils.js";

const el = (id) => document.getElementById(id);

const STATE_LABELS = {
    exclude_once: "只这次不发",
    exclude_always: "已设为不发送",
};

// ---------- 人格（S3） ----------

function renderPersona(persona) {
    const select = el("persona-select");
    select.replaceChildren();
    for (const item of persona.available) {
        const option = document.createElement("option");
        option.value = item.name;
        option.textContent = item.display_name || item.name;
        option.selected = item.name === persona.active;
        select.appendChild(option);
    }
    const display = persona.active_display_name || persona.active;
    const title = el("chat-persona-title");
    if (title) title.textContent = display;
    // 导航里的标签也跟着人格走，否则换了人格它还是旧名字
    const tab = el("chat-tab");
    if (tab) tab.textContent = display;
}

export async function loadPersona() {
    try {
        renderPersona(await get("/persona"));
    } catch (error) {
        showToast(error.message, 3800);
    }
}

function bindPersona() {
    el("persona-select").addEventListener("change", async (event) => {
        try {
            renderPersona(await post("/persona/activate", { name: event.target.value }));
            showToast("换好了。");
            await loadContextPreview(); // 人格占的 token 变了
        } catch (error) {
            showToast(error.message, 3800);
            await loadPersona();
        }
    });

    el("persona-new-toggle").addEventListener("click", () => {
        const form = el("persona-form");
        form.hidden = !form.hidden;
    });

    el("persona-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = el("persona-name").value.trim();
        const content = el("persona-content").value.trim();
        const displayName = el("persona-display-name").value.trim();
        if (!name || !content) return;
        try {
            renderPersona(
                await post("/persona", {
                    name,
                    content,
                    display_name: displayName || null,
                    activate: true,
                }),
            );
            el("persona-form").hidden = true;
            el("persona-name").value = "";
            el("persona-display-name").value = "";
            el("persona-content").value = "";
            showToast("建好了。以后想改就直接编辑那个文件。", 4000);
            await loadContextPreview();
        } catch (error) {
            showToast(error.message, 5000);
        }
    });
}

// ---------- 这次会发送什么（S2） ----------

function blockCard(block) {
    const card = document.createElement("div");
    card.className = "context-block";
    if (block.excluded) card.classList.add("excluded");
    if (block.dropped) card.classList.add("dropped");

    const head = document.createElement("button");
    head.type = "button";
    head.className = "context-block-head";

    const left = document.createElement("div");
    const name = document.createElement("div");
    name.className = "context-block-name";
    name.textContent = block.label;

    const meta = document.createElement("div");
    meta.className = "context-block-meta";
    const bits = [];
    if (block.excluded) bits.push(STATE_LABELS[block.state] || "未发送");
    else if (block.dropped) bits.push("超预算，本次不发送");
    if (block.source) bits.push(block.source);
    meta.textContent = bits.join(" · ");
    left.append(name, meta);

    const tokens = document.createElement("div");
    tokens.className = "context-block-tokens";
    tokens.textContent = `${block.tokens} tok`;

    head.append(left, tokens);

    const body = document.createElement("pre");
    body.className = "context-block-body";
    body.textContent = block.content;
    body.hidden = true;

    head.addEventListener("click", () => {
        body.hidden = !body.hidden;
    });

    const actions = document.createElement("div");
    actions.className = "context-block-actions";

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "mini-button";
    toggle.textContent = block.excluded ? "恢复发送" : "永久不发";
    toggle.addEventListener("click", () => {
        setState(block.id, block.excluded ? "include" : "exclude_always");
    });

    const onceButton = document.createElement("button");
    onceButton.type = "button";
    onceButton.className = "mini-button secondary";
    onceButton.textContent = "只这次不发";
    onceButton.hidden = block.excluded;
    onceButton.addEventListener("click", () => setState(block.id, "exclude_once"));

    actions.append(toggle, onceButton);

    card.append(head, body, actions);
    return card;
}

function render(preview) {
    const container = el("context-blocks");
    container.replaceChildren();
    for (const block of preview.blocks) container.appendChild(blockCard(block));

    const total = el("context-total");
    total.textContent = `合计 ${preview.total_tokens} / 预算 ${preview.budget} tok`;
    total.classList.toggle("over", Boolean(preview.over_budget));

    const cache = el("context-cache");
    if (preview.cache_hit_tokens === null || preview.cache_hit_tokens === undefined) {
        cache.textContent = "缓存命中：说一句话之后可见";
    } else {
        cache.textContent = `本次缓存命中 ${preview.cache_hit_tokens} tok`;
    }

    const notice = el("context-notice");
    if (preview.boundary_notice) {
        notice.textContent = preview.boundary_notice;
        notice.hidden = false;
    } else {
        notice.hidden = true;
    }
}

export async function loadContextPreview() {
    try {
        render(await get("/context/preview"));
    } catch (error) {
        showToast(error.message, 3800);
    }
}

async function setState(blockId, state) {
    try {
        render(await post("/context/exclude", { block_id: blockId, state }));
    } catch (error) {
        showToast(error.message, 3800);
    }
}

export function initContextPanel() {
    bindPersona();
    loadPersona();
    loadContextPreview().catch(() => {});
}
