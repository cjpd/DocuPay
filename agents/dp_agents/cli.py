"""
python -m dp_agents.cli <command>

  run        the daemon: one tick per minute, daily report at REPORT_HOUR (UTC)
  tick       one pass of the graph, then exit
  dashboard  the live dashboard (127.0.0.1:8765 by default)
  report     print today's report (add --send to post it to Telegram)
  check      the final check; exits 1 unless every answer is clean (the service runs it first)
"""
import argparse
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # agents/
REPO = ROOT.parent


def load_env() -> None:
    """Read KEY=VALUE lines from the env file into os.environ. Values are never printed."""
    path = Path(os.environ.get("DP_AGENTS_ENV", ROOT / ".env"))
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_ctx():
    from . import graph
    from .engine import Ctx
    from .github import GitHub
    from .notify import Null, Telegram
    from .opus import ClaudeCodeOpus
    from .store import Store
    from .workspace import Workspace

    store = Store(os.environ.get("DP_AGENTS_DB", ROOT / "data" / "state.sqlite3"))
    tg = Telegram()
    return Ctx(
        store=store, opus=ClaudeCodeOpus(), gh=GitHub(),
        ws=Workspace(REPO, Path(os.environ.get("DP_AGENTS_WORK", ROOT / "work")), os.environ.get("DP_BASE_BRANCH", "main")),
        notify=tg if tg.enabled else Null(), repo=REPO, graph=graph.load(),
    )


# -- final check --------------------------------------------------------------------------------

def final_check(ctx=None) -> tuple[bool, list[str]]:
    from . import graph as graph_mod
    from . import limits

    problems, notes = [], []

    # 1. Does every loop have a stop rule?
    text = graph_mod.GRAPH_MD.read_text()
    table = text.split("## Nodes", 1)[1].split("##", 1)[0]
    rows = [r for r in table.splitlines() if re.match(r"\|\s*N\d", r)]
    for r in rows:
        cells = [c.strip() for c in r.strip("|").split("|")]
        if len(cells) < 5 or not cells[4]:
            problems.append(f"node {cells[0]} has no stop rule")
    if len(rows) != 8:
        problems.append(f"graph.md lists {len(rows)} nodes, expected 8")
    for name in ("MAX_PLAN_ATTEMPTS", "MAX_BUILD_ROUNDS", "MAX_REVIEW_ROUNDS", "MAX_OPUS_CALLS_PER_TICKET",
                 "MAX_USD_PER_TICKET", "MAX_USD_PER_DAY", "MAX_MINUTES_PER_TICKET"):
        if not getattr(limits, name, 0) > 0:
            problems.append(f"limits.{name} must be a positive number")

    # 2. Is any hard limit owned by a model instead of code?
    try:
        g = graph_mod.parse(text)
    except Exception as exc:
        problems.append(f"graph.md is invalid: {exc}")
        g = None
    block = graph_mod.gates_block(text)
    for name in dir(limits):
        if name.isupper() and name.lower() in block:
            problems.append(f"hard limit {name} also appears in graph.md")
    for protected in ("agents/dp_agents/", "agents/graph.md"):
        if not protected.startswith(limits.FORBIDDEN_PATHS):
            problems.append(f"{protected} is not in limits.FORBIDDEN_PATHS")
    if g and set(g.human_gates) != {"G1", "G2", "G3", "G4"}:
        problems.append("graph.md must define human gates G1..G4")

    # 3. Which step sends work back most often?
    if ctx is not None:
        top = ctx.store.one("SELECT src, COUNT(*) AS n FROM events WHERE kind='back' GROUP BY src ORDER BY n DESC LIMIT 1")
        notes.append(f"most back edges come from {top['src']} ({top['n']})" if top else "no back edges recorded yet")

    # 4. What could break this week? (environment and dependencies)
    need = {"GITHUB_TOKEN": "GitHub API", "GITHUB_REPO": "GitHub repo", "GITHUB_OWNER_LOGIN": "owner login",
            "TELEGRAM_BOT_TOKEN": "Telegram alerts", "TELEGRAM_CHAT_ID": "Telegram chat", "DASHBOARD_TOKEN": "dashboard"}
    for key, what in need.items():
        if not os.environ.get(key):
            problems.append(f"{key} is not set ({what})")
    if len(os.environ.get("DASHBOARD_TOKEN", "")) < 24:
        problems.append("DASHBOARD_TOKEN must be at least 24 characters")
    if not shutil.which(os.environ.get("CLAUDE_BIN", "claude")):
        problems.append("the claude CLI is not on PATH (Opus)")
    for rel in ("backend/.venv/bin/python", "frontend/node_modules"):
        if not (REPO / rel).exists():
            problems.append(f"{rel} is missing: the verify node cannot run the checks")
    free = shutil.disk_usage(ROOT).free / 1e9
    if free < 5:
        problems.append(f"only {free:.1f} GB disk free; worktrees and builds need 5 GB")
    return not problems, problems + [f"note: {n}" for n in notes]


def print_check(ok: bool, lines: list[str]) -> None:
    print("FINAL CHECK")
    print("1. Every loop has a stop rule:", "yes" if not any("stop rule" in l for l in lines) else "NO")
    print("2. No hard limit owned by a model:", "yes" if not any(k in l for l in lines for k in ("hard limit", "G1..G4", "FORBIDDEN")) else "NO")
    back = next((l for l in lines if "back edges" in l), "unknown")
    print("3. Step that sends work back most:", back.removeprefix("note: "))
    print("\nWHAT COULD BREAK THIS WEEK?")
    for l in lines:
        print(" -", l)
    if not lines:
        print(" - nothing found by the automatic checks")
    print("\nResult:", "CLEAN: may run unattended" if ok else "NOT CLEAN: refusing to run unattended")


# -- commands -----------------------------------------------------------------------------------

def cmd_run(args):
    from . import engine, report

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ctx = make_ctx()
    ok, lines = final_check(ctx)
    if not ok:
        print_check(ok, lines)
        ctx.notify.send("⛔ DocuPay agents refused to start unattended:\n" + "\n".join(lines[:10]))
        sys.exit(1)
    ctx.notify.send("▶️ DocuPay agents started")
    report_hour = int(os.environ.get("REPORT_HOUR", "17"))
    while True:
        started = time.time()
        try:
            engine.tick(ctx)
        except Exception as exc:  # the daemon survives; the error is logged and alerted
            logging.exception("tick failed")
            ctx.notify.send(f"❗ tick failed: {type(exc).__name__}: {str(exc)[:300]}")
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if time.gmtime().tm_hour >= report_hour and ctx.store.meta("report_sent") != today:
            ctx.notify.send(report.text(report.build(ctx.store)))
            ctx.store.set_meta("report_sent", today)
        time.sleep(max(5, 60 - (time.time() - started)))


def cmd_tick(args):
    from . import engine

    logging.basicConfig(level=logging.INFO)
    engine.tick(make_ctx())


def cmd_dashboard(args):
    from .dashboard.app import main

    main()


def cmd_report(args):
    from . import report
    from .store import Store

    r = report.build(Store(os.environ.get("DP_AGENTS_DB", ROOT / "data" / "state.sqlite3")))
    print(report.text(r))
    if args.send:
        from .notify import Telegram

        Telegram().send(report.text(r))


def cmd_check(args):
    try:
        ctx = make_ctx()
    except Exception as exc:
        print_check(False, [f"cannot build the graph context: {type(exc).__name__}: {exc}"])
        sys.exit(1)
    ok, lines = final_check(ctx)
    print_check(ok, lines)
    sys.exit(0 if ok else 1)


def main(argv=None):
    load_env()
    p = argparse.ArgumentParser(prog="dp_agents")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    sub.add_parser("tick").set_defaults(fn=cmd_tick)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)
    r = sub.add_parser("report"); r.add_argument("--send", action="store_true"); r.set_defaults(fn=cmd_report)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
