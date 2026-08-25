import { request, uploadRequest } from "./client";
import type { DocumentItem, Page } from "./types";

export function apiListDocuments(limit = 100, offset = 0): Promise<Page<DocumentItem>> {
  return request(`/documents?limit=${limit}&offset=${offset}`);
}

export function apiUploadDocument(file: File, conversationId?: string): Promise<DocumentItem> {
  return uploadRequest("/documents", file, "file", conversationId ? { conversation_id: conversationId } : {});
}

export function apiDeleteDocument(id: string): Promise<void> {
  return request(`/documents/${id}`, { method: "DELETE" });
}
