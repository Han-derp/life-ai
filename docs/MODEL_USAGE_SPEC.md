# 模型使用方式（OpenAI 兼容三档架构）

## 当前权威规范（2026-11-15 清理后）

**只有本节是现行模型架构依据。下方「历史正文」整体失效**，其中的“当前有效”“唯一依据”“不得更改”、实施清单及隐私承诺均不再生效；与本节冲突时以本节为准，不得按历史正文恢复已移除功能。

- 模型调用统一使用 `OpenAICompatProvider`；`deepseek` / `openai` 是兼容配置别名，不是对具体服务商、模型可用性或能力的保证。
- 保留三档可选覆盖：`LLM_TIER_<TIER>_PROVIDER / _BASE_URL / _API_KEY / _MODEL`。未覆盖的字段沿用默认配置（兼容保留 `DEEPSEEK_*` 命名）。配置地址、Key、模型名必须由用户向所用服务核实。

| 档 | 任务 | 边界 |
|---|---|---|
| `roleplay`（陪聊） | `chat` / `proactive` / `review` / `deadline_breakdown` | 聊天图片随聊天请求发给此档；需要用户配置支持图片输入的兼容模型 |
| `analysis`（硬核） | `extract` / `impression` | 记忆抽取与印象归纳会向此档发送所需原话、事实等数据 |
| `vision`（独立 OCR） | `vision` | 仅独立图片转文字；不是聊天图片的自动路由或回退档 |

- 已移除 Ollama Provider、本地模式切换、模型发现、本地失败转云端回退和 AIRI/pet 接口；不再提供“一键完全离线”或“完整聊天记录永不出本机”的承诺。
- 数据库存于本机不等于模型处理留在本机。聊天上下文、记忆、生活数据摘要、原话和图片可能发送至对应档的 `base_url`；后台抽取、主动联系等同样适用。实际隐私边界取决于端点和部署，不能仅凭档名或配置别名判断。
- 聊天直接看图与独立 OCR 是两条路径。后台图片记忆抽取也使用 `vision`，识别结果可能自动进入记忆，并非必有用户确认；不应把 `vision` 当作所有图片聊天的模型。
- 历史正文列出的品牌型号、停用日期、视觉能力、费用和性能数据仅为旧记录，**不作为现时有效信息**；使用前核实所选服务的最新文档和实际连接结果。
- 迁移步骤见 [移除与迁移说明](<../MIGRATION_OLLAMA_AIRI_REMOVAL.md>)；不得覆盖用户真实配置或存储目录。

---

## 历史正文（整体失效，仅留作背景记录，禁止照此操作）

> 下列旧规范保留原文供追溯，不是现行操作说明或实现依据。

> **本文是模型层的唯一依据。** 实现窗口照此执行，不要自行更改分工。
> 设计背景见 `docs/LOCAL_MODEL_PLAN.md`（含三轮实测数据），本文只讲"怎么做"。
>
> 生成时间点：本地模型三轮实测完成之后。项目当时已有：`personas/` 目录（S3 部分完成）、
> `app/prompts/proactive.md`、`scripts/try_local_model.py`、`scripts/local_latency_test.py`。

---

## 0. ❗ 先做这一件事：你的模型名已停用

**实测发现（2026 查证）**：`deepseek-chat` / `deepseek-reasoner` 已于 **7 月 24 日停用**。
项目 `backend/.env` 里现在写的是：

```env
DEEPSEEK_MODEL=deepseek-chat        # ❌ 这个模型名已经停用
```

**必须改。** 当前可用模型与选择依据：

| 模型 | Vision | 每百万 token 输入（缓存未命中） | 输出 | 用途 |
|---|---|---|---|---|
| **`deepseek-flash`** | ✅ **支持** | 峰时 $0.3 / 闲时 $0.15 | 峰时 $1.2 / 闲时 $0.6 | **本项目的聊天 + 印象归纳 + 视觉** |
| `deepseek-v4-pro` | ❌ 不支持 | 峰时 $1.32 / 闲时 $0.66 | 峰时 $3.96 / 闲时 $1.98 | 不用 |

**结论：统一用 `deepseek-flash`。** 理由：
1. 它**支持视觉**（v4-pro 不支持），而你需要发图片
2. 便宜一个数量级，且你的任务（人格对话、结构化抽取）不需要 pro 的推理深度
3. 缓存命中价 $0.003–0.006 / 百万 token，**几乎免费**

峰时 = UTC 周一至周五 01:00–04:00 与 06:00–10:00；
**中国时间 = 09:00–12:00 与 14:00–18:00 的工作日**。其余时间（含整个周末和法定假日）都是闲时。

```env
# 修改后
DEEPSEEK_MODEL=deepseek-flash
```

⚠️ **改完必须验证**：跑一次聊天，确认不再返回模型不存在的错误。

---

## 1. 冻结的分工：按任务分派 Provider

### 1.1 分工表（**不得更改**）

| 任务标识 | 做什么 | 用哪个 | 理由 |
|---|---|---|---|
| `chat` | 生成她的回复 | **`deepseek-flash`（云端）** | 实测首字延迟 64.6s，本地不可用（见 §2） |
| `extract` | 把聊天拆成 L2 结构化事件 | **本地 Ollama（`qwen2.5:3b`）** | **它要读每一句原话，是隐私最敏感的一步**；且**异步**，60 秒无所谓 |
| `impression` | 从事件归纳 L3 印象 | **`deepseek-flash`（云端）** | 3B 做不了多事件推理；记忆库建立在"不许编造"上 |
| `vision` | 图片 → 文字（OCR） | **`deepseek-flash`（云端）**，本地 VL 为可选回退 | 见 §3 |
| `review` | Daily Review | **`deepseek-flash`（云端）** | 需要判断力，且非高频 |

**关键：`extract` 走本地，是这套方案隐私成立的理由。**
完整聊天记录永不出本机；离开本机的只有【提炼后的记忆摘要】+【用户刚打的那一句】。

### 1.2 代码结构

```
backend/app/llm/
├── base.py          LLMProvider 抽象（**需要扩展**，见 §1.3）
├── deepseek.py      现有实现（需小改：支持 json_mode / vision）
├── ollama.py        ★ 新建
└── factory.py       ★ 新建：get_provider(task)
```

```python
# app/llm/factory.py
TASK_PROVIDERS = {
    "chat":       ("deepseek", DEEPSEEK_CHAT_MODEL),
    "impression": ("deepseek", DEEPSEEK_CHAT_MODEL),
    "review":     ("deepseek", DEEPSEEK_CHAT_MODEL),
    "vision":     ("deepseek", DEEPSEEK_VISION_MODEL),
    "extract":    ("ollama",   OLLAMA_MODEL),
}

def get_provider(task: str) -> LLMProvider:
    ...
```

`.env` 新增：

```env
LLM_CHAT_PROVIDER=deepseek
LLM_EXTRACT_PROVIDER=ollama
LLM_IMPRESSION_PROVIDER=deepseek
LLM_REVIEW_PROVIDER=deepseek
LLM_VISION_PROVIDER=deepseek

OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_NUM_CTX=4096          # ❗ 必须显式设，Ollama 默认只有 2048
OLLAMA_TIMEOUT_SECONDS=300   # 本地抽取慢，超时放宽

DEEPSEEK_MODEL=deepseek-flash
```

### 1.3 `LLMProvider` 抽象需要扩展

现在只有：

```python
def chat(self, messages, temperature=None) -> str
```

**记忆抽取需要严格 JSON，视觉需要传图片**，所以扩展为：

```python
class LLMProvider(ABC):
    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        temperature: float | None = None,
        json_mode: bool = False,        # ★ 新增：强制结构化输出
    ) -> str: ...

    @property
    def available(self) -> bool: ...     # ★ 新增：统一能力探测（供回退判断）
```

⚠️ **保持同步实现**（现有 `DeepSeekProvider` 用 `httpx.post` 同步）。
**不要引入 `openai` SDK，也不要把调用链改成 async** —— 现有 service 层全是同步的。

**Ollama 的关键优势**：`/api/chat` 支持 `"format": "json"`，**在解码层强制 JSON**，
比"调模型 → 解析失败 → 再 repair 一次"（`deadline_service.py` 现有做法）可靠。

---

## 2. 为什么 `chat` 不能用本地（实测数据，供实现窗口理解边界）

三轮实测（详见 `LOCAL_MODEL_PLAN.md` §0）：

| 模型 | 生成速度 | 首字延迟 | 人格跟随 |
|---|---|---|---|
| `qwen2.5vl:7b` | 7.6 tok/s | 约 12s（688 token） | ❌ 说"我是通义千问""我没有情感" |
| `qwen2.5:3b` | **18.3 tok/s** ✅ | — | ⚠️ 更好，但会推开亲密、下命令、躲问题 |
| **`qwen2.5:3b` + 3781 token 上下文** | 11.1 tok/s | **64.6s** ❌ | ✅ **质量够用** |

**决定性数字：提示词处理只有 61 tok/s**（比生成回复还慢 → 内存带宽瓶颈，核显与 CPU 共享）。

**死结算术**：

```
人格 + 规则 + 禁止项 = 967 token（已是最精简）→ 约 16 秒
+ 记忆 ~1500            → 约 25 秒
+ Today Context ~2500   → 约 41 秒
+ 最近聊天 3781（实测）  → 62 秒
```

> 上下文短 → 首字快 → **但她没有记忆和人格 → 人机感**
> 上下文长 → 她像个人 → **但每句等 60 秒**
> **与模型大小无关。**

**⚠️ 但注意：本地模型的质量是够的。** 长上下文那次，她正确引用了记忆里的事实
（"写实验报告""考试周熬夜""越晚越焦虑"）并给出了自己的判断，没有编造。
**问题只在速度。** 换有独显的机器（≥8GB 显存）这条路就通了 —— 而 `get_provider(task)`
让那次切换只改 `.env`。

---

## 3. 图片：模型可以直看图；只有「入库」才需要转文字

> **§3.3 已于 2026-10-04 修正（原表述自相矛盾，由实现窗口发现）。**
> 原文写"图片一律先转成文字，再进入**对话**流程"—— 但同节又在教怎么直接把图发给模型。
> 那条件是 `LOCAL_MODEL_PLAN` §3.1 针对**本地 7B VL 模型**写的；R4 推翻该前提
> （云端 `deepseek-flash` 原生支持视觉）时**没有回头改这一句**。现修正如下。

### 3.3 ✅ 修正后的铁律（两条，不得违反）

**① 分界线是「要不要落库」，不是「能不能看图」。**

| 场景 | 能否让模型直接看图 | 为什么 |
|---|---|---|
| **聊天**（你发图问他，或随口发一张） | ✅ **可以，直接把图发给模型** | 云端模型本身能看图；绕一层 OCR 只会丢信息、多一次失败点 |
| **写入记忆**（图片内容要变成一条长期事实 / 日程） | ⚠️ **必须先转成文字** | L1 是文本、要被 FTS5 检索、要被抽取引用 —— 二进制没法追溯 |
| **印象归纳判断** | ❌ **不由视觉模型做** | VL 强项是识别不是判断；且印象必须能追溯到文本事件 |

**为什么"聊天直看图"是对的**：`build_image_message()` 已经实现了两条路
（OpenAI block 数组 / Ollama 裸 base64）。**把图先 OCR 成文字再问，等于把一次
模型能直接完成的事拆成两步，而 OCR 那一步会引入错误。**

**为什么"入库必须转文字"仍然成立**：L1 是"只追加、永不被 AI 改写"的事实源，
它需要能被检索、被引用、被追溯（不变量 5）。一张图片本身没法承担这些。

**② 图片转出的文字，必须先经用户确认才能写入日程/记忆。**
理由：识别会漏会错，而记忆库建立在"不许编造"上（设计原则 3）。

**管道（R4 定的，指的是落库路径）**：

```
图片 → 转文字 → 用户确认 → L1 原始资料 → 正常 L2 抽取 → 写库
```

⚠️ 这条管道**约束的是落库**，不约束"聊天时能不能直接看图"。两者不矛盾。

### 3.1 云端 `deepseek-flash`（**推荐**）

**它原生支持视觉**，无需下载任何模型：

- 支持格式：JPEG / PNG / GIF / WebP（**按文件内容识别，不信任文件名**）
- **每张图上限 1024 token**（会自动缩放：小于约 544×544 的放大，大于的缩到约 1300×1300 等价像素）
- 三种传法：base64 内联 / 外部 URL / Files API
- **图片只能放在 `user` 消息里**（放 system 或 assistant 会 400）
- 可加 `detail: "low"`（缩到 512×512）**更便宜更快** —— OCR 课表类建议用 `low`

```python
# content 从字符串变成 block 数组
messages = [{
    "role": "user",
    "content": [
        {"type": "text", "text": "把这张课表转成文字，只输出表格内容"},
        {"type": "image_url",
         "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}},
    ],
}]
```

### 3.2 本地 `qwen2.5vl:7b`（可选回退）

- Ollama 的 `/api/chat` 通过 `images: [base64]` 字段支持图片
- **隐私更好**（图片不出本机），但：首字延迟高、OCR 质量未实测
- **它是你已下载的 6GB 模型，别浪费**：作为"不想把图发给云端"时的选项

⚠️ **本地视觉有两个已踩过的坑**（已修，记在这里防复发）：
1. **历史图片不能再按 OpenAI 形状发给 Ollama** —— `content` 必须是字符串 + `images` 裸 base64。
   曾导致整个请求 400（`cannot unmarshal array into ... content of type string`）。
2. **本地模式不要重发历史图片** —— VL 模型一看到历史里有图就去描述它，
   结果两个不同问题得到同一段描述。

---

## 3.4 三档模型（roleplay / analysis / vision）

> **本节是回应实现窗口的"追认三档"请求。** 三档已在 `config.TASK_TIER` 实现，
> 但从未写进规范 —— 现补入，作为 §1.1 的**上层抽象**。

### 三档是什么

**它们不是"三个聪明程度"，是三种不同的用法。** 这是理解三档的关键：

| 档 | 用途 | 默认 provider | 为什么这样定 |
|---|---|---|---|
| **`roleplay`**（陪聊） | 聊天 / 主动联系 / Daily Review | deepseek（云端） | 要**说人话**、要有温度；本地 64.6s 首字延迟不可用 |
| **`analysis`**（硬核） | 记忆抽取 / 印象归纳 | **ollama（本地）** | 要**听话、严格 JSON、不编造**；而且是异步的，慢无所谓 |
| **`vision`**（看图） | 图片识别 | deepseek（云端） | 要**多模态**；本地 VL 太慢 |

### 任务 → 档的映射（`config.TASK_TIER`）

```python
TASK_TIER = {
    "chat":       "roleplay",
    "proactive":  "roleplay",
    "review":     "roleplay",
    "extract":    "analysis",
    "impression": "analysis",
    "vision":     "vision",
}
```

### 优先级（高 → 低）

```
1. 全本地模式（LLM_LOCAL_ONLY）      → 一律 ollama，且绝不回退云端
2. 这一档的配置（LLM_TIER_<TIER>_*）  → 界面上的三行表单写的就是它
3. 旧的逐任务 .env（LLM_<TASK>_PROVIDER） → 文档默认值
```

**第 2、3 条的顺序是刻意的**：档里填了就以档为准；没填就回落到逐任务配置。
**所以老配置不用改一行就能继续跑**（有测试锁这条）。

### 每档可独立配置的字段

```env
LLM_TIER_ROLEPLAY_PROVIDER / _BASE_URL / _API_KEY / _MODEL
LLM_TIER_ANALYSIS_PROVIDER / _BASE_URL / _API_KEY / _MODEL
LLM_TIER_VISION_PROVIDER   / _BASE_URL / _API_KEY / _MODEL
```

**留空即回落** `DEEPSEEK_*`。这意味着**换供应商 = 填三行**（用户决定 D2：先不接第二家，
但表单留着）。

### ⚠️ 两条不得违反

1. **不要按"哪个模型更聪明"去改 `TASK_TIER`。** 分工依据是两条实测结论：
   **隐私边界**（唯一读完整原话的 `extract` 跑在本机）和**首字延迟**。
2. **不要给"同步交互"的任务选 `analysis` 档（本地）。**
   本地单次 60 秒起步 —— 用户点一下就在等结果，这是不可接受的。
   （实现窗口在 C7 已正确识别这一点：`deadline_service` 拆解虽是结构化抽取，
   但**它是同步的**，所以走云端。）

### 档级实例缓存的注意点

`get_provider()` 的实例缓存 key 必须带 `tier` —— 两个档用同一模型名时，
不带 tier 会共用实例，而实例上的 `last_usage` 会串（影响 S2 面板的缓存命中显示）。

### 3.3 ⚠️ 铁律（两条，不得违反）

1. **图片一律先转成文字，再进入记忆和对流程。**
   视觉模型**不参与对话和印象判断**。理由：VL 模型强项是"识别"不是"判断"，让它做推理会编造。
2. **OCR 结果必须先给用户确认，才能写入日程/记忆。**
   理由：识别会漏会错，而记忆库建立在"不许编造"上（设计原则 3）。

---

## 4. 成本实测估算（供实现窗口判断"值不值得优化"）

**前提假设**（实现窗口应把这些做成可调参数，并在面板里显示实际值）：
- 每天 50 轮对话
- system prompt 约 1200 token（人格 + 规则 + 记忆摘要 + Today Context）
- 聊天历史约 1500 token
- 她每条回复约 200 token
- 换算：¥1 ≈ $0.14，即 $1 ≈ ¥7.2（按实际汇率调整）

### 4.1 正常情况（**KV 缓存生效**）

| 项 | token/天 | 费用 |
|---|---|---|
| 缓存未命中输入（新历史 + 你当前消息） | 约 1,500 | ¥0.0016 |
| 缓存命中输入（system prompt + 旧历史） | 约 60,000 | ¥0.0003 |
| 输出（她的回复） | 10,000 | ¥0.0043 |
| **小计** | | **约 ¥0.006/天** |
| **每月** | | **约 ¥0.2/月** |

### 4.2 最坏情况（缓存全失效 + 全峰时 + 回复更长）

| 项 | 估算 |
|---|---|
| 每天 | 约 ¥0.4–1.0 |
| **每月** | **约 ¥12–30/月** |

### 4.3 结论

> **即使按最坏情况估，一个月也就几十块人民币。按正常情况，几毛钱。**

**所以"本地模型能不能省钱"这个问题的答案是：省不了多少。**
真正的成本是**时间**（64.6 秒/句），不是钱。

### 4.4 ⚠️ 但有一件事会显著影响费用：缓存

**DeepSeek 的 KV 缓存要求前缀完全匹配。** 项目现在的问题是
`_system_prompt()` 把 `now`（当前时间戳）放在前面 —— **每次请求前缀都变 → 缓存全废**。

**这正是 S1 的修复项**，它同时是：
- **费用优化**（正常 vs 最坏差 20–100 倍）
- **人机感修复**（人格被字符硬切）
- **面板的地基**（`ContextBlock` 分块）

**实现窗口请注意**：`now` 应移到提示词**末尾**，或干脆不发给模型
（她不需要精确 UTC 时间；时间锚点用 `messages.created_at` 就够）。

**验证方式**：面板里显示 `prompt_cache_hit_tokens`，**用数据确认缓存真的生效**，不要靠推测。

---

## 5. 给实现窗口的检查清单

按依赖顺序，逐项打勾。**每项都要有可验证的判据，不要"差不多就行"。**

### 5.1 立即修（阻塞级）

- [ ] `backend/.env` 里 `DEEPSEEK_MODEL` 改为 `deepseek-flash`
- [ ] 跑一次聊天验证不再报"模型不存在"
- [ ] `grep -rn "deepseek-chat" backend/` 确认没有其他地方写死旧模型名

### 5.2 S1 上下文分块（已有进展，继续）

- [ ] `app/ai/context/blocks.py`：`ContextBlock` 结构
- [ ] `app/ai/context/assembler.py`：`get_context_blocks()`
- [ ] `chat_service._system_prompt()` 改为走 assembler
- [ ] **整块丢弃**替代 `truncate_text()` 字符硬切
- [ ] **`now` 移出 system prompt 前部**
- [ ] 判据：连续两次请求，`prompt_cache_hit_tokens > 0`

### 5.3 `LLMProvider` 扩展 + 工厂

- [ ] `base.py` 加 `json_mode` 参数和 `available` 属性
- [ ] `deepseek.py` 支持 `json_mode` 与 **vision（content block 数组）**
- [ ] `ollama.py` 新建（同步实现，用 `httpx`，**不引入 `openai` SDK**）
  - [ ] `format: "json"` 在 `json_mode=True` 时传入
  - [ ] `num_ctx` 显式传（**默认 2048 会导致静默截断**）
- [ ] `factory.py`：`get_provider(task)`
- [ ] 替换三处硬编码单例：
  - `chat_service.py:18`
  - `review_service.py:16`
  - `deadline_service.py:21`
- [ ] 判据：`.env` 里把 `LLM_CHAT_PROVIDER` 改 `ollama` 后，聊天确实走了本地（日志可证）

### 5.4 回退机制（**必须有**）

- [ ] `extract` 调用失败时（Ollama 没启动 / 超时）**自动回退云端**，并记录日志
- [ ] 判据：关掉 Ollama，跑一次聊天 → 聊天正常 + 抽取走云端 + 有日志

### 5.5 视觉（可最后做）

- [ ] 新 API：上传图片 → 返回 OCR 文字（**不直接入库**）
- [ ] 前端：显示识别结果 + **确认按钮**
- [ ] 确认后才写入日程/记忆
- [ ] 判据：未经确认的 OCR 结果绝不落库

---

## 6. 不要做的事（避免被误导）

| ❌ 不要 | 原因 |
|---|---|
| 把 `chat` 换成本地模型 | 实测首字延迟 64.6s |
| 照做"AIRI → Ollama 直连 `localhost:11434`" | **会绕过 life-ai，没有记忆库、没有上下文控制**，正好回到项目最初要摆脱的状态。AIRI 只能当身体（见 `DEVELOPMENT_PLAN.md` S9） |
| 引入 `openai` SDK / 把调用链改 async | 现有 service 全是同步；这是较大改动，且无收益 |
| 让视觉模型参与对话或印象判断 | VL 强项是识别不是判断，会编造 |
| OCR 结果直接入库 | 会漏会错，记忆库禁止编造 |
| 为省 token 把记忆注入砍掉 | 那她就失忆了。要省就**修缓存**（§4.4），不是砍上下文 |
| 现在给旧表加"排除字段" | 临时排除不落库（`exclude_once` 只存当次请求），**等建记忆表时 `excluded_at` 一起加，省一次 migration** |

---

## 7. 一句话总纲

> **云端负责"当下要说得像人"（聊天、印象、视觉），本地负责"长期要记得住"（抽取）。**
> **隐私的边界是：完整聊天记录永不出本机。**
> 而这一切能安全落地，是因为 `get_provider(task)` 让分工**只改 `.env` 就能调整**。
