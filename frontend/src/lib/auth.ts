import { apiClient } from "./apiClient";

type Tokens = {
  access: string;
  refresh?: string;
};

export type User = {
  id: number;
  username: string;
  email: string;
  first_name?: string;
  last_name?: string;
};

const ACCESS_KEY = "idp_access_token";
const REFRESH_KEY = "idp_refresh_token";

export function storeTokens(tokens: Tokens) {
  if (typeof window === "undefined") return;
  localStorage.setItem(ACCESS_KEY, tokens.access);
  if (tokens.refresh) {
    localStorage.setItem(REFRESH_KEY, tokens.refresh);
  }
}

export function clearTokens() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export function getAccessToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_KEY);
}

export async function login(username: string, password: string) {
  const { data } = await apiClient.post("/api/auth/token/", { username, password });
  storeTokens(data);
  return data as Tokens;
}

export async function fetchMe() {
  const { data } = await apiClient.get<User>("/api/users/me/");
  return data;
}
