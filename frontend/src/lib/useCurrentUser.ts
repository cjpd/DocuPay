"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchMe, getAccessToken } from "./auth";

export function useCurrentUser() {
  const hasToken = typeof window !== "undefined" ? !!getAccessToken() : false;
  return useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
    enabled: hasToken,
    staleTime: 60_000,
  });
}
