import { get, post, put } from "./api.js";
import { THEMES, applyTheme, currentTheme } from "./theme.js";
import { showToast } from "./utils.js";

const el = (id) => document.getElementById(id);

// 每个任务在界面上说什么 —— 用他的话，不用术语
const TASK_LABELS = {
    chat: "聊天回复",
    extract: "记忆抽取",
    impression: "印象归纳",
    review: "每日复盘",
    vision: "图片识别",
    // R15：它曾经走 `chat` —— 独立出来是为了以后改聊天模型时不会静默带上它 ✓
    deadline_breakdown: "DDL 拆解",
};

const MODEL_NOTES = {
    "deepseek-flash": "deepseek-flash（推荐：支持图片，缓存命中后近乎免费）",
    "deepseek-v4-pro": "deepseek-v4-pro（不需要：不支持图片，贵一个数量级）",
};

function renderStatus(status) {
    el("settings-key-state").textContent = status.api_key_configured
        ? `已配置（${status.api_key_hint}）`
        : "还没配置 —— 他暂时无法说话";
    el("settings-key-state").classList.toggle("ready", status.api_key_configured);

    const select = el("settings-model");
    const models = [...new Set([...status.recommended_models, status.model])];
    select.replaceChildren();
    for (const model of models) {
        select.appendChild(new Option(MODEL_NOTES[model] || model, model));
    }
    select.value = status.model;

    const routing = el("settings-routing");
    routing.replaceChildren();
    for (const [task, provider] of Object.entries(status.providers)) {
        const row = document.createElement("div");
        row.className = "settings-row";
        const name = document.createElement("span");
        name.textContent = TASK_LABELS[task] || task;
        const value = document.createElement("strong");
        value.textContent = provider === "legacy_removed"
            ? "需修复旧配置"
            : "OpenAI 兼容服务";
        row.append(name, value);
        routing.appendChild(row);
    }

    el("settings-env-path").textContent = status.env_path;
    renderConfigurationWarnings(status);
}

function renderConfigurationWarnings(status) {
    const box = el("settings-config-warning");
    if (!box) return;
    const warnings = status.configuration_warnings || [];
    box.replaceChildren();
    box.hidden = warnings.length === 0;
    for (const warning of warnings) {
        const line = document.createElement("p");
        line.textContent = warning;
        box.append(line);
    }
    if (warnings.length) {
        const hint = document.createElement("p");
        hint.textContent = "请从 backend/.env 删除 LLM_LOCAL_ONLY、OLLAMA_* 和值为 ollama 的 LLM_*_PROVIDER 旧配置；再为 roleplay、analysis、vision 三档保存当前 OpenAI 兼容服务配置。";
        box.append(hint);
    }
}

// ---------- 三档模型（陪聊 / 硬核 / 看图） ----------
//
// 用户要求：把任务按"需要什么能力"分开，自己在界面上填模型。
//   · 陪聊 → 要人格一致、语气像人
//   · 硬核 → 要听话、严格 JSON、不编造（不变量 5 靠它）
//   · 看图 → 要多模态
//
// ⚠️ 字段**留空就沿用左边那块「默认配置」** —— 所以不动它也完全正确 ✓。
// 档里的配置改完不用重启；每档都使用 OpenAI 兼容接口。

const TIER_META = [
    ["roleplay", "陪聊", "聊天 · 主动联系 · 每日回顾", "要人格一致、语气像人"],
    ["analysis", "硬核", "记忆抽取 · 印象归纳", "要听话、严格 JSON、不编造"],
    ["vision", "看图", "图片识别", "要多模态（能读图）"],
];

function tierRow(tier, title, tasks, need, config) {
    const row = document.createElement("div");
    row.className = "tier-row";

    const head = document.createElement("div");
    head.className = "tier-head";
    const name = document.createElement("strong");
    name.textContent = title;
    const detail = document.createElement("span");
    detail.className = "muted";
    detail.textContent = `${tasks} —— ${need}`;
    const badge = document.createElement("span");
    badge.className = `tier-badge${config.customized ? " custom" : ""}`;
    badge.textContent = config.customized ? "已自定义" : "沿用默认配置";
    head.append(name, badge, detail);
    row.append(head);

    const fields = {};

    const provider = document.createElement("span");
    provider.className = "tier-provider-label muted";
    provider.textContent = config.provider === "ollama"
        ? "旧配置：Ollama 已移除（保存此档可修复）"
        : "OpenAI 兼容服务";
    fields.provider = provider;

    for (const [field, placeholder] of [
        ["base_url", "接口地址（留空 = 沿用默认）"],
        ["api_key", "API Key"],
        ["model", "模型名"],
    ]) {
        const input = document.createElement("input");
        input.type = field === "api_key" ? "password" : "text";
        input.value = field === "api_key" ? "" : config[field] || "";
        input.placeholder =
            field === "api_key" && config.api_key_configured
                ? `已配置 ${config.api_key_hint}（留空 = 不改）`
                : placeholder;
        fields[field] = input;
    }

    const line = document.createElement("div");
    line.className = "tier-fields";
    line.append(fields.provider, fields.base_url, fields.api_key, fields.model);
    row.append(line);

    const actions = document.createElement("div");
    actions.className = "settings-actions";

    const save = document.createElement("button");
    save.type = "button";
    save.className = "ghost-button";
    save.textContent = "保存这一档";
    save.addEventListener("click", async () => {
        const payload = {
            provider: "deepseek",
            base_url: fields.base_url.value.trim(),
            model: fields.model.value.trim(),
        };
        // ⚠️ Key 空着**不传** —— 传空串会把它清掉 ✗（用户可能只想改模型）。
        const key = fields.api_key.value.trim();
        if (key) payload.api_key = key;

        try {
            await post("/settings", { tiers: { [tier]: payload } });
            showToast(`${title}档已保存。下一条消息就生效（不用重启）。`, 5000);
            await loadSettings();
        } catch (error) {
            showToast(error.message, 7000);
        }
    });

    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "ghost-button";
    reset.textContent = "恢复默认";
    reset.title = "清空这一档，回到沿用默认配置";
    reset.addEventListener("click", async () => {
        try {
            await post("/settings", {
                tiers: {
                    [tier]: { provider: "", base_url: "", api_key: "", model: "" },
                },
            });
            showToast(`${title}档已恢复默认。`, 5000);
            await loadSettings();
        } catch (error) {
            showToast(error.message, 7000);
        }
    });

    actions.append(save, reset);
    row.append(actions);
    return row;
}

function renderTiers(status) {
    const box = el("settings-tiers");
    if (!box) return;
    box.replaceChildren();

    const note = document.createElement("p");
    note.className = "muted";
    note.textContent =
        "按任务需要的能力分三档。留空 = 沿用左边那块「默认配置」；" +
        "想让某一档换别家，就把地址和 Key 填上。";
    box.append(note);

    const tiers = status.tiers || {};
    for (const [tier, title, tasks, need] of TIER_META) {
        box.append(tierRow(tier, title, tasks, need, tiers[tier] || {}));
    }
}

// ---------- 编辑人格 ----------
//
// **人格文件归你，通用规则归设计。** 所以这里只编辑人格。
//
// 它按文件修改时间缓存 → 保存之后**下一条消息就生效，不用重启** ✓。
// 保存前 `save_persona_text` 会留一份 `persona.md.bak` ✓（原设计不许
// 应用内编辑，理由正是"怕误覆盖"—— 备份满足那个目的之后才敢开这个口子）。

async function renderPersona() {
    const box = el("settings-persona");
    if (!box) return;
    box.replaceChildren();

    let state;
    try {
        state = await get("/persona");
    } catch (error) {
        showToast(error.message, 4000);
        return;
    }

    const label = document.createElement("p");
    label.className = "muted";
    label.textContent =
        `正在编辑「${state.active_display_name}」的设定 —— ` +
        "保存后下一条消息就生效（不用重启），旧版留一份 .bak。";
    box.append(label);

    const area = document.createElement("textarea");
    area.id = "settings-persona-text";
    area.value = state.content;
    area.spellcheck = false;
    box.append(area);

    const row = document.createElement("div");
    row.className = "settings-actions";

    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary-button";
    save.textContent = "保存人格";
    save.addEventListener("click", async () => {
        if (area.value.trim() === state.content.trim()) {
            showToast("没有改动。", 3000);
            return;
        }
        try {
            await put("/persona/content", { content: area.value });
            showToast("已保存。下一条消息就用新的人格。", 5000);
            await renderPersona();
        } catch (error) {
            showToast(error.message, 7000);
        }
    });

    const discard = document.createElement("button");
    discard.type = "button";
    discard.className = "ghost-button";
    discard.textContent = "放弃改动";
    discard.addEventListener("click", () => renderPersona());

    row.append(save, discard);
    box.append(row);
}

// ---------- 外观 ----------
//
// 两个主题，切换即时生效 ✓（只改观感，不动任何功能 ✓）。
// 复用通用卡片组件显示主题选项。

function renderTheme() {
    const box = el("settings-theme");
    if (!box) return;
    box.replaceChildren();

    const active = currentTheme();
    const row = document.createElement("div");
    row.className = "mode-row";

    for (const [name, title, hint] of THEMES) {
        row.append(
            modeCard({
                name: title,
                hint,
                active: name === active,
                onPick: () => {
                    applyTheme(name);
                    renderTheme();
                },
            })
        );
    }
    box.append(row);
}

// ---------- 访问密码 ----------
//
// ⚠️ 它保护的是**全部记忆**（包括"下载完整备份"那个按钮 ✓），所以：
//   · 保存时**后端会顺手把当前这台浏览器登录上** ✓（否则页面当场被自己拦成 401 ✗）
//   · **脚本 / API 客户端** 用 `Authorization: Bearer <密码>`。
//   · 改密码 → 所有旧 Cookie 立刻失效 ✓（token 是无状态的 ✓）

async function submitPassword(value) {
    try {
        await post("/settings", { app_password: value });
        showToast(
            value ? "已启用访问密码，这台浏览器也登录好了。" : "已清除访问密码。",
            5000,
        );
        await loadSettings();
    } catch (error) {
        showToast(error.message, 7000);
    }
}

function renderPassword(status) {
    const box = el("settings-password");
    if (!box) return;
    box.replaceChildren();

    const state = document.createElement("p");
    state.className = "muted";
    state.textContent = status.password_enabled
        ? "✅ 已启用 —— 本机和手机打开都要先输密码，别的人即使在同一网络也进不来。"
        : "⚠️ 未设置 —— 任何能连到这台电脑的人都能看你的记忆，也能下载完整备份。";
    box.append(state);

    const row = document.createElement("div");
    row.className = "mode-picker";

    const input = document.createElement("input");
    input.type = "password";
    input.id = "settings-password-input";
    input.autocomplete = "new-password";
    input.placeholder = status.password_enabled
        ? "输入新密码（至少 6 位）"
        : "至少 6 位";
    row.append(input);

    const save = document.createElement("button");
    save.type = "button";
    save.className = "ghost-button";
    save.textContent = status.password_enabled ? "改密码" : "设密码";
    save.addEventListener("click", () => submitPassword(input.value));
    row.append(save);

    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "ghost-button";
    clear.textContent = "清除";
    clear.disabled = !status.password_enabled;
    clear.addEventListener("click", () => submitPassword(""));
    row.append(clear);

    box.append(row);

    const hint = document.createElement("p");
    hint.className = "muted";
    hint.textContent =
        "保存后立刻生效，不用重启。⚠️ 走的是明文 HTTP —— " +
        "别在校园网这类共享网络上开；手机用的话连自己的热点。";
    box.append(hint);
}

async function renderExtraction() {
    const pending = await get("/memory/extract/pending");
    el("extract-pending").textContent = pending.pending_messages;
}

export async function loadSettings() {
    try {
        const status = await get("/settings");
        renderStatus(status);
        renderTiers(status);
        renderPassword(status);
        renderTheme();
        await renderPersona();
        await renderExtraction();

    } catch (error) {
        showToast(error.message, 3800);
    }
}

function bindSettings() {
    el("settings-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const apiKey = el("settings-api-key").value.trim();
        const model = el("settings-model").value;

        try {
            const status = await post("/settings", {
                api_key: apiKey || null,
                model: model || null,
            });
            // 提交后立刻清空输入框：Key 不应该停留在 DOM 里
            el("settings-api-key").value = "";
            renderStatus(status);
            showToast("已写入 .env，并立即生效（不用重启）", 4000);
        } catch (error) {
            showToast(error.message, 6000);
        }
    });

    el("settings-test").addEventListener("click", async () => {
        const box = el("settings-test-result");
        box.hidden = false;
        box.classList.remove("ready");
        box.textContent = "正在发一次最小请求…";

        try {
            const result = await post("/settings/test", {});
            if (result.ok) {
                box.textContent = `连通正常 · 模型 ${result.model} · 他回了一句「${result.reply}」`;
                box.classList.add("ready");
            } else {
                // 把原始报错贴出来 —— "模型不存在"这类信息只在这里看得到
                box.textContent = `失败：${result.detail}`;
            }
        } catch (error) {
            box.textContent = error.message;
        }
    });

    el("import-now").addEventListener("click", async () => {
        const box = el("import-text");
        if (!box.value.trim()) {
            showToast("先贴一份清单进来（每行一条）。", 3000);
            return;
        }
        try {
            const result = await post("/memory/import", { text: box.value });
            box.value = "";
            showToast(
                `已导入 ${result.imported_lines} 条 —— 点「现在抽取」它就会变成事实。`,
                7000,
            );
            await loadSettings();
        } catch (error) {
            showToast(error.message, 7000);
        }
    });

    el("extract-now").addEventListener("click", async () => {
        const box = el("extract-result");
        box.hidden = false;
        box.textContent = "正在抽取…";

        try {
            const result = await post("/memory/extract", { limit: 10 });
            let text =
                `检查 ${result.checked_messages} 条 · 新增 ${result.created_observations} 条观察` +
                ` · 因核不到出处丢掉 ${result.dropped_without_source} 条`;
            if (result.model) text += ` · 使用模型 ${result.model}`;

            if (result.suggested_tags > 0) {
                text +=
                    `\n\nTA 建议了 ${result.suggested_tags} 个新标签。` +
                    "匹不上现有清单时 TA **不硬塞**，只提建议 —— 去「关于TA」页批准或拒绝。";
            }

            if (result.checked_messages > 0 && result.created_observations === 0) {
                // "0 条"太容易被当成"坏了"。说清楚它为什么是 0。
                text +=
                    "\n\n没有值得记的事实 —— 问句、寒暄、以及「你喜欢我吗」这类对你的提问" +
                    "都不会变成记忆。这是故意的：宁缺毋滥，编一条比少记一条糟得多。\n" +
                    "想看到它工作，说一件具体的事（例如「今天背了两小时单词」）再抽一次。";
            }
            box.textContent = text;
            await renderExtraction();
        } catch (error) {
            box.textContent = error.message;
        }
    });

    el("impression-now").addEventListener("click", async () => {
        const box = el("impression-result");
        box.hidden = false;
        box.textContent = "正在归纳…";

        try {
            const result = await post("/memory/impression-generate", {});
            if (result.detail) {
                // 三条路都要看得见。尤其「补同方向证据」——
                // 它**故意不新建判断**（R6），不说明会让人以为没生效。
                box.textContent = `${result.detail}。`;
            } else if (result.generated > 0) {
                box.textContent = `归纳出 ${result.generated} 条判断。去「关于TA」页看它带着哪些依据。`;
            } else {
                // 说清**为什么**没做 —— 否则用户只会觉得"坏了"
                box.textContent = result.skipped_reason || "这次没有可归纳的内容。";
            }
        } catch (error) {
            box.textContent = error.message;
        }
    });
}

export function initSettings() {
    bindSettings();
}
