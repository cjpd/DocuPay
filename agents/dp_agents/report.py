"""The daily report: loops closed, forks answered, hours saved, Opus calls, Jev latency and cost."""
import os
import time
from typing import Optional


def build(store, since: Optional[float] = None) -> dict:
    since = since or time.time() - 86400
    q = lambda sql, *a: store.one(sql, (since, *a))
    loops = q("SELECT COUNT(*) AS c FROM events WHERE ts>=? AND kind='forward'")["c"]
    back = q("SELECT COUNT(*) AS c FROM events WHERE ts>=? AND kind='back'")["c"]
    escalations = q("SELECT COUNT(*) AS c FROM events WHERE ts>=? AND kind IN ('escalation','error')")["c"]
    shipped = store.rows("SELECT number, title, data FROM tickets WHERE state='shipped' AND closed_at>=?", (since,))
    forks = q("SELECT COUNT(*) AS n, SUM(route='jev') AS sharp, SUM(route='opus') AS opus, "
              "SUM(route='opus-fallback') AS fallback FROM forks WHERE ts>=?")
    calls = store.rows("SELECT jev_call, MAX(latency_ms) AS ms, MAX(input_tokens) AS tin, MAX(output_tokens) AS tout, "
                       "COUNT(*) AS forks FROM forks WHERE ts>=? AND jev_call IS NOT NULL GROUP BY jev_call", (since,))
    opus = q("SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS usd, SUM(ok=0) AS failed FROM opus_calls WHERE ts>=?")
    gates = q("SELECT COUNT(*) AS n, SUM(status='pending') AS pending FROM gates WHERE ts>=?")

    import json
    hours_per_point = float(os.environ.get("HOURS_PER_POINT", "1.0"))
    points = sum(json.loads(t["data"]).get("ticket", {}).get("points", 0) for t in shipped)
    n_calls = len(calls)
    avg_ms = sum(c["ms"] or 0 for c in calls) / n_calls if n_calls else None
    slow = sum(1 for c in calls if (c["ms"] or 0) > 1000)
    price_call = os.environ.get("JEV_USD_PER_CALL")
    n_forks = forks["n"] or 0
    jev_cost = float(price_call) * n_calls if price_call else None
    return {
        "since": since,
        "loops_closed": loops, "back_edges": back, "escalations": escalations,
        "tickets_shipped": [f"#{t['number']} {t['title']}" for t in shipped],
        "hours_saved_estimate": round(points * hours_per_point, 1),
        "forks_answered": n_forks,
        "forks_sharp_by_jev": forks["sharp"] or 0, "forks_to_opus": forks["opus"] or 0,
        "forks_jev_failed": forks["fallback"] or 0,
        "jev_calls": n_calls, "jev_avg_latency_ms": round(avg_ms, 1) if avg_ms is not None else None,
        "jev_calls_over_1s": slow,
        "jev_cost_per_decision_usd": round(jev_cost / n_forks, 6) if jev_cost is not None and n_forks else None,
        "jev_tokens_per_call": round(sum((c["tin"] or 0) + (c["tout"] or 0) for c in calls) / n_calls, 1) if n_calls else None,
        "opus_calls": opus["n"] or 0, "opus_usd": round(opus["usd"], 2), "opus_failed": opus["failed"] or 0,
        "gates_opened": gates["n"] or 0, "gates_pending": gates["pending"] or 0,
    }


def text(r: dict) -> str:
    def v(x, unit=""):
        return "n/a" if x is None else f"{x}{unit}"
    cost = v(r["jev_cost_per_decision_usd"]) if r["jev_cost_per_decision_usd"] is not None else "set JEV_USD_PER_CALL"
    return "\n".join([
        "📊 DocuPay agents, last 24 h",
        f"Loops closed: {r['loops_closed']} (back edges {r['back_edges']}, escalations {r['escalations']})",
        f"Tickets ready for PR: {len(r['tickets_shipped'])} " + (", ".join(r["tickets_shipped"][:5]) if r["tickets_shipped"] else ""),
        f"Hours saved (estimate): {r['hours_saved_estimate']}",
        f"Forks: {r['forks_answered']} — Jev sharp {r['forks_sharp_by_jev']}, to Opus {r['forks_to_opus']}, Jev failed {r['forks_jev_failed']}",
        f"Jev: {r['jev_calls']} calls, avg {v(r['jev_avg_latency_ms'], ' ms')}, over 1 s: {r['jev_calls_over_1s']}, cost/decision: {cost}",
        f"Opus: {r['opus_calls']} calls, ${r['opus_usd']}, failed {r['opus_failed']}",
        f"Gates: {r['gates_opened']} opened, {r['gates_pending']} waiting for you",
    ])
