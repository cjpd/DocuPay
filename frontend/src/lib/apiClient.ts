import axios, { AxiosHeaders } from "axios";
import { getAccessToken, clearTokens } from "./auth";

const baseURL =
  process.env.NEXT_PUBLIC_API_BASE ||
  (typeof window === "undefined" ? "http://backend:8000" : "http://localhost:8000");

export const apiClient = axios.create({
  baseURL,
});

apiClient.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = getAccessToken();
    if (token) {
      if (!config.headers) {
        config.headers = new AxiosHeaders();
      }
      (config.headers as AxiosHeaders).set("Authorization", `Bearer ${token}`);
    }
  }
  return config;
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (typeof window !== "undefined" && error?.response?.status === 401) {
      clearTokens();
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    }
    return Promise.reject(error);
  }
);
