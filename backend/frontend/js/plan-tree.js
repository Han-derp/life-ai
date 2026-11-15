// 「计划」列表的**纯逻辑**（不碰 DOM ✓）。
//
// ## 为什么单独一个文件
//
// 这些函数决定"哪几条东西会被显示出来、按什么顺序" ✓ ——
// 它们错了**不会报错** ✗，只会让列表少一条 / 顺序不对 ✓，
// 而且只有人盯着屏幕才看得出来 ✓。
//
// 所以刻意把它们从 `today.js`（那个模块一 import 就要 `document`）里分出来 ✓：
// **纯函数可以在 Node 里直接跑** ✓ —— 见 `tests/test_frontend_plan_tree.py` ✓。
// 这是这个项目里第一条**行为级**的前端测试（在此之前只有静态契约检查 ✓）。

/** ISO 时间 → 毫秒 ✓。解析不出来时给 +∞ ✓（让它排到最后，而不是变成 `NaN` 把排序搞乱 ✓）。 */
export const timeOf = (iso) => {
    const value = Date.parse(iso);
    return Number.isNaN(value) ? Number.POSITIVE_INFINITY : value;
};

/**
 * DDL / 日程的排序。三种筛选口径**不一样** ✓：
 *
 * | 筛选 | 顺序 | 为什么 |
 * |---|---|---|
 * | 未完成 ✓ | 最急的在上 ✓ | 它是**待办** ✓ |
 * | 已完成 ✓ | 最近的在上 ✓ | 它是**翻看** ✓ |
 * | 全部 ✓ | 保持接口顺序 ✓ | 接口是"未完成在前、组内按截止升序" ✓ —— "还剩什么"最该在最上面 ✓ |
 *
 * ⚠️ 只在本文件里用 ✓（`deadlineGroups`）—— 不导出 ✓，
 * 因为"顺序对不对"应该由 `deadlineGroups` 的行为来验 ✓，不是单独验一个排序函数 ✓。
 */
function sortDeadlines(items, filter) {
    if (filter === "pending") return [...items].sort((a, b) => timeOf(a.due_at) - timeOf(b.due_at));
    if (filter === "completed") return [...items].sort((a, b) => timeOf(b.due_at) - timeOf(a.due_at));
    return items;
}

/**
 * ★ DDL 列表是一棵**两层树**：顶层 DDL + 它拆解出来的里程碑。
 *
 * ⚠️ 用户 2026-10-06 实测：里程碑直接铺在父任务下面 ✗，
 * 一个拆成 4 步的 DDL 立刻把列表撑得很长 ✓ → 子任务**默认收起来** ✓
 *（收起是渲染层的事 ✓，这里只负责"谁归谁" ✓）。
 *
 * ⚠️⚠️ 这里有一条**很容易写错**的规则：
 * **父任务自己不符合筛选、但它的子任务符合时，父任务必须留下** ✓。
 * 否则那些里程碑会**凭空消失** ✗ —— 数量对不上，而且没有任何提示 ✓。
 *
 * 例：筛「已完成」时，一个还没做完的父 DDL 下面有一条已完成的里程碑 ✓；
 * 筛「未完成」时，一个已取消的父 DDL 下面有一条还没做完的里程碑 ✓。
 *
 * 返回 `{ item, children, total }`：
 * - `children` = **当前筛选下**要显示的子任务 ✓
 * - `total` = **全部**子任务数 ✓（和筛选无关 ✓）——
 *   「已拆解 N」那个徽标说的是"这条 DDL 拆过几个" ✓，
 *   不该随当前筛选变来变去 ✗（否则同一个数字在三个标签页里三个样子 ✓）。
 */
export function deadlineGroups(items, filter) {
    const matches = (item) => filter === "all" || item.completion_status === filter;

    const childrenByParent = new Map();
    for (const item of items) {
        if (!item.parent_id) continue;
        if (!childrenByParent.has(item.parent_id)) childrenByParent.set(item.parent_id, []);
        childrenByParent.get(item.parent_id).push(item);
    }

    const allChildren = (item) => childrenByParent.get(item.id) || [];
    const shown = (item) => sortDeadlines(allChildren(item).filter(matches), filter);

    const parents = items.filter(
        (item) => !item.parent_id && (matches(item) || shown(item).length > 0),
    );

    return sortDeadlines(parents, filter).map((item) => ({
        item,
        children: shown(item),
        total: allChildren(item).length,
    }));
}
