"use client";

/**
 * 客户端认证上下文：token 存 localStorage，挂载时用 /auth/me 校验。
 * 401 时 api/client 会清 token 并跳转 /login；这里负责正常流程的登录态。
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { apiLogin, apiMe, apiRegister } from "./api/auth";
import { AUTH_UNAUTHORIZED_EVENT, clearToken, getToken, setToken } from "./api/client";
import type { User } from "./api/types";

interface AuthContextValue {
  user: User | null;
  /** 初始校验中：避免闪跳登录页 */
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!getToken()) {
        setLoading(false);
        return;
      }
      try {
        const me = await apiMe();
        if (!cancelled) setUser(me);
      } catch {
        // token 失效：api/client 已清 token 并派发 auth:unauthorized
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 任意请求 401 时跳转登录页（fetch 层不能直接改 location，通过事件解耦）
  useEffect(() => {
    const handleUnauthorized = () => {
      setUser(null);
      router.replace("/login");
    };
    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
    return () => window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, handleUnauthorized);
  }, [router]);

  const login = useCallback(async (username: string, password: string) => {
    const { access_token } = await apiLogin(username, password);
    setToken(access_token);
    setUser(await apiMe());
  }, []);

  const register = useCallback(async (username: string, password: string) => {
    await apiRegister(username, password);
    await login(username, password);
  }, [login]);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return ctx;
}
