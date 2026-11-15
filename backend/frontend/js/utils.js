export function localDisplay(iso, withDate = true) {
    if (!iso) return "";
    const text = iso.slice(0, 16).replace("T", " ");
    return withDate ? text : text.slice(11);
}

export function toDatetimeLocalValue(date = new Date()) {
    const pad = (v) => String(v).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function todayHuman() {
    return new Intl.DateTimeFormat("zh-CN", {
        month: "long",
        day: "numeric",
        weekday: "long",
    }).format(new Date());
}

export function escapeText(value) {
    return String(value ?? "");
}

export function showToast(message, duration = 2400) {
    const toast = document.getElementById("toast");
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => {
        toast.hidden = true;
    }, duration);
}

export function setBusy(button, busy, busyText = "处理中…") {
    if (!button) return;
    if (busy) {
        button.dataset.originalText = button.textContent;
        button.textContent = busyText;
        button.disabled = true;
    } else {
        button.textContent = button.dataset.originalText || button.textContent;
        button.disabled = false;
    }
}

// ---------- 自绘弹层 ----------
//
// ⚠️ 刻意不用 `window.confirm` / `window.prompt`：
// 破坏性操作要**一次摆清所有选项**（只删聊天 / 连记忆一起删 / 连档案也抹掉），
// 而原生对话框只能问是非；而且它的样式和界面其余部分完全对不上。
//
// 约定：**总是** resolve 成 `{ value, text }`。
// `value` = 你点的那一项（点取消或空白处是 null），`text` = 输入框的值（没输入框时是 ""）。

// ⚠️⚠️ 它必须是 **`<dialog>` + `showModal()`** ✗ —— 不能是一个 `position: fixed` 的 div ✓。
//
// ## 为什么（2026-10-06 用户实测抓到）
//
// 用户原话："展开详情页后，如果删除某个日程 / ddl / 习惯，
// 删除界面被压在详情页下面，需要关掉详情页才能点掉删除。"
//
// 病因：`showModal()` 打开的 `<dialog>` 会被放进浏览器的 **top layer** ✓，
// 那是**凌驾于所有 z-index 之上**的一层 ✗ ——
// 所以这个确认层无论 `z-index: 9999` 都压不过它 ✓（改成 div 就是白改 ✓）。
//
// 修法：确认层自己也 `showModal()` ✓ —— 两个 modal 是**后开的在上面** ✓，
// 于是"在详情页里再点删除"就有了第二个 top-layer 元素 ✓。
//
// ⚠️ 顺带修掉一个从来没被发现的问题：Esc 关掉它时，等它的那行代码会**永远挂住** ✗
//（promise 永远不 settle —— 不报错、没反应 ✓）。所以下面必须监听 `close` ✓。

let dialogLayer = null;
let resolveDialog = null;
/** 每次 `showDialog()` 换一个新代号 ✓ —— 用来判断"这个 close 事件是谁的" ✓。 */
let dialogToken = null;

function finishDialog(value, text) {
    const resolve = resolveDialog;
    resolveDialog = null;
    // ⚠️ 先 resolve、再 close ✓ —— 反过来的话，`close` 事件会看到
    // `resolveDialog` 还在，于是又进来一次 ✓（靠这个顺序保证不重入 ✓）。
    if (resolve) resolve({ value, text });
    if (dialogLayer) {
        if (dialogLayer.open) dialogLayer.close();
        dialogLayer.replaceChildren();
    }
}

export function showDialog({ title, body = "", input = null, options = [] }) {
    // ⚠️ **每次调用一个代号** ✓（`close` 里靠它判断"这个事件是不是我自己发的" ✓）
    const token = Symbol("dialog");
    dialogToken = token;

    if (!dialogLayer) {
        dialogLayer = document.createElement("dialog");
        dialogLayer.className = "dialog-layer";
        // 点空白处 = 取消 ✓（modal dialog 的 `::backdrop` 被点时，
        // 事件目标就是 dialog 元素本身 ✓）。
        dialogLayer.addEventListener("click", (event) => {
            if (event.target === dialogLayer) finishDialog(null, "");
        });
        document.body.append(dialogLayer);
    }

    // ⚠️⚠️ 这个监听**每次调用都要重新挂** ✗ —— 放在上面那个 `if` 里的话，
    // 它只会拿到**第一次**的 token ✓ → 判断永远错 ✓ → 第二个框照样被结算掉 ✗。
    //
    // 为什么需要代号（`close` 事件是**异步**派发的 ✓）：
    //   ① 第一个框 resolve → 你的代码接着跑
    //   ② 第二个框弹出来 → `resolveDialog` 变成**第二个**的了
    //   ③ 第一个框的 close 事件**这才到** → 它看到 `resolveDialog` 有值
    //      → 把**第二个框**用 `null` 结算掉 ✗ → 第二个框一闪就没了
    //（2026-10-05 用户实测："点「放日程」只弹了一个框，后面什么都没发生" ✓）
    //
    // `once` ✓：每个框用完就自动摘，不会越挂越多 ✓。
    dialogLayer.addEventListener(
        "close",
        () => {
            if (resolveDialog && dialogToken === token) finishDialog(null, "");
        },
        { once: true },
    );

    dialogLayer.replaceChildren();

    const card = document.createElement("div");
    card.className = "dialog-card";

    const heading = document.createElement("h3");
    heading.textContent = title;
    card.append(heading);

    if (body) {
        const text = document.createElement("div");
        text.className = "dialog-body";
        text.textContent = body;
        card.append(text);
    }

    let field = null;
    if (input) {
        field = document.createElement("textarea");
        field.className = "dialog-input";
        field.rows = 3;
        field.value = input.value ?? "";
        card.append(field);
    }

    const actions = document.createElement("div");
    actions.className = "dialog-actions";

    for (const option of options) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `dialog-button${option.danger ? " danger" : ""}`;
        button.textContent = option.label;
        if (option.hint) button.title = option.hint;
        button.addEventListener("click", () =>
            finishDialog(option.value, field ? field.value : ""),
        );
        actions.append(button);
    }

    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "dialog-button ghost";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => finishDialog(null, ""));
    actions.append(cancel);

    card.append(actions);
    dialogLayer.append(card);
    if (!dialogLayer.open) dialogLayer.showModal();
    if (field) field.focus();

    return new Promise((resolve) => {
        resolveDialog = resolve;
    });
}
