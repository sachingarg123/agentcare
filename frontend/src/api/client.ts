const TOKEN_KEY = "agentcare_token";
const USER_KEY = "agentcare_user";

export type User = {
  id: string;
  name: string;
  email: string;
  role: "PATIENT" | "STAFF" | "ADMIN";
  patient_id?: string | null;
};

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser(): User | null {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || "null");
  } catch {
    return null;
  }
}

export function setSession(token: string, user: User) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function roleHome(role: string): string {
  if (role === "ADMIN") return "/staff/admin";
  if (role === "STAFF") return "/staff";
  return "/patient";
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let body = options.body;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }

  const { json: _json, ...rest } = options;
  const res = await fetch(`/api/v1${path}`, { ...rest, headers, body });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }

  if (!res.ok) {
    const detail = (data as { detail?: unknown })?.detail;
    const msg =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join("; ")
          : res.statusText;
    throw new ApiError(msg || `HTTP ${res.status}`, res.status);
  }
  return data as T;
}

export function wsUrl(workflowId: string): string {
  const token = getToken() || "";
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  // Dev: Vite proxies /api → backend; use same host for WS
  return `${proto}://${window.location.host}/api/v1/ws/workflows/${workflowId}?token=${encodeURIComponent(token)}`;
}
