"""Final check, dashboard, report and calibration."""
import time

import pytest
from fastapi.testclient import TestClient

from dp_agents import calibrate, cli, report
from dp_agents.dashboard.app import create_app

TOKEN = "t" * 32


def test_final_check_refuses_without_env(make_ctx, monkeypatch):
    for k in ("GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "TYPESAFE_API_KEY", "DASHBOARD_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    ok, lines = cli.final_check(make_ctx(), probe=False)
    assert not ok
    assert any("GITHUB_TOKEN" in l for l in lines)
    assert any("never answered a fork" in l for l in lines)
    assert not any("stop rule" in l for l in lines), "every node in graph.md has a stop rule"
    assert not any("hard limit" in l for l in lines), "no hard limit is reachable by a model"


def test_final_check_detects_a_missing_stop_rule(make_ctx, monkeypatch, tmp_path):
    from dp_agents import graph
    text = graph.GRAPH_MD.read_text().replace("| 1 attempt per run, 3 runs per window, aborts when the window closes; threshold/gate changes go to G4 |", "| |")
    path = tmp_path / "graph.md"
    path.write_text(text)
    monkeypatch.setattr(graph, "GRAPH_MD", path)
    ok, lines = cli.final_check(make_ctx(), probe=False)
    assert any("N9 improve has no stop rule" in l for l in lines)


def test_dashboard_requires_token_and_records_heartbeat(make_ctx):
    ctx = make_ctx()
    with pytest.raises(RuntimeError):
        create_app(ctx.store, "short")
    client = TestClient(create_app(ctx.store, TOKEN))
    assert client.get("/").status_code == 200
    assert client.get("/api/state").status_code == 401
    assert client.get("/login", params={"token": "wrong"}).status_code == 401
    client.get("/login", params={"token": TOKEN}, follow_redirects=False)
    assert client.get("/api/state").status_code == 200
    client.post("/api/heartbeat")
    assert ctx.store.last_activity() and time.time() - ctx.store.last_activity() < 5


def test_dashboard_gate_decision_needs_csrf_header(make_ctx):
    ctx = make_ctx()
    gid = ctx.store.open_gate("G1", 3, "Open PR", {"x": 1})
    client = TestClient(create_app(ctx.store, TOKEN))
    h = {"authorization": f"Bearer {TOKEN}"}
    assert client.post(f"/api/gates/{gid}/approve", headers=h).status_code == 403
    assert client.post(f"/api/gates/{gid}/approve", headers={**h, "x-requested-with": "dashboard"}).status_code == 200
    assert client.post(f"/api/gates/{gid}/approve", headers={**h, "x-requested-with": "dashboard"}).status_code == 409
    assert ctx.store.one("SELECT status FROM gates")["status"] == "approved"


def test_report_counts_latency_per_jev_call(make_ctx):
    ctx = make_ctx()
    for fork in ("done:1", "done:2"):
        ctx.store.fork(ticket=1, node="N5", fork=fork, state={}, outcomes=[], route="jev", jev_call=7, latency_ms=300)
    ctx.store.fork(ticket=1, node="N3", fork="retry", state={}, outcomes=[], route="opus", jev_call=8, latency_ms=1500)
    ctx.store.opus_call(1, "N3", "build", 0.5, 1000, True)
    r = report.build(ctx.store)
    assert r["forks_answered"] == 3 and r["forks_sharp_by_jev"] == 2 and r["jev_calls"] == 2
    assert r["jev_avg_latency_ms"] == 900.0 and r["jev_calls_over_1s"] == 1
    assert r["opus_calls"] == 1 and r["opus_usd"] == 0.5
    assert "Jev: 2 calls" in report.text(r)


def test_calibration_picks_thresholds_with_no_wrong_sharp_answer():
    pl = [(0.97, True), (0.92, True), (0.88, False), (0.30, True), (0.10, False), (0.04, False)]
    yes_t, no_t, sharp = calibrate.best_thresholds(pl, 0.5)
    assert yes_t == 0.89 and no_t < 0.30
    assert not any((p >= yes_t and not y) or (p <= no_t and y) for p, y in pl)


def test_seed_set_is_real_history():
    lines = calibrate.SEED.read_text().splitlines()
    assert len(lines) >= 50


def test_calibration_records_labeled_forks_and_opens_g4(make_ctx):
    from conftest import yes
    from dp_agents.jev import FakeJev

    class SeedJev(FakeJev):
        def ask(self, state, qs):
            from dp_agents.jev import JevResult
            name = next(iter(qs))
            # Sharp and right on vague owner requests, unsure elsewhere.
            p = 0.03 if not state.get("acceptance") and name == "specified" else 0.7
            return JevResult({name: yes(p)}, 300.0, 50, 0)
    ctx = make_ctx(jev=SeedJev())
    out = calibrate.main(ctx, apply_gate=True)
    n = ctx.store.one("SELECT COUNT(*) AS c FROM forks WHERE node='CAL' AND label IS NOT NULL")["c"]
    assert n == len(calibrate.SEED.read_text().splitlines())
    calibrate.main(ctx, apply_gate=False)            # a second run does not duplicate rows
    assert ctx.store.one("SELECT COUNT(*) AS c FROM forks WHERE node='CAL'")["c"] == n
    if out["proposals"]:
        assert ctx.store.one("SELECT gate, status FROM gates") == {"gate": "G4", "status": "pending"}
    assert ctx.graph.thresholds == __import__("dp_agents.graph", fromlist=["load"]).load().thresholds
