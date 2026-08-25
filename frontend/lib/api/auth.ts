import { request } from "./client";
import type { TokenResponse, User } from "./types";

export function apiRegister(username: string, password: string): Promise<{ id: string; username: string }> {
  return request("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function apiLogin(username: string, password: string): Promise<TokenResponse> {
  return request("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

export function apiMe(): Promise<User> {
  return request("/auth/me");
}
