"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { Doc, Organization, Page, ReviewTask, Stats, User, Vendor } from "./types";

const ACTIVE: Doc["status"][] = ["pending", "processing"];
const hasActive = (docs?: Doc[]) => !!docs?.some((d) => ACTIVE.includes(d.status));

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<User>("/api/users/me/") });
}

export function useOrganizations() {
  return useQuery({
    queryKey: ["orgs"],
    queryFn: () => api<Page<Organization>>("/api/organizations/").then((p) => p.results),
  });
}

export function useStats() {
  return useQuery({
    queryKey: ["stats"],
    queryFn: () => api<Stats>("/api/documents/stats/?days=30"),
    // Keep the numbers live while documents are being read.
    refetchInterval: (q) => ((q.state.data?.in_progress ?? 0) > 0 ? 3000 : 30000),
  });
}

export function useDocuments(status?: string) {
  return useQuery({
    queryKey: ["documents", status ?? "all"],
    queryFn: () => api<Page<Doc>>(`/api/documents/${status ? `?status=${status}` : ""}`).then((p) => p.results),
    refetchInterval: (q) => (hasActive(q.state.data) ? 2000 : 20000),
  });
}

export function useDocumentPage({ status, q, page }: { status?: string; q?: string; page: number }) {
  const params = new URLSearchParams({ page: String(page) });
  if (status) params.set("status", status);
  if (q) params.set("q", q);
  return useQuery({
    queryKey: ["documents", "page", status ?? "all", q ?? "", page],
    queryFn: () => api<Page<Doc>>(`/api/documents/?${params}`),
    placeholderData: (prev) => prev,
    refetchInterval: (query) => (hasActive(query.state.data?.results) ? 2000 : 20000),
  });
}

/** The oldest 20 open review tasks, plus the total number waiting. */
export function useReviewQueue() {
  return useQuery({
    queryKey: ["reviews", "pending"],
    queryFn: () => api<Page<ReviewTask>>("/api/documents/reviews/?status=pending"),
    refetchInterval: 15000,
  });
}

/** One review task, for links to invoices beyond the first page of the queue. */
export function useReviewTask(id: number | null) {
  return useQuery({
    queryKey: ["reviews", "one", id],
    enabled: !!id,
    queryFn: () => api<ReviewTask>(`/api/documents/reviews/${id}/`),
  });
}

export function useDocumentPreview(id: number, page: number) {
  return useQuery({
    queryKey: ["preview", id, page],
    staleTime: Infinity,
    gcTime: 5 * 60 * 1000,
    queryFn: async () => {
      const res = await api<Response>(`/api/documents/${id}/preview/?page=${page}`, { raw: true });
      const pageCount = Number(res.headers.get("X-Page-Count") || 1);
      if ((res.headers.get("Content-Type") || "").startsWith("text/")) return { pageCount, text: await res.text(), url: "" };
      return { pageCount, url: URL.createObjectURL(await res.blob()), text: undefined };
    },
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    for (const key of ["documents", "reviews", "stats"]) qc.invalidateQueries({ queryKey: [key] });
  };
}

export function useUpload() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api<Doc>("/api/documents/upload/", { form });
    },
    onSuccess: invalidate,
  });
}

export function useApprove() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ taskId, corrections }: { taskId: number; corrections: Record<string, unknown> }) =>
      api(`/api/documents/reviews/${taskId}/approve/`, { body: { corrections } }),
    onSuccess: invalidate,
  });
}

export function useReject() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (taskId: number) => api(`/api/documents/reviews/${taskId}/reject/`, { method: "POST" }),
    onSuccess: invalidate,
  });
}

export function useReprocess() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => api(`/api/documents/${id}/reprocess/`, { method: "POST" }),
    onSuccess: invalidate,
  });
}

export function useDeleteDocument() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => api(`/api/documents/${id}/`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}

export function useUpdateOrganization() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...patch }: Partial<Organization> & { id: number }) =>
      api<Organization>(`/api/organizations/${id}/`, { method: "PATCH", body: patch }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orgs"] }),
  });
}

export function useVendors(q: string, page: number) {
  const params = new URLSearchParams({ page: String(page) });
  if (q) params.set("q", q);
  return useQuery({
    queryKey: ["vendors", q, page],
    queryFn: () => api<Page<Vendor>>(`/api/documents/vendors/?${params}`),
    placeholderData: (prev) => prev,
  });
}

export function useSaveVendor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<Vendor> & { id?: number }) =>
      api<Vendor>(id ? `/api/documents/vendors/${id}/` : "/api/documents/vendors/", { method: id ? "PATCH" : "POST", body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vendors"] }),
  });
}

export function useDeleteVendor() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api(`/api/documents/vendors/${id}/`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["vendors"] }),
  });
}
