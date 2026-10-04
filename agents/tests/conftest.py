import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dp_agents import graph  # noqa: E402
from dp_agents.engine import Ctx  # noqa: E402
from dp_agents.notify import Null  # noqa: E402
from dp_agents.opus import FakeOpus  # noqa: E402
from dp_agents.store import Store  # noqa: E402
from dp_agents.workspace import Workspace  # noqa: E402


def run(cwd, *cmd):
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


BASELINE = {
    "extractor": {"heuristic": {"micro_accuracy_all_invoices": 0.6, "stp_rate_clean": {"rate": 0.3},
                                "false_approve_negatives": {"rate": 0.0}}},
    "corruption": {"types": {"wrong_vendor_tax_id": {"false_approve_rate": 0.0},
                             "changed_bank_account": {"false_approve_rate": 0.0},
                             "wrong_total": {"false_approve_rate": 0.0}}},
}


@pytest.fixture
def repo(tmp_path):
    """A real git repo with an 'origin' remote, like the DocuPay checkout on the VPS."""
    origin = tmp_path / "origin.git"
    run(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "repo"
    run(tmp_path, "git", "clone", "-q", str(origin), str(work))
    run(work, "git", "config", "user.email", "agent@test")
    run(work, "git", "config", "user.name", "agent")
    (work / "backend" / "benchmark").mkdir(parents=True)
    (work / "backend" / "benchmark" / "results.json").write_text(json.dumps(BASELINE))
    (work / "backend" / "app.py").write_text("x = 1\n")
    (work / "frontend").mkdir()
    (work / "frontend" / "page.tsx").write_text("export {}\n")
    run(work, "git", "add", "-A")
    run(work, "git", "commit", "-qm", "init")
    run(work, "git", "push", "-q", "origin", "main")
    return work


class FakeGitHub:
    def __init__(self, tickets=None):
        self.tickets = tickets or []
        self.labels = []
        self.prs = []

    def ready_tickets(self):
        return list(self.tickets)

    def add_label(self, n, label):
        self.labels.append((n, label))

    def open_draft_pr(self, **kw):
        self.prs.append(kw)
        return f"https://github.com/x/y/pull/{len(self.prs)}"


@pytest.fixture
def make_ctx(tmp_path, repo):
    def factory(opus=None, gh=None):
        return Ctx(
            store=Store(tmp_path / "state.sqlite3"), opus=opus or FakeOpus({}),
            gh=gh or FakeGitHub(), ws=Workspace(repo, tmp_path / "work", "main"), notify=Null(),
            repo=repo, graph=graph.load(),
        )
    return factory
