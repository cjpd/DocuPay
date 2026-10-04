"""
The nodes. Each one is a single loop: one narrow action, a check that proves it is done, and a
stop rule counted by code. Each returns (next node, edge payload, edge kind).
"""
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import forks, limits
from . import opus as opus_mod
from .edges import (Commit, Escalation, Evaluated, Failure, Finding, Findings, Plan, Regression,
                    Reviewed, ShipRequest, Ticket, Unplannable, Verified)
from .workspace import check_diff, git

STOP = "STOP"
ESCALATE = "ESCALATE"


def _schema(model) -> dict:
    s = model.model_json_schema()
    s.pop("title", None)
    return s


# -- N1 intake ---------------------------------------------------------------------------------

def n1_intake(ctx) -> int | None:
    """Pick the next ready issue. Code sorts; Jev only says whether it is specified enough."""
    for labeled_at, ticket, has_acceptance in ctx.gh.ready_tickets():
        known = ctx.store.ticket(ticket.number)
        if known and known["state"] in ("parked", "needs_spec", "rejected") and labeled_at \
                and labeled_at > (known["labeled_at"] or 0) + 1:
            # The owner fixed the issue and labeled it again: start over in a clean worktree.
            ctx.ws.discard(ticket.number)
            ctx.store.exec("DELETE FROM tickets WHERE number=?", (ticket.number,))
            known = None
        if known and known["state"] != "new":
            continue
        ctx.store.upsert_ticket(ticket.number, title=ticket.title, state="new", node="N1",
                                labeled_by=ticket.labeled_by, labeled_at=labeled_at)
        reason = None
        if not has_acceptance:
            reason = "the issue has no acceptance criteria (a '- [ ]' checklist)"
        elif ticket.points > limits.MAX_TICKET_POINTS:
            reason = f"{ticket.points} points is above the {limits.MAX_TICKET_POINTS}-point limit; split it"
        else:
            state = {"title": ticket.title, "body": ticket.body[:6000], "acceptance": ticket.acceptance}
            d = forks.ask(ctx, ticket=ticket.number, node="N1", state=state, forks=[forks.noul_fork("specified", ctx)])
            if not d["specified"]:
                reason = "Jev and Opus judged the issue not specified enough to build"
        if reason:
            ctx.gh.add_label(ticket.number, "needs-spec")
            ctx.store.upsert_ticket(ticket.number, state="needs_spec", result=reason)
            ctx.store.edge(ticket.number, "N1", STOP, Unplannable(reason=reason), kind="stop")
            ctx.notify.send(f"#{ticket.number} needs a clearer spec: {reason}")
            continue
        ctx.store.upsert_ticket(ticket.number, state="running", node="N2", data={"ticket": ticket.model_dump()},
                                branch=ctx.ws.branch(ticket.number))
        ctx.store.edge(ticket.number, "N1", "N2", ticket)
        return ticket.number
    return None


# -- N2 plan -----------------------------------------------------------------------------------

def n2_plan(ctx, n: int, ticket: Ticket):
    attempts = ctx.store.bump(n, "plan_attempts")
    if attempts > limits.MAX_PLAN_ATTEMPTS:
        return ESCALATE, Escalation(node="N2", reason=f"no valid plan after {limits.MAX_PLAN_ATTEMPTS} attempts"), "escalation"
    wt = ctx.ws.ensure(n)
    last_error = (ctx.store.ticket(n)["data"] or {}).get("plan_error")
    numbered = "\n".join(f"{i}. {a}" for i, a in enumerate(ticket.acceptance, 1))
    data = opus_mod.call(
        ctx.opus, ctx.store, ticket=n, node="N2", purpose="plan",
        prompt=(f"Plan GitHub issue #{n} for this repository. Read the code you need. Do not edit anything.\n"
                f"Title: {ticket.title}\nIssue body:\n{opus_mod.untrusted(ticket.body)}\n"
                f"Acceptance criteria (numbered):\n{opus_mod.untrusted(numbered)}\n"
                "Return the plan: steps, every file to change (existing paths), new files, and in `tests` one "
                "test per acceptance criterion keyed by its number as a string ('1', '2', ...)."
                + (f"\nThe last plan was refused by code: {last_error}" if last_error else "")),
        schema=_schema(Plan), cwd=str(wt), tools=opus_mod.READ_TOOLS,
    )
    plan = Plan.model_validate(data)
    # The check: files exist (or are declared new) and every criterion maps to a test.
    missing = [f for f in plan.files if f not in plan.new_files and not (wt / f).exists()]
    expected = {str(i) for i in range(1, len(ticket.acceptance) + 1)}
    error = None
    if missing:
        error = f"files do not exist and are not declared new: {missing[:5]}"
    elif set(plan.tests) != expected:
        error = f"tests must map exactly to criteria {sorted(expected)}, got {sorted(plan.tests)}"
    if error:
        ctx.store.upsert_ticket(n, data={**ctx.store.ticket(n)["data"], "plan_error": error})
        return "N2", Unplannable(reason=error), "retry"
    start = None
    existing = [f for f in plan.files if f not in plan.new_files]
    if len(existing) >= 2:
        d = forks.ask(ctx, ticket=n, node="N2",
                      state={"plan": plan.summary, "first_step": plan.steps[0].description, "files": existing},
                      forks=[forks.choice_fork("target_file", ctx, existing)])
        start = d["target_file"]
    ctx.store.upsert_ticket(n, node="N3", data={**ctx.store.ticket(n)["data"], "plan": plan.model_dump(),
                                                "start_file": start, "plan_error": None})
    return "N3", plan, "forward"


# -- N3 build ----------------------------------------------------------------------------------

def n3_build(ctx, n: int, ticket: Ticket, plan: Plan, feedback: Any = None):
    rounds = ctx.store.bump(n, "build_rounds")
    if rounds > limits.MAX_BUILD_ROUNDS:
        return ESCALATE, Escalation(node="N3", reason=f"still failing after {limits.MAX_BUILD_ROUNDS} build rounds"), "escalation"
    if feedback is not None:
        d = forks.ask(ctx, ticket=n, node="N3",
                      state={"round": rounds, "max_rounds": limits.MAX_BUILD_ROUNDS,
                             "failure": json.loads(feedback.model_dump_json())},
                      forks=[forks.noul_fork("retry", ctx)])
        if not d["retry"]:
            return ESCALATE, Escalation(node="N3", reason="retry judged pointless for this failure"), "escalation"
    wt = ctx.ws.ensure(n)
    start = (ctx.store.ticket(n)["data"] or {}).get("start_file")
    fb = ""
    if feedback is not None:
        fb = f"\nThe last attempt was sent back with:\n{opus_mod.untrusted(feedback.model_dump_json(indent=1), 12000)}\nFix that first."
    opus_mod.call(
        ctx.opus, ctx.store, ticket=n, node="N3", purpose="build",
        prompt=(f"Implement this plan for issue #{n} by editing files in this worktree. Write the tests too.\n"
                f"Plan:\n{plan.model_dump_json(indent=1)}\n"
                f"Acceptance criteria:\n{opus_mod.untrusted(chr(10).join(ticket.acceptance))}\n"
                + (f"Start with {start}.\n" if start else "")
                + "Do not commit, push or touch files outside backend/ and frontend/." + fb
                + "\nAnswer with a one-line summary of what you changed."),
        schema={"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
        cwd=str(wt), tools=opus_mod.BUILD_TOOLS, edit=True,
    )
    files, lines = ctx.ws.diff_files(n)
    check = check_diff(files, lines)
    if not check.ok:
        git(wt, "reset", "-q")  # unstage; the next round sees the reason
        return "N3", Failure(check="diff limits", log_tail=check.reason), "back"
    sha = ctx.ws.commit(n, f"agent: #{n} {ticket.title} (round {rounds})")
    return "N4", Commit(branch=ctx.ws.branch(n), sha=sha, diff_lines=lines, files=files, sensitive=check.sensitive), "forward"


# -- N4 verify (code only) ---------------------------------------------------------------------

def n4_verify(ctx, n: int, commit: Commit):
    wt = ctx.ws.path(n)
    for name, cmd in limits.VERIFY_COMMANDS:
        try:
            r = subprocess.run(cmd, shell=True, cwd=wt, capture_output=True, text=True, timeout=limits.VERIFY_TIMEOUT_S)
            ok, out = r.returncode == 0, (r.stdout + r.stderr)
        except subprocess.TimeoutExpired:
            ok, out = False, f"timed out after {limits.VERIFY_TIMEOUT_S}s"
        if not ok:
            return "N3", Failure(check=name, log_tail=out[-4000:]), "back"
    return "N5", Verified(sha=commit.sha, checks=[c for c, _ in limits.VERIFY_COMMANDS]), "forward"


# -- N5 review ---------------------------------------------------------------------------------

def n5_review(ctx, n: int, ticket: Ticket, verified: Verified):
    rounds = ctx.store.bump(n, "review_rounds")
    diff = ctx.ws.diff_text(n)
    data = opus_mod.call(
        ctx.opus, ctx.store, ticket=n, node="N5", purpose="review",
        prompt=("You are the Critic. Review this diff for correctness, security (tenant isolation, auth, money "
                "movement), and missing tests. Report only real problems. Severity 'blocking' means it must not ship.\n"
                f"Acceptance criteria:\n{opus_mod.untrusted(chr(10).join(ticket.acceptance))}\n"
                f"Diff:\n{opus_mod.untrusted(diff, 60000)}"),
        schema=_schema(Findings), cwd=str(ctx.ws.path(n)), tools=opus_mod.READ_TOOLS,
    )
    findings = Findings.model_validate(data)
    state = {"acceptance": ticket.acceptance, "diff": diff[:20000],
             "findings": [f.model_dump() for f in findings.findings]}
    fork_list = [forks.score_fork("risk", ctx)]
    for i, criterion in enumerate(ticket.acceptance, 1):
        f = forks.noul_fork(f"done:{i}", ctx)
        f.question["instructions"] += f"\nCriterion {i}: {criterion}"
        fork_list.append(f)
    d = forks.ask(ctx, ticket=n, node="N5", state=state, forks=fork_list)
    done = {k: bool(v) for k, v in d.items() if k.startswith("done:")}
    blocking = [f for f in findings.findings if f.severity == "blocking"]
    for k, ok in done.items():
        if not ok:
            blocking.append(Finding(severity="blocking", file="", message=f"criterion {k.split(':')[1]} is not done: "
                                    f"{ticket.acceptance[int(k.split(':')[1]) - 1]}"))
    if blocking and rounds > limits.MAX_REVIEW_ROUNDS:
        return ESCALATE, Escalation(node="N5", reason=f"blocking findings after {rounds} review rounds: "
                                    + "; ".join(f.message for f in blocking[:3])), "escalation"
    if blocking:
        return "N3", Findings(findings=blocking + [f for f in findings.findings if f.severity != "blocking"]), "back"
    risk_level = d["risk"]
    data_now = ctx.store.ticket(n)["data"]
    ctx.store.upsert_ticket(n, data={**data_now, "risk": risk_level,
                                     "should_fix": [f.model_dump() for f in findings.findings]})
    risk_value = {"low": 0.0, "mid": 2.0, "high": 4.0}.get(risk_level, 2.0)
    return "N6", Reviewed(sha=verified.sha, risk=risk_value, done=done), "forward"


# -- N6 evaluate (code only) -------------------------------------------------------------------

def benchmark_metrics(results: dict) -> dict[str, float]:
    h = results["extractor"]["heuristic"]
    m = {
        "accuracy": float(h["micro_accuracy_all_invoices"]),
        "stp_clean": float(h["stp_rate_clean"]["rate"]),
        "false_approve_negatives": float(h["false_approve_negatives"]["rate"]),
    }
    for name, t in results["corruption"]["types"].items():
        m[f"false_approve:{name}"] = float(t["false_approve_rate"])
    return m


def regressions(base: dict[str, float], now: dict[str, float]) -> list[Regression]:
    out = []
    for cat in limits.FRAUD_CATEGORIES:  # hard limit: must stay exactly 0
        if now.get(f"false_approve:{cat}", 1.0) != 0.0:
            out.append(Regression(metric=f"false_approve:{cat}", baseline=0.0, now=now.get(f"false_approve:{cat}", 1.0)))
    for k, b in base.items():
        v = now.get(k)
        if v is None:
            out.append(Regression(metric=k, baseline=b, now=-1))
        elif k.startswith("false_approve") and v > b + 1e-9:
            out.append(Regression(metric=k, baseline=b, now=v))
        elif not k.startswith("false_approve") and v < b - 1e-9:
            out.append(Regression(metric=k, baseline=b, now=v))
    return out


def n6_evaluate(ctx, n: int, reviewed: Reviewed):
    wt = ctx.ws.path(n)
    base = json.loads(git(wt, "show", f"origin/{ctx.ws.base}:backend/benchmark/results.json"))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "results.json"
        r = subprocess.run(limits.BENCHMARK_COMMAND.format(out=out), shell=True, cwd=wt,
                           capture_output=True, text=True, timeout=limits.VERIFY_TIMEOUT_S)
        if r.returncode != 0:
            return "N3", Failure(check="benchmark", log_tail=(r.stdout + r.stderr)[-4000:]), "back"
        now = json.loads(out.read_text())
    base_m, now_m = benchmark_metrics(base), benchmark_metrics(now)
    regs = regressions(base_m, now_m)
    if regs:
        return "N3", Failure(check="benchmark regression", log_tail=json.dumps([r.model_dump() for r in regs])), "back"
    return "N7", Evaluated(sha=reviewed.sha, metrics=now_m), "forward"


# -- N7 ship -----------------------------------------------------------------------------------

def n7_ship(ctx, n: int, ticket: Ticket, evaluated: Evaluated):
    ctx.ws.push(n)
    t = ctx.store.ticket(n)
    files, lines = ctx.ws.diff_files(n)
    sensitive = check_diff(files, lines).sensitive
    risk_level = t["data"].get("risk", "mid")
    should_fix = t["data"].get("should_fix", [])
    body = (f"Closes #{n}\n\nBuilt by the DocuPay agent graph.\n\n"
            f"- Build rounds: {t['build_rounds']}, review rounds: {t['review_rounds']}, Opus calls: {t['opus_calls']}, "
            f"cost: ${t['usd']:.2f}\n- Risk: {risk_level}{' (touches sensitive paths)' if sensitive else ''}\n"
            f"- Files: {len(files)}, lines: {lines}\n"
            + ("\nNon-blocking review notes:\n" + "\n".join(f"- {f['file']}: {f['message']}" for f in should_fix[:10])
               if should_fix else ""))
    req = ShipRequest(branch=ctx.ws.branch(n), sha=evaluated.sha, title=f"#{n}: {ticket.title}", body=body,
                      risk={"low": 0.0, "mid": 2.0, "high": 4.0}.get(risk_level, 2.0), sensitive=sensitive)
    return "G1", req, "gate"
