import { request, uploadRequest } from "./client";
import type { FileItem, Page, UploadedFile } from "./types";

export function apiListFiles(): Promise<Page<FileItem>> {
  return request("/files");
}

export function apiUploadFile(file: File): Promise<UploadedFile> {
  return uploadRequest("/files/upload", file);
}
