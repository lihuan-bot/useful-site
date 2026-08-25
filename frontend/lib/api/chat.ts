/** 聊天控制接口。流式发送 / 续答在 hooks/use-chat-stream.ts 中通过 SSE 完成。 */

import { request } from "./client";

export function apiStopGeneration(conversationId: string): Promise<{ status: string }> {
  return request(`/conversations/${conversationId}/stop`, { method: "POST" });
}
