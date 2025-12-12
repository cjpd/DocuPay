import axios from "axios";
import { getAccessToken, clearTokens } from "./auth";

export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000",
});

apiClient.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = getAccessToken();
    if (token) {
      config.headers = { ...config.headers, Authorization: `Bearer ${token}` };
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
