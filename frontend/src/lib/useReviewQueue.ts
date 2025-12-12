"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "./apiClient";
import { ReviewTask } from "./types";

export function useReviewQueue() {
  return useQuery({
    queryKey: ["review-tasks"],
    queryFn: async () => {
      const { data } = await apiClient.get("/api/documents/reviews/");
      return Array.isArray(data) ? (data as ReviewTask[]) : ((data?.results || []) as ReviewTask[]);
    },
    staleTime: 30_000,
  });
}
