"""One git worktree per ticket, on agent/<number>. Code checks every diff against the hard limits."""
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import limits


def git(cwd, *args, check=True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[-400:]}")
    return r.stdout


@dataclass
class DiffCheck:
    ok: bool
    files: list[str]
    lines: int
    sensitive: bool
    reason: str = ""


def check_diff(files: list[str], lines: int) -> DiffCheck:
    for f in files:
        if f.startswith(limits.FORBIDDEN_PATHS) or Path(f).name.startswith(".env"):
            return DiffCheck(False, files, lines, True, f"forbidden path {f}")
        if not f.startswith(limits.ALLOWED_PATHS):
            return DiffCheck(False, files, lines, False, f"path outside the allowed folders: {f}")
    if lines > limits.MAX_DIFF_LINES:
        return DiffCheck(False, files, lines, False, f"diff has {lines} lines, limit {limits.MAX_DIFF_LINES}")
    if not files:
        return DiffCheck(False, files, lines, False, "the build changed nothing")
    sensitive = any(f.startswith(limits.SENSITIVE_PATHS) for f in files)
    return DiffCheck(True, files, lines, sensitive)


class Workspace:
    def __init__(self, repo: Path, work_root: Path, base: str):
        self.repo, self.work_root, self.base = Path(repo), Path(work_root), base

    def branch(self, number: int) -> str:
        b = f"{limits.WORK_BRANCH_PREFIX}{number}"
        assert b not in limits.PROTECTED_BRANCHES
        return b

    def path(self, number: int) -> Path:
        return self.work_root / str(number)

    def ensure(self, number: int) -> Path:
        p = self.path(number)
        if not p.exists():
            self.work_root.mkdir(parents=True, exist_ok=True)
            git(self.repo, "fetch", "origin", self.base, check=False)
            start = f"origin/{self.base}" if git(self.repo, "rev-parse", "--verify", f"origin/{self.base}", check=False) else self.base
            git(self.repo, "worktree", "add", "-B", self.branch(number), str(p), start)
            # Reuse the installed environments instead of reinstalling per ticket.
            for rel in ("backend/.venv", "frontend/node_modules"):
                src, dst = self.repo / rel, p / rel
                if src.exists() and not dst.exists():
                    os.symlink(src, dst)
        return p

    def discard(self, number: int) -> None:
        """Remove this ticket's own worktree (inside the agent's work root only)."""
        p = self.path(number)
        if p.exists() and self.work_root in p.parents:
            git(self.repo, "worktree", "remove", "--force", str(p), check=False)
        git(self.repo, "worktree", "prune", check=False)

    def diff_files(self, number: int) -> tuple[list[str], int]:
        p = self.path(number)
        git(p, "add", "-A", "--", ".", ":(exclude)backend/.venv", ":(exclude)frontend/node_modules")
        names = [l for l in git(p, "diff", "--cached", "--name-only", f"origin/{self.base}").splitlines() if l]
        stat = git(p, "diff", "--cached", "--numstat", f"origin/{self.base}")
        lines = sum(int(a) + int(b) for a, b, *_ in (l.split("\t") for l in stat.splitlines()) if a.isdigit() and b.isdigit())
        return names, lines

    def commit(self, number: int, message: str) -> str:
        p = self.path(number)
        if git(p, "diff", "--cached", "--name-only"):
            git(p, "commit", "-m", message)
        return git(p, "rev-parse", "HEAD").strip()

    def diff_text(self, number: int, limit: int = 60000) -> str:
        return git(self.path(number), "diff", f"origin/{self.base}...HEAD")[:limit]

    def push(self, number: int) -> None:
        b = self.branch(number)
        if b in limits.PROTECTED_BRANCHES or not b.startswith(limits.WORK_BRANCH_PREFIX):
            raise RuntimeError(f"refusing to push {b}")
        git(self.path(number), "push", "-u", "origin", f"{b}:{b}")  # never --force
