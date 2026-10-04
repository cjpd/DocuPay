"""
Fill a demo database by running three tickets through the real engine with scripted Jev and Opus.
Nothing leaves this machine: GitHub, Jev and Opus are fakes, and the git repo is a temp copy.

    agents/.venv/bin/python agents/scripts/demo.py agents/data/demo.sqlite3
    DP_AGENTS_DB=agents/data/demo.sqlite3 DASHBOARD_TOKEN=... agents/.venv/bin/python -m dp_agents.cli dashboard
"""
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from conftest import BASELINE, FakeGitHub  # noqa: E402
from dp_agents import engine, graph, limits, questions  # noqa: E402
from dp_agents.edges import Ticket  # noqa: E402
from dp_agents.engine import Ctx  # noqa: E402
from dp_agents.jev import Answer, JevResult  # noqa: E402
from dp_agents.notify import Null  # noqa: E402
from dp_agents.opus import FakeOpus  # noqa: E402
from dp_agents.store import Store  # noqa: E402
from dp_agents.workspace import Workspace  # noqa: E402

random.seed(7)


class DemoJev:
    """Mostly sharp, sometimes split, latency around 250-700 ms."""

    def __init__(self, split_every=4):
        self.n = 0
        self.split_every = split_every

    def ask(self, state, qs):
        answers = {}
        for name, q in qs.items():
            self.n += 1
            split = self.n % self.split_every == 0
            if q["type"] == "noul":
                p = random.uniform(0.55, 0.8) if split else random.uniform(0.96, 0.995)
                if name == "retry" and state.get("failure", {}).get("check") == "frontend lint" and not split:
                    p = 0.9
                answers[name] = Answer("noul", {"yes": p}, p)
            elif q["type"] == "choice":
                labels = list(q["criteria"])
                top = 0.6 if split else 0.93
                probs = {l: (top if i == 0 else (1 - top) / (len(labels) - 1)) for i, l in enumerate(labels)}
                answers[name] = Answer("choice", probs, labels[0], top)
            else:
                answers[name] = Answer("score", {"0": 0.1, "1": 0.7, "2": 0.2, "3": 0, "4": 0}, 1.1 if not split else 2.1, 0.7)
        return JevResult(answers, random.uniform(240, 700), 400, 0, "jev-demo")


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
    review = {"findings": [{"severity": "nit", "file": "backend/apps/documents/export.py", "line": 12, "message": "rename var"}]}
    opus = FakeOpus({"plan": [plan], "build": [{"summary": "done"}], "review": [review],
                     "decide N1 forks": [{"specified": True}], "decide N2 forks": [{"target_file": plan["files"][0]}],
                     "decide N3 forks": [{"retry": True}],
                     "decide N5 forks": [{"risk": "low", "done:1": True, "done:2": True}]}, cost=0.42, on_edit=edit)

    def t(n, title, acc, points=2):
        return (time.time() - 3600, Ticket(number=n, title=title, body="demo", acceptance=acc, points=points,
                                          priority=1, labeled_by="cjpd"), True)

    gh = FakeGitHub([t(41, "CSV: add a 'Due in days' column", ["Column in invoice export", "Header label in CSV"]),
                     t(42, "Show vendor tax ID on the review card", ["Tax ID on the card", "Masked when long"]),
                     t(43, "PO matching for invoices", ["Match PO numbers", "Flag mismatches"], points=8)])
    store = Store(db_path)
    ctx = Ctx(store=store, jev=DemoJev(), opus=opus, gh=gh, ws=Workspace(repo, tmp / "work", "main"),
              notify=Null(), repo=repo, graph=graph.load(), questions=questions.load())
    store.touch("dashboard", time.time())
    engine.tick(ctx)                                   # 41: lint fails once, back edge, then G1
    gate = store.one("SELECT id FROM gates WHERE ticket=41")
    store.decide_gate(gate["id"], True, "demo")
    engine.tick(ctx)                                   # approve -> draft PR; 42 starts and waits at G1
    engine.tick(ctx)
    print("demo database:", db_path)
    for r in store.rows("SELECT number, state, result FROM tickets"):
        print(r)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "agents/data/demo.sqlite3")
