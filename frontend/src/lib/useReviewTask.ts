"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "./apiClient";
import { ReviewTask } from "./types";

export function useReviewTask(id: string | number) {
  return useQuery({
    queryKey: ["review-task", id],
    queryFn: async () => {
      const { data } = await apiClient.get<ReviewTask>(`/api/documents/reviews/${id}/`);
      return data;
    },
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useReviewActions(id: string | number) {
  const queryClient = useQueryClient();
  const approve = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post(`/api/documents/reviews/${id}/approve/`, {});
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["review-task", id] });
      queryClient.invalidateQueries({ queryKey: ["review-tasks"] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  const reject = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post(`/api/documents/reviews/${id}/reject/`, {});
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["review-task", id] });
      queryClient.invalidateQueries({ queryKey: ["review-tasks"] });
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
  });
  return { approve, reject };
}
