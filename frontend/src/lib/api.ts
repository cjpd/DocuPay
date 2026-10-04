// Small fetch client for the Django API: JWT auth with automatic refresh,
// the active organization header, and readable errors.
import { useSyncExternalStore } from "react";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000").replace(/\/$/, "");

const ACCESS = "dp_access";
const REFRESH = "dp_refresh";
const ORG = "dp_org";

const store = {
  get: (key: string) => (typeof window === "undefined" ? null : window.localStorage.getItem(key)),
  set: (key: string, value: string | null) => {
    if (typeof window === "undefined") return;
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
    window.dispatchEvent(new Event(CHANGE));
  },
};

const CHANGE = "dp-session";

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE, onChange);
  };
}

/** Read session state in a component; undefined during the static prerender. */
export function useSession<T>(read: () => T): T | undefined {
  return useSyncExternalStore(subscribe, read, () => undefined);
}

export const session = {
  isSignedIn: () => !!store.get(ACCESS),
  save: (access: string, refresh?: string) => {
    store.set(ACCESS, access);
    if (refresh) store.set(REFRESH, refresh);
  },
  clear: () => {
    store.set(ACCESS, null);
    store.set(REFRESH, null);
  },
  org: () => store.get(ORG),
  setOrg: (id: number | null) => store.set(ORG, id === null ? null : String(id)),
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public data?: unknown) {
    super(message);
  }
}

function messageFrom(data: unknown, status: number): string {
  if (data && typeof data === "object") {
    const d = data as Record<string, unknown>;
    if (typeof d.detail === "string") return d.detail;
    const first = Object.entries(d)[0];
    if (first) return `${first[0]}: ${Array.isArray(first[1]) ? first[1].join(" ") : String(first[1])}`;
  }
  return status >= 500 ? "The server had a problem. Try again in a moment." : `Request failed (${status}).`;
}

let refreshing: Promise<boolean> | null = null;

async function refreshAccess(): Promise<boolean> {
  const refresh = store.get(REFRESH);
  if (!refresh) return false;
  refreshing ??= fetch(`${API_BASE}/api/auth/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  })
    .then(async (r) => {
      if (!r.ok) return false;
      const data = await r.json();
      session.save(data.access, data.refresh);
      return true;
    })
    .catch(() => false)
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

type Options = { method?: string; body?: unknown; form?: FormData; raw?: boolean };

export async function api<T = unknown>(path: string, opts: Options = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = {};
  const token = store.get(ACCESS);
  if (token) headers.Authorization = `Bearer ${token}`;
  const org = store.get(ORG);
  if (org) headers["X-Organization-ID"] = org;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  const res = await fetch(`${API_BASE}${path}`, {
    method: opts.method || (opts.body !== undefined || opts.form ? "POST" : "GET"),
    headers,
    body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
  });

  if (res.status === 401 && !retried && (await refreshAccess())) return api<T>(path, opts, true);
  if (res.status === 401) {
    session.clear();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      // Outside React (no router here), so a full navigation to the sign-in page.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign(`/login/?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
    }
    throw new ApiError(401, "Your session has ended. Sign in again.");
  }
  if (opts.raw && res.ok) return res as unknown as T;
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) throw new ApiError(res.status, messageFrom(data, res.status), data);
  return data as T;
}

function safeJson(text: string) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function signIn(username: string, password: string) {
  const res = await fetch(`${API_BASE}/api/auth/token/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new ApiError(res.status, res.status === 401 ? "Wrong username or password." : "Sign-in failed.");
  const data = await res.json();
  session.save(data.access, data.refresh);
}
