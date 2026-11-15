// 外观主题：**只影响观感，不影响任何功能** ✓。
//
// 两个：`classic`（默认，简洁明快）+ `soft`（用户说的"文艺一点、少女一点"）。
//
// ⚠️ 存在 localStorage —— 它是**这台机器的观感偏好**，不是用户数据，
// 所以既不该进数据库、也不该进 `.env` ✓。
//
// ⚠️ 真正换来换去对比之后，选定的那一个可以直接改成默认
// （把 CSS 里 `[data-theme="soft"]` 的值搬到统一层那个 `:root` 就行 ✓）。

const KEY = "life-ai-theme";

export const THEMES = [
    ["classic", "清爽", "干净利落，现在的这版"],
    ["soft", "柔和", "暖粉底 + 玫瑰色，圆角更大"],
];

export function currentTheme() {
    const saved = localStorage.getItem(KEY);
    return THEMES.some(([name]) => name === saved) ? saved : "classic";
}

export function applyTheme(name) {
    if (name === "classic") {
        // 默认值用"没有属性"表示 —— 这样 CSS 里只需要一组覆盖规则 ✓
        document.documentElement.removeAttribute("data-theme");
    } else {
        document.documentElement.dataset.theme = name;
    }
    localStorage.setItem(KEY, name);
}

export function applySavedTheme() {
    applyTheme(currentTheme());
}
