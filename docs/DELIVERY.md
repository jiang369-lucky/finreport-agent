# 项目交付

项目目录：D:\yolo\finreport-agent-2025

## 启动

1. 重新执行 .\setup.ps1 安装新增 PostgreSQL 驱动，保留既有模型配置。
2. 之后部署时执行 .\start-db.ps1，再在 .env 填写 DASHSCOPE_API_KEY 并运行 .\start.ps1。
3. 浏览器访问 http://127.0.0.1:8765 。完整容器部署与迁移说明见 docs/POSTGRESQL.md。

本次按要求只修改代码与部署配置，没有启动 PostgreSQL、执行迁移或运行数据库测试。现有 .venv 需要 setup.ps1 安装新增驱动。

## 已完成

中文财报 40 份 / 10 家公司 / 9,425 页 / 28,143 个检索片段；数据、模型与依赖按 2025-08-31 时间边界选择。

LangGraph harness、4 项官方 MCP stdio 工具、带原文证据的财报研究、Decimal 计算、任务模板、PostgreSQL 会话、近期完整问答与早期摘要、工具原始结果审计回查、中文本地工作台。

此前 SQLite 版本通过 31 项回归检查与真实 MCP/HTTP 验证；该记录不代表本次 PostgreSQL 版本已完成运行验证。本次只进行静态检查。
全部 55 个运行依赖和 3 个安装构建工具的发布时间已核对，记录见 data 下的审计 JSON。

20 道数值参考用于小规模检索诊断；另附 100 道中文任务草稿及 RAG/Agent 对比评测入口。
当前小样本开发题证据页 Recall@5 为 15/15，按公司留出的五题为 4/5。它们不是模型答案准确率。

真实通义千问联调已按用户要求留到其自行配置密钥之后。默认固定历史模型 qwen3-235b-a22b-instruct-2507，不自动换到更晚模型。

详细结构、实现取舍和时间依据见 README.md、docs/ARCHITECTURE.md、docs/TIME_BOUNDARY.md。

## 上下文简化

近期最多保留 4 轮完整问答，更早对话生成最多 1,000 字符的关键摘录摘要；历史问答过长时提前纳入摘要，至少保留最新一轮完整问答。摘要写入 PostgreSQL，完整记录仍可回查。移除了独立长期记忆、跨会话召回与模型侧归档回取工具；原 SQLite 数据文件保留，新增历史版本 PostgreSQL 驱动。真实模型联调仍待本地密钥配置。

## PostgreSQL 与任务模板

正式运行后端已改为 PostgreSQL 17.6，驱动 psycopg[binary] 3.2.9；新增 Dockerfile、compose.yaml、start-db.ps1 和只读源数据迁移脚本。三类流程统一称为任务模板，不再把自定义模板写成 Skill。代码已交付，数据库迁移和运行验证未执行。
