"""A ticket through the whole graph on a real git worktree, with scripted Jev and Opus."""
import json
from pathlib import Path

import pytest

from conftest import BASELINE, FakeGitHub, risk, yes
from dp_agents import engine, limits
from dp_agents.edges import Ticket
from dp_agents.jev import FakeJev
from dp_agents.opus import FakeOpus

PLAN = {"summary": "change x", "steps": [{"description": "edit app", "files": ["backend/app.py"]}],
        "files": ["backend/app.py"], "new_files": [], "tests": {"1": "test_x", "2": "test_y"}}
CLEAN_REVIEW = {"findings": [{"severity": "nit", "file": "backend/app.py", "line": 1, "message": "name"}]}


def ticket(n=12, **kw):
    t = Ticket(number=n, title="Make x two", body="Set x to 2.\n- [ ] x is 2\n- [ ] test proves it",
               acceptance=["x is 2", "test proves it"], points=2, priority=1, labeled_by="cjpd")
    return (1.0, t.model_copy(update=kw), True)


def edit(cwd, data):
    (Path(cwd) / "backend" / "app.py").write_text("x = 2\n")


@pytest.fixture(autouse=True)
def fast_checks(monkeypatch, tmp_path):
    monkeypatch.setattr(limits, "VERIFY_COMMANDS", (("unit", "true"),))
    monkeypatch.setattr(limits, "BENCHMARK_COMMAND", "cp backend/benchmark/results.json {out}")


def sharp_jev(**over):
    script = {"specified": yes(0.97), "done": yes(0.99), "risk": risk(0.4), "retry": yes(0.9)}
    script.update(over)
    return FakeJev(script)


def opus(review=CLEAN_REVIEW, **more):
    return FakeOpus({"plan": [PLAN], "build": [{"summary": "x=2"}], "review": [review], **more}, on_edit=edit)


def test_ticket_reaches_the_pr_gate_and_waits(make_ctx):
    gh = FakeGitHub([ticket()])
    ctx = make_ctx(jev=sharp_jev(), opus=opus(), gh=gh)
    engine.tick(ctx)
    t = ctx.store.ticket(12)
    assert t["state"] == "waiting"
    kinds = [(e["src"], e["dst"], e["edge_type"]) for e in ctx.store.rows("SELECT * FROM events ORDER BY id")]
    assert kinds == [("N1", "N2", "Ticket"), ("N2", "N3", "Plan"), ("N3", "N4", "Commit"), ("N4", "N5", "Verified"),
                     ("N5", "N6", "Reviewed"), ("N6", "N7", "Evaluated"), ("N7", "G1", "ShipRequest")]
    gate = ctx.store.one("SELECT * FROM gates")
    assert gate["gate"] == "G1" and gate["status"] == "pending"
    assert not gh.prs, "a PR is never opened before the owner approves G1"
    assert any("Gate G1" in m for m in ctx.notify.sent)
    # The work branch was pushed (reversible); main was not touched.
    import subprocess
    heads = subprocess.run(["git", "ls-remote", "--heads", "origin"], cwd=ctx.repo, capture_output=True, text=True).stdout
    assert "refs/heads/agent/12" in heads

    # Owner approves G1: code opens a draft PR, the ticket ships, forks get ground-truth labels.
    ctx.store.decide_gate(gate["id"], True, "test")
    engine.tick(ctx)
    assert gh.prs and gh.prs[0]["head"] == "agent/12" and gh.prs[0]["base"] == "main"
    assert ctx.store.ticket(12)["state"] == "shipped"
    labels = dict(ctx.store.exec("SELECT fork, label FROM forks").fetchall())
    assert labels["done:1"] == "true" and labels["risk"] == '"approved"' and labels["specified"] == "true"


def test_failed_check_goes_back_to_the_build_not_to_a_person(make_ctx, monkeypatch, tmp_path):
    marker = tmp_path / "failed-once"
    monkeypatch.setattr(limits, "VERIFY_COMMANDS",
                        (("unit", f"test -f {marker} || (touch {marker}; echo FAILED test_x; exit 1)"),))
    ctx = make_ctx(jev=sharp_jev(), opus=FakeOpus({"plan": [PLAN], "build": [{"summary": "a"}, {"summary": "b"}],
                                                   "review": [CLEAN_REVIEW]}, on_edit=edit), gh=FakeGitHub([ticket()]))
    engine.tick(ctx)
    back = ctx.store.rows("SELECT src, dst, edge_type, kind FROM events WHERE kind='back'")
    assert back == [{"src": "N4", "dst": "N3", "edge_type": "Failure", "kind": "back"}]
    assert ctx.store.ticket(12)["build_rounds"] == 2
    assert ctx.store.ticket(12)["state"] == "waiting"


def test_retry_fork_no_stops_the_loop(make_ctx, monkeypatch):
    monkeypatch.setattr(limits, "VERIFY_COMMANDS", (("unit", "echo 'Error 111 connecting to localhost:6379'; exit 1"),))
    ctx = make_ctx(jev=sharp_jev(retry=yes(0.05)), opus=opus(), gh=FakeGitHub([ticket()]))
    engine.tick(ctx)
    t = ctx.store.ticket(12)
    assert t["state"] == "parked" and "retry judged pointless" in t["result"]
    assert ("12", "agent-parked") in [(str(n), l) for n, l in ctx.gh.labels]


def test_stop_rule_caps_build_rounds(make_ctx, monkeypatch):
    monkeypatch.setattr(limits, "VERIFY_COMMANDS", (("unit", "echo 'assert 1 == 2'; exit 1"),))
    ctx = make_ctx(jev=sharp_jev(), opus=opus(), gh=FakeGitHub([ticket()]))
    engine.tick(ctx)
    t = ctx.store.ticket(12)
    assert t["state"] == "parked" and t["build_rounds"] == limits.MAX_BUILD_ROUNDS + 1
    assert "build rounds" in t["result"]


def test_blocking_review_finding_is_a_back_edge(make_ctx):
    blocking = {"findings": [{"severity": "blocking", "file": "backend/app.py", "line": 1, "message": "tenant leak"}]}
    ctx = make_ctx(jev=sharp_jev(), gh=FakeGitHub([ticket()]),
                   opus=FakeOpus({"plan": [PLAN], "build": [{"summary": "a"}], "review": [blocking, CLEAN_REVIEW]}, on_edit=edit))
    engine.tick(ctx)
    back = ctx.store.rows("SELECT src, dst, edge_type FROM events WHERE kind='back'")
    assert back == [{"src": "N5", "dst": "N3", "edge_type": "Findings"}]
    assert ctx.store.ticket(12)["state"] == "waiting"


def test_benchmark_regression_on_fraud_is_refused(make_ctx, monkeypatch, tmp_path):
    bad = json.loads(json.dumps(BASELINE))
    bad["corruption"]["types"]["changed_bank_account"]["false_approve_rate"] = 0.1
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    monkeypatch.setattr(limits, "BENCHMARK_COMMAND", f"cp {tmp_path / 'bad.json'} {{out}}")
    ctx = make_ctx(jev=sharp_jev(), opus=opus(), gh=FakeGitHub([ticket()]))
    engine.tick(ctx)
    back = ctx.store.rows("SELECT src, dst, payload FROM events WHERE kind='back'")
    assert back and back[0]["src"] == "N6" and "changed_bank_account" in back[0]["payload"]


def test_unspecified_ticket_is_labeled_and_skipped(make_ctx):
    gh = FakeGitHub([(1.0, ticket()[1], False)])  # no acceptance checklist
    ctx = make_ctx(gh=gh)
    engine.tick(ctx)
    assert (12, "needs-spec") in gh.labels and ctx.store.ticket(12)["state"] == "needs_spec"


def test_sensitive_diff_is_flagged_at_the_gate(make_ctx):
    def edit_settings(cwd, data):
        (Path(cwd) / "backend" / "config").mkdir(exist_ok=True)
        (Path(cwd) / "backend" / "config" / "settings.py").write_text("DEBUG = False\n")
    plan = dict(PLAN, files=["backend/config/settings.py"], new_files=["backend/config/settings.py"])
    ctx = make_ctx(jev=sharp_jev(), gh=FakeGitHub([ticket()]),
                   opus=FakeOpus({"plan": [plan], "build": [{"summary": "s"}], "review": [CLEAN_REVIEW]}, on_edit=edit_settings))
    engine.tick(ctx)
    gate = ctx.store.one("SELECT payload FROM gates")
    assert json.loads(gate["payload"])["sensitive"] is True
    assert any("sensitive paths" in m for m in ctx.notify.sent)


def test_budget_exceeded_parks_the_ticket(make_ctx, monkeypatch):
    monkeypatch.setattr(limits, "MAX_OPUS_CALLS_PER_TICKET", 1)
    ctx = make_ctx(jev=sharp_jev(), opus=opus(), gh=FakeGitHub([ticket()]))
    engine.tick(ctx)
    t = ctx.store.ticket(12)
    assert t["state"] == "parked" and "call limit" in t["result"]


def test_telegram_approval_from_owner_only(make_ctx):
    from dp_agents.notify import Telegram

    tg = Telegram(token="t", chat_id="42")
    tg.enabled = True

    class Resp:
        def json(self):
            return {"result": [
                {"update_id": 1, "message": {"chat": {"id": 99}, "text": "/approve 1", "date": 1}},
                {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/approve 1", "date": 2}},
            ]}
    import httpx
    orig = httpx.get
    httpx.get = lambda *a, **k: Resp()
    try:
        assert tg.commands() == [("approve", 1, 2.0)]
    finally:
        httpx.get = orig


def test_relabeled_parked_ticket_starts_over(make_ctx, monkeypatch):
    monkeypatch.setattr(limits, "VERIFY_COMMANDS", (("unit", "echo 'Error 111'; exit 1"),))
    gh = FakeGitHub([ticket()])
    ctx = make_ctx(jev=sharp_jev(retry=yes(0.05)), opus=opus(), gh=gh)
    engine.tick(ctx)
    assert ctx.store.ticket(12)["state"] == "parked"
    engine.tick(ctx)                                    # same label: stays parked
    assert ctx.store.ticket(12)["state"] == "parked"
    monkeypatch.setattr(limits, "VERIFY_COMMANDS", (("unit", "true"),))
    gh.tickets = [(1000.0, ticket()[1], True)]          # owner labeled it again later
    engine.tick(ctx)
    t = ctx.store.ticket(12)
    assert t["state"] == "waiting" and t["build_rounds"] == 1
