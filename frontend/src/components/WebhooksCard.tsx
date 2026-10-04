"use client";

import { useState } from "react";
import { Copy, KeyRound, Plus, RotateCw, Send, Trash2, Webhook as WebhookIcon } from "lucide-react";
import { toast } from "sonner";
import { ago } from "@/lib/format";
import {
  useDeleteWebhook, useDeliveries, useRetryDelivery, useRotateSecret, useSaveWebhook, useTestWebhook, useWebhooks,
} from "@/lib/hooks";
import type { Webhook, WebhookDelivery, WebhookEvent } from "@/lib/types";
import { Badge, Button, Card, CardHeader, EmptyState, Skeleton, cx } from "./ui";

const EVENTS: { value: WebhookEvent; label: string }[] = [
  { value: "invoice.approved", label: "Approved" },
  { value: "invoice.needs_review", label: "Needs review" },
  { value: "invoice.rejected", label: "Rejected" },
  { value: "invoice.failed", label: "Failed" },
];

const STATUS_TONE = { delivered: "ok", pending: "info", failed: "bad" } as const;

export function WebhooksCard({ canEdit }: { canEdit: boolean }) {
  const hooks = useWebhooks();
  const deliveries = useDeliveries();
  const [adding, setAdding] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);

  return (
    <Card className="mt-6">
      <CardHeader
        title="Webhooks"
        hint="Send each invoice to your own system as soon as it is approved. Messages are signed so you can check they came from DocuPay."
        action={canEdit && !adding && <Button size="sm" onClick={() => setAdding(true)}><Plus aria-hidden className="size-4" /> Add endpoint</Button>}
      />
      {secret && <SecretOnce secret={secret} onClose={() => setSecret(null)} />}
      {adding && <WebhookForm onDone={(s) => { setAdding(false); if (s) setSecret(s); }} />}
      {hooks.isLoading ? (
        <div className="p-5"><Skeleton className="h-12" /></div>
      ) : !hooks.data?.length && !adding ? (
        <EmptyState icon={<WebhookIcon className="size-5" />} title="No endpoints yet">
          Add an https address of your accounting or ERP system. You can also export to CSV from Documents.
        </EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {hooks.data?.map((h) => <WebhookRow key={h.id} hook={h} canEdit={canEdit} onSecret={setSecret} />)}
        </ul>
      )}
      {!!deliveries.data?.length && <DeliveryLog deliveries={deliveries.data.slice(0, 10)} canEdit={canEdit} />}
    </Card>
  );
}

function SecretOnce({ secret, onClose }: { secret: string; onClose: () => void }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(secret);
      toast.success("Signing secret copied");
    } catch {
      toast.error("Copy failed. Select the text and copy it.");
    }
  }
  return (
    <div role="status" className="mx-5 mt-4 rounded-[var(--radius-control)] border border-accent/30 bg-accent-soft p-4">
      <p className="flex items-center gap-2 text-sm font-medium"><KeyRound aria-hidden className="size-4 text-accent" /> Signing secret</p>
      <p className="mt-1 text-xs text-ink-2">Save it in your system now. It is shown only once. Use it to check the DocuPay-Signature header.</p>
      <div className="mt-2 flex gap-2">
        <code className="min-w-0 flex-1 truncate rounded bg-surface px-2 py-1.5 font-mono text-xs select-all">{secret}</code>
        <Button size="sm" onClick={copy}><Copy aria-hidden className="size-4" /> Copy</Button>
        <Button size="sm" variant="ghost" onClick={onClose}>Done</Button>
      </div>
    </div>
  );
}

function WebhookForm({ onDone }: { onDone: (secret?: string) => void }) {
  const save = useSaveWebhook();
  const [url, setUrl] = useState("https://");
  const [description, setDescription] = useState("");
  const [events, setEvents] = useState<WebhookEvent[]>(["invoice.approved"]);
  const input = "mt-1 h-9 w-full rounded-[var(--radius-control)] border border-line bg-surface px-2.5 text-sm outline-none focus:border-accent";

  function submit(e: React.FormEvent) {
    e.preventDefault();
    save.mutate({ target_url: url, description, events }, {
      onSuccess: (hook) => { toast.success("Endpoint added"); onDone((hook as Webhook).signing_secret ?? undefined); },
      onError: (err) => toast.error(err.message),
    });
  }

  return (
    <form onSubmit={submit} className="grid gap-3 border-b border-line px-5 py-4 sm:grid-cols-2">
      <label className="text-xs font-medium text-ink-2 sm:col-span-2">Endpoint URL
        <input required type="url" value={url} onChange={(e) => setUrl(e.target.value)} className={`${input} font-mono`} />
      </label>
      <label className="text-xs font-medium text-ink-2 sm:col-span-2">Description <span className="font-normal text-muted">(optional)</span>
        <input value={description} placeholder="ERP production" onChange={(e) => setDescription(e.target.value)} className={input} />
      </label>
      <fieldset className="sm:col-span-2">
        <legend className="text-xs font-medium text-ink-2">Send these events</legend>
        <div className="mt-1.5 flex flex-wrap gap-3">
          {EVENTS.map((ev) => (
            <label key={ev.value} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox" checked={events.includes(ev.value)}
                onChange={(e) => setEvents((cur) => (e.target.checked ? [...cur, ev.value] : cur.filter((x) => x !== ev.value)))}
              />
              {ev.label}
            </label>
          ))}
        </div>
        <p className="mt-1 text-xs text-muted">Only approved invoices are final. Use the other events to follow progress.</p>
      </fieldset>
      <div className="flex justify-end gap-2 sm:col-span-2">
        <Button type="button" variant="ghost" onClick={() => onDone()}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending || !events.length}>Add endpoint</Button>
      </div>
    </form>
  );
}

function WebhookRow({ hook, canEdit, onSecret }: { hook: Webhook; canEdit: boolean; onSecret: (s: string) => void }) {
  const save = useSaveWebhook();
  const test = useTestWebhook();
  const rotate = useRotateSecret();
  const remove = useDeleteWebhook();
  const [confirming, setConfirming] = useState(false);
  const last = hook.last_delivery;

  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5">
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 text-sm font-medium">
          <span className="truncate font-mono text-[13px]">{hook.target_url}</span>
          {!hook.is_active && <Badge>Off</Badge>}
        </p>
        <p className="truncate text-xs text-muted">
          {hook.description && `${hook.description} · `}
          {hook.events.map((e) => EVENTS.find((x) => x.value === e)?.label).join(", ")}
          {last && <> · last {last.event_type}: <span className={last.status === "failed" ? "text-bad" : ""}>{last.status}</span> {ago(last.at)}</>}
        </p>
      </div>
      {canEdit && (
        <div className="flex flex-wrap gap-1">
          <Button size="sm" disabled={test.isPending} onClick={() => test.mutate(hook.id, {
            onSuccess: () => toast.success("Test event sent. See the delivery log below."), onError: (e) => toast.error(e.message),
          })}><Send aria-hidden className="size-4" /> Test</Button>
          <Button size="sm" variant="ghost" onClick={() => save.mutate({ id: hook.id, is_active: !hook.is_active }, { onError: (e) => toast.error(e.message) })}>
            {hook.is_active ? "Turn off" : "Turn on"}
          </Button>
          <Button size="sm" variant="ghost" aria-label="New signing secret" title="New signing secret" onClick={() => rotate.mutate(hook.id, {
            onSuccess: (h) => { const s = (h as Webhook).signing_secret; if (s) onSecret(s); }, onError: (e) => toast.error(e.message),
          })}><KeyRound aria-hidden className="size-4" /></Button>
          {confirming ? (
            <>
              <Button size="sm" variant="danger" onClick={() => remove.mutate(hook.id, { onError: (e) => toast.error(e.message) })}>Delete</Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>Keep</Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" aria-label="Delete endpoint" onClick={() => setConfirming(true)}><Trash2 aria-hidden className="size-4" /></Button>
          )}
        </div>
      )}
    </li>
  );
}

function DeliveryLog({ deliveries, canEdit }: { deliveries: WebhookDelivery[]; canEdit: boolean }) {
  const retry = useRetryDelivery();
  return (
    <div className="border-t border-line px-5 py-4">
      <h3 className="mb-2 text-sm font-semibold">Recent deliveries</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-muted">
            <tr><th className="py-1.5 pr-3 font-medium">Event</th><th className="py-1.5 pr-3 font-medium">Status</th>
              <th className="py-1.5 pr-3 font-medium">Answer</th><th className="py-1.5 pr-3 font-medium">Tries</th>
              <th className="py-1.5 pr-3 font-medium">When</th><th /></tr>
          </thead>
          <tbody className="divide-y divide-line">
            {deliveries.map((d) => (
              <tr key={d.id}>
                <td className="py-2 pr-3 font-mono">{d.event_type}{d.document && <span className="text-muted"> #{d.document}</span>}</td>
                <td className="py-2 pr-3"><Badge tone={STATUS_TONE[d.status]}>{d.status}</Badge></td>
                <td className={cx("max-w-56 truncate py-2 pr-3", d.last_error && "text-bad")} title={d.last_error}>
                  {d.status_code ? `HTTP ${d.status_code}` : d.last_error || "—"}{d.response_ms !== null && ` · ${d.response_ms} ms`}
                </td>
                <td className="py-2 pr-3 tabular">{d.attempts}{d.next_attempt_at && <span className="text-muted"> · next {ago(d.next_attempt_at)}</span>}</td>
                <td className="py-2 pr-3 text-muted">{ago(d.created_at)}</td>
                <td className="py-2 text-right">
                  {canEdit && d.status === "failed" && (
                    <Button size="sm" variant="ghost" disabled={retry.isPending}
                      onClick={() => retry.mutate(d.id, { onError: (e) => toast.error(e.message) })}>
                      <RotateCw aria-hidden className="size-3.5" /> Retry
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
