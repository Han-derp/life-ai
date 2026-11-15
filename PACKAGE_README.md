# Life AI（分享版）

一个运行在个人电脑上的生活助手：日程、DDL、习惯、番茄钟、时间轴、行动计划、长期记忆和 AI 行动搭档。

**生活数据保存在本机；AI 请求发送到你在设置中配置的模型服务地址。** 如果使用第三方云服务，聊天上下文、记忆、生活数据摘要和图片内容可能离开本机。请在使用前确认服务商的数据处理政策。

## 三步跑起来（Windows）

1. 安装 Python 3.11 或更高版本，并勾选 `Add python.exe to PATH`。
2. 在 `backend/` 下创建虚拟环境并安装依赖：

   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

3. 编辑 `backend/.env`，填写 `DEEPSEEK_API_KEY`，然后启动：

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

   浏览器打开 `http://127.0.0.1:8000`。

**不填 API Key 也能使用**日程、DDL、习惯、番茄钟、时间轴等非 AI 功能；聊天、AI 计划、记忆抽取和图片理解等 AI 功能需要可用的模型服务。

## 数据在哪

数据保存在 `backend/storage/`：

| 路径 | 内容 |
|---|---|
| `app.db` | 生活数据、聊天记录、记忆与设置 |
| `images/` | 聊天中上传的图片 |
| `backups/` | 设置页导出的备份 ZIP |

删除 `backend/storage/` 会清空本地数据，请先备份。备份与恢复入口在设置页。

## 手机也能用

- 同一个 Wi-Fi：手机访问 `http://<电脑的局域网 IP>:8000`。
- 设置页启用访问密码；不要在不可信的公共网络上直接暴露明文 HTTP 服务。

## 出问题对照表

| 现象 | 原因 / 怎么办 |
|---|---|
| 页面写「AI 未配置」 | 检查 `backend/.env` 中的 `DEEPSEEK_API_KEY`，或对应模型档的 API Key |
| 模型连接失败 | 检查 Base URL、API Key、模型名与服务商兼容性；在设置页使用「测试连接」 |
| 发现旧 Ollama 配置提示 | 参照 `MIGRATION_OLLAMA_AIRI_REMOVAL.md` 清理旧环境变量并保存三个模型档 |
| 端口已被占用 | 关闭已有服务进程，或更换端口 |
| 想彻底重来 | 先导出备份，再停止程序；不要误删仍需保留的 `backend/storage/` |

## 当前版本边界

- 已移除 Ollama、本地模型开关和本地模型实验脚本。
- 已移除 AIRI 专用兼容端点和 pet 状态模块。
- 保留 Life AI 自身 REST API（`/api/v1/`），供网页和后续 Android 客户端复用。
- 不包含第三方桌宠运行时。
