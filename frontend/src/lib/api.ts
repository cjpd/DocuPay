// Small fetch client for the Django API.
//
// Sign-in uses an httpOnly session cookie set by the API: page scripts never see a token,
// so a script injected into the page cannot steal the session. Requests that change data
// send the CSRF token, which is kept in memory only.
import { useSyncExternalStore } from "react";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000").replace(/\/$/, "");

const ORG = "dp_org";
const CHANGE = "dp-session";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

function setStored(key: string, value: string | null) {
  if (typeof window === "undefined") return;
  if (value === null) window.localStorage.removeItem(key);
  else window.localStorage.setItem(key, value);
  window.dispatchEvent(new Event(CHANGE));
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE, onChange);
  };
}

/** Read stored preferences in a component; undefined during the static prerender. */
export function useSession<T>(read: () => T): T | undefined {
  return useSyncExternalStore(subscribe, read, () => undefined);
}

export const session = {
  /** The company selected in the sidebar (not a secret). */
  org: () => (typeof window === "undefined" ? null : window.localStorage.getItem(ORG)),
  setOrg: (id: number | null) => setStored(ORG, id === null ? null : String(id)),
};

// Tokens from the earlier localStorage sign-in are removed, so none stay readable.
if (typeof window !== "undefined") {
  window.localStorage.removeItem("dp_access");
  window.localStorage.removeItem("dp_refresh");
}

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

let csrfToken: string | null = null;

async function csrf(): Promise<string> {
  if (csrfToken) return csrfToken;
  const res = await fetch(`${API_BASE}/api/auth/csrf/`, { credentials: "include" });
  csrfToken = (await res.json()).csrfToken;
  return csrfToken as string;
}

function goToSignIn() {
  if (typeof window === "undefined" || window.location.pathname.startsWith("/login")) return;
  // Outside React (no router here), so a full navigation to the sign-in page.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign(`/login/?next=${encodeURIComponent(window.location.pathname + window.location.search)}`);
}

type Options = { method?: string; body?: unknown; form?: FormData; raw?: boolean };

export async function api<T = unknown>(path: string, opts: Options = {}, retried = false): Promise<T> {
  const method = opts.method || (opts.body !== undefined || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = {};
  const org = session.org();
  if (org) headers["X-Organization-ID"] = org;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE_METHODS.has(method)) headers["X-CSRFToken"] = await csrf();

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: "include",
    body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
  });

  if (res.status === 401) {
    goToSignIn();
    throw new ApiError(401, "Your session has ended. Sign in again.");
  }
  if (opts.raw && res.ok) return res as unknown as T;
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  // A rotated CSRF token (after sign-in elsewhere): get a fresh one and try once more.
  if (res.status === 403 && !retried && typeof data === "object" && /CSRF/i.test(JSON.stringify(data))) {
    csrfToken = null;
    return api<T>(path, opts, true);
  }
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
  csrfToken = null;
  const res = await fetch(`${API_BASE}/api/auth/login/`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRFToken": await csrf() },
    body: JSON.stringify({ username, password }),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 429) throw new ApiError(429, "Too many attempts. Wait a minute and try again.");
  if (!res.ok) throw new ApiError(res.status, data.detail || "Sign-in failed.");
  csrfToken = data.csrfToken;
}

export async function signOut() {
  try {
    await fetch(`${API_BASE}/api/auth/logout/`, {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRFToken": await csrf() },
    });
  } finally {
    csrfToken = null;
  }
}
