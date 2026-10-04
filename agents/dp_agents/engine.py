"""
The graph runner. Code owns the loop: which node runs next, every counter, every gate.
One ticket at a time; the pending edge is stored so a restart resumes where it stopped.
"""
import json
import logging
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import edges as E
from . import graph as graph_mod
from . import nodes
from . import questions as questions_mod
from . import windows
from .opus import BudgetExceeded

log = logging.getLogger("dp_agents")


@dataclass
class Ctx:
    store: Any
    jev: Any
    opus: Any
    gh: Any
    ws: Any
    notify: Any
    repo: Path
    graph: graph_mod.Graph
    questions: dict


def _pending(ctx, n) -> tuple[Optional[str], Any]:
    p = (ctx.store.ticket(n)["data"] or {}).get("pending")
    if not p:
        return None, None
    return p["node"], E.EDGE_TYPES[p["type"]].model_validate(p["payload"])


def _set_pending(ctx, n, node: Optional[str], payload: Any) -> None:
    data = ctx.store.ticket(n)["data"]
    data["pending"] = None if node is None else {"node": node, "type": type(payload).__name__,
                                                 "payload": json.loads(payload.model_dump_json())}
    ctx.store.upsert_ticket(n, data=data, node=node)


def escalate(ctx, n: int, esc: E.Escalation, kind: str = "escalation") -> None:
    ctx.store.edge(n, esc.node, nodes.ESCALATE, esc, kind=kind)
    ctx.store.upsert_ticket(n, state="parked", result=esc.reason, closed_at=time.time())
    _set_pending(ctx, n, None, None)
    try:
        ctx.gh.add_label(n, "agent-parked")
    except Exception:
        pass
    label_forks(ctx, n, success=False)
    ctx.notify.send(f"⚠️ #{n} parked at {esc.node}: {esc.reason}")


def step(ctx, n: int) -> str:
    """Run the pending node of ticket n once. Returns the ticket state afterwards."""
    t = ctx.store.ticket(n)
    ticket = E.Ticket.model_validate(t["data"]["ticket"])
    plan = E.Plan.model_validate(t["data"]["plan"]) if t["data"].get("plan") else None
    node, payload = _pending(ctx, n)
    if node is None:
        node, payload = "N2", ticket
    if node == "N2":
        nxt, out, kind = nodes.n2_plan(ctx, n, ticket)
    elif node == "N3":
        feedback = payload if isinstance(payload, (E.Failure, E.Findings)) else None
        nxt, out, kind = nodes.n3_build(ctx, n, ticket, plan, feedback)
    elif node == "N4":
        nxt, out, kind = nodes.n4_verify(ctx, n, payload)
    elif node == "N5":
        nxt, out, kind = nodes.n5_review(ctx, n, ticket, payload)
    elif node == "N6":
        nxt, out, kind = nodes.n6_evaluate(ctx, n, payload)
    elif node == "N7":
        nxt, out, kind = nodes.n7_ship(ctx, n, ticket, payload)
    else:
        raise RuntimeError(f"ticket {n} has unknown pending node {node}")

    if nxt == nodes.ESCALATE:
        escalate(ctx, n, out)
        return "parked"
    ctx.store.edge(n, node, nxt, out, kind=kind)
    if nxt == "G1":
        gate_id = ctx.store.open_gate("G1", n, f"Open PR for #{n}: {ticket.title}", out)
        ctx.store.upsert_ticket(n, state="waiting")
        _set_pending(ctx, n, "G1", out)
        flags = " ⚠️ HIGH RISK" if out.risk >= ctx.graph.thresholds.risk_high else ""
        flags += " ⚠️ sensitive paths" if out.sensitive else ""
        ctx.notify.send(f"🚦 Gate G1 #{gate_id}: open a draft PR for #{n} \"{ticket.title}\"{flags}\n"
                        f"Branch {out.branch}. Reply /approve {gate_id} or /reject {gate_id}")
        return "waiting"
    _set_pending(ctx, n, nxt, out)
    return "running"


def run_ticket(ctx, n: int) -> str:
    state = "running"
    while state == "running":
        try:
            state = step(ctx, n)
        except BudgetExceeded as exc:
            escalate(ctx, n, E.Escalation(node=ctx.store.ticket(n)["node"] or "?", reason=str(exc)))
            return "parked"
        except Exception as exc:  # an unexpected error parks the ticket; it never loops
            log.exception("node failed")
            escalate(ctx, n, E.Escalation(node=ctx.store.ticket(n)["node"] or "?",
                                          reason=f"error: {type(exc).__name__}: {str(exc)[:300]}"), kind="error")
            ctx.store.edge(n, "engine", "log", {"traceback": traceback.format_exc()[-3000:]}, kind="error")
            return "parked"
    return state


def handle_gates(ctx) -> None:
    for g in ctx.store.rows("SELECT * FROM gates WHERE status!='pending' AND COALESCE(decided_by,'') NOT LIKE 'done:%'"):
        if g["gate"] == "G1" and g["ticket"] is not None:
            n = g["ticket"]
            req = E.ShipRequest.model_validate_json(g["payload"])
            if g["status"] == "approved":
                url = ctx.gh.open_draft_pr(head=req.branch, base=ctx.ws.base, title=req.title, body=req.body)
                ctx.store.upsert_ticket(n, state="shipped", result=url, closed_at=time.time())
                label_forks(ctx, n, success=True)
                ctx.notify.send(f"✅ Draft PR opened for #{n}: {url}\nMerging is gate G2: only you merge.")
            else:
                ctx.store.upsert_ticket(n, state="rejected", result="rejected at G1", closed_at=time.time())
                label_forks(ctx, n, success=False)
                ctx.notify.send(f"#{n} rejected at G1. The branch stays for you to inspect; nothing was deleted.")
            _set_pending(ctx, n, None, None)
        elif g["gate"] == "G4" and g["status"] == "approved":
            from . import improve
            improve.apply_threshold_proposal(ctx, json.loads(g["payload"]))
            ctx.notify.send(f"✅ G4 #{g['id']} applied: thresholds updated in graph.md")
        ctx.store.exec("UPDATE gates SET decided_by = 'done:' || COALESCE(decided_by,'') WHERE id=?", (g["id"],))


def label_forks(ctx, n: int, success: bool) -> None:
    """Ground truth for tuning and for the N9 replay, known only once the ticket has ended."""
    rows = ctx.store.rows("SELECT id, fork, answer FROM forks WHERE ticket=? AND label IS NULL ORDER BY id", (n,))
    if not rows:
        return
    # Each N5 call logs its risk fork first, then done:1..k, so the final round is everything after the last risk fork.
    last_risk = max((r["id"] for r in rows if r["fork"] == "risk"), default=None)
    final_files = set()
    try:
        final_files = set(ctx.ws.diff_files(n)[0])
    except Exception:
        pass
    retry_rows = [r for r in rows if r["fork"] == "retry"]
    for r in rows:
        base = r["fork"].split(":")[0]
        label = None
        if base == "specified":
            label = True if success else None  # a parked ticket may have failed for other reasons
        elif base == "done" and last_risk is not None and r["id"] > last_risk:
            label = success
        elif base == "retry":
            label = success if r is retry_rows[-1] else True
        elif base == "target_file" and r["answer"]:
            label = json.loads(r["answer"]) if json.loads(r["answer"]) in final_files else None
        elif base == "risk":
            label = "approved" if success else "rejected"
        if label is not None:
            ctx.store.label_fork(r["id"], json.dumps(label))


def poll_inputs(ctx) -> None:
    try:
        at = ctx.gh.owner_last_activity()
        if at:
            ctx.store.touch("github", at)
    except Exception:
        log.warning("github activity poll failed", exc_info=True)
    for verb, gate_id, at in ctx.notify.commands():
        ctx.store.touch("telegram", at or time.time())
        if verb in ("approve", "reject"):
            ok = ctx.store.decide_gate(gate_id, verb == "approve", "telegram")
            ctx.notify.send(f"Gate #{gate_id} {verb}d." if ok else f"Gate #{gate_id} is not pending.")


def tick(ctx) -> None:
    """One pass of the daemon loop."""
    ctx.graph = graph_mod.load()
    ctx.questions = questions_mod.load()
    poll_inputs(ctx)
    handle_gates(ctx)
    running = ctx.store.one("SELECT number FROM tickets WHERE state='running' ORDER BY started_at LIMIT 1")
    n = running["number"] if running else None
    if n is None and not ctx.store.one("SELECT number FROM tickets WHERE state='waiting'"):
        n = nodes.n1_intake(ctx)
    if n is not None:
        run_ticket(ctx, n)
    from . import improve
    improve.maybe_run(ctx, windows.current(ctx.store, ctx.graph.window))
