"use client";

import { useState } from "react";
import { Check, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { AppShell, PageHeader } from "@/components/AppShell";
import { Button, Card, CardHeader, ErrorState, Skeleton } from "@/components/ui";
import { WebhooksCard } from "@/components/WebhooksCard";
import { CHECK_LABELS } from "@/lib/format";
import { session, useSession } from "@/lib/api";
import { useOrganizations, useUpdateOrganization } from "@/lib/hooks";
import type { Organization } from "@/lib/types";

export default function SettingsPage() {
  return (
    <AppShell>
      <Settings />
    </AppShell>
  );
}

function Settings() {
  const orgs = useOrganizations();
  const selected = useSession(session.org);
  const org = orgs.data?.find((o) => String(o.id) === selected) ?? orgs.data?.[0];

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="Settings" hint={org ? `Rules for ${org.name}` : undefined} />
      {orgs.isError && <ErrorState message={(orgs.error as Error).message} onRetry={() => orgs.refetch()} />}
      {orgs.isLoading && <Skeleton className="h-64" />}
      {org && <Rules key={org.id} org={org} />}
      {org && <WebhooksCard canEdit={org.my_role === "owner" || org.my_role === "admin"} />}
      <HowItWorks />
    </div>
  );
}

function Rules({ org }: { org: Organization }) {
  const update = useUpdateOrganization();
  const [limit, setLimit] = useState(org.auto_approve_max_amount ?? "");
  const [newVendors, setNewVendors] = useState(org.review_new_vendors);
  const dirty = limit !== (org.auto_approve_max_amount ?? "") || newVendors !== org.review_new_vendors;

  function save(e: React.FormEvent) {
    e.preventDefault();
    update.mutate(
      { id: org.id, auto_approve_max_amount: limit === "" ? null : limit, review_new_vendors: newVendors },
      { onSuccess: () => toast.success("Rules saved"), onError: (err) => toast.error(err.message) },
    );
  }

  return (
    <Card>
      <CardHeader title="Auto-approval rules" hint="An invoice is approved automatically only when every check passes and these rules allow it." />
      <form onSubmit={save} className="divide-y divide-line">
        <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
          <div className="max-w-md">
            <label htmlFor="limit" className="text-sm font-medium">Amount limit</label>
            <p className="text-sm text-muted">Invoices above this total always come to a person. Leave empty for no limit.</p>
          </div>
          <input
            id="limit" inputMode="decimal" placeholder="No limit" value={limit}
            onChange={(e) => setLimit(e.target.value.replace(/[^\d.]/g, ""))}
            className="h-10 w-40 rounded-[var(--radius-control)] border border-line bg-surface px-3 text-right font-mono text-sm tabular outline-none focus:border-accent"
          />
        </div>
        <div className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
          <div className="max-w-md">
            <p id="nv-label" className="text-sm font-medium">Review new vendors</p>
            <p className="text-sm text-muted">The first invoice from a vendor you have never approved comes to a person.</p>
          </div>
          <button
            type="button" role="switch" aria-checked={newVendors} aria-labelledby="nv-label"
            onClick={() => setNewVendors((v) => !v)}
            className={`relative h-6 w-11 rounded-full transition-colors ${newVendors ? "bg-accent" : "bg-line"}`}
          >
            <span className={`absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow transition-transform ${newVendors ? "translate-x-5" : ""}`} />
          </button>
        </div>
        <div className="flex justify-end px-5 py-4">
          <Button variant="primary" type="submit" disabled={!dirty || update.isPending}>Save rules</Button>
        </div>
      </form>
    </Card>
  );
}

function HowItWorks() {
  return (
    <Card className="mt-6">
      <CardHeader title="What every invoice is checked for" hint="If any of these fails, the invoice comes to you with the reason." />
      <ul className="grid gap-x-6 gap-y-2.5 px-5 py-4 sm:grid-cols-2">
        {Object.entries(CHECK_LABELS).map(([key, label]) => (
          <li key={key} className="flex items-start gap-2 text-sm text-ink-2">
            {key === "new_vendor" || key === "amount_limit"
              ? <ShieldCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-accent" />
              : <Check aria-hidden className="mt-0.5 size-4 shrink-0 text-ok" />}
            {label}
          </li>
        ))}
      </ul>
    </Card>
  );
}
