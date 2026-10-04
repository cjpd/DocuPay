"use client";

import { useState } from "react";
import { Ban, Building2, Pencil, Plus, Search, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AppShell, PageHeader } from "@/components/AppShell";
import { Badge, Button, Card, EmptyState, ErrorState, Skeleton } from "@/components/ui";
import { ago, maskAccount } from "@/lib/format";
import { session, useSession } from "@/lib/api";
import { useDeleteVendor, useOrganizations, useSaveVendor, useVendors } from "@/lib/hooks";
import type { Vendor } from "@/lib/types";

export default function VendorsPage() {
  return (
    <AppShell>
      <Vendors />
    </AppShell>
  );
}

function useCanEdit() {
  const orgs = useOrganizations();
  const selected = useSession(session.org);
  const org = orgs.data?.find((o) => String(o.id) === selected) ?? orgs.data?.[0];
  return org?.my_role === "owner" || org?.my_role === "admin";
}

function Vendors() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const vendors = useVendors(q.trim(), page);
  const canEdit = useCanEdit();
  const pages = vendors.data ? Math.max(1, Math.ceil(vendors.data.count / 20)) : 1;

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Vendors"
        hint="Learned from the invoices you approve. Invoices are checked against this list: tax ID, currency, blocked vendors."
        action={canEdit && !adding && <Button variant="primary" onClick={() => setAdding(true)}><Plus aria-hidden className="size-4" /> Add vendor</Button>}
      />
      {adding && <Card className="mb-4"><VendorForm onDone={() => setAdding(false)} /></Card>}

      <label className="relative mb-4 block sm:w-80">
        <span className="sr-only">Search vendors by name or tax ID</span>
        <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
        <input
          type="search" value={q} placeholder="Name or tax ID"
          onChange={(e) => { setQ(e.target.value); setPage(1); }}
          className="h-10 w-full rounded-[var(--radius-control)] border border-line bg-surface pr-3 pl-9 text-sm outline-none focus:border-accent"
        />
      </label>

      <Card>
        {vendors.isError ? (
          <div className="p-5"><ErrorState message={(vendors.error as Error).message} onRetry={() => vendors.refetch()} /></div>
        ) : vendors.isLoading ? (
          <div className="space-y-3 p-5">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12" />)}</div>
        ) : !vendors.data?.results.length ? (
          <EmptyState icon={<Building2 className="size-5" />} title={q ? "No vendor matches" : "No vendors yet"}>
            {q ? "Try another name or tax ID." : "When you approve an invoice, its vendor is added here automatically."}
          </EmptyState>
        ) : (
          <ul className="divide-y divide-line">{vendors.data.results.map((v) => <Row key={v.id} vendor={v} canEdit={canEdit} />)}</ul>
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

function Row({ vendor, canEdit }: { vendor: Vendor; canEdit: boolean }) {
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const save = useSaveVendor();
  const remove = useDeleteVendor();
  if (editing) return <li><VendorForm vendor={vendor} onDone={() => setEditing(false)} /></li>;

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5">
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-sm font-medium">
          {vendor.name}
          {vendor.is_blocked ? <Badge tone="bad"><Ban aria-hidden className="size-3" /> Blocked</Badge> : null}
        </p>
        <p className="truncate text-xs text-muted">
          {vendor.tax_id ? <span className="font-mono">{vendor.tax_id}</span> : "No tax ID"}
          {vendor.default_currency && ` · bills in ${vendor.default_currency}`}
          {vendor.bank_account ? <> · pays to <span className="font-mono">{maskAccount(vendor.bank_account)}</span></> : " · no bank account yet"}
          {vendor.aliases.length > 0 && ` · also "${vendor.aliases.join('", "')}"`}
        </p>
      </div>
      <p className="w-36 text-right text-xs text-muted tabular">
        {vendor.invoice_count} invoice{vendor.invoice_count === 1 ? "" : "s"}
        {vendor.last_invoice_at && <><br />last {ago(vendor.last_invoice_at)}</>}
      </p>
      {canEdit && (
        <div className="flex gap-1">
          <Button size="sm" variant="ghost" aria-label={`Edit ${vendor.name}`} onClick={() => setEditing(true)}><Pencil aria-hidden className="size-4" /></Button>
          <Button
            size="sm" variant={vendor.is_blocked ? "secondary" : "danger"} disabled={save.isPending}
            onClick={() => save.mutate({ id: vendor.id, is_blocked: !vendor.is_blocked }, {
              onSuccess: () => toast.success(vendor.is_blocked ? `${vendor.name} unblocked` : `${vendor.name} blocked: its invoices will come to a person`),
              onError: (e) => toast.error(e.message),
            })}
          >
            {vendor.is_blocked ? <><ShieldCheck aria-hidden className="size-4" /> Unblock</> : <><Ban aria-hidden className="size-4" /> Block</>}
          </Button>
          {confirming ? (
            <>
              <Button size="sm" variant="danger" onClick={() => remove.mutate(vendor.id, { onError: (e) => toast.error(e.message) })}>Delete</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Keep</Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" aria-label={`Delete ${vendor.name}`} onClick={() => setConfirming(true)}><Trash2 aria-hidden className="size-4" /></Button>
          )}
        </div>
      )}
    </li>
  );
}

function VendorForm({ vendor, onDone }: { vendor?: Vendor; onDone: () => void }) {
  const save = useSaveVendor();
  const [name, setName] = useState(vendor?.name ?? "");
  const [taxId, setTaxId] = useState(vendor?.tax_id ?? "");
  const [currency, setCurrency] = useState(vendor?.default_currency ?? "");
  const [aliases, setAliases] = useState((vendor?.aliases ?? []).join(", "));
  const [bank, setBank] = useState(vendor?.bank_account ?? "");
  const [bankCode, setBankCode] = useState(vendor?.bank_code ?? "");
  const bankChanged = !!vendor?.bank_account && bank.replace(/\s/g, "").toUpperCase() !== vendor.bank_account;
  const input = "mt-1 h-9 w-full rounded-[var(--radius-control)] border border-line bg-surface px-2.5 text-sm outline-none focus:border-accent";

  function submit(e: React.FormEvent) {
    e.preventDefault();
    save.mutate(
      { id: vendor?.id, name, tax_id: taxId, default_currency: currency, bank_account: bank, bank_code: bankCode,
        aliases: aliases.split(",").map((a) => a.trim()).filter(Boolean) },
      { onSuccess: () => { toast.success(vendor ? "Vendor saved" : "Vendor added"); onDone(); }, onError: (err) => toast.error(err.message) },
    );
  }

  return (
    <form onSubmit={submit} className="grid gap-3 px-5 py-4 sm:grid-cols-2">
      <label className="text-xs font-medium text-ink-2 sm:col-span-2">Name
        <input required value={name} onChange={(e) => setName(e.target.value)} className={input} />
      </label>
      <label className="text-xs font-medium text-ink-2">Tax ID (VAT, EIN)
        <input value={taxId} onChange={(e) => setTaxId(e.target.value)} className={`${input} font-mono`} />
      </label>
      <label className="text-xs font-medium text-ink-2">Usual currency
        <input value={currency} maxLength={3} placeholder="USD" onChange={(e) => setCurrency(e.target.value.toUpperCase())} className={`${input} font-mono`} />
      </label>
      <label className="text-xs font-medium text-ink-2">Bank account (IBAN or account number)
        <input value={bank} onChange={(e) => setBank(e.target.value)} spellCheck={false} className={`${input} font-mono`} />
      </label>
      <label className="text-xs font-medium text-ink-2">BIC / routing / sort code
        <input value={bankCode} onChange={(e) => setBankCode(e.target.value)} spellCheck={false} className={`${input} font-mono`} />
      </label>
      {bankChanged && (
        <p role="alert" className="rounded-[var(--radius-control)] bg-bad-soft px-3 py-2 text-xs text-bad sm:col-span-2">
          You are changing where this vendor is paid. Confirm the new account with the vendor by phone, using a number
          you already have, not one from the invoice or email.
        </p>
      )}
      <label className="text-xs font-medium text-ink-2 sm:col-span-2">Other names on invoices <span className="font-normal text-muted">(comma separated)</span>
        <input value={aliases} onChange={(e) => setAliases(e.target.value)} className={input} />
      </label>
      <div className="flex justify-end gap-2 sm:col-span-2">
        <Button type="button" variant="ghost" onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>{vendor ? "Save" : "Add vendor"}</Button>
      </div>
    </form>
  );
}
