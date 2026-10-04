"""
Tune thresholds on DocuPay's own history (data/seed_forks.jsonl) plus every labeled fork.

For each yes/no fork it finds the loosest thresholds with zero wrong sharp decisions on the
examples, never looser than graph.py allows. It never writes graph.md: it opens gate G4.
"""
import json
from pathlib import Path

from . import forks as forks_mod
from . import graph as graph_mod
from . import improve

SEED = Path(__file__).resolve().parent.parent / "data" / "seed_forks.jsonl"
FIELDS = {"specified": ("specified_yes", "specified_no"), "retry": ("retry_yes", "retry_no"), "done": ("done_yes", "done_no")}
FLOOR = {"done_yes": 0.90}


def seed_rows(ctx) -> list[tuple[dict, dict, bool]]:
    rows = []
    for line in SEED.read_text().splitlines():
        ex = json.loads(line)
        f = forks_mod.noul_fork(ex["fork"], ctx)
        if ex.get("criterion"):
            f.question["instructions"] += f"\nCriterion 1: {ex['criterion']}"
        rows.append((ex["state"], {ex["fork"]: f.question}, ex["label"]))
    for r in improve.labeled_rows(ctx.store):
        base = r["fork"].split(":")[0]
        if base in FIELDS:
            rows.append((json.loads(r["state"]), {r["fork"]: json.loads(r["question"])}, json.loads(r["label"])))
    return rows


def best_thresholds(probs_labels: list[tuple[float, bool]], yes_floor: float = 0.5) -> tuple[float, float, float]:
    """(yes threshold, no threshold, share decided sharply) with zero wrong sharp answers."""
    grid = [round(x / 100, 2) for x in range(50, 100)]
    yes = next((t for t in grid if t >= yes_floor and not any(p >= t and not y for p, y in probs_labels)), 0.99)
    no = next((t for t in reversed([round(1 - g, 2) for g in grid]) if not any(p <= t and y for p, y in probs_labels)), 0.01)
    no = min(no, round(yes - 0.05, 2))
    sharp = sum(1 for p, _ in probs_labels if p >= yes or p <= no) / max(1, len(probs_labels))
    return yes, no, sharp


def main(ctx, apply_gate: bool = True) -> dict:
    import time

    from . import router

    by_fork: dict[str, list[tuple[float, bool]]] = {k: [] for k in FIELDS}
    seen = {r["state"] + r["fork"] for r in ctx.store.rows("SELECT state, fork FROM forks WHERE node='CAL'")}
    for state, question, label in seed_rows(ctx):
        name = next(iter(question))
        call_id = time.time_ns()
        res = ctx.jev.ask(state, question)
        a = res.answers.get(name)
        if a is None:
            continue
        by_fork[name.split(":")[0]].append((a.probs["yes"], bool(label)))
        if json.dumps(state) + name in seen:
            continue
        # Recorded as labeled forks: the final check and N9's replay use them as real evidence.
        route = router.for_fork(name, a, ctx.graph.thresholds)
        fork_id = ctx.store.fork(ticket=None, node="CAL", fork=name, state=state, question=question[name],
                                 outcomes=["True", "False"], probs=a.probs,
                                 answer=json.dumps(route.value) if route.sharp else None,
                                 route="jev" if route.sharp else "opus", jev_call=call_id, latency_ms=res.latency_ms,
                                 input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                                 questions_version=ctx.questions.get("version"))
        ctx.store.label_fork(fork_id, json.dumps(bool(label)))
    current = ctx.graph.thresholds
    proposals, report = [], {}
    for fork, pl in by_fork.items():
        if len(pl) < 8:
            report[fork] = f"only {len(pl)} answered examples: keep current thresholds"
            continue
        yes_f, no_f = FIELDS[fork]
        yes, no, sharp = best_thresholds(pl, FLOOR.get(yes_f, 0.5))
        report[fork] = {"examples": len(pl), "yes": yes, "no": no, "sharp_share": round(sharp, 2),
                        "current": (getattr(current, yes_f), getattr(current, no_f))}
        for name, value in ((yes_f, yes), (no_f, no)):
            if abs(getattr(current, name) - value) >= 0.01:
                proposals.append({"name": name, "value": value,
                                  "why": f"{len(pl)} labeled {fork} examples, 0 wrong sharp answers, {sharp:.0%} decided by Jev"})
    print(json.dumps(report, indent=1))
    if proposals and apply_gate:
        # Validate the proposal as a whole before asking the owner.
        test = dict(vars(current))
        test.update({p["name"]: p["value"] for p in proposals})
        graph_mod._check(graph_mod.Thresholds(**test))
        gate_id = ctx.store.open_gate("G4", None, "Calibrated thresholds from DocuPay history", {"proposals": proposals})
        ctx.notify.send(f"🚦 Gate G4 #{gate_id}: calibration proposes "
                        + ", ".join(f"{p['name']}={p['value']}" for p in proposals) + f". /approve {gate_id} or /reject {gate_id}")
    return {"report": report, "proposals": proposals}
