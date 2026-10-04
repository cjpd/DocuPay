"""N9 only runs inside a work window, ships wording only after the replay, and sends thresholds to G4."""
import json
import time

import pytest
import yaml

from conftest import yes
from dp_agents import improve, questions, windows
from dp_agents.jev import FakeJev
from dp_agents.opus import FakeOpus


def seed_labeled(ctx, n=12):
    q = questions.noul(ctx.questions["forks"]["retry"])
    for i in range(n):
        ctx.store.fork(ticket=1, node="N3", fork="retry", state={"i": i}, question=q, outcomes=["True", "False"],
                       probs={"yes": 0.5}, answer="true", route="opus", latency_ms=5, questions_version=1)
    ctx.store.exec("UPDATE forks SET label='true'")


def proposal(ctx, wording="Will one more build attempt fix this specific failure?", thresholds=()):
    new = yaml.safe_load(yaml.safe_dump(ctx.questions))
    new["version"] += 1
    new["forks"]["retry"]["instructions"] = wording
    return {"root_causes": ["retry wording too vague"], "questions_yaml": yaml.safe_dump(new),
            "threshold_proposals": list(thresholds)}


@pytest.fixture
def isolated_questions(tmp_path, monkeypatch):
    path = tmp_path / "questions.yaml"
    path.write_text(questions.QUESTIONS_YAML.read_text())
    monkeypatch.setattr(questions, "QUESTIONS_YAML", path)
    monkeypatch.setattr(improve, "_checks_pass", lambda repo: True)
    return path


def active(ctx):
    ctx.store.touch("dashboard", time.time())
    return windows.current(ctx.store, ctx.graph.window)


def test_closed_window_never_runs(make_ctx):
    ctx = make_ctx()
    seed_labeled(ctx)
    ok, why = improve.should_run(ctx.store, ctx.graph.improve, windows.current(ctx.store, ctx.graph.window))
    assert not ok and why == "window closed"


def test_runs_on_evidence_and_respects_interval_and_window_cap(make_ctx):
    ctx = make_ctx()
    w = active(ctx)
    assert "labeled forks" in improve.should_run(ctx.store, ctx.graph.improve, w)[1]   # waits quietly, no row
    seed_labeled(ctx)
    ctx.store.exec("UPDATE forks SET route='jev'")                                    # labeled but no misses
    assert improve.should_run(ctx.store, ctx.graph.improve, w)[1] == "not enough new evidence"
    ctx.store.exec("UPDATE forks SET route='opus'")
    assert improve.should_run(ctx.store, ctx.graph.improve, w)[0]
    ctx.store.exec("INSERT INTO improve_runs (ts, window_start, status) VALUES (?, ?, 'shipped')", (time.time(), w.started_at))
    assert "interval" in improve.should_run(ctx.store, ctx.graph.improve, w)[1]
    ctx.store.exec("UPDATE improve_runs SET ts = ts - 7200")
    for _ in range(2):
        ctx.store.exec("INSERT INTO improve_runs (ts, window_start, status) VALUES (?, ?, 'rejected')", (time.time() - 7200, w.started_at))
    assert "run limit" in improve.should_run(ctx.store, ctx.graph.improve, w)[1]


def test_better_wording_ships_after_replay(make_ctx, isolated_questions):
    # Jev is sharp and right with the new wording, split with the old one.
    class WordingJev(FakeJev):
        def ask(self, state, qs):
            from dp_agents.jev import JevResult
            q = next(iter(qs.values()))
            p = 0.95 if "specific failure" in q["instructions"] else 0.5
            return JevResult({k: yes(p) for k in qs}, 5.0)
    ctx = make_ctx(jev=WordingJev(), opus=FakeOpus({"improve": [proposal(make_ctx())]}))
    seed_labeled(ctx)
    status = improve.run(ctx, active(ctx))
    assert status == "shipped"
    assert "specific failure" in isolated_questions.read_text()
    run = ctx.store.one("SELECT * FROM improve_runs ORDER BY id DESC")
    assert run["score_new"] > run["score_old"]


def test_worse_wording_is_rejected(make_ctx, isolated_questions):
    class WrongJev(FakeJev):
        def ask(self, state, qs):
            q = next(iter(qs.values()))
            p = 0.01 if "specific failure" in q["instructions"] else 0.5   # new wording is sharp and wrong
            from dp_agents.jev import JevResult
            return JevResult({k: yes(p) for k in qs}, 5.0)
    before = isolated_questions.read_text()
    ctx = make_ctx(jev=WrongJev(), opus=FakeOpus({"improve": [proposal(make_ctx())]}))
    seed_labeled(ctx)
    assert improve.run(ctx, active(ctx)) == "rejected"
    assert isolated_questions.read_text() == before


def test_rewrite_that_changes_a_fork_shape_is_refused(make_ctx, isolated_questions):
    ctx = make_ctx()
    bad = proposal(ctx)
    data = yaml.safe_load(bad["questions_yaml"])
    data["forks"]["done"]["type"] = "score"
    bad["questions_yaml"] = yaml.safe_dump(data)
    ctx.opus = FakeOpus({"improve": [bad]})
    seed_labeled(ctx)
    assert improve.run(ctx, active(ctx)) == "rejected"


def test_threshold_proposals_open_g4_and_are_not_applied(make_ctx, isolated_questions):
    ctx = make_ctx()
    before = ctx.graph.thresholds.retry_yes
    ctx.opus = FakeOpus({"improve": [proposal(ctx, thresholds=[{"name": "retry_yes", "value": 0.6, "why": "x"},
                                                              {"name": "MAX_USD_PER_DAY", "value": 999, "why": "x"}])]})
    seed_labeled(ctx)
    improve.run(ctx, active(ctx))
    gate = ctx.store.one("SELECT * FROM gates WHERE gate='G4'")
    assert gate and gate["status"] == "pending"
    assert [p["name"] for p in json.loads(gate["payload"])["proposals"]] == ["retry_yes"]   # hard limits are not proposable
    assert ctx.graph.thresholds.retry_yes == before


def test_approved_g4_applies_valid_thresholds_only(make_ctx, tmp_path, monkeypatch):
    from dp_agents import graph
    path = tmp_path / "graph.md"
    path.write_text(graph.GRAPH_MD.read_text())
    monkeypatch.setattr(graph, "GRAPH_MD", path)
    ctx = make_ctx()
    improve.apply_threshold_proposal(ctx, {"proposals": [{"name": "retry_yes", "value": 0.75}]})
    assert graph.load(path).thresholds.retry_yes == 0.75
    with pytest.raises(ValueError):
        improve.apply_threshold_proposal(ctx, {"proposals": [{"name": "retry_no", "value": 0.9}]})
    assert graph.load(path).thresholds.retry_no == 0.20


def test_window_closing_mid_run_aborts(make_ctx, isolated_questions, monkeypatch):
    ctx = make_ctx(opus=FakeOpus({"improve": [proposal(make_ctx())]}))
    seed_labeled(ctx)
    w = active(ctx)
    monkeypatch.setattr(windows, "current", lambda *a, **k: windows.Window("closed", None))
    assert improve.run(ctx, w) == "aborted"
