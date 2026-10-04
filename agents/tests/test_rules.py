"""Graph rules owned by code: thresholds, question shapes, routing, diff limits, windows, budgets."""
import time

import pytest
import yaml

from conftest import pick, risk, yes
from dp_agents import graph, limits, questions, router, windows
from dp_agents.opus import Budget, BudgetExceeded
from dp_agents.workspace import check_diff


def test_graph_md_parses_with_all_gates():
    g = graph.load()
    assert set(g.human_gates) == {"G1", "G2", "G3", "G4"}
    assert g.thresholds.done_yes >= 0.9


@pytest.mark.parametrize("bad", [
    ("retry_no: 0.20", "retry_no: 0.90"),        # no above yes
    ("done_yes: 0.95", "done_yes: 0.60"),        # done too loose
    ("choice_sharp: 0.85", "choice_sharp: 1.5"),  # out of range
    ("risk_high: 3.0", "risk_high: 0.5"),        # high below low
])
def test_nonsense_thresholds_are_refused(bad):
    text = graph.GRAPH_MD.read_text().replace(*bad)
    with pytest.raises(ValueError):
        graph.parse(text)


def test_question_rewrite_may_change_wording_only():
    old = questions.load()
    new = yaml.safe_load(yaml.safe_dump(old))
    new["version"] = old["version"] + 1
    new["forks"]["done"]["instructions"] = "Is criterion fully done, with a test?"
    questions.validate_rewrite(old, new)
    for mutate in (
        lambda q: q["forks"]["done"].update(type="score"),
        lambda q: q["forks"].pop("retry"),
        lambda q: q["forks"]["risk"]["levels"].pop(),
        lambda q: q.update(version=old["version"] + 3),
        lambda q: q["forks"]["retry"].update(criteria={"true": "a", "false": "b", "maybe": "c"}),
    ):
        broken = yaml.safe_load(yaml.safe_dump(new))
        mutate(broken)
        with pytest.raises(ValueError):
            questions.validate_rewrite(old, broken)


def test_router_sharp_and_split():
    t = graph.load().thresholds
    assert router.for_fork("done:1", yes(0.97), t).value is True
    assert router.for_fork("done:1", yes(0.02), t).value is False
    assert not router.for_fork("done:1", yes(0.90), t).sharp      # 0.90 < 0.95: Opus decides
    assert router.for_fork("retry", yes(0.85), t).value is True
    assert not router.for_fork("retry", yes(0.5), t).sharp
    assert router.for_fork("target_file", pick("a.py", 0.9), t, ["a.py", "x"]).value == "a.py"
    assert not router.for_fork("target_file", pick("a.py", 0.6), t, ["a.py", "x"]).sharp
    assert not router.for_fork("target_file", pick("evil.py", 0.99), t, ["a.py", "x"]).sharp  # not a fixed outcome
    assert router.for_fork("risk", risk(0.5), t).value == "low"
    assert router.for_fork("risk", risk(3.5), t).value == "high"
    assert not router.for_fork("risk", risk(2.0), t).sharp
    assert not router.for_fork("done:1", None, t).sharp            # no answer is never a yes


@pytest.mark.parametrize("files,lines,ok,sensitive", [
    (["backend/app.py"], 10, True, False),
    (["backend/config/settings.py"], 10, True, True),
    (["agents/dp_agents/limits.py"], 1, False, True),     # the agent may not loosen its own limits
    (["agents/graph.md"], 1, False, True),
    (["backend/.env"], 1, False, True),
    ("README.md".split(), 1, False, False),
    (["backend/app.py"], limits.MAX_DIFF_LINES + 1, False, False),
    ([], 0, False, False),
])
def test_diff_limits(files, lines, ok, sensitive):
    r = check_diff(files, lines)
    assert r.ok is ok
    if ok:
        assert r.sensitive is sensitive


def test_windows(make_ctx):
    ctx = make_ctx()
    cfg = ctx.graph.window
    now = time.time()
    assert windows.current(ctx.store, cfg, now).state == "closed"
    ctx.store.touch("github", now - 10 * 60)
    w = windows.current(ctx.store, cfg, now)
    assert w.state == "active" and w.started_at
    ctx.store.touch("github", now - 3 * 3600)
    ctx.store.exec("DELETE FROM activity")
    ctx.store.touch("github", now - 3 * 3600)
    assert windows.current(ctx.store, cfg, now).state == "closed"   # no owner ticket in flight
    ctx.store.upsert_ticket(7, state="running", labeled_at=now - 3600)
    assert windows.current(ctx.store, cfg, now).state == "passive"
    ctx.store.exec("DELETE FROM activity")
    ctx.store.touch("github", now - 9 * 3600)
    assert windows.current(ctx.store, cfg, now).state == "closed"


def test_budget_limits(make_ctx):
    ctx = make_ctx()
    ctx.store.upsert_ticket(1, state="running")
    b = Budget(ctx.store, 1)
    assert b.check() <= limits.MAX_USD_PER_TICKET
    ctx.store.exec("UPDATE tickets SET opus_calls=? WHERE number=1", (limits.MAX_OPUS_CALLS_PER_TICKET,))
    with pytest.raises(BudgetExceeded):
        b.check()
    ctx.store.exec("UPDATE tickets SET opus_calls=0, usd=? WHERE number=1", (limits.MAX_USD_PER_TICKET,))
    with pytest.raises(BudgetExceeded):
        b.check()
    ctx.store.exec("UPDATE tickets SET usd=0, started_at=? WHERE number=1", (time.time() - limits.MAX_MINUTES_PER_TICKET * 61,))
    with pytest.raises(BudgetExceeded):
        b.check()
    ctx.store.opus_call(None, "N9", "improve", limits.MAX_USD_PER_DAY, 1, True)
    with pytest.raises(BudgetExceeded):
        Budget(ctx.store, None).check()
