# Life AI

Life AI 是一套本地保存生活数据、通过可配置模型服务提供 AI 能力的个人行动助手。
它管理日程、DDL、习惯、番茄钟、实际时间轴和长期记忆；AI 结合这些信息理解情况、提出建议，但**不会绕过用户确认直接修改生活数据**。

> **项目状态与开发约束** → [PROJECT_STATUS.md](PROJECT_STATUS.md)
> **手工验收流程** → [ACCEPTANCE.md](ACCEPTANCE.md)
> **本次 Ollama / AIRI 移除说明** → [MIGRATION_OLLAMA_AIRI_REMOVAL.md](MIGRATION_OLLAMA_AIRI_REMOVAL.md)

## 功能概览

| 模块 | 功能 |
|---|---|
| 生活数据 | 日程、DDL 与子任务、习惯、番茄钟、实际时间轴 |
| 行动计划 | AI 草拟行动方案、用户确认、步骤执行与实际耗时记录 |
| 长期记忆 | 原话、事实观察、印象判断、标签与证据链 |
| AI 搭档 | 流式聊天、图片理解、每日复盘、主动联系、基线预警 |
| 数据管理 | JSON 导出、完整 ZIP 备份、记忆导入 |
| 多设备访问 | 浏览器访问、手机 / 平板通过同一 REST API 使用、可选访问密码 |

## 当前 AI 架构与隐私边界

当前版本**只内置 OpenAI 兼容模型 Provider**，通过三档配置分别处理陪聊、分析和视觉任务。默认配置使用 DeepSeek；也可以为各档填写其他 OpenAI 兼容服务的 Base URL、API Key 和模型名。

- 不再包含 Ollama Provider、全本地模式或本地模型实验脚本。
- 不再包含 AIRI 专用 OpenAI 兼容入口或 pet 状态接口。
- Life AI 的生活数据与图片仍保存在本机 `backend/storage/`。
- AI 请求会发送到对应模型档配置的 `base_url`。若该地址属于第三方云服务，发送的内容会离开本机；上下文可能包含当前对话、记忆、日程 / DDL 等生活数据，以及用户发送的图片。
- 如果配置的是自建模型服务，请自行确认该服务的部署位置、日志和数据留存策略。
- 不配置 API Key 时，日程、DDL、习惯、番茄钟、时间轴等非 AI 功能仍可使用；AI 功能不可用。

## Windows 环境准备

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 backend/.env，填写 DEEPSEEK_API_KEY
```

启动：

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 本机浏览器：`http://127.0.0.1:8000`
- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`GET /api/v1/health`
- 手机 / 平板需与电脑在同一网络，并在设置页启用访问密码；共享网络不建议使用明文 HTTP。

## 测试与自检

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m scripts.smoke_test
```

测试数量以实际 `pytest` 输出为准。当前数据库 Schema 版本为 v19；Smoke Test 同时检查核心表、索引、外键、WAL 和时区。

## 核心设计原则

1. 数据库保存事实，AI 不作为事实来源。
2. Plan 与 Reality 分离。
3. AI 只读现实数据并提出建议；落地操作由用户确认。
4. 记忆观察必须能追溯原始出处。
5. 数据库 Migration 只追加，不修改已经发布的迁移。
6. API、Workflow、Service、Database 保持分层；LLM Provider 与业务 Service 同级。

## 文件地图

| 文件 | 用途 |
|---|---|
| `PROJECT_STATUS.md` | 当前架构、模块地图、约束和已知问题 |
| `ACCEPTANCE.md` | 手工验收与发布前检查 |
| `TODO.md` | 历史开发记录与按日期追加的状态 |
| `MIGRATION_OLLAMA_AIRI_REMOVAL.md` | 旧版本升级与配置清理说明 |
