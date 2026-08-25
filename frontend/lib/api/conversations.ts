import { request } from "./client";
import type { Conversation, ConversationDetail, Message, Page } from "./types";

export function apiListConversations(limit = 100, offset = 0): Promise<Page<Conversation>> {
  return request(`/conversations?limit=${limit}&offset=${offset}`);
}

export function apiCreateConversation(title?: string): Promise<Conversation> {
  return request("/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title ?? null }),
  });
}

export function apiGetConversation(id: string): Promise<ConversationDetail> {
  return request(`/conversations/${id}`);
}

export function apiDeleteConversation(id: string): Promise<void> {
  return request(`/conversations/${id}`, { method: "DELETE" });
}

export function apiListMessages(
  id: string,
  limit = 200,
  offset = 0,
): Promise<Page<Message>> {
  return request(`/conversations/${id}/messages?limit=${limit}&offset=${offset}`);
}
