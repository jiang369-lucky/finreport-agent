## PostgreSQL 部署与迁移

当前运行后端已改为 PostgreSQL 17.6，Python 驱动为 psycopg[binary] 3.2.9。数据源与模型的截止日仍为 2025-08-31。

### 本次交付状态

本次只更新代码和部署配置，没有启动 PostgreSQL、执行数据迁移或运行数据库回归测试。之前 31 项回归检查及 MCP/HTTP 验证属于 SQLite 版本，不能作为 PostgreSQL 版本已验证的证据。现有 .venv 需要重新执行 setup.ps1 安装新增驱动。

### 本机 Python 应用与 Docker 数据库

1. 执行 .\setup.ps1 安装锁定依赖，生成本地数据库配置；现有通义千问配置会保留。
2. 之后需要部署时，确保 Docker Desktop 已运行，再执行 .\start-db.ps1。
3. start-db.ps1 仅启动数据库，等待就绪，初始化 schema，并在目标为空时导入 data/research.sqlite3。
4. 在 .env 填入 DASHSCOPE_API_KEY 后，执行 .\start.ps1。未填密钥时仍可使用证据检索模式。

数据库端口默认仅绑定 127.0.0.1:55432，数据保存在 Docker 命名卷 postgres_data。不要执行带 --volumes 的删除命令，除非确实要删除数据库内容。

### 应用和数据库一起容器部署

已提供 Dockerfile、compose.yaml 和 .dockerignore；应用镜像不包含 .env 密钥、原始数据或本机虚拟环境。部署时挂载现有 data/ 与 logs/。

    python -m scripts.configure_database
    docker compose --profile app up -d --build

容器中的 DATABASE_URL 指向 db:5432；本机 Python 的 URL 指向 127.0.0.1:55432。应用监听宿主机 127.0.0.1:8765。Docker Compose 使用固定 PostgreSQL 17.6 与 Python 3.11.9 镜像版本，不使用 latest。

### 迁移规则

- scripts/migrate_sqlite.py 以只读方式打开原 SQLite 文件，在 PostgreSQL 的单个事务中使用 COPY 批量导入。
- 仅向空 schema 导入；目标非空时停止，避免覆盖已有内容。失败会回滚本次导入。
- 保留会话、完整问答、摘要、运行记录和工具结果；turns/runs 的原始顺序迁移为 seq，并恢复 identity 的后续计数。
- 各表导入后核对行数和内容摘要，通过后提交并写入 data/postgres-migration.json。
- 原 SQLite 文件和其中已停用的旧长期笔记表保留在本地，不删除；运行后端不再使用 SQLite。
- 新版保持原 ISO 日期字符串及 JSON 文本序列化格式，数据库索引覆盖公司/报告期/披露日、指标候选、会话顺序和工具归档。中文相关性排序仍由 Python BM25 完成，没有声称 PostgreSQL 原生全文索引实现中文 BM25。

### 之后的验证入口

部署 PostgreSQL 后再执行：

    .venv\Scripts\python.exe -m unittest tests.test_core -v
    .venv\Scripts\python.exe -m scripts.mcp_probe

回归数据写入单独的 finagent_test schema，不清空正式 finagent schema。真实模型联调和评测继续等待用户本地配置密钥。

