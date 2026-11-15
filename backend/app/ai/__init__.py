"""AI 层（DEVELOPMENT_PLAN v2.0 §4.1）。

当前包含：
- context/  上下文分块与组装（S1）

后续（按方案推进）：
- llm/      Provider 抽象（现仍在 app/llm/，需迁移时再搬，不做一次性搬家）

依赖方向：ai/context → services（查询接口）。
services 里只有 chat 编排会引用本层，业务 service 不得依赖 ai。
"""
