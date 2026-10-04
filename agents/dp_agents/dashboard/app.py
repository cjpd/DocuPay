"""
Live dashboard: every decision, who took it (a code rule or Opus), the edges and the gates.

Bind it to 127.0.0.1 and reach it over an SSH tunnel or Tailscale. Every API call needs the
DASHBOARD_TOKEN (cookie after /login?token=..., or a Bearer header).
"""
import asyncio
import hmac
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse

from .. import report
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

    def decisions_since(after_id: int, limit: int = 200):
        rows = store.rows("SELECT id, ts, ticket, node, name, decision, source, detail FROM decisions "
                          "WHERE id > ? ORDER BY id DESC LIMIT ?", (after_id, limit))
        for r in rows:
            r["decision"] = json.loads(r["decision"])
        return rows

    def events_since(after_id: int, limit: int = 100):
        return store.rows("SELECT id, ts, ticket, src, dst, edge_type, kind FROM events WHERE id > ? "
                          "ORDER BY id DESC LIMIT ?", (after_id, limit))

    @app.get("/api/state", dependencies=[Depends(auth)])
    def state():
        return {
            "report": report.build(store),
            "decisions": decisions_since(0),
            "events": events_since(0),
            "tickets": store.rows("SELECT number, title, state, node, build_rounds, review_rounds, opus_calls, usd, "
                                  "result FROM tickets ORDER BY COALESCE(closed_at, 9e12) DESC, number DESC LIMIT 30"),
            "gates": store.rows("SELECT id, ts, gate, ticket, summary, status FROM gates ORDER BY id DESC LIMIT 20"),
        }

    @app.get("/api/stream", dependencies=[Depends(auth)])
    async def stream(request: Request):
        async def gen():
            last_f = (store.one("SELECT MAX(id) AS v FROM decisions")["v"] or 0)
            last_e = (store.one("SELECT MAX(id) AS v FROM events")["v"] or 0)
            last_g = (store.one("SELECT COUNT(*) || ':' || COALESCE(MAX(decided_at),0) AS v FROM gates")["v"])
            while not await request.is_disconnected():
                f, e = decisions_since(last_f), events_since(last_e)
                g = store.one("SELECT COUNT(*) || ':' || COALESCE(MAX(decided_at),0) AS v FROM gates")["v"]
                if f or e or g != last_g:
                    last_f = max([last_f] + [r["id"] for r in f])
                    last_e = max([last_e] + [r["id"] for r in e])
                    last_g = g
                    yield f"data: {json.dumps({'decisions': f, 'events': e, 'gates_changed': True})}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.post("/api/gates/{gate_id}/{verb}", dependencies=[Depends(auth)])
    def decide(gate_id: int, verb: str, request: Request):
        if verb not in ("approve", "reject"):
            raise HTTPException(400, "approve or reject")
        if request.headers.get("x-requested-with") != "dashboard":  # simple CSRF guard with SameSite=strict
            raise HTTPException(403, "missing header")
        if not store.decide_gate(gate_id, verb == "approve", "dashboard"):
            raise HTTPException(409, "gate is not pending")
        return {"ok": True}

    return app


def main():
    import uvicorn

    store = Store(os.environ.get("DP_AGENTS_DB", "agents/data/state.sqlite3"))
    app = create_app(store, os.environ.get("DASHBOARD_TOKEN", ""))
    uvicorn.run(app, host=os.environ.get("DASHBOARD_HOST", "127.0.0.1"), port=int(os.environ.get("DASHBOARD_PORT", "8765")),
                log_level="warning")
