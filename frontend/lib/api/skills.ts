import { request, uploadRequest } from "./client";
import type { Page, Skill } from "./types";

export function apiListSkills(): Promise<Page<Skill>> {
  return request("/skills");
}

export function apiCreateSkill(body: {
  name: string;
  description: string;
  instructions: string;
}): Promise<Skill> {
  return request("/skills", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function apiUpdateSkill(
  name: string,
  body: { description?: string; instructions?: string },
): Promise<Skill> {
  return request(`/skills/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function apiDeleteSkill(name: string): Promise<void> {
  return request(`/skills/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export function apiImportSkill(file: File): Promise<Skill> {
  return uploadRequest("/skills/import", file);
}
