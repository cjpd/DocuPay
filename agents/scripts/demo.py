"""
Fill a demo database by running tickets through the real engine with a scripted Opus.
Nothing leaves this machine: GitHub and Opus are fakes, and the git repo is a temp copy.

    agents/.venv/bin/python agents/scripts/demo.py agents/data/demo.sqlite3
    DP_AGENTS_DB=agents/data/demo.sqlite3 DASHBOARD_TOKEN=... agents/.venv/bin/python -m dp_agents.cli dashboard
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from conftest import BASELINE, FakeGitHub  # noqa: E402
from dp_agents import engine, graph, limits  # noqa: E402
from dp_agents.edges import Ticket  # noqa: E402
from dp_agents.engine import Ctx  # noqa: E402
from dp_agents.notify import Null  # noqa: E402
from dp_agents.opus import FakeOpus  # noqa: E402
from dp_agents.store import Store  # noqa: E402
from dp_agents.workspace import Workspace  # noqa: E402

def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def main(db_path: str):
    tmp = Path(tempfile.mkdtemp())
    origin, repo = tmp / "origin.git", tmp / "repo"
    git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp, "clone", "-q", str(origin), str(repo))
    git(repo, "config", "user.email", "demo@local"); git(repo, "config", "user.name", "demo")
    (repo / "backend/benchmark").mkdir(parents=True)
    (repo / "backend/benchmark/results.json").write_text(json.dumps(BASELINE))
    for f in ("backend/apps/documents/export.py", "backend/apps/processing/validation.py", "frontend/src/app/review/page.tsx"):
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("# demo\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "init"); git(repo, "push", "-q", "origin", "main")

    limits.VERIFY_COMMANDS = (("backend tests", f"test -f {tmp}/lint-ok || (touch {tmp}/lint-ok; echo 'src/app/review/page.tsx 144:33 error Compilation Skipped'; exit 1)"),)
    limits.BENCHMARK_COMMAND = "cp backend/benchmark/results.json {out}"

    def edit(cwd, data):
        p = Path(cwd) / "backend/apps/documents/export.py"
        p.write_text(p.read_text() + f"# change {time.time()}\n")

    plan = {"summary": "Add the column", "steps": [{"description": "edit export", "files": ["backend/apps/documents/export.py"]}],
            "files": ["backend/apps/documents/export.py", "backend/apps/processing/validation.py"], "new_files": [],
            "tests": {"1": "test_export_column", "2": "test_header"}}
    review = {"findings": [{"severity": "nit", "file": "backend/apps/documents/export.py", "line": 12, "message": "rename var"}],
              "done": [True, True], "risk": 1}
    opus = FakeOpus({"plan": [plan], "build": [{"summary": "done"}], "review": [review]}, cost=0.42, on_edit=edit)

    def t(n, title, acc, points=2):
        return (time.time() - 3600, Ticket(number=n, title=title, body="demo", acceptance=acc, points=points,
                                          priority=1, labeled_by="cjpd"), True)

    gh = FakeGitHub([t(41, "CSV: add a 'Due in days' column", ["Column in invoice export", "Header label in CSV"]),
                     t(42, "Show vendor tax ID on the review card", ["Tax ID on the card", "Masked when long"]),
                     t(43, "PO matching for invoices", ["Match PO numbers", "Flag mismatches"], points=8)])
    store = Store(db_path)
    ctx = Ctx(store=store, opus=opus, gh=gh, ws=Workspace(repo, tmp / "work", "main"),
              notify=Null(), repo=repo, graph=graph.load())
    engine.tick(ctx)                                   # 41: lint fails once, back edge, then G1
    gate = store.one("SELECT id FROM gates WHERE ticket=41")
    store.decide_gate(gate["id"], True, "demo")
    engine.tick(ctx)                                   # approve -> draft PR; 42 starts and waits at G1
    gate = store.one("SELECT id FROM gates WHERE ticket=42")
    store.decide_gate(gate["id"], True, "demo")
    engine.tick(ctx)                                   # 42 ships; 43 (8 points) is refused at N1
    print("demo database:", db_path)
    for r in store.rows("SELECT number, state, result FROM tickets"):
        print(r)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "agents/data/demo.sqlite3")
