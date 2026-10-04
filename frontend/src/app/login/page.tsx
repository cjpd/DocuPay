"use client";

import { useRouter } from "next/navigation";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ArrowRight, Loader2 } from "lucide-react";
import { signIn } from "@/lib/api";
import { Logo } from "@/components/AppShell";
import { Button } from "@/components/ui";

export default function LoginPage() {
  return (
    <Suspense>
      <Login />
    </Suspense>
  );
}

function Login() {
  const router = useRouter();
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(username, password);
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-screen lg:grid-cols-[1fr_1.1fr]">
      <div className="flex flex-col justify-center px-6 py-12 sm:px-12 lg:px-20">
        <div className="mx-auto w-full max-w-sm animate-rise">
          <div className="mb-10 flex items-center gap-2">
            <Logo className="size-8" />
            <span className="text-lg font-semibold tracking-tight">DocuPay</span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
          <p className="mt-1 text-sm text-muted">Your invoices are waiting.</p>
          <form onSubmit={submit} className="mt-8 space-y-4">
            <div>
              <label htmlFor="username" className="text-sm font-medium text-ink-2">Username</label>
              <input
                id="username" autoComplete="username" required value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="mt-1.5 h-10 w-full rounded-[var(--radius-control)] border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
              />
            </div>
            <div>
              <label htmlFor="password" className="text-sm font-medium text-ink-2">Password</label>
              <input
                id="password" type="password" autoComplete="current-password" required value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1.5 h-10 w-full rounded-[var(--radius-control)] border border-line bg-surface px-3 text-sm outline-none focus:border-accent"
              />
            </div>
            {error && <p role="alert" className="text-sm text-bad">{error}</p>}
            <Button variant="primary" type="submit" disabled={busy} className="w-full">
              {busy ? <Loader2 aria-hidden className="size-4 animate-spin" /> : <ArrowRight aria-hidden className="size-4" />}
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </div>
      </div>
      <aside className="relative hidden overflow-hidden border-l border-line bg-surface-2 lg:block" aria-hidden>
        <LoginArt />
      </aside>
    </main>
  );
}

/** A stack of invoices passing their checks: what the product does, at a glance. */
function LoginArt() {
  const rows = [
    ["Northwind Traders", "INV-20931", "$4,180.00", "Approved"],
    ["Fabrikam GmbH", "RE-2026-118", "€912.40", "Approved"],
    ["Contoso Ltd", "C-7781", "£1,250.00", "Needs you"],
    ["Tailspin Toys", "TT-00442", "$389.99", "Approved"],
  ];
  return (
    <div className="flex h-full flex-col justify-center px-16">
      <p className="mb-6 max-w-md text-3xl font-semibold leading-tight tracking-tight text-ink">
        Every invoice read, checked and approved. A person only where it matters.
      </p>
      <div className="max-w-md space-y-3">
        {rows.map(([vendor, number, total, state], i) => (
          <div
            key={number}
            style={{ animationDelay: `${150 + i * 120}ms` }}
            className="flex animate-rise items-center justify-between rounded-[var(--radius-card)] border border-line bg-surface px-4 py-3"
          >
            <div>
              <p className="text-sm font-medium">{vendor}</p>
              <p className="font-mono text-xs text-muted">{number}</p>
            </div>
            <div className="text-right">
              <p className="font-mono text-sm tabular">{total}</p>
              <p className={state === "Approved" ? "text-xs font-medium text-ok" : "text-xs font-medium text-warn"}>{state}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
