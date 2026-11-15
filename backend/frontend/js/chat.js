import { get, patch, post, remove } from "./api.js";
import { loadContextPreview } from "./context.js";
import { setBusy, showDialog, showToast } from "./utils.js";

const el = (id) => document.getElementById(id);

// ---------- 「这一条会去哪」常驻状态（设计裁决 Q3.3） ----------
//
// ⚠️ 隐私裁决要求它是**事前可见**的常驻一行，不是弹窗、不需要点击。
// 对私密内容来说最大的风险不是"用户选了云端"，而是"用户不知道现在是云端"。
async function renderPrivacyStatus() {
    const box = el("privacy-status");
    if (!box) return;
    box.textContent =
        "⚠️ AI 请求会发送到设置中配置的模型服务地址；若使用第三方云服务，聊天、记忆与图片内容会离开本机。";
    box.className = "privacy-status cloud";
}

function messageImage(message) {
    const image = message.metadata?.image;
    if (!image) return null;
    // 本地刚发出去的那条还没落盘，只有 data URL；落盘之后是文件名
    return image.path ? `/images/${image.path}` : image.dataUrl || null;
}

function appendMessage(message) {
    const box = document.createElement("div");
    box.className = `message ${message.role}`;

    const src = messageImage(message);
    if (src) {
        const picture = document.createElement("img");
        picture.className = "message-image";
        picture.src = src;
        picture.alt = "你发的图片";
        box.append(picture);
    }

    const text = document.createElement("div");
    text.className = "message-text";
    text.textContent = message.content;
    box.append(text);

    const actions = messageActions(message);
    if (actions) box.append(actions);

    el("chat-messages").appendChild(box);
    el("chat-messages").scrollTop = el("chat-messages").scrollHeight;
    return box;
}

// ---------- 单条消息的操作（删除 / 编辑 / 重新生成 / 回滚） ----------

function action(label, handler, hint) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "message-action";
    button.textContent = label;
    if (hint) button.title = hint;
    button.addEventListener("click", handler);
    return button;
}

function messageActions(message) {
    // 还没落库的临时气泡（你刚发出去那一刻）没有 id，也就没有操作
    if (!message.id) return null;

    const bar = document.createElement("div");
    bar.className = "message-actions";

    if (message.role === "assistant") {
        bar.append(
            action("重新生成", () => regenerateMessage(message.id), "重做这条回复（替换掉它）"),
            action("删除", () => confirmDestructive(message, "delete")),
            action("回滚到此处", () => confirmDestructive(message, "rollback"), "保留这条和它之前的一切，删掉之后的"),
        );
    } else {
        if (message.metadata?.generation_status === "failed") {
            // 生成失败时**根本没有 assistant 消息**可重做 ——
            // 重试只能挂在这条用户消息上。这条入口以前完全不存在。
            bar.append(action("重试", () => regenerateMessage(message.id)));
        }
        bar.append(
            action("编辑", () => editMessage(message)),
            action("删除", () => confirmDestructive(message, "delete")),
            action("回滚到此处", () => confirmDestructive(message, "rollback")),
        );
    }
    return bar;
}

async function confirmDestructive(message, mode) {
    const endpoint =
        mode === "delete"
            ? `/chat/messages/${message.id}/impact`
            : `/chat/rollback/${message.id}/impact`;

    let impact;
    try {
        impact = await get(endpoint);
    } catch (error) {
        showToast(error.message, 4000);
        return;
    }

    const affected = impact.affected_observations || [];

    // ⚠️ 这是整个功能最重要的地方：**必须说出来**。
    // 删聊天记录不会让他忘掉 —— 你要在删之前就知道这件事，
    // 而且三个选项要**一次摆清**（原生 confirm 问不出来）。
    let body = mode === "rollback" ? "它之后的消息都会被删掉。\n\n" : "";
    if (!affected.length) {
        body += "他还没有从这里面记下任何东西。";
    } else {
        body +=
            `他还记着这件事 —— 有 ${affected.length} 条事实来自这里：\n` +
            affected
                .slice(0, 4)
                .map((item) => `・${item.content}`)
                .join("\n") +
            (affected.length > 4 ? `\n…还有 ${affected.length - 4} 条` : "");
    }

    const options = [
        { value: "chat", label: "只删聊天记录", hint: "他仍然记得（含出处）" },
    ];
    if (affected.length) {
        options.push(
            { value: "memory", label: "连记忆一起删", hint: "事实删掉，档案原话保留" },
            { value: "all", label: "连档案也抹掉", hint: "彻底没有痕迹", danger: true },
        );
    }

    const { value } = await showDialog({
        title: mode === "delete" ? "删掉这条消息？" : "回滚到这条消息？",
        body,
        options,
    });
    if (!value) return;

    await runDestructive(message.id, mode, value !== "chat", value === "all");
}

async function runDestructive(messageId, mode, withMemory, purgeArchive) {
    try {
        const query = `with_memory=${withMemory}&purge_archive=${purgeArchive}`;
        const result =
            mode === "delete"
                ? await remove(`/chat/messages/${messageId}?${query}`)
                : await post(`/chat/rollback/${messageId}?${query}`, {});

        let text = `删掉 ${result.deleted_messages} 条消息`;
        if (result.deleted_observations) {
            text += `，连带 ${result.deleted_observations} 条记忆`;
        } else {
            text += "（记忆保留）";
        }
        if (result.purged_raw_entries) {
            text += `，抹掉 ${result.purged_raw_entries} 份档案原话`;
        }
        showToast(text, 4500);

        if (result.refused?.length) {
            // 不变量 6：那些事实是某条判断的最后一条依据，删不得 ——
            // 所以它们那份原话也留着（否则出处会指向一句不存在的话）
            showToast(
                `有 ${result.refused.length} 条记忆没删掉 —— 它们是某条判断的最后一条依据。`,
                6500,
            );
        }

        await loadChat();
        await loadContextPreview();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

async function editMessage(message) {
    // 先问影响范围：他记住的是**旧版本**，这件事必须让你在改之前看到。
    let affected = [];
    try {
        affected = (await get(`/chat/messages/${message.id}/impact`)).affected_observations || [];
    } catch (_) {
        // 拿不到影响范围就不打扰你，按"只改聊天记录"处理
    }

    const options = [
        { value: "chat", label: "只改聊天记录", hint: "他继续按旧版本记" },
    ];
    let body = "改的是聊天记录。";

    if (affected.length) {
        body =
            `他记住的是旧版本 —— 有 ${affected.length} 条事实来自这句话：\n` +
            affected
                .slice(0, 4)
                .map((item) => `・${item.content}`)
                .join("\n") +
            (affected.length > 4 ? `\n…还有 ${affected.length - 4} 条` : "");
        options.push(
            {
                value: "memory",
                label: "让他按新版本重记",
                hint: "旧事实删掉，几分钟后重新抽取",
            },
            {
                value: "all",
                label: "并抹掉旧档案原话",
                hint: "彻底没有痕迹",
                danger: true,
            },
        );
    }

    const { value, text } = await showDialog({
        title: "改这句话",
        body,
        input: { value: message.content },
        options,
    });
    if (!value) return;

    const content = (text || "").trim();
    if (!content || content === message.content) return;

    try {
        const result = await patch(
            `/chat/messages/${message.id}` +
                `?with_memory=${value !== "chat"}&purge_archive=${value === "all"}`,
            { content },
        );
        showToast(result.note, 7000);
        await loadChat();
        await loadContextPreview();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

async function regenerateMessage(messageId) {
    try {
        await post(`/chat/messages/${messageId}/regenerate`, {});
        await loadChat();
        await loadContextPreview();
    } catch (error) {
        showToast(error.message, 5000);
    }
}

function createBubble() {
    const box = document.createElement("div");
    box.className = "message assistant";
    el("chat-messages").appendChild(box);
    return box;
}

function pushDelta(bubble, text) {
    bubble.textContent += text;
    el("chat-messages").scrollTop = el("chat-messages").scrollHeight;
}

// 逐字消费 SSE。EventSource 不支持 POST，所以用 fetch + ReadableStream。
// ---------- 发图 ----------
//
// 图片先在浏览器里缩到长边 1024 再转 data URL。
// 不缩的话手机照片动辄 3–5 MB，base64 之后更大 —— 又慢又贵。
// 模型那边再用 detail: "low" 压一次（后端发请求时带上的）。
const IMAGE_MAX_EDGE = 1024;

let pendingImage = null;
// 输入框自动长高的函数（发完之后要收回去）
let growComposer = null;

function bindComposer() {
    const form = el("chat-form");
    const input = el("chat-image-input");

    // ⚠️ **事件委托**，不逐个 addEventListener。
    // 上一版"去掉这张图"点不动，就是因为按钮的位置和结构一起被我搞乱了 ✗。
    // 委托之后所有按钮的点击只有这一个入口，结构改了也不会静默失效。
    form.addEventListener("click", (event) => {
        const button = event.target.closest("button");
        if (!button) return;

        if (button.id === "chat-image-pick") {
            input.click();
        } else if (button.id === "chat-image-clear") {
            pendingImage = null;
            renderImagePreview();
        }
    });

    input.addEventListener("change", async () => {
        const file = input.files?.[0];
        input.value = ""; // 同一张图连选两次也要能触发
        if (!file) return;

        try {
            pendingImage = await downscaleToDataUrl(file);
            renderImagePreview();
        } catch (error) {
            showToast(error.message, 4000);
        }
    });

    // 输入框自动长高（微信那样），最多 160px
    const textarea = el("chat-input");
    growComposer = () => {
        textarea.style.height = "auto";
        textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
    };
    textarea.addEventListener("input", growComposer);
    growComposer();
}

function downscaleToDataUrl(file) {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const image = new Image();

        image.onload = () => {
            URL.revokeObjectURL(url);

            const scale = Math.min(
                1,
                IMAGE_MAX_EDGE / Math.max(image.width, image.height),
            );
            const canvas = document.createElement("canvas");
            canvas.width = Math.max(1, Math.round(image.width * scale));
            canvas.height = Math.max(1, Math.round(image.height * scale));
            canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);

            // PNG 保留透明（截图常见），其余一律转 JPEG 压小
            const mime = file.type === "image/png" ? "image/png" : "image/jpeg";
            resolve(canvas.toDataURL(mime, 0.85));
        };
        image.onerror = () => {
            URL.revokeObjectURL(url);
            reject(new Error("这张图读不出来。"));
        };
        image.src = url;
    });
}

function renderImagePreview() {
    const box = el("chat-image-preview");
    box.hidden = !pendingImage;
    if (!pendingImage) return;

    el("chat-image-thumb").src = pendingImage;
    // 用户定下的原则：本该只在本机的东西要离开本机时，必须告诉他
    el("chat-image-note").textContent =
        "这张图会发到云端 —— 那是他唯一能看见它的方式。";
}

// （旧的 bindImagePicker 已并入 bindComposer：改成事件委托，
//   否则某个按钮点不动时会静默失效 —— 上一版就是这么坏的。）

async function streamReply(content, image, onDelta) {
    const response = await fetch("/api/v1/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, image }),
    });

    if (!response.ok) {
        let detail = `请求失败（${response.status}）`;
        try {
            const body = await response.json();
            if (typeof body.detail === "string") detail = body.detail;
        } catch (_) {
            // 保留默认文案
        }
        throw new Error(detail);
    }
    if (!response.body) throw new Error("这个浏览器不支持流式响应。");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalText = "";
    let failure = null;

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            boundary = buffer.indexOf("\n\n");

            const line = frame.split("\n").find((part) => part.startsWith("data:"));
            if (!line) continue;

            let payload;
            try {
                payload = JSON.parse(line.slice(5).trim());
            } catch (_) {
                continue;
            }

            if (payload.type === "delta") {
                finalText += payload.text;
                onDelta(payload.text);
            } else if (payload.type === "done") {
                finalText = payload.content ?? finalText;
            } else if (payload.type === "error") {
                failure = payload.detail || "生成失败";
            }
        }
    }

    if (failure) throw new Error(failure);
    return finalText;
}

export async function loadChat() {
    const messages = await get("/chat/messages?limit=120");
    el("chat-messages").replaceChildren();

    if (!messages.length) {
        const placeholder = document.createElement("div");
        placeholder.className = "empty-state";
        placeholder.textContent = "这里还没有聊天。你可以直接问他今天怎么样。";
        el("chat-messages").appendChild(placeholder);
    } else {
        for (const message of messages) appendMessage(message);
    }

    await showPendingProactive();
}

// 机制二：他写下的东西**不推送**，等你回来（打开聊天）才给你看
async function showPendingProactive() {
    try {
        const pending = await get("/proactive/pending");
        if (!pending.length) return;

        const placeholder = el("chat-messages").querySelector(".empty-state");
        if (placeholder) placeholder.remove();

        for (const contact of pending) {
            appendMessage({ role: "assistant", content: contact.content });
            await post(`/proactive/contacts/${contact.id}/deliver`, {});
        }
    } catch (_) {
        // 主动联系出问题不该挡住聊天
    }
}

async function send(text) {
    const content = text.trim();
    const image = pendingImage;

    if (!content && !image) {
        showToast("说点什么，或者发张图。", 2200);
        return;
    }

    pendingImage = null;
    renderImagePreview();

    const sendButton = el("chat-send");
    setBusy(sendButton, true, "想一下…");
    const input = el("chat-input");
    input.value = "";

    const placeholder = el("chat-messages").querySelector(".empty-state");
    if (placeholder) placeholder.remove();

    // 你这条立刻显示，不等服务器
    appendMessage({
        role: "user",
        content,
        metadata: image ? { image: { dataUrl: image } } : null,
    });

    let bubble = null;
    try {
        await streamReply(content, image, (delta) => {
            if (!bubble) bubble = createBubble();
            pushDelta(bubble, delta);
        });
        if (!bubble) {
            bubble = createBubble();
            bubble.textContent = "（没有回复）";
        }
        // 重新读一遍：刚发的这两条这时才有 id，才挂得上
        // 「删除 / 重新生成 / 回滚到此处」
        await loadChat();
    } catch (error) {
        showToast(error.message, 5000);
        // 出错时以服务端落库的为准，别让界面和数据库不一致
        await loadChat();
    } finally {
        setBusy(sendButton, false);
        input.focus();
        if (growComposer) growComposer(); // 发完把输入框收回一行
        // 缓存命中要等一次真实请求之后才有数
        loadContextPreview();
    }
}

export function initChat() {
    bindComposer();
    renderPrivacyStatus();

    el("chat-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        await send(el("chat-input").value);
    });
    el("chat-input").addEventListener("keydown", async (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            await send(el("chat-input").value);
        }
    });
    el("quick-today-question").addEventListener("click", () => send("我今天怎么样？"));
}
