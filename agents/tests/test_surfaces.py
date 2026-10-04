"""Final check, dashboard and report."""
import time

import pytest
from fastapi.testclient import TestClient

from dp_agents import cli, report
from dp_agents.dashboard.app import create_app

TOKEN = "t" * 32


def test_final_check_refuses_without_env(make_ctx, monkeypatch):
    for k in ("GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "DASHBOARD_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    ok, lines = cli.final_check(make_ctx())
    assert not ok
    assert any("GITHUB_TOKEN" in l for l in lines)
    assert not any("jev" in l.lower() or "typesafe" in l.lower() for l in lines)
    assert not any("stop rule" in l for l in lines), "every node in graph.md has a stop rule"
    assert not any("hard limit" in l for l in lines), "no hard limit is reachable by a model"


def test_final_check_detects_a_missing_stop_rule(make_ctx, monkeypatch, tmp_path):
    from dp_agents import graph
    text = graph.GRAPH_MD.read_text().replace("| opening the PR waits at gate G1 |", "| |")
    path = tmp_path / "graph.md"
    path.write_text(text)
    monkeypatch.setattr(graph, "GRAPH_MD", path)
    ok, lines = cli.final_check(make_ctx())
    assert any("N7 ship has no stop rule" in l for l in lines)


def test_dashboard_requires_token(make_ctx):
    ctx = make_ctx()
    with pytest.raises(RuntimeError):
        create_app(ctx.store, "short")
    client = TestClient(create_app(ctx.store, TOKEN))
    assert client.get("/").status_code == 200
    assert client.get("/api/state").status_code == 401
    assert client.get("/login", params={"token": "wrong"}).status_code == 401
    client.get("/login", params={"token": TOKEN}, follow_redirects=False)
    assert client.get("/api/state").status_code == 200


def test_dashboard_gate_decision_needs_csrf_header(make_ctx):
    ctx = make_ctx()
    gid = ctx.store.open_gate("G1", 3, "Open PR", {"x": 1})
    client = TestClient(create_app(ctx.store, TOKEN))
    h = {"authorization": f"Bearer {TOKEN}"}
    assert client.post(f"/api/gates/{gid}/approve", headers=h).status_code == 403
    assert client.post(f"/api/gates/{gid}/approve", headers={**h, "x-requested-with": "dashboard"}).status_code == 200
    assert client.post(f"/api/gates/{gid}/approve", headers={**h, "x-requested-with": "dashboard"}).status_code == 409
    assert ctx.store.one("SELECT status FROM gates")["status"] == "approved"


def test_report_counts_decisions_and_cost(make_ctx):
    ctx = make_ctx()
    for name, source in (("specified", "code"), ("retry", "code"), ("done:1", "opus")):
        ctx.store.decision(ticket=1, node="N5", name=name, decision=True, source=source)
    ctx.store.opus_call(1, "N3", "build", 0.5, 1000, True)
    r = report.build(ctx.store)
    assert r["decisions"] == 3 and r["decisions_by_code"] == 2 and r["decisions_by_opus"] == 1
    assert r["opus_calls"] == 1 and r["opus_usd"] == 0.5 and r["usd_per_shipped_ticket"] is None
    assert "by code rules 2" in report.text(r)
