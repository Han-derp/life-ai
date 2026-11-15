# Ollama / AIRI / pet 模块移除说明（2026-11-15）

本次版本按用户决定移除了：

- Ollama Provider、`LLM_LOCAL_ONLY` 全本地模式、Ollama 模型发现与延迟测试脚本；
- 本地失败后回退云端的逻辑与 `llm_notice_service`；
- AIRI 专用 `/v1/models`、`/v1/chat/completions` OpenAI 兼容入口；
- `pet` 状态 API、workflow、schema 及对应测试。

保留：

- `OpenAICompatProvider` 与 `deepseek` / `openai` 配置别名；
- `roleplay`、`analysis`、`vision` 三档及按任务路由；
- Life AI 自身 `/api/v1/` REST API，供网页和后续 Android 客户端复用；
- `UTF8JSONResponse`、Bearer 访问密码、图片理解、记忆抽取与异步调度。

## 升级现有工作目录

1. 先停止后端，备份 [真实后端配置](<backend/.env>)、`backend/storage/` 和原 Git 仓库。**不要以示例配置、清理包或其他工作目录的内容覆盖真实 `.env` / storage；不要删除数据库或图片目录。** 本次清理不自动修改用户数据。
2. 在原配置中定向删除以下变量（如存在），保留其他配置和密钥：
   - `LLM_LOCAL_ONLY`
   - `OLLAMA_BASE_URL`、`OLLAMA_MODEL`、`OLLAMA_NUM_CTX`、`OLLAMA_TIMEOUT_SECONDS`
   - 所有值为 `ollama` 的 `LLM_*_PROVIDER` / `LLM_TIER_*_PROVIDER`（或将仍需使用的 provider 字段改为支持的兼容别名）
3. **同时检查系统级、用户级及启动进程中的同名环境变量。** 环境变量可能优先于 `.env`；只清理文件不足以移除旧值。修改系统/用户变量后，应新开终端或重新启动负责拉起后端的进程，避免继承旧环境。
4. **完成清理后重启后端**，再进入设置页，为 `roleplay`、`analysis`、`vision` 三档检查并保存 OpenAI 兼容服务配置。未覆盖字段沿用默认配置，不能因此假定默认地址或模型一定可用。
5. 核实服务商的 Base URL、API Key、模型名、图片输入能力及当前计价；用设置页「测试连接」检查基础连通性，后续再分别验收实际任务。本说明不保证历史文档中的品牌型号、停用日期或能力描述现时有效。
6. 注意：AI 请求会发往对应档配置的 `base_url`。聊天上下文、原话、记忆、生活数据摘要与图片可能离开本机，后台任务亦然；不再承诺“一键完全离线”或“完整聊天记录永不出本机”。

### 三档与图片边界

- `roleplay`：聊天（包括聊天图片）、主动联系、每日复盘、DDL 拆解。聊天发图要求此档模型支持图片输入。
- `analysis`：记忆抽取与印象归纳。
- `vision`：**仅独立 OCR / 图片转文字**，不是聊天图片的替代路由或自动回退。后台图片记忆抽取也会调用此档；结果可能自动进入记忆，不能假定必有人工确认，应检查并纠正识别错误。

### 启动监听与访问安全

启动脚本保留 `0.0.0.0` 监听行为，便于局域网设备访问；**`0.0.0.0` + 空访问密码会使所有可达该端口的人可能访问、修改数据或下载备份**。开放防火墙或远程隧道前先设置访问密码，并限制网络可达范围；只需本机访问时可手动绑定 `127.0.0.1`。模型 API Key 与应用访问密码不是同一种凭据。

本次定向文档更新不运行测试；上述连接检查和实际任务验收是升级部署后的操作建议，不表示已经执行。

## 旧 API 兼容性

以下接口已删除，不再支持旧 AIRI 客户端：

- `GET /v1/models`
- `POST /v1/chat/completions`
- `GET /api/v1/pet/state`

后续客户端应调用 Life AI 自身 `/api/v1/` REST API。Android 客户端可以复用这些接口，不需要重新实现业务逻辑。

## 数据库与迁移

本次代码清理不修改现有业务表结构，不新增 Schema Migration；Schema 版本仍为 v19。旧 `app_settings` 中可能残留的回退提示键不会被当前代码读取，不影响业务数据。
