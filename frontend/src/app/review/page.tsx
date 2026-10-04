"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, CheckCircle2, ChevronDown, PartyPopper, ShieldAlert, ThumbsDown } from "lucide-react";
import { toast } from "sonner";
import { AppShell, PageHeader } from "@/components/AppShell";
import { DocumentPreview } from "@/components/DocumentPreview";
import { Badge, Button, Card, EmptyState, ErrorState, Kbd, Skeleton, cx } from "@/components/ui";
import { FIELD_LABELS, ago, checkLabel, money } from "@/lib/format";
import { useApprove, useReject, useReviewQueue, useReviewTask } from "@/lib/hooks";
import type { Check as CheckT, ExtractedData, ReviewTask } from "@/lib/types";

export default function ReviewPage() {
  return (
    <AppShell>
      <Suspense>
        <Review />
      </Suspense>
    </AppShell>
  );
}

function Review() {
  const queue = useReviewQueue();
  const params = useSearchParams();
  const router = useRouter();
  const wanted = Number(params.get("id"));
  const page = useMemo(() => queue.data?.results ?? [], [queue.data]);
  // A link can point at an invoice beyond the first page of the queue: load it on its own.
  const inPage = page.some((t) => t.id === wanted);
  const linked = useReviewTask(wanted && queue.data && !inPage ? wanted : null);
  const tasks = useMemo(
    () => (linked.data && linked.data.status === "pending" && !inPage ? [linked.data, ...page] : page),
    [linked.data, inPage, page],
  );
  const waiting = queue.data?.count ?? 0;
  const [cleared, setCleared] = useState(0);

  const index = Math.max(0, tasks.findIndex((t) => t.id === wanted));
  const task = tasks[index];

  const go = useCallback(
    (i: number) => {
      const next = tasks[(i + tasks.length) % tasks.length];
      if (next) router.replace(`/review/?id=${next.id}`, { scroll: false });
    },
    [tasks, router],
  );

  if (queue.isLoading || linked.isLoading) return <Skeleton className="h-[70vh]" />;
  if (queue.isError) return <ErrorState message={(queue.error as Error).message} onRetry={() => queue.refetch()} />;
  if (!task) return <AllCaughtUp cleared={cleared} />;

  return (
    <div>
      <PageHeader
        title="Review"
        hint={`${waiting} invoice${waiting === 1 ? "" : "s"} need${waiting === 1 ? "s" : ""} your decision. Oldest first.`}
        action={
          <p className="hidden items-center gap-3 text-xs text-muted md:flex">
            <span><Kbd>A</Kbd> approve</span><span><Kbd>R</Kbd> reject</span><span><Kbd>J</Kbd>/<Kbd>K</Kbd> next / previous</span>
          </p>
        }
      />
      <div className="grid gap-4 xl:grid-cols-[220px_minmax(0,1fr)_400px]">
        <QueueList tasks={tasks} current={task.id} onPick={(i) => go(i)} />
        <Card className="min-w-0 p-3"><DocumentPreview key={task.document} id={task.document} name={task.document_detail.file_name} /></Card>
        <Decision
          key={task.id}
          task={task}
          position={`${index + 1} of ${waiting}`}
          onDone={(verb) => {
            setCleared((n) => n + 1);
            toast.success(verb === "approved" ? "Approved" : "Rejected", { duration: 1800 });
            if (tasks.length > 1) go(index + 1 === tasks.length ? 0 : index + 1);
          }}
          onNext={() => go(index + 1)}
          onPrev={() => go(index - 1)}
        />
      </div>
    </div>
  );
}

function QueueList({ tasks, current, onPick }: { tasks: ReviewTask[]; current: number; onPick: (i: number) => void }) {
  return (
    <Card className="hidden max-h-[80vh] overflow-y-auto xl:block">
      <ul className="divide-y divide-line" aria-label="Review queue">
        {tasks.map((t, i) => {
          const d = t.document_detail.extracted_data;
          const fails = d?.validation.filter((c) => c.status === "fail").length ?? 0;
          return (
            <li key={t.id}>
              <button
                onClick={() => onPick(i)}
                aria-current={t.id === current ? "true" : undefined}
                className={cx("block w-full px-3 py-2.5 text-left transition-colors", t.id === current ? "bg-accent-soft" : "hover:bg-surface-2")}
              >
                <p className="truncate text-sm font-medium">{d?.vendor_name || t.document_detail.file_name}</p>
                <p className="flex justify-between text-xs text-muted">
                  <span className="font-mono tabular">{money(d?.total_amount, d?.currency || undefined)}</span>
                  <span className="text-warn">{fails} issue{fails === 1 ? "" : "s"}</span>
                </p>
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

const EDITABLE: (keyof ExtractedData)[] = [
  "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date", "currency", "subtotal", "tax_amount", "total_amount", "amount_due", "purchase_order", "bank_account", "bank_code",
];
const DATE_FIELDS = new Set(["invoice_date", "due_date"]);
const MONEY_FIELDS = new Set(["subtotal", "tax_amount", "total_amount", "amount_due"]);

function Decision({ task, position, onDone, onNext, onPrev }: {
  task: ReviewTask; position: string; onDone: (verb: "approved" | "rejected") => void; onNext: () => void; onPrev: () => void;
}) {
  const doc = task.document_detail;
  const data = doc.extracted_data;
  const initial = useMemo(() => Object.fromEntries(EDITABLE.map((f) => [f, (data?.[f] as string | null) ?? ""])), [data]);
  const [values, setValues] = useState<Record<string, string>>(initial);
  const approve = useApprove();
  const reject = useReject();
  const busy = approve.isPending || reject.isPending;

  const failed = data?.validation.filter((c) => c.status === "fail") ?? [];
  const passed = data?.validation.filter((c) => c.status === "pass") ?? [];
  const flagged = new Set(failed.flatMap((c) => c.fields));
  const changed = Object.keys(values).filter((k) => values[k] !== initial[k]);

  const doApprove = useCallback(async () => {
    // Empty dates and amounts are sent as null; empty text stays "".
    const corrections = Object.fromEntries(
      changed.map((k) => [k, values[k] === "" && (DATE_FIELDS.has(k) || MONEY_FIELDS.has(k)) ? null : values[k]]),
    );
    try {
      await approve.mutateAsync({ taskId: task.id, corrections });
      onDone("approved");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, [approve, changed, values, task.id, onDone]);

  const doReject = useCallback(async () => {
    try {
      await reject.mutateAsync(task.id);
      onDone("rejected");
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, [reject, task.id, onDone]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (busy || e.metaKey || e.ctrlKey || e.altKey || ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
      const k = e.key.toLowerCase();
      if (k === "a") { e.preventDefault(); doApprove(); }
      else if (k === "r") { e.preventDefault(); doReject(); }
      else if (k === "j") onNext();
      else if (k === "k") onPrev();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, doApprove, doReject, onNext, onPrev]);

  return (
    <Card className="flex max-h-none flex-col xl:max-h-[80vh]">
      <div className="border-b border-line px-5 py-4">
        <div className="flex items-center justify-between text-xs text-muted">
          <span>{position}</span><span>Received {ago(doc.created_at)}</span>
        </div>
        <h2 className="mt-1 truncate text-lg font-semibold">{data?.vendor_name || doc.file_name}</h2>
        <VendorBadge data={data} />
        <p className="font-mono text-2xl font-semibold tabular">{money(values.total_amount, values.currency || undefined)}</p>
      </div>

      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
        {doc.error_message && <ErrorState message={doc.error_message} />}
        {failed.length > 0 && (
          <section aria-labelledby="why">
            <h3 id="why" className="mb-2 text-sm font-semibold">Why this needs you</h3>
            <ul className="space-y-2">{failed.map((c) => <FailedCheck key={c.name} check={c} />)}</ul>
          </section>
        )}

        <section aria-labelledby="fields">
          <h3 id="fields" className="mb-2 text-sm font-semibold">Fields <span className="font-normal text-muted">· edit anything that is wrong</span></h3>
          <div className="grid grid-cols-2 gap-3">
            {EDITABLE.map((f) => (
              <Field
                key={f} name={f} value={values[f]} flagged={flagged.has(f)} changed={values[f] !== initial[f]}
                confidence={data?.field_confidences?.[f]}
                onChange={(v) => setValues((s) => ({ ...s, [f]: v }))}
              />
            ))}
          </div>
        </section>

        {!!data?.line_items.length && (
          <section aria-labelledby="lines">
            <h3 id="lines" className="mb-2 text-sm font-semibold">Line items</h3>
            <div className="overflow-x-auto rounded border border-line">
              <table className="w-full text-xs">
                <thead className="bg-surface-2 text-left text-muted">
                  <tr><th className="px-2 py-1.5 font-medium">Description</th><th className="px-2 py-1.5 text-right font-medium">Qty</th><th className="px-2 py-1.5 text-right font-medium">Amount</th></tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {data.line_items.map((li, i) => (
                    <tr key={i}>
                      <td className="px-2 py-1.5">{li.description || "—"}</td>
                      <td className="px-2 py-1.5 text-right font-mono tabular">{li.quantity ?? "—"}</td>
                      <td className="px-2 py-1.5 text-right font-mono tabular">{money(li.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {passed.length > 0 && <PassedChecks checks={passed} />}
      </div>

      <div className="flex items-center gap-2 border-t border-line px-5 py-4">
        <Button variant="danger" onClick={doReject} disabled={busy} aria-keyshortcuts="r">
          <ThumbsDown aria-hidden className="size-4" /> Reject
        </Button>
        <Button variant="success" onClick={doApprove} disabled={busy} className="flex-1" aria-keyshortcuts="a">
          <Check aria-hidden className="size-4" /> {changed.length ? `Approve with ${changed.length} fix${changed.length === 1 ? "" : "es"}` : "Approve"}
        </Button>
      </div>
    </Card>
  );
}

function VendorBadge({ data }: { data: ExtractedData | null }) {
  if (!data?.vendor_name) return null;
  const v = data.vendor_detail;
  if (!v) return <p className="mt-0.5 text-xs text-muted">New vendor: not in your vendor list yet. Approving adds it.</p>;
  return (
    <p className="mt-0.5 flex items-center gap-1.5 text-xs">
      {v.is_blocked ? <Badge tone="bad">Blocked vendor</Badge> : <Badge tone="ok">Known vendor</Badge>}
      <a href="/vendors/" className="text-muted hover:underline">{v.name}{v.tax_id ? ` · ${v.tax_id}` : ""}</a>
    </p>
  );
}

function FailedCheck({ check }: { check: CheckT }) {
  // A changed bank account is the classic invoice fraud: it gets the strongest warning.
  const danger = check.name === "bank_account" && check.severity === "critical";
  return (
    <li className={cx("flex gap-2.5 rounded-[var(--radius-control)] border px-3 py-2.5",
      danger ? "border-bad/40 bg-bad-soft" : "border-warn/30 bg-warn-soft")}>
      {danger
        ? <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-bad" />
        : <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />}
      <div className="min-w-0">
        <p className="text-sm font-medium text-ink">{checkLabel(check)}</p>
        <p className="text-xs text-ink-2">{check.message}</p>
      </div>
    </li>
  );
}

function PassedChecks({ checks }: { checks: CheckT[] }) {
  const [open, setOpen] = useState(false);
  return (
    <section>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="flex w-full items-center gap-2 text-sm text-ok">
        <CheckCircle2 aria-hidden className="size-4" /> {checks.length} checks passed
        <ChevronDown aria-hidden className={cx("ml-auto size-4 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <ul className="mt-2 space-y-1 pl-6 text-xs text-ink-2">
          {checks.map((c) => <li key={c.name}>{checkLabel(c)}</li>)}
        </ul>
      )}
    </section>
  );
}

function Field({ name, value, flagged, changed, confidence, onChange }: {
  name: string; value: string; flagged: boolean; changed: boolean; confidence?: number; onChange: (v: string) => void;
}) {
  const id = `field-${name}`;
  const wide = name === "vendor_name" || name === "bank_account";
  return (
    <div className={wide ? "col-span-2" : ""}>
      <label htmlFor={id} className="flex items-center gap-1.5 text-xs font-medium text-ink-2">
        {FIELD_LABELS[name] || name}
        {flagged && !changed && <Badge tone="warn" className="px-1.5 py-0 text-[10px]">check</Badge>}
        {changed && <Badge tone="info" className="px-1.5 py-0 text-[10px]">edited</Badge>}
        {!flagged && !changed && confidence === 1 && <span className="sr-only">(verified)</span>}
      </label>
      <input
        id={id}
        spellCheck={false}
        type={DATE_FIELDS.has(name) ? "date" : "text"}
        inputMode={MONEY_FIELDS.has(name) ? "decimal" : undefined}
        value={value}
        onChange={(e) => onChange(name === "currency" ? e.target.value.toUpperCase().slice(0, 3) : e.target.value)}
        className={cx(
          "mt-1 h-9 w-full rounded-[var(--radius-control)] border bg-surface px-2.5 text-sm outline-none focus:border-accent",
          MONEY_FIELDS.has(name) && "text-right font-mono tabular",
          (name === "bank_account" || name === "bank_code" || name === "vendor_tax_id") && "font-mono",
          flagged && !changed ? "border-warn/60" : changed ? "border-accent/60" : "border-line",
        )}
      />
    </div>
  );
}

function AllCaughtUp({ cleared }: { cleared: number }) {
  return (
    <div className="mx-auto max-w-lg pt-16">
      <Card>
        <EmptyState
          icon={<span style={{ animation: "pop 500ms ease-out" }}>{cleared ? <PartyPopper className="size-6 text-ok" /> : <CheckCircle2 className="size-6 text-ok" />}</span>}
          title={cleared ? `Queue cleared: ${cleared} decision${cleared === 1 ? "" : "s"} made` : "Nothing to review"}
        >
          Every invoice has passed its checks or has been decided. New ones that need you will show up here.
        </EmptyState>
      </Card>
    </div>
  );
}
