"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FileStack, Inbox, LayoutDashboard, LogOut, Settings } from "lucide-react";
import { session, signOut, useSession } from "@/lib/api";
import { useMe, useOrganizations, useReviewQueue } from "@/lib/hooks";
import { cx } from "./ui";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/review/", label: "Review", icon: Inbox, badge: true },
  { href: "/documents/", label: "Documents", icon: FileStack },
  { href: "/settings/", label: "Settings", icon: Settings },
];

export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  // The session cookie is httpOnly, so ask the API who is signed in. A 401 sends the
  // visitor to the sign-in page (see api.ts).
  const me = useMe();
  if (!me.data) return null;
  return <Shell path={path}>{children}</Shell>;
}

function Shell({ path, children }: { path: string; children: ReactNode }) {
  const router = useRouter();
  const qc = useQueryClient();
  const queue = useReviewQueue();
  const pending = queue.data?.count ?? 0;
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href.replace(/\/$/, "")));

  return (
    <div className="min-h-screen md:grid md:grid-cols-[232px_1fr]">
      <aside className="sticky top-0 z-20 flex items-center gap-2 border-b border-line bg-surface px-4 py-3 md:h-screen md:flex-col md:items-stretch md:gap-1 md:border-r md:border-b-0 md:px-3 md:py-5">
        <Link href="/" className="mr-auto flex items-center gap-2 px-2 md:mr-0 md:mb-6">
          <Logo />
          <span className="text-[15px] font-semibold tracking-tight">DocuPay</span>
        </Link>
        <nav className="flex gap-1 md:flex-col" aria-label="Main">
          {NAV.map(({ href, label, icon: Icon, badge }) => (
            <Link
              key={href}
              href={href}
              aria-current={active(href) ? "page" : undefined}
              className={cx(
                "flex items-center gap-3 rounded-[var(--radius-control)] px-2.5 py-2 text-sm transition-colors",
                active(href) ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
              )}
            >
              <Icon aria-hidden className="size-4" />
              <span className="hidden sm:inline">{label}</span>
              {badge && pending > 0 && (
                <span className="ml-auto rounded-full bg-warn px-1.5 text-[11px] font-semibold text-white tabular dark:text-[#1d1300]">
                  {pending}
                  <span className="sr-only"> invoices need you</span>
                </span>
              )}
            </Link>
          ))}
        </nav>
        <div className="md:mt-auto md:border-t md:border-line md:pt-3">
          <OrgSwitcher />
          <Account onSignOut={async () => { await signOut(); qc.clear(); router.replace("/login/"); }} />
        </div>
      </aside>
      <main className="min-w-0 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">{children}</main>
    </div>
  );
}

function OrgSwitcher() {
  const orgs = useOrganizations();
  const qc = useQueryClient();
  const current = useSession(session.org);
  if (!orgs.data || orgs.data.length < 2) return null;
  return (
    <label className="hidden px-2 pb-2 md:block">
      <span className="text-[11px] font-medium uppercase tracking-wide text-muted">Company</span>
      <select
        className="mt-1 w-full rounded-[var(--radius-control)] border border-line bg-surface px-2 py-1.5 text-sm"
        value={current ?? ""}
        onChange={(e) => {
          const id = e.target.value ? Number(e.target.value) : null;
          session.setOrg(id);
          qc.invalidateQueries();
        }}
      >
        <option value="">All companies</option>
        {orgs.data.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
      </select>
    </label>
  );
}

function Account({ onSignOut }: { onSignOut: () => void }) {
  const me = useMe();
  const name = me.data ? me.data.first_name || me.data.username : "";
  return (
    <div className="flex items-center gap-2 md:px-2">
      <span aria-hidden className="hidden size-7 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent uppercase md:grid">
        {name.slice(0, 1)}
      </span>
      <span className="hidden min-w-0 flex-1 truncate text-sm text-ink-2 md:block">{name}</span>
      <button onClick={onSignOut} className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-ink" aria-label="Sign out" title="Sign out">
        <LogOut aria-hidden className="size-4" />
      </button>
    </div>
  );
}

export function Logo({ className = "size-7" }: { className?: string }) {
  // A folded receipt with a check mark: a document that has been verified.
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect x="4" y="3" width="24" height="26" rx="5" fill="var(--accent)" />
      <path d="M10 10h12M10 15h8" stroke="var(--accent-ink)" strokeWidth="2.2" strokeLinecap="round" opacity=".55" />
      <path d="m11 21 3.2 3.2L21.5 17" stroke="var(--accent-ink)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </svg>
  );
}

export function PageHeader({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink text-balance">{title}</h1>
        {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
      </div>
      {action}
    </header>
  );
}
