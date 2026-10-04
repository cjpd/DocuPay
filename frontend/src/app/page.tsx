"use client";

import Link from "next/link";
import { ArrowRight, CheckCircle2, FileStack, Inbox, Sparkles } from "lucide-react";
import { AppShell, PageHeader } from "@/components/AppShell";
import { Pipeline } from "@/components/Pipeline";
import { StatusBadge } from "@/components/StatusBadge";
import { TrendChart } from "@/components/TrendChart";
import { UploadDropzone } from "@/components/UploadDropzone";
import { Card, CardHeader, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { ago, money, percent } from "@/lib/format";
import { useDocuments, useMe, useStats } from "@/lib/hooks";
import type { Stats } from "@/lib/types";

export default function OverviewPage() {
  return (
    <AppShell>
      <Overview />
    </AppShell>
  );
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function Overview() {
  const me = useMe();
  const stats = useStats();
  const docs = useDocuments();
  const name = me.data?.first_name || me.data?.username;

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title={name ? `${greeting()}, ${name}` : greeting()}
        hint="Last 30 days. Numbers update as documents are read."
      />

      {stats.isError && <ErrorState message={(stats.error as Error).message} onRetry={() => stats.refetch()} />}

      <div className="grid gap-4 lg:grid-cols-[1.25fr_1fr]">
        <StraightThrough stats={stats.data} />
        <NeedsYou stats={stats.data} />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-3">
        <Metric label="Documents received" value={stats.data?.received} />
        <Metric label="Approved value" value={stats.data ? approvedValue(stats.data) : undefined} />
        <Metric
          label="Processing cost"
          value={stats.data ? money(stats.data.processing_cost_usd, "USD") : undefined}
          hint={stats.data && stats.data.received ? `${money(Number(stats.data.processing_cost_usd) / stats.data.received, "USD")} per document` : undefined}
        />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-[1.25fr_1fr]">
        <Card>
          <CardHeader title="Recent documents" action={<Link href="/documents/" className="text-sm font-medium text-accent hover:underline">All documents</Link>} />
          {docs.isLoading ? (
            <div className="space-y-3 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>
          ) : !docs.data?.length ? (
            <EmptyState icon={<FileStack className="size-5" />} title="No documents yet">
              Drop your first invoice on the right. It is read and checked in seconds.
            </EmptyState>
          ) : (
            <ul className="divide-y divide-line">
              {docs.data.slice(0, 6).map((d) => (
                <li key={d.id} className="flex items-center gap-4 px-5 py-3 animate-rise">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-ink">{d.extracted_data?.vendor_name || d.file_name}</p>
                    <p className="text-xs text-muted">
                      {d.extracted_data?.invoice_number ? (
                        <span className="font-mono">{d.extracted_data.invoice_number}</span>
                      ) : d.status === "pending" || d.status === "processing" ? "Reading…" : d.status === "failed" ? "Could not be read" : "No invoice number found"}{" "}
                      · {ago(d.created_at)}
                    </p>
                  </div>
                  <div className="hidden sm:block"><Pipeline doc={d} compact /></div>
                  <span className="w-28 text-right font-mono text-sm tabular">
                    {money(d.extracted_data?.total_amount, d.extracted_data?.currency || undefined)}
                  </span>
                  <span className="w-28 text-right"><StatusBadge doc={d} /></span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="space-y-4">
          <UploadDropzone />
          <Card className="p-5">
            <h2 className="mb-3 text-[15px] font-semibold">Last 14 days</h2>
            {stats.data ? <TrendChart series={stats.data.series} /> : <Skeleton className="h-36" />}
          </Card>
        </div>
      </div>
    </div>
  );
}

function approvedValue(s: Stats) {
  const entries = Object.entries(s.approved_value);
  if (!entries.length) return money(0);
  return entries.map(([cur, v]) => money(v, cur === "?" ? undefined : cur)).join(" · ");
}

function StraightThrough({ stats }: { stats?: Stats }) {
  const rate = stats?.straight_through_rate;
  const R = 44, C = 2 * Math.PI * R;
  return (
    <Card className="flex items-center gap-6 p-6">
      <div className="relative size-28 shrink-0">
        <svg viewBox="0 0 100 100" className="size-28 -rotate-90" aria-hidden>
          <circle cx="50" cy="50" r={R} fill="none" stroke="var(--surface-2)" strokeWidth="10" />
          <circle
            cx="50" cy="50" r={R} fill="none" stroke="var(--accent)" strokeWidth="10" strokeLinecap="round"
            strokeDasharray={C} strokeDashoffset={C * (1 - (rate ?? 0))}
            style={{ transition: "stroke-dashoffset 900ms cubic-bezier(.2,.8,.2,1)" }}
          />
        </svg>
        <span className="absolute inset-0 grid place-items-center text-2xl font-semibold tabular">{stats ? percent(rate) : "…"}</span>
      </div>
      <div>
        <p className="flex items-center gap-1.5 text-sm font-medium text-ink-2"><Sparkles aria-hidden className="size-4 text-accent" /> Handled without you</p>
        <p className="mt-1 text-sm text-muted">
          {stats && stats.auto_approved + stats.approved_by_person + stats.needs_review > 0
            ? `${stats.auto_approved} of ${stats.auto_approved + stats.approved_by_person + stats.needs_review} processed invoices passed every check and were approved automatically.`
            : "Invoices that pass every check are approved automatically. The rest come to you."}
        </p>
      </div>
    </Card>
  );
}

function NeedsYou({ stats }: { stats?: Stats }) {
  const n = stats?.needs_review ?? 0;
  if (stats && n === 0)
    return (
      <Card className="flex items-center gap-4 p-6">
        <span className="grid size-12 place-items-center rounded-full bg-ok-soft text-ok" style={{ animation: "pop 500ms ease-out" }}>
          <CheckCircle2 aria-hidden className="size-6" />
        </span>
        <div>
          <p className="text-[15px] font-semibold">You are all caught up</p>
          <p className="text-sm text-muted">No invoice is waiting for a decision.</p>
        </div>
      </Card>
    );
  return (
    <Link href="/review/" className="group block rounded-[var(--radius-card)] border border-warn/30 bg-warn-soft p-6 transition-colors hover:border-warn/60">
      <p className="flex items-center gap-1.5 text-sm font-medium text-warn"><Inbox aria-hidden className="size-4" /> Needs your decision</p>
      <p className="mt-2 text-4xl font-semibold tabular text-ink">{stats ? n : "…"}</p>
      <p className="mt-2 flex items-center gap-1 text-sm font-medium text-ink">
        Start reviewing <ArrowRight aria-hidden className="size-4 transition-transform group-hover:translate-x-0.5" />
      </p>
    </Link>
  );
}

function Metric({ label, value, hint }: { label: string; value?: string | number; hint?: string }) {
  return (
    <Card className="p-5">
      <p className="text-sm text-muted">{label}</p>
      {value === undefined ? <Skeleton className="mt-2 h-7 w-24" /> : <p className="mt-1 text-xl font-semibold tabular">{value}</p>}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </Card>
  );
}
