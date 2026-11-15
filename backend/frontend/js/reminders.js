import { get } from "./api.js";

const el = (id) => document.getElementById(id);

/**
 * 站内提醒 ✓（2026-10-05 第三轮 ✓）。
 *
 * ⚠️ **不催命** ✗ —— 每条都带一个**动作** ✓，最多 5 条 ✓（后端已经砍好 ✓）。
 * ⚠️ **不弹浏览器通知** ✗ —— "关闭浏览器之后的通知"是**另一个交付能力** ✓
 * （用户明确划的边界），这里只做**打开页面时**看得见的那一条 ✓。
 * ⚠️ 拉不到就**安静地不显示** ✗ —— 提醒只是提醒，不该自己变成一个错误 ✓。
 */

async function refresh() {
    const box = el("reminder-strip");
    if (!box) return;
    box.replaceChildren();
    try {
        const items = await get("/reminders");
        if (!items.length) {
            box.hidden = true;
            return;
        }
        for (const item of items) {
            const row = document.createElement("div");
            row.className = `reminder-row ${item.urgency}`;

            const title = document.createElement("span");
            title.className = "reminder-title";
            title.textContent = item.title;

            const action = document.createElement("span");
            action.className = "reminder-action";
            action.textContent = item.action;

            row.append(title, action);
            box.append(row);
        }
        box.hidden = false;
    } catch (error) {
        box.hidden = true;
    }
}

export function initReminders() {
    if (!el("reminder-strip")) return;
    refresh();
}

export const refreshReminders = refresh;
