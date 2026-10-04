"""Every decision the graph takes, logged with who took it: a code rule or Opus."""
import re
from typing import Any, Optional

from . import limits


def record(ctx, *, ticket: Optional[int], node: str, name: str, decision: Any, source: str, detail: str = "") -> Any:
    ctx.store.decision(ticket=ticket, node=node, name=name, decision=decision, source=source, detail=detail[:500])
    return decision


def retry_rule(failure_tail: str, previous_tail: Optional[str]) -> tuple[bool, str]:
    """N3: retry unless the failure is environmental or exactly repeats the last one."""
    low = (failure_tail or "").lower()
    for pattern in limits.ENVIRONMENT_FAILURES:
        if pattern in low:
            return False, f"environment failure ('{pattern}'): another build cannot fix it"
    norm = lambda s: re.sub(r"\d+(\.\d+)?s\b|0x[0-9a-f]+", "", (s or "").strip())[-1500:]
    if previous_tail and norm(previous_tail) == norm(failure_tail):
        return False, "the same failure twice in a row"
    return True, "a new, specific failure"
