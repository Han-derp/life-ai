# 项目状态（开发者入口）

> **当前状态快照（2026-11-15）**：本次源码整合基于 2026-11-15 提供的代码汇总包；Schema **v19**。已移除 Ollama Provider / 本地模型模式、AIRI 专用 OpenAI 兼容入口，以及 pet 状态模块。所有 AI 任务统一通过 OpenAI 兼容 Provider 调用。
>
> 完整测试数量以本次清理后 `pytest` 的实际运行结果为准；不要沿用本文件历史记录中的旧测试数。
>
> **升级提醒**：旧版 `backend/.env` 中的 `LLM_LOCAL_ONLY`、`OLLAMA_*` 和 `LLM_*_PROVIDER=ollama` 不再支持。当前代码不会静默把旧 Ollama 路由改发云端；设置页会提示旧路由错误。升级后应清理旧变量，并在三个模型档中保存当前 OpenAI 兼容服务配置。

## 当前架构结论

- 生活数据与图片继续保存在本机 `backend/storage/`。
- AI 请求通过 `OpenAICompatProvider` 发送到对应模型档的 `base_url`。第三方云端地址会接收发送给模型的上下文；自建地址的部署与留存由使用者负责。
- `roleplay`、`analysis`、`vision` 三档保留；按任务映射到模型档的机制保留。
- `factory.current_provider(task)` 仍提供动态配置代理；`factory.get_provider(task)` 返回真实 Provider 实例。
- Ollama 失败回退、云端回退通知和本地耗时面板均已移除。
- AIRI 专用 `/v1/models`、`/v1/chat/completions` 路由已移除；Life AI 自身 `/api/v1/` REST API 保留，供网页及未来 Android 客户端复用。
- `pet` 状态 API、workflow、schema 和测试已移除；未来吉祥物系统需要时另行设计，不保留旧桌宠状态计算模块。

## 当前发布前待办

| 项 | 状态 |
|---|---|
| Smoke Test 核心表清单 | 已补 `tag_suggestions`、`action_plans`、`action_plan_steps` |
| Python 缓存与测试临时文件 | 清理出交付源码包，并由 `.gitignore` 排除 |
| Ollama / AIRI / pet 代码清理 | 本次执行 |
| 完整 pytest | 以本次实际运行结果为准 |
| 真实模型连接、浏览器人工验收 | 需要在使用者自己的 API Key 和运行环境中完成 |
| Git 历史密钥检查 | 需要在原始 Git 仓库中执行，源码 ZIP 无法替代 |

---

## 0. 一句话

**运行在用户自己电脑上的生活管理与 AI 行动搭档**：有长期记忆（原话 → 事实 → 判断）、生活数据（日程 / DDL / 习惯 / 番茄 / 时间轴）、主动联系、每日复盘和图片理解。模型调用通过可配置的 OpenAI 兼容服务完成。

单用户、本机存储、无账号体系 / 无多租户；可选访问密码保护远程访问。

---

## 1. 怎么跑

```powershell
# 日常：双击项目根目录的 启动.bat 就行（--host 0.0.0.0 + 自动开浏览器 ✓）
# 手动等价写法：
cd C:\Users\19362\Desktop\life-ai-v1-complete\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
.\.venv\Scripts\python.exe -m pytest          # 测试数量以实际输出为准
.\.venv\Scripts\python.exe -m scripts.smoke_test
```

⚠️ **必须是 `--host 0.0.0.0`** ✗（不是 `127.0.0.1` ✗）—— 否则**手机连不上** ✓
（手机那条路的完整说明在 `USER_GUIDE.md` §7 ✓）。

`backend/scripts/` 包含数据库初始化、集成联调和 Smoke Test 脚本；本地模型延迟与 Ollama 试跑脚本已移除。

技术栈：**Python 3.14 + FastAPI + SQLite(WAL) + 零构建原生 JS ES modules** ✓。
没有 npm、没有打包步骤 ✓（前端就是 `backend/frontend/` 里的静态文件 ✓）。

---

## 2. 分层（**唯一的硬性架构约束**）

```
api  →  workflows  →  services  →  database
                ↘        ↗
              ai/context
```

- ✅ `api → workflows | services` ✓
- ✅ `workflows → services` ✓
- ✅ `ai/context → services` ✓（组装上下文要读记忆 ✓）
- ✅ `llm` 是 `services` 的**同级** ✓（谁都别依赖谁 ✓）
- ❌ **`services` 不许 import `workflows`** ✗ —— 这条被违反过（`tone_service` 就是为了绕它才单独存在的 ✓）

**加功能时先问自己在哪一层** ✓ —— 放错了会立刻在这条线上打架 ✓。

---

## 3. 文件地图

| 路径 | 干什么 |
|---|---|
| `app/main.py` | 应用组装、静态挂载、`UTF8JSONResponse`（供 REST 客户端正确解码中文）|
| `app/api/v1/*.py` | REST 端点（每个资源一个文件 ✓）|
| `app/api/v1/router.py` | 端点注册表（**加端点要在这里挂** ✓）|
| `app/api/auth.py` | **访问密码**：登录接口 + Cookie / Bearer 校验 + `password_matches()` 与 `_matches()`（⚠️ 两者**不许合并** ✗，见 §7 红线 7）|
| `app/workflows/` | **跨多个 service 的流程**：`extraction`（抽取）`impression`（归纳）`proactive`（主动联系）`baseline`（基线预警）`chat_history`（删除 / 编辑 / 回滚连带处理）`tone`（主动开口的口吻底线）|
| `app/services/` | 单一职责：`chat_service`（含**记忆导入** ✓）`memory_service` `settings_service` `persona_service` `image_service` `export_service` `tone_service`（聊天客服腔 ✓）`stats_service`（回顾统计 ✓）`deadline_service` `schedule_service` `habit_service` `pomodoro_service` `timeline_service` `commitment_service` `rule_service` `review_service` `tag_suggestion_service` `context_service` `context_exclusion_service` `status_utils` |
| `app/schemas/` | **请求 / 响应的形状**（Pydantic ✓，一个资源一个文件 ✓）—— 改接口先看这里 ✓ |
| `app/ai/context/` | **上下文组装**：`assembler.py`（块顺序 + 预算 + 丢弃 ✓）`blocks.py` |
| `app/llm/` | `base` / `deepseek.py`（通用 OpenAI 兼容实现，类名 `OpenAICompatProvider`）/ `factory.py`（任务→档→Provider）|
| `app/core/` | `config.py`（**所有环境变量的唯一入口** ✓）`time_utils` `prompt_loader`（mtime 缓存 ✓）`persona_loader` `token_budget` |
| `app/database/` | `connection` `migrations.py`（**只追加** ✓）`bootstrap` |
| `app/scheduler/` | 后台 tick（抽取 / 主动联系 / 基线 ✓）|
| `app/prompts/*.md` | **提示词**（改了立刻生效 ✓ —— 按文件 mtime 缓存 ✓）|
| `personas/<name>/persona.md` | **人格**（用户的东西 ✓，界面上能编辑 ✓）|
| `frontend/` | `index.html` + `style.css` + `js/*.js`（无构建 ✓）|
| `tests/` | 前缀 `test_`；`conftest.py` 里有两个 **autouse** fixture（见 §6 ✓）|

---

## 4. 不变量（以 `docs/DEVELOPMENT_PLAN.md` 为准 ✓，这里列最常踩的）

| # | 不变量 | 代码在哪强制 |
|---|---|---|
| 1 | 数据库是事实来源；**计划 ≠ 现实** | `today:*` 各块分开 ✓ |
| 2 | **AI 只能读 + 建议**，不许直接改数据 | 没有任何 workflow 调写接口的 AI 路径 ✓ |
| 3 | 事实优先级：用户当前输入 > 实时数据 > 最近聊天 > 推断 | `behavior_rules.md` ✓ |
| **5** | **没有出处，就不许"我记得"** | `extraction._locate()` ✓ 核不到**整条丢掉** ✓ |
| **6** | 判断可以被反驳，但**永不删除** | `impressions.status` 的 CHECK 只允许 **`active` / `disputed` / `superseded`** ✓（`migrations.py` ✓）+ `revise_stale()` ✓。⚠️ **`countered` 不是状态** ✗ —— 它只是"反例"方向上的**计数键** ✓（2026-10-05 由审计更正 ✓） |
| 7 | 可以说"我想你"，不能说"你欠我" | `workflows/tone.py` ✓ |
| 8 | 只给事实和关心，**不下命令** | `tone.py` + `behavior_rules.md` ✓ |
| 9 | 零记录成本（用户不用手打标签 ✓） | 自动抽取 + 建议制 ✓ |
| 10 | **LLM 永不写 SQL** | 没有任何字符串拼 SQL 走模型 ✓ |

---

## 5. 五个必须懂的机制

### ① 上下文块顺序 = 钱（别动 ✗）

```
persona(100) → rules(95) → memory:impressions(90) → memory:observations(80)
→ today:schedule/deadlines(70/75) → history(60) → pomodoro(50)(40)(30)
```

优先级数字**越大越不会被丢** ✓。
**顺序同时是"变化频率从低到高"** ✓ —— 稳定前缀 = DeepSeek **KV 缓存命中** ✓ = 便宜 50 倍 ✓。
连"（现在：…）"都挂在**最后一条用户消息**上 ✓（进 system 就会毁掉前缀 ✓）。

### ② 三档模型（用户可在界面填 ✓）

| 档 | 任务 | 要求 |
|---|---|---|
| `roleplay` | `chat` `proactive` `review` `deadline_breakdown` | 人格一致、语气像人 |
| `analysis` | `extract` `impression` | 听话、严格 JSON、**不编造** |
| `vision` | `vision` | 多模态 |

`deadline_breakdown` 独立成任务，是为了避免调整聊天任务路由时意外改变 DDL 拆解的模型配置（R15）。当前没有内置本地 Provider。

环境变量：`LLM_TIER_<TIER>_{PROVIDER,BASE_URL,API_KEY,MODEL}` ✓；
留空回落 `DEEPSEEK_*` ✓（`config.tier_config()` ✓）。
当前版本不支持 `LLM_LOCAL_ONLY`、Ollama Provider 或本地模型路由。

### ③ provider 代理（所以改设置不用重启 ✓）

`factory.current_provider(task)` 返回一个**代理** ✓ —— 每次调用都去问当前配置 ✓。
`chat_service` / `proactive` / `baseline` / `deadline_service` / `review_service` 抓的都是它 ✓。
配 `settings_service.save()` 里的 `reset_cache()` ✓ → **立刻生效** ✓。

⚠️ `factory.get_provider()` **必须继续返回真实例** ✗ —— 测试里有 `isinstance` 断言 ✓。

### ④ 记忆链路

```
messages(原话) --extract--> raw_entries(L1) --→ observations(L2) --impression--> impressions(L3)
                                     ↑ 每条都能追回原话（不变量 5）
```

- 检索是 **FTS5**（migration 011 ✓）：中文按**单字**切 ✓，查询是相邻二字短语 OR ✓
- **图片**：聊天直发图 ✓（云端原生支持 ✓）；要**进记忆**必须先 OCR 成文字 ✓
  （`extraction._describe_image()` → 存进 `messages.metadata_json.image_text` ✓ → 它也能当出处 ✓）
- 抽取是**异步**的 ✓（调度器 ✓），别塞进请求路径 ✗

### ⑤ 口吻有两道代码护栏

| 位置 | 管什么 | 粒度 |
|---|---|---|
| `workflows/tone.py` | 主动开口不许有**索取感**（"你欠我""我等了你"） | **整条作废** ✓ |
| `services/tone_service.py` | 聊天不许有**客服腔**（"如果你有任何问题…"） | **逐句删** ✓ |

⚠️ **已知缺口**：`tone_service` 只作用于**非流式**那条路 ✗ —— 而网页聊天走的是**流式** ✗。
要覆盖它只能"按句缓冲"（牺牲逐字手感 ✓）。**这是有意的取舍，不是漏做** ✓。

### ⑥ 访问密码（`app/api/auth.py`）

**默认不启用** ✓（`APP_PASSWORD` 为空 ✓）—— 只听 `127.0.0.1` 时完全用不到 ✓。
一旦设了，**所有**请求都要过中间件 ✓（唯一例外是 `/login` 和登录接口 ✓）。

- **浏览器**：HttpOnly Cookie ✓，值是 `HMAC(密码, 固定串)` ✓ —— **不是密码本身** ✓
- **脚本 / API 客户端**：`Authorization: Bearer <密码>`（密码或 token 都认）
- **无状态** ✓：重启不用重新登录 ✓；**改密码 → 所有旧 Cookie 立刻失效** ✓
- **设置页能设 / 改 / 清** ✓，**保存后立刻生效** ✓（`auth` 每次请求都读环境变量 ✓），
  而且**顺手把当前这台浏览器登录上** ✓ —— 否则密码一存上，页面下一次请求就被自己拦成 401 ✗

**四个端点 + 白名单** ✓：

| | |
|---|---|
| `POST /api/v1/auth/login` ✓ | 校验密码 ✓ → 种 Cookie ✓（错了给 **401**，不是 400 ✓） |
| `POST /api/v1/auth/logout` ✓ | 清 Cookie ✓ |
| `GET /api/v1/auth/status` ✓ | 前端问"要不要密码 / 我过了没有" ✓ |
| `GET /login` ✓ | 自带样式的登录页（英文无依赖内联 ✓） |
| `PUBLIC_PATHS` ✓ | `("/login", "/api/v1/auth/login")` ✓ —— **越少越好** ✓ |
| `POST /api/v1/settings` ✓ | 带新密码时会**顺手种 Cookie** ✓（`api/v1/settings.py` ✓） |

### ⑦ 记忆抽取的"回退云端"披露（`app/api/v1/extraction.py`）

⚠️ **前提**：这一条只在「**某一档被设成本地**」时才可能发生 ✓ ——
**2026-10-05 起 `analysis` 档默认是云端** ✓（用户拍板"云端和本地完全分开" ✓），
所以**默认安装走不到这里** ✓（详见 `TODO.md` 的 G17 ✓）。

当某档是本地 ✓、而本地没起 / 不可用时，它会**回退云端** ✗：

- 返回值带 `went_to_cloud: true` ✓
- 在 `GET /api/v1/memory/extract/notice` 留痕 ✓ → 界面上显示成「**走的是云端（回退了）**」✓

⚠️ **这不是调试信息，是隐私披露** ✓ —— 它意味着：那些**本该只留在本机的原话，
被发到云端去了** ✗。所以界面必须把它显示出来 ✓，**不许吞掉** ✓。

⚠️ **能力边界**（别给它假安全感 ✗）：走的是**明文 HTTP** ✓ —— 它防的是
"随手扫到端口的人" ✓，**不防同一个 WiFi 上抓包的人** ✗；也没有防暴力破解 ✗。
→ 要真正安全：**用手机热点或家里的网** ✓。

---

## 6. 测试纪律（这一轮在这里踩了 5 次坑 ✗）

**① 断言"意义"，不要断言"措辞"** ✗

这一轮有 5 条测试**因为正常改动而变红** ✓，全是因为锁了表层形态：
标题字符串 ✓、最后一句话 ✓、省略号 ✓、当前进程的配置 ✓。

> 写断言前先问：**"我想守住的是什么？措辞变了，它还在吗？"** ✓

**② 测试必须**不依赖本机配置** ✓**

`conftest.py` 有两个 **autouse** fixture：
- `isolated_database` ✓ —— 每个测试一个临时 SQLite ✓
- `isolated_llm_config` ✓ —— **把模型路由钉回默认值** ✓
  （不钉的话：用户在 `.env` 里开了 `LLM_LOCAL_ONLY` → **34 条测试同时红** ✗ —— 真发生过 ✓）

**③ 别让测试碰真实数据** ✗

- 人格测试：`monkeypatch.setattr(persona_loader, "PERSONAS_DIR", tmp_path)` ✓
- 备份测试：`monkeypatch.setattr(export_service, "BACKUP_DIR", tmp_path)` ✓
- 假 provider 要**完整实现**用到的方法 ✓ —— 缺一个就会被当成"不支持"，然后**静默失败** ✗

**④ 前端有 4 条静态契约测试** ✓（`tests/test_frontend_contracts.py` ✓）：
全局 `[hidden]` 护栏 ✓ / 每个 `el("id")` 都对得上 ✓ / 引用的 js·css 都在 ✓ / 相对 import 都能解析 ✓。
**它们不测交互** ✗ —— 点按类的 bug 还要靠人发现 ✓。

**⑤ ⚠️ 加任务/配置要「两端一起改」** —— 这一类**连续炸了三次** ✗：

| 加了什么 | 忘了什么 | 后果 |
|---|---|---|
| `.env` 的 `LLM_LOCAL_ONLY=true` | 测试没隔离它 | 34 条红 |
| `.env` 的 `APP_PASSWORD` | 测试没隔离它 | 一批 401 |
| 任务 `deadline_breakdown`（factory 三张表 ✓） | `config.py` 的常量 ✗ | **572 条全红**（autouse fixture 崩在 setup ✓） |
| 设置页把「硬核」档填成 deepseek（`.env` 里的 `LLM_TIER_ANALYSIS_*`） | conftest 只钉了**任务级**、没钉**档位级** ✗ | 11 条红 + **测试真的上网了** ✗（那次 6 分 47 秒 ✓，还花钱 ✓） |

⚠️⚠️ **这一轮最大的陷阱（2026-10-05）：档位级和任务级读的不是同一个地方** ✗

| | 读哪里 | 怎么在测试里改 |
|---|---|---|
| **档位级** `LLM_TIER_<T>_PROVIDER` | **环境变量**（`config.tier_env()` 只认 `os.getenv` ✓） | **`monkeypatch.setenv`** ✓ |
| **任务级** `LLM_<TASK>_PROVIDER` | `config.X` 属性 ✓ | `monkeypatch.setattr(config, ...)` ✓ |

**两套机制不一致** ✗ —— 我第一版就把档位写成了 `setattr` ✓（**是个没人读的假动作** ✗）。
而且「**档位优先于任务**」✓ → 只要任务有档位（现在**每个任务都有** ✓），
**任务级怎么改都改不动** ✗（两条写在档位系统之前的老测试就是这么红的 ✓）。

**还有一条闸** ✓：conftest 把 `DEEPSEEK_BASE_URL` 指到 `http://127.0.0.1:9` ✓（丢弃端口 ✓）——
**任何测试意外联网都会立刻失败** ✓✓，而不是打 6 分钟 + 花钱 ✗。

**规律**：凡是「A 处声明、B 处消费」的东西（任务↔环境变量 ✓、`.env`↔测试 ✓），
**改一处就要 grep 另一处** ✓。而且**测试必须与用户的 `.env` 隔离** ✓（conftest 的两个 autouse fixture 就是干这个的 ✓）。

**⑥ 全红时第一步要什么** ✓：让用户跑

```powershell
.\.venv\Scripts\python.exe -c "import app.main; print('import ok')"
.\.venv\Scripts\python.exe -m pytest -x -q 2>&1 | Select-Object -First 40
```

**这台机器上 agent 跑不了 pwsh** ✗（`0xC0000142` ✓）—— **用户的终端输出是唯一的眼睛** ✓。
**别猜** ✗：全红 = setup/导入 阶段崩 ✓，第一条报错直接写着文件名和行号 ✓。

**⑦ 后端做完了 ≠ 做完了** ✓（2026-10-05 由用户发现）

`deadline_breakdown` 的后端**全都在** ✓（suggest / apply 两个端点 ✓ + 8 条测试 ✓），
但**前端一个调用都没有** ✗ —— 用户找不到入口 ✓，功能等于不存在 ✓。

→ **加后端功能时要同时问一句："用户从哪儿按到它？"** ✓
（前端契约测试只能证明"引用的东西都在" ✓，证明不了"有没有入口" ✗。）

**⑧ ⚠️ 用任何一个名字之前，先确认它在作用域里** ✓

2026-10-05 这一类错误**犯了两次** ✗，每次都让用户白跑一轮（~3 分钟 ✓）：

| 用了一个不存在的名字 | 后果 |
|---|---|
| `config.LLM_DEADLINE_BREAKDOWN_PROVIDER` ✗（常量没加 ✓） | **572 条全红** |
| `local_datetime_to_utc_iso` ✗（在测试里忘了 import ✓） | 2 条红 |

**规则** ✓：写新代码时用到 `config.X` / 某个 helper ✓ →
**先 grep 一眼**（`grep "def X\|^X ="` 或看该文件的 import ✓）✓。
**5 秒的事，换用户 3 分钟** ✓。

---

## 7. 红线（改代码前先看这一节）

1. **migration 只追加，已发布的脚本永不修改** ✗。改了要同步 `scripts/smoke_test.py` 的 `EXPECTED_SCHEMA_VERSION`（当前 v19）
2. **别动 `assembler.py` 里的块顺序** ✗ —— 那是 KV 缓存（= 钱 ✓）
3. **别把 `behavior_rules.md` 改回编号条目格式** ✗ —— 那会让模型复述「我是个虚拟助手」✓（R11 ✓）
4. **`style.css` 里 `[hidden]` 的全局护栏别删** ✗ —— 同一个坑（`display` 盖掉 `hidden`）踩过 **3 次** ✓
5. **别删 `main.py` 里的 `UTF8JSONResponse`** ✗ —— 它给所有 JSON 响应加上 `charset=utf-8` ✓。
   没有它，部分 REST 客户端（例如 Windows PowerShell）可能按错误字符集解码中文
6. **`.env` 与 `storage/` 已在 `.gitignore` 里** ✓ —— 别把 Key 或用户的库提交进去 ✗
7. **`password_matches()` 和 `_matches()` 不许合并** ✗ —— 它们比的是**两个不同的东西**
   （前者比**密码** ✓、后者比 **token / HMAC** ✓）。合并过一次，
   后果是"**登录永远失败**" ✓（2026-10-05 ✓）
8. **历史消息带时间戳之后，任何拿历史当检索词的地方都要 `strip_stamp()`** ✓
   —— 否则面板检索结果和真实请求**对不上** ✗（`build_context_plan()` 已经做了 ✓）

---

## 8. 文档谁说了算

```
PRIVACY_RULING.md = DESIGN_RULINGS.md = MODEL_USAGE_SPEC.md
        >  DEVELOPMENT_PLAN.md  >  其它
```

⚠️ **但"实现状态"只有一个真相来源：`TODO.md` 的 G12/G13/G14 ✓**（按日期追加 ✓）。
`docs/**` 里的进度信息**可能落后** ✗ —— 设计窗口自己都因此误判过两次 ✓（以为 S1 没做完 ✓、以为 R6 没做 ✓）。

**改了实现状态就写进 `TODO.md` 并带日期** ✓（R16 ✓）。

---

## 9. 已定的决定（别重复讨论 ✗）

| # | 决定 | 日期 |
|---|---|---|
| D1 | **隐私标记（R-5/R-6 那套按条标记）不做** ✗ —— 用户选"所有内容上云"，标记价值归零 | 2026-10-04 |
| D2 | 不接第二家供应商（三档表单留着，随时能填 ✓） | 2026-10-04 |
| D3 | 不做微调 ✗（硬件不支持，收益小） | 2026-10-04 |
| D4 | 本地模式保留 | 2026-10-04；**2026-11-15 已由用户决定废止并移除** |
| D5 | 云端 / 本地两种模式分离 | 2026-10-05；**2026-11-15 已由用户决定移除本地模式** |
| R9 | 删死代码 `truncate_text` / `take_recent_messages` ✓ | 2026-10-04 |
| R10 | `try_local_model.py` 默认模型改 `qwen2.5:3b` ✓ | 2026-10-04 |
| R15 | DDL 拆解独立任务 `deadline_breakdown` ✓ | 2026-10-05 |
| R16 | 状态追加**必须带日期** ✓ | 2026-10-04 |

**R6（同方向证据不建新一代印象）查证：早就实现了** ✓（`impression.py` 的 `add_supporting_evidence` + `counts["supported"]` ✓）。

---

## 10. 剩下的债（都不影响使用 ✓）

| # | 债 | 严重度 |
|---|---|---|
| 1 | **流式聊天不过客服腔护栏** ✗（`tone_service` 只覆盖非流式 ✓） | 🟡 有意的取舍，见 §5⑤ |
| 2 | `app/**/*.py` 注释里约 **100 处「她」** ✗ | ⚪ 纯注释 ✓ |
| 3 | 前端**不测交互** ✗（只做静态契约 ✓） | 🟡 点按 bug 靠人发现 ✓ |
| 4 | 没有 CI ✗ | ⚪ 单机应用可接受 ✓ |
| 5 | `CONTEXT_TOTAL_BUDGET = 4000`；记忆增长后需要重新评估预算 | 🟡 |
| 6 | 真实模型连接与浏览器人工验收 | 🟡 需在目标设备和有效 API Key 下完成 |

---

## 11. 设计资料同步状态

本次提供的源码汇总包不包含原始 `docs/` 设计资料、`USER_GUIDE.md`，因此本次没有直接修改这些未提供的原文件。合并回原仓库时，应从原仓库取回并同步以下文档：

- `MODEL_USAGE_SPEC.md`：移除 Ollama Provider、本地模型档、`LLM_LOCAL_ONLY` 和回退行为，更新为 OpenAI 兼容服务的唯一调用路径。
- `PRIVACY_RULING.md`：将原本地模式相关决定标记为 2026-11-15 已废止；保留“AI 请求会发送到配置服务端点”的披露。
- `LOCAL_MODEL_PLAN.md`：标记为历史方案 / 已废弃，不再作为当前开发规范。
- `USER_GUIDE.md`：删除 Ollama 安装和 AIRI 桌宠配置教程，补充模型服务配置、隐私边界和 Android / 手机 REST 使用说明。

本次已同步更新随源码包提供的 `README.md`、`PACKAGE_README.md`、`PROJECT_STATUS.md`、`TODO.md` 和 `ACCEPTANCE.md`。

---

## 12. 下一步

1. 将本次清理后的 `backend/` 与文档合并回原始 Git 仓库；先备份原仓库和 `backend/storage/`，不要覆盖 `.env` 或数据库。
2. 从原仓库取回未包含在本次上传包中的 `USER_GUIDE.md` 与 `docs/`，按 §11 同步。
3. 清理旧 `.env` 的本地模型变量，并为 `roleplay`、`analysis`、`vision` 三档配置 OpenAI 兼容服务。
4. 在真实设备上完成模型连接、聊天、图片理解、记忆抽取和手机访问的手工验收。
5. 在原 Git 仓库检查 Git 历史是否曾包含 API Key、数据库或私人图片；源码包无法代替历史扫描。
