"""
Live dashboard: every fork, its probabilities, the route taken and the result.

Bind it to 127.0.0.1 and reach it over an SSH tunnel or Tailscale. Every API call needs the
DASHBOARD_TOKEN (cookie after /login?token=..., or a Bearer header). An open, visible tab sends
a heartbeat, which counts as owner activity for the N9 work window.
"""
import asyncio
import hmac
import json
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from .. import graph as graph_mod
from .. import report, windows
from ..store import Store

HERE = Path(__file__).parent
COOKIE = "dp_agents"


def create_app(store: Store, token: str) -> FastAPI:
    if not token or len(token) < 24:
        raise RuntimeError("DASHBOARD_TOKEN must be set (24+ characters)")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def auth(request: Request):
        given = request.cookies.get(COOKIE) or request.headers.get("authorization", "").removeprefix("Bearer ")
        if not given or not hmac.compare_digest(given, token):
            raise HTTPException(401, "sign in with /login?token=...")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (HERE / "index.html").read_text()

    @app.get("/login")
    def login(request: Request):
        given = request.query_params.get("token", "")
        if not hmac.compare_digest(given, token):
            raise HTTPException(401, "wrong token")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        max_age=30 * 86400)
        return resp

    def forks_since(after_id: int, limit: int = 200):
        rows = store.rows("SELECT id, ts, ticket, node, fork, probs, answer, route, latency_ms, label FROM forks "
                          "WHERE id > ? ORDER BY id DESC LIMIT ?", (after_id, limit))
        for r in rows:
            r["probs"] = json.loads(r["probs"]) if r["probs"] else None
            r["answer"] = json.loads(r["answer"]) if r["answer"] else None
            r["label"] = json.loads(r["label"]) if r["label"] else None
        return rows

    def events_since(after_id: int, limit: int = 100):
        return store.rows("SELECT id, ts, ticket, src, dst, edge_type, kind FROM events WHERE id > ? "
                          "ORDER BY id DESC LIMIT ?", (after_id, limit))

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        g = graph_mod.load()
        return {
            "report": report.build(store),
            "window": windows.current(store, g.window).__dict__,
            "thresholds": g.thresholds.__dict__,
            "forks": forks_since(0),
            "events": events_since(0),
            "tickets": store.rows("SELECT number, title, state, node, build_rounds, review_rounds, opus_calls, usd, "
                                  "result FROM tickets ORDER BY COALESCE(closed_at, 9e12) DESC, number DESC LIMIT 30"),
            "gates": store.rows("SELECT id, ts, gate, ticket, summary, status FROM gates ORDER BY id DESC LIMIT 20"),
            "improve": store.rows("SELECT * FROM improve_runs ORDER BY id DESC LIMIT 10"),
        }

    @app.get("/api/stream", dependencies=[Depends(auth)])
    async def stream(request: Request):
        async def gen():
            last_f = (store.one("SELECT MAX(id) AS v FROM forks")["v"] or 0)
            last_e = (store.one("SELECT MAX(id) AS v FROM events")["v"] or 0)
            last_g = (store.one("SELECT COUNT(*) || ':' || COALESCE(MAX(decided_at),0) AS v FROM gates")["v"])
            while not await request.is_disconnected():
                f, e = forks_since(last_f), events_since(last_e)
                g = store.one("SELECT COUNT(*) || ':' || COALESCE(MAX(decided_at),0) AS v FROM gates")["v"]
                if f or e or g != last_g:
                    last_f = max([last_f] + [r["id"] for r in f])
                    last_e = max([last_e] + [r["id"] for r in e])
                    last_g = g
                    yield f"data: {json.dumps({'forks': f, 'events': e, 'gates_changed': True})}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/api/heartbeat", dependencies=[Depends(auth)])
    def heartbeat():
        store.touch("dashboard", time.time())
        return {"ok": True}

    @app.post("/api/gates/{gate_id}/{verb}", dependencies=[Depends(auth)])
    def decide(gate_id: int, verb: str, request: Request):
        if verb not in ("approve", "reject"):
            raise HTTPException(400, "approve or reject")
        if request.headers.get("x-requested-with") != "dashboard":  # simple CSRF guard with SameSite=strict
            raise HTTPException(403, "missing header")
        if not store.decide_gate(gate_id, verb == "approve", "dashboard"):
            raise HTTPException(409, "gate is not pending")
        store.touch("dashboard", time.time())
        return {"ok": True}

    return app


def main():
    import uvicorn

    store = Store(os.environ.get("DP_AGENTS_DB", "agents/data/state.sqlite3"))
    app = create_app(store, os.environ.get("DASHBOARD_TOKEN", ""))
    uvicorn.run(app, host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"), port=int(os.environ.get("DASHBOARD_PORT", "8765")),
                log_level="warning")
