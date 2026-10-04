"""
N9 improve. Runs only inside a work window (see windows.py) and only when there is new evidence.

Opus reads the misses and proposes new question wording. The change goes live only if a replay
of the labeled forks scores at least as well as the current wording AND the agent test suite
passes. Threshold changes are never applied here: they open gate G4 for the owner.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from . import graph as graph_mod
from . import limits
from . import opus as opus_mod
from . import questions as questions_mod
from . import router, windows

MIN_LABELED = 10


# -- replay (shared with calibrate.py) --------------------------------------------------------

def reword(question: dict, old_q: dict, new_q: dict) -> dict:
    """The stored question with the base wording swapped (keeps run-time suffixes like 'Criterion 2: ...')."""
    q = json.loads(json.dumps(question))
    old_base, new_base = old_q["instructions"].strip(), new_q["instructions"].strip()
    if q.get("instructions", "").startswith(old_base):
        q["instructions"] = new_base + q["instructions"][len(old_base):]
    if q["type"] == "noul" and new_q.get("criteria"):
        q["criteria"] = {k: new_q["criteria"][k] for k in ("true", "false") if k in new_q["criteria"]}
    return q


def score_answers(rows: list[dict], answers: list, thresholds) -> float:
    """+1 for a correct sharp decision, 0 for a split (Opus decides), -2 for a wrong sharp one."""
    if not rows:
        return 0.0
    total = 0.0
    for r, a in zip(rows, answers):
        label = json.loads(r["label"])
        opts = json.loads(r["outcomes"]) if r["fork"].startswith("target_file") else None
        route = router.for_fork(r["fork"], a, thresholds, opts)
        if not route.sharp:
            continue
        total += 1.0 if route.value == label else -2.0
    return total / len(rows)


def replay(ctx, rows: list[dict], old_qs: dict, new_qs: dict) -> list:
    answers = []
    for r in rows:
        base = r["fork"].split(":")[0]
        q = reword(json.loads(r["question"]), old_qs["forks"][base], new_qs["forks"][base])
        res = ctx.jev.ask(json.loads(r["state"]), {r["fork"]: q})
        answers.append(res.answers.get(r["fork"]))
    return answers


def labeled_rows(store, limit: int = limits.MAX_REPLAY_FORKS) -> list[dict]:
    # Risk labels are approved/rejected, not a level, so they are left out of the replay score.
    return store.rows("SELECT * FROM forks WHERE label IS NOT NULL AND question IS NOT NULL "
                      "AND fork NOT LIKE 'risk%' ORDER BY id DESC LIMIT ?", (limit,))


# -- trigger ----------------------------------------------------------------------------------

def _last_run(store):
    return store.one("SELECT * FROM improve_runs WHERE status != 'skipped' ORDER BY id DESC LIMIT 1")


def should_run(store, cfg: dict, window: windows.Window, now: float | None = None) -> tuple[bool, str]:
    now = now or time.time()
    if window.state == "closed":
        return False, "window closed"
    last = _last_run(store)
    since = last["ts"] if last else 0.0
    if last and now - since < float(cfg["min_minutes_between_runs"]) * 60:
        return False, "ran less than the minimum interval ago"
    runs = store.one("SELECT COUNT(*) AS c FROM improve_runs WHERE window_start=? AND status != 'skipped'",
                     (window.started_at,))["c"]
    if runs >= int(cfg["max_runs_per_window"]):
        return False, "run limit for this window reached"
    misses = store.one("SELECT COUNT(*) AS c FROM forks WHERE ts>? AND route != 'jev'", (since,))["c"]
    misses += store.one("SELECT COUNT(*) AS c FROM events WHERE ts>? AND kind IN ('back','escalation','error')", (since,))["c"]
    closed = store.one("SELECT COUNT(*) AS c FROM tickets WHERE closed_at>?", (since,))["c"]
    labeled = store.one("SELECT COUNT(*) AS c FROM forks WHERE label IS NOT NULL AND fork NOT LIKE 'risk%'")["c"]
    if labeled < MIN_LABELED:
        return False, f"only {labeled} labeled forks; the replay needs {MIN_LABELED}"
    if misses >= int(cfg["min_misses"]) or closed >= int(cfg["min_closed"]):
        return True, f"{misses} misses, {closed} closed tickets since the last run"
    return False, "not enough new evidence"


def maybe_run(ctx, window: windows.Window) -> str:
    ok, why = should_run(ctx.store, ctx.graph.improve, window)
    if not ok:
        return why
    return run(ctx, window)


# -- the run ----------------------------------------------------------------------------------

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "root_causes": {"type": "array", "items": {"type": "string"}},
        "questions_yaml": {"type": "string"},
        "threshold_proposals": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "value": {"type": "number"}, "why": {"type": "string"}},
            "required": ["name", "value", "why"]}},
    },
    "required": ["root_causes", "questions_yaml", "threshold_proposals"],
}


def _record(store, window, status, **kw):
    store.exec("INSERT INTO improve_runs (ts, window_start, status, score_old, score_new, questions_version, note) "
               "VALUES (?,?,?,?,?,?,?)", (time.time(), window.started_at or 0, status, kw.get("score_old"),
                                          kw.get("score_new"), kw.get("version"), kw.get("note")))


def run(ctx, window: windows.Window) -> str:
    rows = labeled_rows(ctx.store)
    if len(rows) < MIN_LABELED:
        _record(ctx.store, window, "skipped", note=f"only {len(rows)} labeled forks")
        return "skipped"
    last = _last_run(ctx.store)
    since = last["ts"] if last else 0.0
    misses = ctx.store.rows("SELECT fork, probs, answer, label, route FROM forks WHERE ts>? AND "
                            "(route != 'jev' OR (label IS NOT NULL AND label != answer)) LIMIT 60", (since,))
    events = ctx.store.rows("SELECT src, dst, edge_type, kind, substr(payload,1,600) AS payload FROM events "
                            "WHERE ts>? AND kind IN ('back','escalation','error') LIMIT 40", (since,))
    current_text = questions_mod.QUESTIONS_YAML.read_text()
    data = opus_mod.call(
        ctx.opus, ctx.store, ticket=None, node="N9", purpose="improve",
        prompt=("Improve the Jev questions of this agent graph. Find the root cause of each miss below and rewrite "
                "the `instructions` and `criteria` wording in questions.yaml so Jev answers these forks sharply and "
                "correctly. Keep every fork, type and level count. Bump `version` by 1. Return the whole new file. "
                "You may also propose threshold changes; they are not applied without the owner.\n\n"
                f"Current questions.yaml:\n{current_text}\n\nCurrent thresholds:\n{ctx.graph.raw_block}\n\n"
                f"Missed or split forks:\n{opus_mod.untrusted(json.dumps(misses), 20000)}\n\n"
                f"Back edges and escalations:\n{opus_mod.untrusted(json.dumps(events), 12000)}"),
        schema=PROPOSAL_SCHEMA, cwd=str(ctx.repo), tools=opus_mod.READ_TOOLS,
    )
    old_qs = ctx.questions
    try:
        new_qs = yaml.safe_load(data["questions_yaml"])
        questions_mod.validate_rewrite(old_qs, new_qs)
    except Exception as exc:
        _record(ctx.store, window, "rejected", note=f"invalid rewrite: {exc}")
        return "rejected"

    if windows.current(ctx.store, ctx.graph.window).state == "closed":
        _record(ctx.store, window, "aborted", note="window closed before replay")
        return "aborted"
    score_old = score_answers(rows, replay(ctx, rows, old_qs, old_qs), ctx.graph.thresholds)
    score_new = score_answers(rows, replay(ctx, rows, old_qs, new_qs), ctx.graph.thresholds)
    if windows.current(ctx.store, ctx.graph.window).state == "closed":
        _record(ctx.store, window, "aborted", score_old=score_old, score_new=score_new, note="window closed after replay")
        return "aborted"

    status = "rejected"
    if score_new >= score_old and _checks_pass(ctx.repo):
        backup = ctx.repo / "agents" / "data" / f"questions.v{old_qs['version']}.yaml"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(questions_mod.QUESTIONS_YAML, backup)
        questions_mod.QUESTIONS_YAML.write_text(data["questions_yaml"])
        ctx.questions = questions_mod.load()
        status = "shipped"
        ctx.notify.send(f"🧠 N9: questions v{new_qs['version']} live (replay {score_old:.2f} -> {score_new:.2f}). "
                        f"Root causes: {'; '.join(data['root_causes'][:3])}")
    _record(ctx.store, window, status, score_old=score_old, score_new=score_new, version=new_qs.get("version"),
            note="; ".join(data["root_causes"][:5]))

    proposals = [p for p in data["threshold_proposals"] if p["name"] in graph_mod.Thresholds.__dataclass_fields__]
    if proposals:
        gate_id = ctx.store.open_gate("G4", None, "Threshold change proposed by N9", {"proposals": proposals})
        ctx.notify.send(f"🚦 Gate G4 #{gate_id}: N9 proposes " + ", ".join(f"{p['name']}={p['value']}" for p in proposals)
                        + f". Reply /approve {gate_id} or /reject {gate_id}")
    return status


def _checks_pass(repo: Path) -> bool:
    """The same agent test suite that guards a hand-written change."""
    r = subprocess.run("agents/.venv/bin/python -m pytest -q agents/tests", shell=True, cwd=repo,
                       capture_output=True, text=True, timeout=600)
    return r.returncode == 0


def apply_threshold_proposal(ctx, payload: dict) -> None:
    """Only called after the owner approved gate G4. The result must still pass graph.parse()."""
    text = graph_mod.GRAPH_MD.read_text()
    block = graph_mod.gates_block(text)
    new_block = block
    for p in payload.get("proposals", []):
        new_block = re.sub(rf"(^\s*{re.escape(p['name'])}:\s*)[0-9.]+", rf"\g<1>{float(p['value'])}", new_block, flags=re.M)
    new_text = text.replace(block, new_block)
    graph_mod.parse(new_text)  # refuses nonsense such as no >= yes
    graph_mod.GRAPH_MD.write_text(new_text)
    ctx.graph = graph_mod.parse(new_text)
