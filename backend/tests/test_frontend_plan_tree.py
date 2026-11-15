"""`plan-tree.js` 的**行为级**测试 —— 这个项目里第一条真正跑前端代码的测试。

## 为什么要有它（而不是只有静态契约检查）

`tests/test_frontend_contracts.py` 只证明"零件都在" ✓ ——
它证明不了"**该显示的那几条真的会显示出来**" ✗。

而 `deadlineGroups()` 做的正是那件事 ✓：它决定哪些 DDL 出现在列表里、
谁归到谁下面、按什么顺序 ✓。它错了**不会抛异常** ✗ ——
只会让列表少一条、或者顺序不对 ✓，**只有人盯着屏幕才看得出来** ✗
（这个项目已经这么抓到过好几次 bug 了 ✓）。

## 怎么在没有浏览器的情况下跑它

关键是把纯逻辑和 DOM 分开 ✓：

- `plan-tree.js` **完全不碰 `document`** ✓ → 可以在 Node 里直接 `import` ✓
- `today.js` 才碰 DOM ✓（所以它没法这样测 ✓ —— 那是另一件事，需要真的 E2E ✓）

⚠️ Node 不在 PATH 上就 **skip** ✓，不是失败 ✗ ——
它不是这个项目的运行依赖 ✓（只是开发机上恰好有），
把它变成硬依赖会让"拷贝一份代码到别的机器上"变得跑不了测试 ✗。

⚠️ 复制成 `.mjs` 是必须的 ✓：Node 默认把 `.js` 当成 CommonJS ✓，
而 ES module 的 `export` 会让它直接语法报错 ✗。
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
PLAN_TREE = FRONTEND / "js" / "plan-tree.js"

#: Node 里那个极小的驱动：读 spec.json → 调 `deadlineGroups` → 把结果打成 JSON。
#: ⚠️ 刻意**不往仓库里放一个 .mjs 文件** ✓ —— 它只服务于这次测试 ✓，
#: 顺手写在临时目录里就行 ✓（少一个要维护的文件 ✓）。
_DRIVER = """
import fs from "node:fs";

const spec = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const mod = await import(process.argv[3]);

const out = {};
for (const [name, filter] of Object.entries(spec.cases)) {
    out[name] = mod.deadlineGroups(spec.items, filter).map((group) => ({
        id: group.item.id,
        children: group.children.map((child) => child.id),
        total: group.total,
    }));
}
process.stdout.write(JSON.stringify(out));
"""


def _item(item_id, parent_id, status, due_at):
    return {
        "id": item_id,
        "title": f"#{item_id}",
        "parent_id": parent_id,
        "completion_status": status,
        "due_at": due_at,
        "due_at_local": due_at,
        "time_status": "upcoming" if status == "pending" else status,
    }


# 一棵刻意长得别扭的树：
#   父A（未完成）→ 已完成 / 未完成 / 已取消 各一个子任务
#   独立B（已完成）→ 没有子任务
#   父C（未完成）→ 已完成的子任务
#   父D（已取消）→ 未完成的子任务   ← 专门用来咬"父任务不符合筛选"那条规则
ITEMS = [
    _item(1, None, "pending", "2026-10-07T14:00:00+00:00"),
    _item(2, 1, "completed", "2026-10-06T10:00:00+00:00"),
    _item(3, 1, "pending", "2026-10-08T10:00:00+00:00"),
    _item(4, 1, "cancelled", "2026-10-09T10:00:00+00:00"),
    _item(5, None, "completed", "2026-10-05T10:00:00+00:00"),
    _item(6, None, "pending", "2026-10-10T10:00:00+00:00"),
    _item(7, 6, "completed", "2026-10-10T10:00:00+00:00"),
    _item(8, None, "cancelled", "2026-10-11T10:00:00+00:00"),
    _item(9, 8, "pending", "2026-10-11T10:00:00+00:00"),
]

CASES = {
    "全部": "all",
    "未完成": "pending",
    "已完成": "completed",
    "已取消": "cancelled",
}


@pytest.fixture(scope="module")
def node_path():
    node = shutil.which("node")
    if node is None:
        pytest.skip("这台机器上没有 node —— 前端逻辑测试跳过（它不是运行依赖）。")
    return node


def _run(node, workdir: Path, items, cases):
    """把 `plan-tree.js` 复制成 `.mjs` ✓，配一个临时驱动跑一遍 ✓，返回结果 ✓。

    ⚠️ 文件名必须是 `.mjs` ✗ —— Node 默认把 `.js` 当 CommonJS ✓，
    里面那句 `export` 会让它直接语法报错 ✓。

    ⚠️⚠️ **必须显式写 `encoding="utf-8"`** ✗ ——
    这台机器上 `text=True` 默认走 **GBK** ✓（中文 Windows 的 locale ✓），
    而这个驱动的返回里有中文键（「全部」「未完成」…）✓ →
    解码直接抛 `UnicodeDecodeError` ✓，报错还发生在**子进程读取线程里** ✓
    （主线程只看到 "AssertionError" 和一个看不懂的 thread warning ✓）。
    和 `main.py` 里那个 `charset=utf-8` 是同一类坑 ✓。
    """
    module = workdir / "plan-tree.mjs"
    module.write_text(PLAN_TREE.read_text(encoding="utf-8"), encoding="utf-8")
    driver = workdir / "driver.mjs"
    driver.write_text(_DRIVER, encoding="utf-8")
    spec = workdir / "spec.json"
    spec.write_text(json.dumps({"items": items, "cases": cases}), encoding="utf-8")

    result = subprocess.run(
        [node, str(driver), str(spec), module.as_uri()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, f"node 跑挂了：\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def groups(node_path, tmp_path_factory):
    return _run(node_path, tmp_path_factory.mktemp("plan-tree"), ITEMS, CASES)


def test_nothing_is_lost_from_the_list(groups):
    """★★ 最重要的一条：**每一条 DDL 都必须在某一类里出现** ✓。

    ⚠️ 这条咬的是最容易写错的地方 ✓：父任务自己不符合筛选、
    但它的子任务符合时，父任务**必须留下** ✓ —— 否则那些里程碑
    会从列表里**凭空消失** ✗（数量对不上，而且没有任何提示 ✓）。

    | 筛选 | 必须有 |
    |---|---|
    | 全部 ✓ | 四个顶层 DDL（1 / 5 / 6 / 8）✓ |
    | 未完成 ✓ | 父A（子任务 3 未完成）· 父C（自己未完成）· **父D**（自己已取消，但子任务 9 未完成）✓ |
    | 已完成 ✓ | 独立B（自己）· **父A**（子任务 2）· **父C**（子任务 7）✓ |
    | 已取消 ✓ | 父A（子任务 4）· 父D（自己）✓ |
    """
    assert [g["id"] for g in groups["全部"]] == [1, 5, 6, 8]

    assert [g["id"] for g in groups["未完成"]] == [1, 6, 8], (
        "父D（已取消，但子任务 #9 未完成）必须留在「未完成」里 ✗"
    )
    assert [g["id"] for g in groups["已完成"]] == [6, 1, 5], (
        "父A / 父C 自己是未完成，但它们的子任务已完成 ✓ → 必须留下 ✗"
    )
    # ⚠️ 「已取消」不是界面上的三个标签之一 ✓ —— 这里只是顺带确认它不会丢东西 ✓
    #（排序保持接口顺序 ✓，因为 `sortDeadlines` 只管 pending / completed ✓）
    assert [g["id"] for g in groups["已取消"]] == [1, 8]


def test_milestones_are_never_treated_as_top_level(groups):
    """⚠️ 否则每条里程碑会**显示两遍** ✓（一遍当顶层、一遍在父任务下面 ✓）。"""
    for name, result in groups.items():
        assert all(g["id"] in {1, 5, 6, 8} for g in result), f"{name} 里混进了里程碑 ✗"


def test_children_are_filtered_but_the_total_is_not(groups):
    """★ `children` 随筛选变 ✓，`total` **不变** ✓。

    ⚠️ 「已拆解 N」那个徽标读的是 `total` ✓ —— 它说的是
    "这条 DDL 拆过几个" ✓，不该在三个标签页里显示三个不同的数字 ✗。
    """
    by_id = {g["id"]: g for g in groups["全部"]}
    assert by_id[1]["children"] == [2, 3, 4]
    assert by_id[1]["total"] == 3
    assert by_id[5]["children"] == []
    assert by_id[5]["total"] == 0

    assert {g["id"]: g for g in groups["未完成"]}[1]["children"] == [3]
    assert {g["id"]: g for g in groups["未完成"]}[1]["total"] == 3
    assert {g["id"]: g for g in groups["已完成"]}[1]["children"] == [2]
    assert {g["id"]: g for g in groups["已完成"]}[1]["total"] == 3


def test_the_orders_match_each_filter(groups):
    """未完成 → 最急在上 ✓；已完成 → 最近在上 ✓；全部 → 保持接口顺序 ✓。"""
    # 父A(10-07) 在 父C(10-10) 前面 → 未完成是升序 ✓
    assert [g["id"] for g in groups["未完成"]] == [1, 6, 8]
    # 已完成是降序 ✓：父C(10-10) → 父A(10-07) → 独立B(10-05)
    assert [g["id"] for g in groups["已完成"]] == [6, 1, 5]


def test_empty_input_returns_empty(node_path, tmp_path):
    """★ 真的喂一个空数组进去 ✓ —— **新装就是这个状态** ✓，绝不能抛 ✓。"""
    result = _run(node_path, tmp_path, [], {"全部": "all"})

    assert result == {"全部": []}
