"""GitHub REST adapter. Reads issues and activity; writes labels; opens a PR only after gate G1."""
import os
import re
from datetime import datetime
from typing import Optional

import httpx

from .edges import Ticket

API = "https://api.github.com"
PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def parse_acceptance(body: str) -> list[str]:
    """Checklist items ('- [ ] ...'), else the bullets under an 'Acceptance' heading."""
    items = re.findall(r"^\s*[-*]\s*\[[ xX]\]\s*(.+?)\s*$", body or "", re.M)
    if items:
        return items
    m = re.search(r"acceptance[^\n]*\n((?:\s*[-*]\s+.+\n?)+)", body or "", re.I)
    return re.findall(r"^\s*[-*]\s+(.+?)\s*$", m.group(1), re.M) if m else []


def parse_points(labels: list[str], body: str) -> int:
    for l in labels:
        if m := re.fullmatch(r"points?:\s*(\d+)", l, re.I):
            return int(m.group(1))
    m = re.search(r"\bpoints?\s*[:=]\s*(\d+)", body or "", re.I)
    return int(m.group(1)) if m else 3


def parse_priority(labels: list[str]) -> int:
    for l in labels:
        if m := re.fullmatch(r"priority:\s*(\w+)", l, re.I):
            return PRIORITY.get(m.group(1).lower(), 2)
    return 2


class GitHub:
    def __init__(self, repo: Optional[str] = None, token: Optional[str] = None, owner_login: Optional[str] = None):
        self.repo = repo or os.environ["GITHUB_REPO"]              # e.g. cjpd/DocuPay
        self.owner_login = owner_login or os.environ["GITHUB_OWNER_LOGIN"]
        token = token or os.environ.get("GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(base_url=API, headers=headers, timeout=20)

    def _get(self, path, **params):
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def ready_tickets(self, label: str = "agent-ready") -> list[Ticket]:
        out = []
        for i in self._get(f"/repos/{self.repo}/issues", labels=label, state="open", per_page=50):
            if "pull_request" in i:
                continue
            labels = [l["name"] for l in i["labels"]]
            if "agent-parked" in labels or "needs-spec" in labels:
                continue
            labeled_by, labeled_at = self._labeled_by(i["number"], label)
            if labeled_by != self.owner_login:
                continue  # only the owner can hand work to the agent
            acceptance = parse_acceptance(i.get("body") or "")
            out.append((labeled_at, Ticket(
                number=i["number"], title=i["title"], body=i.get("body") or "",
                acceptance=acceptance or ["(none given)"], points=parse_points(labels, i.get("body") or ""),
                priority=parse_priority(labels), labeled_by=labeled_by,
            ), bool(acceptance)))
        out.sort(key=lambda x: (x[1].priority, x[1].number))
        return out

    def _labeled_by(self, number: int, label: str):
        events = self._get(f"/repos/{self.repo}/issues/{number}/events", per_page=100)
        hits = [e for e in events if e.get("event") == "labeled" and e.get("label", {}).get("name") == label]
        if not hits:
            return None, None
        last = hits[-1]
        return (last.get("actor") or {}).get("login"), _ts(last["created_at"])

    def owner_last_activity(self) -> Optional[float]:
        events = self._get(f"/repos/{self.repo}/events", per_page=50)
        mine = [_ts(e["created_at"]) for e in events if (e.get("actor") or {}).get("login") == self.owner_login]
        return max(mine) if mine else None

    def add_label(self, number: int, label: str) -> None:
        self.http.post(f"/repos/{self.repo}/issues/{number}/labels", json={"labels": [label]}).raise_for_status()

    def open_draft_pr(self, *, head: str, base: str, title: str, body: str) -> str:
        """Called only by the engine after gate G1 is approved."""
        r = self.http.post(f"/repos/{self.repo}/pulls",
                           json={"head": head, "base": base, "title": title, "body": body, "draft": True})
        r.raise_for_status()
        return r.json()["html_url"]
