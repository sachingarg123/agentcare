import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  clearSession,
  getStoredUser,
  getToken,
  roleHome,
  setSession,
  type User,
} from "../api/client";

type AuthContextValue = {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  register: (payload: {
    name: string;
    email: string;
    password: string;
    phone?: string;
  }) => Promise<User>;
  logout: () => void;
  refresh: () => Promise<User | null>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(getStoredUser());
  const [token, setToken] = useState<string | null>(getToken());
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const t = getToken();
    if (!t) {
      setUser(null);
      setToken(null);
      return null;
    }
    try {
      const me = await api<User>("/auth/me");
      setSession(t, me);
      setUser(me);
      setToken(t);
      return me;
    } catch {
      clearSession();
      setUser(null);
      setToken(null);
      return null;
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const body = await api<{
      access_token: string;
      role: User["role"];
      user_id: string;
    }>("/auth/login", { method: "POST", json: { email, password } });
    setSession(body.access_token, {
      id: body.user_id,
      role: body.role,
      email,
      name: email,
    });
    setToken(body.access_token);
    const me = await api<User>("/auth/me");
    setSession(body.access_token, me);
    setUser(me);
    return me;
  }, []);

  const register = useCallback(
    async (payload: {
      name: string;
      email: string;
      password: string;
      phone?: string;
    }) => {
      const body = await api<{
        access_token: string;
        role: User["role"];
        user_id: string;
      }>("/auth/register", { method: "POST", json: payload });
      setSession(body.access_token, {
        id: body.user_id,
        role: body.role,
        email: payload.email,
        name: payload.name,
      });
      setToken(body.access_token);
      const me = await api<User>("/auth/me");
      setSession(body.access_token, me);
      setUser(me);
      return me;
    },
    [],
  );

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
    setToken(null);
  }, []);

  const value = useMemo(
    () => ({ user, token, loading, login, register, logout, refresh }),
    [user, token, loading, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export { roleHome };
