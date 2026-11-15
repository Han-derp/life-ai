# 定向合并结果（2026-11-15）

## 已完成

- 从 LifeAI_cleaned_2026-11-15.zip 定向更新47个文件、新增1个测试文件，删除14个已确认废弃模块/脚本/测试；178个相同文件不覆盖。
- 实际后端源码、前端、脚本和测试与清理包一致。额外修订 USER_GUIDE.md、docs/MODEL_USAGE_SPEC.md、MIGRATION_OLLAMA_AIRI_REMOVAL.md，修复 README.md 行尾空格。
- 保留真实 backend/.env 和 backend/storage：合并前后及验证结束时检查341个文件SHA-256，全部不变。没有读取或展示密钥文本，没有调用真实模型服务。
- 保留启动脚本、监听地址、访问密码、历史副本及用户原有未提交工作。不提交、不推送Git。

## 本次验证

- 179个Python文件AST语法检查通过。
- 15个JavaScript文件node --check通过。
- 独立副本、独立数据库的Smoke Test通过（数据库结构、索引、时间/时区）。
- git diff --check通过。
- 完整pytest尝试两次，均在临时数据库初始化阶段遭遇Windows临时目录访问问题；不能声称全套测试通过，也不能把环境初始化错误算成业务断言失败。
- 测试副本位于_merge_validation/backend，未复制真实.env、storage；验证启动器禁用网络连接。测试临时文件及诊断记录仅用于本地验证，不应加入发布源码或Git提交。

## 验证环境障碍

Python 3.14 TemporaryDirectory创建的目录无法在当前受限执行环境中打开SQLite数据库或正常清理。移到工作区内仍复现。授权运行Windows权限诊断脚本后，结果NOT_THIS_CLASS、nextAction=stop：未修改权限、无需回滚，未证明属于脚本可自动修复的类别。诊断记录：_merge_validation/permission-recovery/acl-report-87506c22bcce4f2ba10c12f15bea55f9.jsonl。

若继续完整测试，可临时将会话切为完全权限后重跑隔离测试；仍保留网络禁用及不复制真实配置/运行数据。当前未启动真实服务或进行人工验收。

## 启动前注意

真实配置未改动：如仍含LLM_LOCAL_ONLY=true或ollama Provider，新版会拒绝AI调用而非静默上传。按迁移说明自行确认数据去向、清理旧变量（包括启动进程/用户/系统环境中的同名变量），再重启后端。启动脚本保留0.0.0.0监听行为，开放网络前必须确认访问密码与网络访问范围。
