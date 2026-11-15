# Life AI 结项验收操作单（Ollama / AIRI 清理后）

> 本文件用于手工验收当前版本。自动测试通过不代表真实模型服务、浏览器交互和隐私提示均已人工验收。

## 0. 启动服务

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 `http://127.0.0.1:8000`。需要手机访问时改用 `--host 0.0.0.0`，并在设置页设置访问密码。

## 1. 基础 REST API

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/health | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/settings | ConvertTo-Json -Depth 5
```

判据：

- health 返回 `status = ok`。
- settings 不回显完整 API Key。
- settings 只展示当前任务到 OpenAI 兼容服务的路由，不再出现本地运行模式字段。
- 如果检测到旧 `LLM_LOCAL_ONLY` / `OLLAMA_*` 或 `ollama` provider 配置，设置页应显示迁移提示，而不是静默切换。

## 2. 聊天与模型连接

在设置页配置默认 API Key / 模型，点击「测试连接」。然后在聊天页发送一条中文消息，并验证：

- 能收到模型回复；
- 中文显示正常；
- 聊天记录可刷新后读取；
- 流式输出逐步显示；
- 上下文面板显示本次发送的上下文块和 token 统计；
- 隐私提示说明 AI 请求会发送到配置的模型服务地址。

非流式 API 示例：

```powershell
$body = @{ content = "你好，请简短回复。" } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/chat `
  -Method Post -Body $body -ContentType "application/json"
```

## 3. 记忆抽取与印象归纳

发送一条包含具体事实的消息，例如「今天背了两小时单词」。在设置页点击「现在抽取」，检查：

- 消息进入待抽取队列；
- 成功后生成的观察能追溯到原文；
- 模型服务失败时，消息不会被标记为已抽取，后续可以重试；
- 标签建议与印象归纳流程仍然可用。

## 4. 确认旧集成已移除

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/openapi.json | ConvertTo-Json -Depth 20
```

在 OpenAPI 路径中确认以下路径不存在：

- `/v1/models`
- `/v1/chat/completions`
- `/api/v1/pet/state`

同时确认代码目录中不存在：

- `app/llm/ollama.py`
- `app/api/openai_compat.py`
- `app/api/v1/pet.py`
- `app/workflows/pet.py`
- `app/schemas/pet.py`
- `app/services/llm_notice_service.py`

## 5. 自动测试与 Smoke Test

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m scripts.smoke_test
```

Smoke Test 当前应验证 Schema v19、核心数据表、索引、外键、WAL 和时区。

⚠️ `scripts/integration_test.py` 会向当前配置的数据库写入测试数据；运行前请确认使用的是测试数据库，或先做好备份。

## 6. 发布前安全检查

```powershell
# 在仓库根目录运行
git status --short
git ls-files
git check-ignore -v backend/.env backend/storage/app.db
```

确认 `.env`、数据库、图片、备份、虚拟环境、`__pycache__` 和 `.pyc` 均未被 Git 跟踪。`.gitignore` 不能自动清除已经提交进历史的密钥。
