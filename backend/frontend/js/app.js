import { get } from "./api.js";
import { initChat, loadChat } from "./chat.js";
import { initContextPanel, loadContextPreview } from "./context.js";
import { initHer, loadHer } from "./her.js";
import { initSettings, loadSettings } from "./settings.js";
import { initToday, refreshToday } from "./today.js";
import { initActionCenter } from "./action-center.js";
import { initReminders } from "./reminders.js";
import { applySavedTheme } from "./theme.js";
import { initWeekly, loadWeekly } from "./weekly.js";
import { showToast, todayHuman } from "./utils.js";

const el = (id) => document.getElementById(id);
let currentView = "today";

// 外观偏好是**这台机器**的事（localStorage），所以启动时就应用 ✓。
// 早一点执行能少一次颜色闪动 ✓。
applySavedTheme();

// 标签名要能自己说明这一页干什么 —— "TA"/"生活"这种猜不出内容的词都换掉了。
// 用 TA 而不是"他/她"：人格由使用者自己配，性别不能写死在代码里（设计裁决 R3）。
const VIEW_TITLES = {
    today: "今天",
    weekly: "回顾",
    chat: "聊天",
    her: "关于TA",
    settings: "设置",
};

const VIEWS = ["today", "weekly", "chat", "her", "settings"];

function switchView(view) {
    currentView = view;
    document.querySelectorAll(".tab").forEach((button) => {
        button.classList.toggle("active", button.dataset.view === view);
    });
    for (const name of VIEWS) {
        el(`${name}-view`).classList.toggle("active", view === name);
    }
    el("page-title").textContent = VIEW_TITLES[view];
    el("fab").hidden = view !== "today";
    if (view === "weekly") loadWeekly().catch((error) => showToast(error.message, 3800));
    if (view === "her") loadHer();
    if (view === "settings") {
        loadSettings().catch((error) => showToast(error.message, 3800));
    }
    if (view === "chat") {
        loadChat().catch((error) => showToast(error.message, 3800));
        loadContextPreview();
    }
}

async function loadHealth() {
    try {
        const health = await get("/health");
        const badge = el("ai-status");
        badge.textContent = health.ai_configured ? `${health.model} 已连接` : "AI 未配置";
        badge.classList.toggle("ready", health.ai_configured);
        badge.title = `时区：${health.timezone}`;
    } catch (error) {
        el("ai-status").textContent = "服务异常";
    }
}

function bindGlobal() {
    document.querySelectorAll(".tab").forEach((button) => {
        button.addEventListener("click", () => switchView(button.dataset.view));
    });
    el("refresh-button").addEventListener("click", async () => {
        try {
            if (currentView === "today") {
                await refreshToday();
            } else if (currentView === "weekly") {
                await loadWeekly();
            } else if (currentView === "her") {
                await loadHer();
            } else {
                await loadChat();
                await loadContextPreview();
            }
            showToast("已刷新");
        } catch (_) {}
    });
    const fab = el("fab");
    fab.addEventListener("click", () => {
        el("fab-menu").hidden = !el("fab-menu").hidden;
    });
    document.addEventListener("click", (event) => {
        if (!el("fab-menu").contains(event.target) && event.target !== fab) {
            el("fab-menu").hidden = true;
        }
    });
}

async function boot() {
    el("today-label").textContent = todayHuman();
    bindGlobal();
    initToday();
    initActionCenter();
    initReminders();
    initWeekly();
    initChat();
    initContextPanel();
    initHer();
    initSettings();
    await loadHealth();
    await refreshToday();
}

boot().catch((error) => showToast(error.message, 5000));
