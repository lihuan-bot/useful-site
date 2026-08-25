# 智能助手前端

多用户 AI 助手的前端界面（Next.js 16 + TypeScript + Tailwind CSS v4），
布局参考豆包网页版：左侧会话列表 + 居中消息流 + 底部输入区。

对应后端：`../backend`（FastAPI，`http://localhost:8000/api/v1`）。

## 启动

```bash
npm install
cp .env.example .env        # 按需修改 NEXT_PUBLIC_API_BASE_URL
npm run dev                 # http://localhost:3000
```

后端需先运行（`uv run uvicorn main:app --port 8000`），CORS 已允许 `http://localhost:3000`。

## 页面

| 路由 | 说明 |
| --- | --- |
| `/login` | 登录 / 注册 |
| `/chat` | 新对话（首条消息发出后自动创建会话并跳转 `/chat?c={id}`） |
| `/chat?c={id}` | 会话详情（查询参数定位会话：刷新后自动重连进行中的流式生成） |
| `/skills` | 技能管理（新建 / 编辑 / 导入 / 删除 SKILL.md） |
| `/documents` | 知识库文档（上传 PDF/DOCX/TXT，后台解析自动轮询状态） |
| `/files` | 文件工作区（agent 生成物 + 手动上传，带鉴权下载） |

## 目录结构

```
app/                      # 路由（(main) 为登录后路由组，含守卫）
  (main)/chat/            # 对话页（新对话 + [conversationId] 详情）
  (main)/skills|documents|files/
  login/
components/
  chat/                   # 会话侧边栏、消息流、流式渲染、工具步骤卡、
                          # HITL 补充表单、输入区
  layout/                 # AppShell + 导航栏
  skills/                 # 技能编辑弹窗
  ui/                     # 通用组件（icons/button/modal/toast/…）
hooks/
  use-chat-stream.ts      # SSE 流式会话（发送/resume/停止/重连）
  use-conversation-events.ts  # 会话状态频道（列表实时状态）
lib/
  api/                    # 各资源 API 封装 + 类型（与后端 schema 对应）
  sse.ts                  # fetch 版 SSE 解析（EventSource 不支持自定义头）
  auth.tsx                # AuthContext（token 存 localStorage，401 事件跳登录）
```

## 关键设计

- **流式聊天**：后端生成与页面解耦（Redis 事件日志），浏览器刷新后通过
  `GET /stream` 重放恢复；前端仅在会话列表标志显示有活动生成时才重连，
  避免重放刚完成的历史流。
- **HITL 补充表单**：后端 `field_collect` 中间件下发通用字段描述
  （missing/invalid/known），前端原样渲染表单，提交 `POST /resume`。
- **会话状态**：`GET /conversations/events` 一条 SSE 订阅驱动列表的
  「生成中 / 等待补充」角标，断线自动重连。
- **鉴权**：Bearer token 存 localStorage；任意请求 401 时派发事件，
  AuthProvider 统一跳转登录页。
