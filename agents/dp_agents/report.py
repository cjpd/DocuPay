"""The daily report: loops closed, decisions, hours saved, Opus calls and cost."""
import json
import os
import time
from typing import Optional


def build(store, since: Optional[float] = None) -> dict:
    since = since or time.time() - 86400
    q = lambda sql: store.one(sql, (since,))
    loops = q("SELECT COUNT(*) AS c FROM events WHERE ts>=? AND kind='forward'")["c"]
    back = q("SELECT COUNT(*) AS c FROM events WHERE ts>=? AND kind='back'")["c"]
    escalations = q("SELECT COUNT(*) AS c FROM events WHERE ts>=? AND kind IN ('escalation','error')")["c"]
    shipped = store.rows("SELECT number, title, data FROM tickets WHERE state='shipped' AND closed_at>=?", (since,))
    dec = q("SELECT COUNT(*) AS n, SUM(source='code') AS code, SUM(source='opus') AS opus FROM decisions WHERE ts>=?")
    opus = q("SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS usd, SUM(ok=0) AS failed FROM opus_calls WHERE ts>=?")
    gates = q("SELECT COUNT(*) AS n, SUM(status='pending') AS pending FROM gates WHERE ts>=?")
    hours_per_point = float(os.environ.get("HOURS_PER_POINT", "1.0"))
    points = sum(json.loads(t["data"]).get("ticket", {}).get("points", 0) for t in shipped)
    n_shipped = len(shipped)
    return {
        "since": since,
        "loops_closed": loops, "back_edges": back, "escalations": escalations,
        "tickets_shipped": [f"#{t['number']} {t['title']}" for t in shipped],
        "hours_saved_estimate": round(points * hours_per_point, 1),
        "decisions": dec["n"] or 0, "decisions_by_code": dec["code"] or 0, "decisions_by_opus": dec["opus"] or 0,
        "opus_calls": opus["n"] or 0, "opus_usd": round(opus["usd"], 2), "opus_failed": opus["failed"] or 0,
        "usd_per_shipped_ticket": round(opus["usd"] / n_shipped, 2) if n_shipped else None,
        "gates_opened": gates["n"] or 0, "gates_pending": gates["pending"] or 0,
    }


def text(r: dict) -> str:
    per = f"${r['usd_per_shipped_ticket']} per shipped ticket" if r["usd_per_shipped_ticket"] is not None else "no ticket shipped"
    return "\n".join([
        "📊 DocuPay agents, last 24 h",
        f"Loops closed: {r['loops_closed']} (back edges {r['back_edges']}, escalations {r['escalations']})",
        f"Tickets ready for PR: {len(r['tickets_shipped'])} " + (", ".join(r["tickets_shipped"][:5]) if r["tickets_shipped"] else ""),
        f"Hours saved (estimate): {r['hours_saved_estimate']}",
        f"Decisions: {r['decisions']} — by code rules {r['decisions_by_code']}, by Opus {r['decisions_by_opus']}",
        f"Opus: {r['opus_calls']} calls, ${r['opus_usd']} ({per}), failed {r['opus_failed']}",
        f"Gates: {r['gates_opened']} opened, {r['gates_pending']} waiting for you",
    ])
