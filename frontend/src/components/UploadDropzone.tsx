"use client";

import { useRef, useState } from "react";
import { UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { useUpload } from "@/lib/hooks";
import { cx } from "./ui";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,.txt";
const MAX_MB = 20;

export function UploadDropzone({ compact }: { compact?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const upload = useUpload();

  async function send(files: FileList | null) {
    if (!files?.length) return;
    let sent = 0;
    for (const file of Array.from(files)) {
      if (file.size > MAX_MB * 1024 * 1024) {
        toast.error(`${file.name} is larger than ${MAX_MB} MB`);
        continue;
      }
      try {
        await upload.mutateAsync(file);
        sent += 1;
      } catch (e) {
        toast.error(`${file.name}: ${(e as Error).message}`);
      }
    }
    if (sent) toast.success(sent === 1 ? "Uploaded. Reading it now." : `${sent} documents uploaded. Reading them now.`);
    if (input.current) input.current.value = "";
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        send(e.dataTransfer.files);
      }}
      className={cx(
        "group relative flex items-center gap-4 rounded-[var(--radius-card)] border-2 border-dashed transition-colors",
        compact ? "px-4 py-4" : "flex-col justify-center px-6 py-10 text-center",
        over ? "border-accent bg-accent-soft" : "border-line bg-surface hover:border-accent/50",
      )}
    >
      <span className={cx("grid place-items-center rounded-full bg-accent-soft text-accent", compact ? "size-10" : "size-14")}>
        <UploadCloud aria-hidden className={cx(compact ? "size-5" : "size-7", upload.isPending && "animate-pulse-soft")} />
      </span>
      <div className={compact ? "min-w-0" : ""}>
        <p className="text-sm font-medium text-ink">
          {upload.isPending ? "Uploading…" : "Drop invoices here"}{" "}
          <span className="text-muted">or</span>{" "}
          <button type="button" onClick={() => input.current?.click()} className="font-medium text-accent hover:underline">
            choose files
          </button>
        </p>
        <p className="mt-1 text-xs text-muted">PDF, scans or photos, up to {MAX_MB} MB each</p>
      </div>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        className="sr-only"
        aria-label="Upload invoices"
        onChange={(e) => send(e.target.files)}
      />
    </div>
  );
}
