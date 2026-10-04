import { Check, FileText, ScanText, ShieldCheck, UserRound, X } from "lucide-react";
import type { Doc } from "@/lib/types";
import { cx } from "./ui";

/** Where a document is in its journey: received -> read -> checked -> decided. */
export function Pipeline({ doc, compact }: { doc: Doc; compact?: boolean }) {
  const s = doc.status;
  const done = s === "approved" || s === "requires_review" || s === "processed";
  const steps = [
    { label: "Received", icon: FileText, state: "done" as const },
    { label: "Read", icon: ScanText, state: s === "pending" ? "todo" : s === "processing" ? "active" : s === "failed" ? "failed" : "done" },
    { label: "Checked", icon: ShieldCheck, state: done ? "done" : "todo" },
    {
      label: s === "requires_review" ? "Needs you" : "Approved",
      icon: s === "requires_review" ? UserRound : Check,
      state: s === "approved" || s === "processed" ? "done" : s === "requires_review" ? "attention" : "todo",
    },
  ] as const;

  return (
    <ol className="flex items-center" aria-label="Processing steps">
      {steps.map((step, i) => {
        const Icon = step.state === "failed" ? X : step.icon;
        return (
          <li key={step.label} className="flex items-center">
            {i > 0 && (
              <span aria-hidden className={cx("h-px", compact ? "w-3" : "w-6 sm:w-10", step.state === "todo" ? "bg-line" : "bg-ok/60")} />
            )}
            <span
              title={step.label}
              className={cx(
                "grid place-items-center rounded-full border",
                compact ? "size-6" : "size-8",
                step.state === "done" && "border-ok/40 bg-ok-soft text-ok",
                step.state === "active" && "animate-pulse-soft border-accent/40 bg-accent-soft text-accent",
                step.state === "attention" && "border-warn/40 bg-warn-soft text-warn",
                step.state === "failed" && "border-bad/40 bg-bad-soft text-bad",
                step.state === "todo" && "border-line bg-surface text-muted",
              )}
            >
              <Icon aria-hidden className={compact ? "size-3" : "size-4"} />
              <span className="sr-only">{`${step.label}: ${step.state}`}</span>
            </span>
            {!compact && <span className="ml-2 hidden text-xs text-ink-2 lg:inline">{step.label}</span>}
          </li>
        );
      })}
    </ol>
  );
}
