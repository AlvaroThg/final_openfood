"use client";

// Estado de sesión. Refleja lo que dice el backend: al cargar la página se intenta renovar la
// sesión con la cookie y se consulta /auth/me. El rol mostrado viene del servidor; la
// autorización real la aplica cada servicio en cada petición.
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, refreshSession, setAccessToken } from "./http";
import type { TokenResponse, User } from "./types";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const loadUser = useCallback(async () => {
    setUser(await api<User>("/auth/me"));
  }, []);

  useEffect(() => {
    (async () => {
      try {
        if (await refreshSession()) {
          await loadUser();
        }
      } catch {
        setUser(null);
      } finally {
        setLoading(false);
      }
    })();
  }, [loadUser]);

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await api<TokenResponse>("/auth/login", {
        method: "POST",
        body: { email, password },
        auth: false,
      });
      setAccessToken(tokens.access_token);
      await loadUser();
    },
    [loadUser],
  );

  const register = useCallback(
    async (email: string, password: string) => {
      await api<User>("/auth/register", {
        method: "POST",
        body: { email, password },
        auth: false,
      });
      await login(email, password);
    },
    [login],
  );

  const logout = useCallback(async () => {
    try {
      await api<void>("/auth/logout", { method: "POST", auth: false });
    } finally {
      setAccessToken(null);
      setUser(null);
    }
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth debe usarse dentro de <AuthProvider>");
  }
  return context;
}
