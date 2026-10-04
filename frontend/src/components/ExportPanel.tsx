"use client";

import { useEffect, useRef, useState } from "react";
import { Download, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button, cx } from "./ui";

type Range = "this_month" | "last_month" | "all" | "custom";

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function rangeDates(range: Range): { from: string; to: string } {
  const now = new Date();
  if (range === "this_month") return { from: iso(new Date(now.getFullYear(), now.getMonth(), 1)), to: iso(now) };
  if (range === "last_month")
    return { from: iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)), to: iso(new Date(now.getFullYear(), now.getMonth(), 0)) };
  return { from: "", to: "" };
}

function Choice<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-1.5 text-xs font-medium text-ink-2">{label}</legend>
      <div className="flex flex-wrap gap-1 rounded-[var(--radius-control)] bg-surface-2 p-1">
        {options.map((o) => (
          <button
            key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}
            className={cx("flex-1 rounded-[5px] px-2.5 py-1.5 text-sm whitespace-nowrap transition-colors",
              value === o.value ? "bg-surface font-medium text-ink shadow-sm" : "text-ink-2 hover:text-ink")}
          >
            {o.label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

/** Download approved invoices as a CSV that opens in Excel or Google Sheets. */
export function ExportPanel() {
  const [open, setOpen] = useState(false);
  const [what, setWhat] = useState<"approved" | "all">("approved");
  const [level, setLevel] = useState<"invoice" | "line">("invoice");
  const [range, setRange] = useState<Range>("this_month");
  const [custom, setCustom] = useState({ from: "", to: "" });
  const [busy, setBusy] = useState(false);
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onClick = (e: MouseEvent) => panel.current && !panel.current.contains(e.target as Node) && setOpen(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  async function download() {
    const { from, to } = range === "custom" ? custom : rangeDates(range);
    const params = new URLSearchParams({ level, status: what === "approved" ? "approved" : "all" });
    if (from) params.set("from", from);
    if (to) params.set("to", to);
    setBusy(true);
    try {
      const res = await api<Response>(`/api/documents/export/?${params}`, { raw: true });
      const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1] || "docupay-export.csv";
      const url = URL.createObjectURL(await res.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: name });
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast.success(`Downloaded ${name}`);
      setOpen(false);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative" ref={panel}>
      <Button onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="dialog">
        <Download aria-hidden className="size-4" /> Export CSV
      </Button>
      {open && (
        <div role="dialog" aria-label="Export to CSV"
          className="absolute right-0 z-30 mt-2 w-[min(92vw,380px)] animate-rise rounded-[var(--radius-card)] border border-line bg-surface p-4 shadow-lg">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-[15px] font-semibold">Export to CSV</h2>
            <button onClick={() => setOpen(false)} aria-label="Close" className="rounded p-1 text-muted hover:bg-surface-2"><X aria-hidden className="size-4" /></button>
          </div>
          <div className="space-y-3">
            <Choice label="Documents" value={what} onChange={setWhat}
              options={[{ value: "approved", label: "Approved only" }, { value: "all", label: "All" }]} />
            <Choice label="One row per" value={level} onChange={setLevel}
              options={[{ value: "invoice", label: "Invoice" }, { value: "line", label: "Line item" }]} />
            <Choice label={what === "approved" ? "Approved" : "Uploaded"} value={range} onChange={setRange}
              options={[{ value: "this_month", label: "This month" }, { value: "last_month", label: "Last month" },
                { value: "all", label: "All time" }, { value: "custom", label: "Dates" }]} />
            {range === "custom" && (
              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs text-ink-2">From
                  <input type="date" value={custom.from} onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))}
                    className="mt-1 h-9 w-full rounded-[var(--radius-control)] border border-line bg-surface px-2 text-sm" />
                </label>
                <label className="text-xs text-ink-2">To
                  <input type="date" value={custom.to} onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))}
                    className="mt-1 h-9 w-full rounded-[var(--radius-control)] border border-line bg-surface px-2 text-sm" />
                </label>
              </div>
            )}
            {what === "all" && <p className="text-xs text-warn">Includes invoices that are not approved yet. Their values may still change.</p>}
          </div>
          <Button variant="primary" className="mt-4 w-full" onClick={download} disabled={busy}>
            {busy ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <Download aria-hidden className="size-4" />}
            {busy ? "Preparing…" : "Download"}
          </Button>
        </div>
      )}
    </div>
  );
}
