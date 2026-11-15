# Ollama / AIRI / pet 状态模块清理记录（2026-11-15）

## 已删除的运行时代码

- `backend/app/llm/ollama.py`
- `backend/app/api/openai_compat.py`
- `backend/app/api/v1/pet.py`
- `backend/app/workflows/pet.py`
- `backend/app/schemas/pet.py`
- `backend/app/services/llm_notice_service.py`
- `backend/scripts/local_latency_test.py`
- `backend/scripts/try_local_model.py`

## 已删除的专用测试

- AIRI 兼容 API / pet 状态联调测试
- Ollama Provider、云端回退、全本地模式测试
- 本地模型设置测试

保留并重写 Provider 工厂测试、旧配置检测测试、REST UTF-8 测试与集成路由缺失测试。

## 核心行为变化

1. 所有模型任务使用 `OpenAICompatProvider`，仍保留 `roleplay` / `analysis` / `vision` 三档。
2. `LLM_LOCAL_ONLY=true` 会阻止 AI 请求并提示清理旧配置，不会静默把用户以为留在本机的内容发往云端。
3. 旧 `ollama` provider 值会明确报错；设置 API 会返回迁移警告。
4. 记忆抽取不再进行本地失败后回退云端；模型调用失败时消息仍保持待抽取状态。
5. 删除 AIRI `/v1/models`、`/v1/chat/completions` 与 `/api/v1/pet/state`。
6. 保留 Life AI `/api/v1/` REST API、Bearer 访问密码、`UTF8JSONResponse`、图片理解、记忆抽取与异步调度。
7. 本次不修改业务数据库结构，不新增 Migration；Schema 仍为 v19。

## 仍保留的旧配置检测文字

代码中仍会识别字符串 `Ollama` / `ollama`，但只用于检测旧 `.env`、显示升级提示和拒绝已移除的路由；没有 Ollama Provider、模型请求客户端或本地模型运行路径。
