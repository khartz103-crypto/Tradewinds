const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem("token");
}

export function setToken(token: string) {
  window.localStorage.setItem("token", token);
}

export function clearToken() {
  window.localStorage.removeItem("token");
}

async function apiClient<T = unknown>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options?.headers as Record<string, string>) || {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${BACKEND_URL}${path}`, { ...options, headers });
  if (res.status === 401) {
    clearToken();
    // Avoid a full-page reload when we are already on the login page
    // (e.g. a failed sign-in attempt).
    if (window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(err.detail);
  }
  return res.json() as Promise<T>;
}

export function apiGet<T = unknown>(path: string): Promise<T> {
  return apiClient(path, { method: "GET" });
}

export function apiPost<T = unknown>(path: string, body: unknown): Promise<T> {
  return apiClient(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export default apiClient;
