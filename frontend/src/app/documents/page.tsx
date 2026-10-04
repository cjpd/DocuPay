"use client";

import Link from "next/link";
import { useState } from "react";
import { FileStack, RotateCw, Search, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell, PageHeader } from "@/components/AppShell";
import { Pipeline } from "@/components/Pipeline";
import { StatusBadge } from "@/components/StatusBadge";
import { UploadDropzone } from "@/components/UploadDropzone";
import { Button, Card, EmptyState, ErrorState, Skeleton, cx } from "@/components/ui";
import { ago, date, money } from "@/lib/format";
import { useDeleteDocument, useDocumentPage, useReprocess } from "@/lib/hooks";
import type { Doc } from "@/lib/types";

const FILTERS = [
  { key: "", label: "All" },
  { key: "requires_review", label: "Needs you" },
  { key: "approved", label: "Approved" },
  { key: "pending,processing", label: "In progress" },
  { key: "failed", label: "Failed" },
];

export default function DocumentsPage() {
  return (
    <AppShell>
      <Documents />
    </AppShell>
  );
}

function Documents() {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const docs = useDocumentPage({ status: status || undefined, q: query.trim() || undefined, page });
  const pages = docs.data ? Math.max(1, Math.ceil(docs.data.count / 20)) : 1;

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Documents" hint="Everything you uploaded, newest first." />
      <UploadDropzone compact />

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <div role="tablist" aria-label="Filter by status" className="flex flex-wrap gap-1 rounded-[var(--radius-control)] bg-surface-2 p-1">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              role="tab"
              aria-selected={status === f.key}
              onClick={() => { setStatus(f.key); setPage(1); }}
              className={cx(
                "rounded-[5px] px-3 py-1.5 text-sm transition-colors",
                status === f.key ? "bg-surface font-medium text-ink shadow-sm" : "text-ink-2 hover:text-ink",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label className="relative ml-auto w-full sm:w-72">
          <span className="sr-only">Search by vendor, invoice number or file name</span>
          <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
          <input
            type="search"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            placeholder="Vendor, invoice number, file"
            className="h-10 w-full rounded-[var(--radius-control)] border border-line bg-surface pr-3 pl-9 text-sm outline-none focus:border-accent"
          />
        </label>
      </div>

      <Card className="mt-4">
        {docs.isError ? (
          <div className="p-5"><ErrorState message={(docs.error as Error).message} onRetry={() => docs.refetch()} /></div>
        ) : docs.isLoading ? (
          <div className="space-y-3 p-5">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-12" />)}</div>
        ) : !docs.data?.results.length ? (
          <EmptyState icon={<FileStack className="size-5" />} title={query || status ? "No documents match" : "No documents yet"}>
            {query || status ? "Try another filter or search." : "Drop an invoice above to get started."}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line">{docs.data.results.map((d) => <Row key={d.id} doc={d} />)}</ul>
        )}
      </Card>

      {pages > 1 && (
        <nav className="mt-4 flex items-center justify-end gap-2 text-sm" aria-label="Pages">
          <span className="text-muted">Page {page} of {pages}</span>
          <Button size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button>
          <Button size="sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next</Button>
        </nav>
      )}
    </div>
  );
}

function Row({ doc }: { doc: Doc }) {
  const reprocess = useReprocess();
  const remove = useDeleteDocument();
  const [confirming, setConfirming] = useState(false);
  const data = doc.extracted_data;
  const failedChecks = data?.validation.filter((c) => c.status === "fail").length ?? 0;

  return (
    <li className="grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-2 px-5 py-3.5 md:grid-cols-[minmax(0,1.6fr)_auto_7rem_8rem_auto]">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium">{data?.vendor_name || doc.file_name}</p>
        <p className="truncate text-xs text-muted">
          {data?.invoice_number && <span className="font-mono">{data.invoice_number} · </span>}
          {data?.invoice_date ? `dated ${date(data.invoice_date)}` : doc.file_name} · uploaded {ago(doc.created_at)}
        </p>
        {doc.status === "failed" && doc.error_message && <p className="mt-1 text-xs text-bad">{doc.error_message}</p>}
      </div>
      <div className="hidden md:block"><Pipeline doc={doc} compact /></div>
      <p className="hidden text-right font-mono text-sm tabular md:block">{money(data?.total_amount, data?.currency || undefined)}</p>
      <div className="text-right md:text-left">
        <StatusBadge doc={doc} />
        {doc.status === "requires_review" && failedChecks > 0 && <p className="mt-0.5 text-xs text-muted">{failedChecks} issue{failedChecks === 1 ? "" : "s"}</p>}
      </div>
      <div className="col-span-2 flex justify-end gap-1 md:col-span-1">
        {doc.status === "requires_review" && doc.review_task_id && (
          <Link href={`/review/?id=${doc.review_task_id}`} className="inline-flex h-8 items-center rounded-[var(--radius-control)] bg-accent px-3 text-[13px] font-medium text-accent-ink hover:brightness-110">
            Review
          </Link>
        )}
        {doc.status === "failed" && (
          <Button size="sm" disabled={reprocess.isPending} onClick={() => reprocess.mutate(doc.id, { onError: (e) => toast.error(e.message) })}>
            <RotateCw aria-hidden className={cx("size-3.5", reprocess.isPending && "animate-spin")} /> Try again
          </Button>
        )}
        {confirming ? (
          <>
            <Button size="sm" variant="danger" onClick={() => remove.mutate(doc.id, { onSuccess: () => toast.success("Deleted"), onError: (e) => toast.error(e.message) })}>
              Delete
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Keep</Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" aria-label={`Delete ${doc.file_name}`} onClick={() => setConfirming(true)}>
            <Trash2 aria-hidden className="size-4" />
          </Button>
        )}
      </div>
    </li>
  );
}
