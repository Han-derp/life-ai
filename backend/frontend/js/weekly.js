import { renderExecutionSummary } from "./execution-summary.js";
import { get } from "./api.js";
import { showToast } from "./utils.js";

const el = (id) => document.getElementById(id);

let currentWeekStart = null; // ISO date (YYYY-MM-DD) of the Monday being viewed

function empty(container, text) {
    container.replaceChildren();
    const node = document.createElement("div");
    node.className = "empty-state";
    node.textContent = text;
    container.appendChild(node);
}

function badge(text, type = "") {
    const span = document.createElement("span");
    span.className = `badge ${type}`.trim();
    span.textContent = text;
    return span;
}

function formatWeekRange(stats) {
    const start = stats.week_start.slice(5).replace("-", "/");
    const end = stats.week_end.slice(5).replace("-", "/");
    return `${stats.week_start.slice(0, 4)} 年 ${start} – ${end}`;
}

function renderHero(stats) {
    el("weekly-range-label").textContent = formatWeekRange(stats);
    const isCurrentWeek = currentWeekStart === todayMonday();
    el("weekly-current-badge").hidden = !isCurrentWeek;
}

function renderScheduleStats(stats) {
    const s = stats.schedule;
    el("weekly-schedule-total").textContent = s.total;
    el("weekly-schedule-completed").textContent = s.completed;
    el("weekly-schedule-pending").textContent = s.pending;
    el("weekly-schedule-cancelled").textContent = s.cancelled;
}

function renderPomodoroStats(stats) {
    renderExecutionSummary(el("weekly-pomodoro-minutes").parentElement, stats.execution);
    el("weekly-pomodoro-count").textContent = stats.pomodoro.count;
    el("weekly-pomodoro-minutes").textContent = `${stats.pomodoro.minutes} min`;
}

function renderDeadlineStats(stats) {
    const d = stats.deadlines;
    el("weekly-deadline-completed").textContent = d.completed;
    el("weekly-deadline-overdue").textContent = d.overdue_pending;
    el("weekly-deadline-created").textContent = d.created;
}

function renderHabitStats(stats) {
    const container = el("weekly-habit-list");
    if (!stats.habits.length) return empty(container, "还没有习惯。先建一个真正想追踪的。");
    container.replaceChildren();

    for (const habit of stats.habits) {
        const card = document.createElement("article");
        card.className = "item-card";
        const body = document.createElement("div");
        const title = document.createElement("div");
        title.className = "item-title";
        title.textContent = habit.name;
        if (habit.days_logged > 0 && habit.days_met === habit.days_logged) {
            title.appendChild(badge("全部达标", "ongoing"));
        }
        const meta = document.createElement("div");
        meta.className = "item-meta";
        meta.textContent = `记录 ${habit.days_logged} 天 · 达标 ${habit.days_met} 天`;
        body.append(title, meta);

        const progress = document.createElement("div");
        progress.className = "progress";
        const fill = document.createElement("span");
        const ratio = habit.days_logged > 0 ? habit.days_met / habit.days_logged : 0;
        fill.style.width = `${Math.max(0, Math.min(1, ratio)) * 100}%`;
        progress.appendChild(fill);
        body.appendChild(progress);

        card.appendChild(body);
        container.appendChild(card);
    }
}

function todayMonday() {
    const now = new Date();
    const weekday = (now.getDay() + 6) % 7; // Monday = 0
    const monday = new Date(now);
    monday.setDate(now.getDate() - weekday);
    return toIsoDate(monday);
}

function toIsoDate(date) {
    const pad = (v) => String(v).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function shiftWeek(isoDate, days) {
    const date = new Date(`${isoDate}T00:00:00`);
    date.setDate(date.getDate() + days);
    return toIsoDate(date);
}

export async function loadWeekly(localDate = null) {
    try {
        const stats = await get(`/stats/weekly${localDate ? `?local_date=${localDate}` : ""}`);
        currentWeekStart = stats.week_start;
        renderHero(stats);
        renderScheduleStats(stats);
        renderPomodoroStats(stats);
        renderDeadlineStats(stats);
        renderHabitStats(stats);
    } catch (error) {
        showToast(error.message, 3800);
        throw error;
    }
}

export function initWeekly() {
    currentWeekStart = todayMonday();
    el("weekly-prev").addEventListener("click", () => {
        loadWeekly(shiftWeek(currentWeekStart, -7)).catch(() => {});
    });
    el("weekly-next").addEventListener("click", () => {
        loadWeekly(shiftWeek(currentWeekStart, 7)).catch(() => {});
    });
    el("weekly-current").addEventListener("click", () => {
        loadWeekly(todayMonday()).catch(() => {});
    });
}
