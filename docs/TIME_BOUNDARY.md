# 时间边界：2025-08-31

这是技术和业务资料的可用时间边界，不是把今天编写的代码或今天标注的评测伪装成过去的成果。

## 可使用的技术

- LangGraph：2024-01-17 已公开；实际锁定 0.6.6。
  https://www.langchain.com/blog/langgraph
- MCP：2024-11-25 公开；采用 2025-06-18 协议及 2025-08-22 上传的官方 SDK 1.13.1。
  https://www.anthropic.com/news/model-context-protocol
  https://modelcontextprotocol.io/specification/2025-06-18
  https://pypi.org/pypi/mcp/1.13.1/json
- 通义千问 qwen3-235b-a22b-instruct-2507：2025 年 7 月发布，固定型号。
  https://help.aliyun.com/zh/model-studio/newly-released-models
- 模型后备名单仅包含固定历史快照与初始 Qwen3-8B；本地模型还需由部署者固定当时权重 revision。
- 其他直接/间接依赖（锁文件共 55 个包）的具体版本与首次上传时间见 data/dependency-audit.json。
- Python 3.11、PostgreSQL、BM25、中文分词、近期对话窗口、早期问答摘要和有限状态工作流均可用截止日前技术实现。

## 不使用的产品/规范

Anthropic Agent Skills 在 2025-10-16 推出；开放标准的更新也晚于本项目边界。因此不使用 SKILL.md 的发现/加载生态，不声称该生态当时存在。
https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills

## 资料边界

只使用巨潮在截止日前披露的 2022–2024 年年报与 2025 年半年报。最晚披露日为 2025-08-30。
文件清单保存公告元数据和原始内容 SHA256。后续同步时重新校验日期和哈希。API 不联网获取当前行情或后来发布的财报。

评测问题根据上述旧资料在本次实现中整理，不引入截止日之后的金融事实。

## PostgreSQL 版本

- PostgreSQL 17.6：2025-08-14 发布，使用固定镜像版本 postgres:17.6-bookworm。
  https://www.postgresql.org/docs/17/release-17-6.html
- psycopg / psycopg-binary 3.2.9：2025-05-13 上传 PyPI；tzdata 2025.2：2025-03-23 上传，均已补入依赖审计与带哈希锁文件。
  https://pypi.org/pypi/psycopg/3.2.9/json
- 容器应用使用 Python 3.11.9；Docker 构建不使用滚动 latest 标签。构建和部署尚未在本次执行。
