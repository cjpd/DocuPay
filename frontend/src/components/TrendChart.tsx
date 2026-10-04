import type { Stats } from "@/lib/types";

/** Daily documents received (bar) and auto-approved (filled part), last 14 days. */
export function TrendChart({ series }: { series: Stats["series"] }) {
  const max = Math.max(1, ...series.map((d) => d.received));
  const W = 560, H = 140, gap = 6;
  const bw = (W - gap * (series.length - 1)) / series.length;
  const fmt = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H + 22}`} className="w-full" role="img" aria-label="Documents received and auto-approved per day, last 14 days">
        {[0.5, 1].map((t) => (
          <line key={t} x1="0" x2={W} y1={H - H * t} y2={H - H * t} stroke="var(--line)" strokeDasharray="3 4" />
        ))}
        {series.map((d, i) => {
          const x = i * (bw + gap);
          const h = (d.received / max) * H;
          const ha = (d.auto_approved / max) * H;
          return (
            <g key={d.date}>
              <title>{`${fmt(d.date)}: ${d.received} received, ${d.auto_approved} auto-approved`}</title>
              <rect x={x} y={H - Math.max(h, 2)} width={bw} height={Math.max(h, 2)} rx="3" fill="var(--surface-2)" />
              {ha > 0 && <rect x={x} y={H - ha} width={bw} height={ha} rx="3" fill="var(--accent)" />}
              {(i === 0 || i === series.length - 1 || i === Math.floor(series.length / 2)) && (
                <text x={x + bw / 2} y={H + 16} textAnchor="middle" fontSize="11" fill="var(--muted)">{fmt(d.date)}</text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-2 flex gap-4 text-xs text-muted">
        <span className="flex items-center gap-1.5"><i className="size-2.5 rounded-sm bg-accent" /> Auto-approved</span>
        <span className="flex items-center gap-1.5"><i className="size-2.5 rounded-sm bg-surface-2 ring-1 ring-line" /> Received</span>
      </figcaption>
    </figure>
  );
}
