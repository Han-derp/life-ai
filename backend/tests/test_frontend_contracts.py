"""前端契约测试（静态检查，不需要浏览器）。

## 为什么要有这个文件

这一轮用户实测抓到的 **6 个 bug 全是前端** ✗ —— 而且全是**静默**的那一类：
代码在跑、元素在、事件也挂上了，就是不工作：

| bug | 静默的原因 |
|---|---|
| `.chat-view { display: grid }` 盖掉 `hidden` | 关掉的页面还在渲染 |
| `.dialog-layer { display: grid }` 同上 | 关掉的弹层铺满整屏 → 界面变哑 |
| `.chat-image-preview { display: flex }` 同上 | 缩略图永远挂着，× 看着点不动 |
| `el("typo")` | 查不到的元素 → 什么都不会发生 |

**它们不会抛异常，所以只能靠人去截图发现** ✗。
这个文件就盯这一类：**"本该报错却静默"的东西。**

⚠️ 它**不测交互**（不跑浏览器、不点按钮）——
那是另一件事（真的 E2E）。这里只保证"零件都在、护栏都在"。
"""

import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def _read(name: str) -> str:
    return (FRONTEND / name).read_text(encoding="utf-8")


def _scripts() -> list[Path]:
    return sorted((FRONTEND / "js").glob("*.js"))


# ---------- 1. 全局 [hidden] 护栏 ----------


def test_the_global_hidden_guard_exists():
    """★★ 这条盯的是**同一个坑踩了三次**的那件事。

    三次全是"基类里的 `display` 盖掉了 `hidden` 自带的 `display: none`" ✗：
    `.chat-view` / `.dialog-layer` / `.chat-image-preview`。

    ⚠️ `!important` 是**必须**的 —— 类选择器的优先级高于 `hidden` 的 UA 样式，
    没有它这条规则就是摆设 ✗。
    """
    css = _read("style.css")

    assert re.search(
        r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css
    ), "全局 [hidden] 护栏不见了 —— 那个坑会立刻回来 ✗"


# ---------- 2. 元素 id ----------


def test_every_element_id_the_js_looks_up_exists():
    """★ `el("typo")` 不会报错，只会**静默什么都不做** ✗。

    "去掉这张图点不动"就是这一类：代码在跑、按钮在、事件也挂上了，
    某个 id 对不上，于是点击静默失效 ✓。
    """
    available = set(re.findall(r'id="([^"]+)"', _read("index.html")))
    for path in _scripts():
        # JS 动态创建的元素（`select.id = "..."`）也算存在 ✓
        available |= set(re.findall(r'\.id\s*=\s*"([^"]+)"', path.read_text("utf-8")))

    used: set[str] = set()
    for path in _scripts():
        used |= set(re.findall(r'el\(\s*"([^"]+)"\s*\)', path.read_text("utf-8")))

    missing = sorted(used - available)
    assert not missing, f"这些 id 在 HTML 和 JS 里都找不到：{missing}"


# ---------- 3. 引用的文件 ----------


def test_every_module_the_page_loads_exists():
    """引一个不存在的 js / css，浏览器只会**静默 404** ✗ —— 页面半死不活。"""
    html = _read("index.html")

    for ref in re.findall(r'(?:src|href)="((?:js/|style\.css)[^"]*)"', html):
        assert (FRONTEND / ref).exists(), f"index.html 引了不存在的文件：{ref}"


def test_every_relative_import_resolves():
    """★ `import ... from "./x.js"` 指向一个不存在的文件 → **整个模块都不执行** ✗。

    这次加 `theme.js` 时就是这样：忘了建文件的话，
    `settings.js` 会整个哑掉，而页面上只会少一块，不会报错 ✓。
    """
    for path in _scripts():
        for ref in re.findall(r'from\s+"(\.[^"]+)"', path.read_text("utf-8")):
            assert (path.parent / ref).exists(), f"{path.name} 引了不存在的 {ref}"


# ---------- 4. 「计划」三张卡的三类筛选（两端契约） ----------
#
# 用户 2026-10-05 要求：点开卡片的列表页要"分三类，可以选择看哪一类" ✓。
#
# ⚠️ 这是一处**新的两端契约** ✗：HTML 里的筛选按钮 ↔ `today.js` 里的 `PLAN_FILTERS` ✓。
# 两边不一致时，**两种方向都是静默的** ✓：
#
#   · HTML 加了按钮、JS 没认 → 点了**什么都不发生** ✗
#   · JS 认了 HTML 上不存在的值 → 那一类**永远看不到** ✗
#
# 两种情况都不会报错 ✓ —— 正是这个文件要盯的那一类 ✓。

PLAN_KINDS = {"schedule": "日程", "deadline": "DDL", "habit": "习惯"}


def test_each_plan_list_offers_exactly_the_three_filters():
    html = _read("index.html")
    today_js = (FRONTEND / "js" / "today.js").read_text("utf-8")

    for kind, label in PLAN_KINDS.items():
        block = re.search(rf'data-list-filter="{kind}"(.*?)</div>', html, re.S)
        assert block, f"{label} 的三类筛选按钮不见了 ✗"
        html_values = re.findall(r'data-filter-value="([^"]+)"', block.group(1))

        declared = re.search(rf'{kind}:\s*\[([^\]]+)\]', today_js)
        assert declared, f"today.js 里找不到 {kind} 的 PLAN_FILTERS ✗"
        js_values = re.findall(r'"([^"]+)"', declared.group(1))

        assert len(html_values) == 3, f"{label} 应该正好三类，现在是 {html_values}"
        assert html_values == js_values, (
            f"{label} 的筛选按钮和 today.js 的 PLAN_FILTERS 对不上："
            f"HTML={html_values} JS={js_values}"
        )


def test_the_plan_summary_cards_and_list_containers_exist():
    """⚠️ 三张摘要卡各要一个"只放一条"的容器 ✓，列表页各要一个列表容器 ✓。

    `el("...")` 的静态检查（上面那条）其实已经覆盖了 ✓ ——
    这里额外锁的是**成对存在** ✓：少了列表容器的话，
    卡片点开是一个**永远空着的**弹窗 ✗，而代码不会报错 ✓。
    """
    html = _read("index.html")
    ids = set(re.findall(r'id="([^"]+)"', html))

    for kind, label in PLAN_KINDS.items():
        assert f"{kind}-summary" in ids, f"{label} 摘要卡的容器不见了 ✗"
        assert f"{kind}-all-list" in ids, f"{label} 列表的容器不见了 ✗"
        assert f"{kind}-list-dialog" in ids, f"{label} 的列表弹窗不见了 ✗"
        assert f"plan-card-{kind}" in ids, f"{label} 的摘要卡不见了 ✗"


# ---------- 5. 确认层必须在 top layer 里 ----------
#
# ⚠️ 这是**用户实测抓到的**一个 bug ✓（2026-10-06）：
#
#   "展开详情页后，如果删除某个日程 / ddl / 习惯，
#    删除界面被压在详情页下面，需要关掉详情页才能点掉删除。"
#
# 病因：`showModal()` 打开的 `<dialog>` 在浏览器的 **top layer** 里 ✓，
# 那一层**凌驾于所有 z-index 之上** ✗ —— 所以确认层只要是个普通 div，
# 无论 `z-index` 写多大都压不过它 ✓（去调 z-index 是白费力气 ✓）。
#
# ⚠️ 这条测试锁的是**机制**（`<dialog>` + `showModal()`）而不是措辞 ✓ ——
# 因为那个机制就是**唯一**能满足"出现在模态弹窗之上"的办法 ✓。
# 有人把它改回 `position: fixed` 的 div，这条就会红 ✓。


def test_the_confirm_layer_is_a_top_layer_dialog():
    utils_js = (FRONTEND / "js" / "utils.js").read_text("utf-8")

    assert 'createElement("dialog")' in utils_js, (
        "确认层不是 <dialog> 了 ✗ —— 普通 div 压不过 showModal() 打开的详情弹窗，"
        "删除确认会被压在底下（2026-10-06 用户实测过）✗"
    )
    assert "showModal()" in utils_js, (
        "确认层没有走 showModal() ✗ —— 不进 top layer 就压不过详情弹窗 ✓"
    )
    # ⚠️ 反面：不能再靠 `hidden` 开关它 ✗（`<dialog>` 的开关是 `open` ✓）。
    assert "dialogLayer.hidden" not in utils_js, (
        "确认层还在用 `hidden` 开关 ✗ —— <dialog> 的开关是 `open` ✓"
    )


def test_the_escape_key_still_settles_the_promise():
    """⚠️ 这是同一个 bug 的另一半 ✓，而且它**从来没被发现过** ✗。

    `showDialog()` 返回一个 promise，调用方 `await` 它 ✓。
    Esc 关掉弹层时如果不结算它，那行代码会**永远挂住** ✓ ——
    不报错、没反应，而且用户下次点同一个按钮还会再挂一个 ✓。
    """
    utils_js = (FRONTEND / "js" / "utils.js").read_text("utf-8")

    assert re.search(r'addEventListener\(\s*"close"', utils_js), (
        "确认层没有监听 close ✗ —— 用 Esc 关掉时那个 promise 永远不结算 ✓"
    )


def test_nobody_uses_the_browser_native_dialogs():
    """⚠️ `window.confirm` / `alert` / `prompt` 一律不许用 ✓。

    `utils.js` 顶上写着为什么 ✓：破坏性操作要**一次摆清所有选项**
    （"只删聊天 / 连记忆一起删 / 连档案也抹掉"），而原生对话框只能问是非 ✓；
    而且它的样式和界面其余部分完全对不上 ✓。

    ⚠️ 这条是**清完最后一处之后**才加的 ✓ —— 习惯的「归档」曾经是全项目
    唯一残留的 `confirm()` ✓（2026-10-06 顺手换掉了 ✓）。

    ⚠️ 前瞻 / 后顾的写法有讲究 ✓：`(?<![\\w])` 而不是 `(?<![\\w.])` ✗ ——
    后者会把 **`window.confirm(...)`** 漏掉 ✓，而那是原生对话框**最常见**的写法 ✓
    （第一版就写漏了 ✓，用一个假样本当场试出来 ✓）。
    `confirmDialog(` 这种则天然不会被抓 ✓（`confirm` 后面不是 `(` ✓）。
    """
    offenders = []
    for path in _scripts():
        for match in re.finditer(r"(?<!\w)(confirm|alert|prompt)\s*\(", path.read_text("utf-8")):
            offenders.append(f"{path.name}: {match.group(1)}()")

    assert not offenders, f"这些地方在用浏览器原生对话框：{offenders}"
