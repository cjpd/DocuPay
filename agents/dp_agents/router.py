"""
Sharp or split. Pure functions: a Jev answer plus the graph.md thresholds gives either a decision
code can act on, or None (split: ask Opus). Nothing here calls a model.
"""
from dataclasses import dataclass
from typing import Any, Optional

from .graph import Thresholds
from .jev import Answer


@dataclass(frozen=True)
class Route:
    sharp: bool
    value: Any = None      # the decision when sharp
    reason: str = ""


def noul(answer: Optional[Answer], yes: float, no: float) -> Route:
    if answer is None or answer.kind != "noul":
        return Route(False, reason="no answer")
    p = answer.probs["yes"]
    if p >= yes:
        return Route(True, True, f"p={p:.2f}>={yes}")
    if p <= no:
        return Route(True, False, f"p={p:.2f}<={no}")
    return Route(False, reason=f"split p={p:.2f}")


def choice(answer: Optional[Answer], sharp: float, allowed: list[str]) -> Route:
    if answer is None or answer.kind != "choice":
        return Route(False, reason="no answer")
    label, p = max(answer.probs.items(), key=lambda kv: kv[1])
    if label not in allowed:  # Jev can only pick a fixed outcome; anything else is refused
        return Route(False, reason=f"unknown label {label!r}")
    if p >= sharp:
        return Route(True, label, f"p={p:.2f}>={sharp}")
    return Route(False, reason=f"split top={label} p={p:.2f}")


def risk(answer: Optional[Answer], t: Thresholds) -> Route:
    """Sharp 'low' or 'high'; the middle band is split and gets an extra Opus review."""
    if answer is None or answer.kind != "score":
        return Route(False, reason="no answer")
    s = float(answer.value)
    if s <= t.risk_low:
        return Route(True, "low", f"risk={s:.2f}<={t.risk_low}")
    if s >= t.risk_high:
        return Route(True, "high", f"risk={s:.2f}>={t.risk_high}")
    return Route(False, reason=f"split risk={s:.2f}")


def for_fork(fork: str, answer: Optional[Answer], t: Thresholds, options: Optional[list[str]] = None) -> Route:
    base = fork.split(":")[0]
    if base == "specified":
        return noul(answer, t.specified_yes, t.specified_no)
    if base == "retry":
        return noul(answer, t.retry_yes, t.retry_no)
    if base == "done":
        return noul(answer, t.done_yes, t.done_no)
    if base == "target_file":
        return choice(answer, t.choice_sharp, options or [])
    if base == "risk":
        return risk(answer, t)
    raise ValueError(f"unknown fork {fork}")
