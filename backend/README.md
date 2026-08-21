<!--
 * @Author: lihuan
 * @Date: 2026-08-16 22:39:42
 * @LastEditors: lihuan
 * @LastEditTime: 2026-08-17 12:53:13
 * @Email: 17719495105@163.com
-->
# agent-backend

多用户 AI agent 后端：FastAPI + deepagents + PostgreSQL + OpenSandbox 沙箱 + RustFS 对象存储。

## 功能

- 多轮对话（agent 可在沙箱中执行代码、读写文件）
- 多会话管理 + 历史记录（LangGraph checkpointer 持久化，可恢复续聊）
- 文件上传 + RAG 知识库（pgvector 检索，文档存 RustFS）
- 联网搜索（占位工具，供应商待接入）
- 多用户：用户名密码 + JWT；数据按 user 隔离

## 架构

```
FastAPI (SSE) ──> deepagents create_deep_agent (per-request)
                     │ backend = CompositeBackend
                     │   default: 预热 OpenSandbox 沙箱池 (sandbox/pool.py)
                     │   /files/: RustFS 对象存储 (BackendProtocol → boto3)
                     │ checkpointer = AsyncPostgresSaver (线程恢复)
                     └ tools = web_search + search_knowledge_base (pgvector)
PostgreSQL: 业务表 (alembic) + checkpoints 三表 (AsyncPostgresSaver.setup) + pgvector
```

## 运行

```bash
# 0) 依赖与配置
uv sync
cp .env.example .env   # 填 LLM_API_KEY（DeepSeek OpenAI 兼容）、EMBEDDING_*

# 1) PostgreSQL 隧道（远程服务器 124.221.180.74）
ssh -N -L 5432:127.0.0.1:5432 root@124.221.180.74

# 2) 迁移
uv run alembic upgrade head

# 3) 启动
uv run uvicorn main:app --port 8000
```

端点前缀 `/api/v1`，Swagger 见 `/docs`。端到端验收：`bash test/e2e.sh`。

## 配置（.env 要点）

| 变量 | 说明 |
|---|---|
| `DATABASE_DSN` | `postgresql+psycopg://...`（SQLAlchemy 驱动后缀） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容模型（DeepSeek等） |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | 独立 embedding 配置，维度须与 `chunks.embedding vector(N)` 一致 |
| `OPENSANDBOX_*` | 沙箱服务地址 / api key / 镜像 / 预热数 |
| `RUSTFS_*` | S3 兼容端点 / keys / 桶 |

## 运维注意

- **Redis 必填**：流事件、会话单飞、每用户并发上限与 `/stop` 信号全部入 Redis（`app/services/stream_store.py`，`REDIS_URL` 未配置或连不上则启动失败），任意 worker 可 attach/stop 任意会话，单/多 worker 同一条代码路径。沙箱预热池也共享：状态在 Redis（`RedisPoolStateStore`，primary lock 保证只有一个 worker 的 reconciler 预热/回收），预热总数 = `OPENSANDBOX_MAX_IDLE`（多 worker 建议调大，如 workers × 3）；孤儿沙箱清理带互斥锁 + worker 存活租约，只扫真孤儿。
- **沙箱回收**：每个请求从预热池取沙箱、用完即杀；异常/断连由 finally 兜底，服务端 TTL（10 分钟）双保险。
- **pgvector**：远程 PG 为 Docker 部署，扩展装在容器层（`postgresql-17-pgvector`）；重建 `pgsql` 容器后需重新安装并 `CREATE EXTENSION vector`。
- **对话真相源**：messages 表仅为展示镜像；续聊恢复依赖 checkpointer 的 `thread_id = {user_id}:{conversation_id}`。


