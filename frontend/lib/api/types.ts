/** 与后端 pydantic schemas 一一对应的类型定义。 */

export interface User {
  id: string;
  username: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface Page<T> {
  items: T[];
  total: number;
}

// -- conversations ------------------------------------------------------

export interface Conversation {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  /** 该会话当前有正在运行的生成任务 */
  streaming: boolean;
  /** 最新一条助手回复未完成且没有运行中的生产者（用户停止或进程中断） */
  interrupted: boolean;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  is_complete: boolean;
  /** agent 交付物路径列表（/files/ 前缀），流结束时由后端持久化 */
  artifacts: string[] | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

// -- documents ----------------------------------------------------------

export type DocumentStatus = "pending" | "processing" | "ready" | "failed";

export interface DocumentItem {
  id: string;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  status: string;
  error: string | null;
  chunk_count: number;
  conversation_id: string | null;
  created_at: string;
}

// -- skills -------------------------------------------------------------

export interface Skill {
  name: string;
  description: string;
  instructions: string;
  path: string;
  status: "ok" | "broken";
  load_error: string | null;
}

// -- files --------------------------------------------------------------

export interface FileItem {
  name: string;
  size: number;
  last_modified: string | null;
}

export interface UploadedFile {
  path: string;
  name: string;
  size: number;
  content_type: string | null;
  download_url: string;
  is_image: boolean;
}

// -- SSE / 聊天流 ---------------------------------------------------------

/** HITL 中断表单字段（后端 field_collect 中间件下发，前端原样渲染） */
export interface InterruptField {
  name: string;
  label: string;
  hint: string | null;
  placeholder: string | null;
  prompt: string;
}

export interface InterruptPayload {
  request: string;
  tool: string;
  missing: InterruptField[];
  invalid: InterruptField[];
  known: Record<string, string>;
}

export interface MessageEvent {
  message_id: string;
  delta: string;
}

export interface ToolCallEvent {
  tool_call_id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolResultEvent {
  tool_call_id: string;
  output: string;
  is_error: boolean;
}

export interface ArtifactEvent {
  name: string;
  download_url: string;
  tool_call_id: string;
}

export interface DoneEvent {
  conversation_id: string;
  thread_id: string;
  message_id: string;
}

export interface ErrorEvent {
  code: string;
  message: string;
}

export interface InterruptEvent {
  payload: InterruptPayload;
}

export type ConversationStatus = "running" | "done" | "awaiting_input" | "interrupted";

export interface ConversationStatusEvent {
  conversation_id: string;
  status: ConversationStatus;
}

export interface StreamStatusEvent {
  active: boolean;
  pending: boolean;
}
