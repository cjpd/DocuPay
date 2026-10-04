"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, FileWarning } from "lucide-react";
import { useDocumentPreview } from "@/lib/hooks";
import { Button, Skeleton } from "./ui";

/** The original document, one page at a time, rendered as an image by the API
 * (works the same on every browser, including phones that cannot show PDFs). */
export function DocumentPreview({ id, name }: { id: number; name: string }) {
  const [page, setPage] = useState(1);
  const preview = useDocumentPreview(id, page);
  const count = preview.data?.pageCount ?? 1;

  return (
    <div className="flex h-full flex-col">
      {count > 1 && (
        <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-xs text-muted tabular">Page {page} of {count}</span>
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" aria-label="Previous page" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              <ChevronLeft aria-hidden className="size-4" />
            </Button>
            <Button size="sm" variant="ghost" aria-label="Next page" disabled={page >= count} onClick={() => setPage((p) => p + 1)}>
              <ChevronRight aria-hidden className="size-4" />
            </Button>
          </div>
        </div>
      )}
      <div className="flex-1 overflow-auto rounded bg-surface-2 p-2 xl:max-h-[74vh]">
        {preview.isLoading ? (
          <Skeleton className="mx-auto aspect-[1/1.3] w-full max-w-2xl" />
        ) : preview.isError || !preview.data ? (
          <div className="grid min-h-[320px] place-items-center text-center text-sm text-muted">
            <div><FileWarning aria-hidden className="mx-auto mb-2 size-6" />The original file could not be shown.</div>
          </div>
        ) : preview.data.text !== undefined ? (
          <pre className="mx-auto max-w-2xl whitespace-pre-wrap rounded bg-surface p-5 font-mono text-xs leading-relaxed text-ink">{preview.data.text}</pre>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={preview.data.url} alt={`${name}, page ${page}`} className="mx-auto w-full max-w-2xl rounded bg-white shadow-sm" />
        )}
      </div>
    </div>
  );
}
