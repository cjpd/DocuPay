"""Read the thresholds block from graph.md and check the graph's invariants."""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GRAPH_MD = ROOT / "graph.md"

_REQUIRED = {
    "thresholds": {"choice_sharp", "retry_yes", "retry_no", "done_yes", "done_no",
                   "specified_yes", "specified_no", "risk_low", "risk_high"},
    "window": {"active_minutes", "passive_hours"},
    "improve": {"min_misses", "min_closed", "min_minutes_between_runs", "max_runs_per_window"},
    "human_gates": {"G1", "G2", "G3", "G4"},
}


@dataclass(frozen=True)
class Thresholds:
    choice_sharp: float
    retry_yes: float
    retry_no: float
    done_yes: float
    done_no: float
    specified_yes: float
    specified_no: float
    risk_low: float
    risk_high: float


@dataclass(frozen=True)
class Graph:
    thresholds: Thresholds
    window: dict
    improve: dict
    human_gates: dict
    raw_block: str


def gates_block(text: str) -> str:
    """The fenced yaml block under '## Gates'. Its exact text is what G4 protects."""
    section = text.split("## Gates", 1)
    if len(section) != 2:
        raise ValueError("graph.md has no '## Gates' section")
    m = re.search(r"```yaml\n(.*?)```", section[1], re.S)
    if not m:
        raise ValueError("graph.md '## Gates' section has no yaml block")
    return m.group(1)


def parse(text: str) -> Graph:
    block = gates_block(text)
    data = yaml.safe_load(block) or {}
    for key, fields in _REQUIRED.items():
        missing = fields - set(data.get(key) or {})
        if missing:
            raise ValueError(f"graph.md gates block: {key} is missing {sorted(missing)}")
    t = Thresholds(**{k: float(data["thresholds"][k]) for k in _REQUIRED["thresholds"]})
    _check(t)
    return Graph(t, data["window"], data["improve"], data["human_gates"], block)


def _check(t: Thresholds) -> None:
    """Thresholds that make no sense are refused, so a typo cannot open a gate."""
    for name, value in vars(t).items():
        if name.startswith("risk"):
            if not 0 <= value <= 4:
                raise ValueError(f"{name}={value} must be in 0..4")
        elif not 0 <= value <= 1:
            raise ValueError(f"{name}={value} must be in 0..1")
    pairs = [("retry_no", "retry_yes"), ("done_no", "done_yes"),
             ("specified_no", "specified_yes"), ("risk_low", "risk_high")]
    for lo, hi in pairs:
        if getattr(t, lo) >= getattr(t, hi):
            raise ValueError(f"{lo} must be below {hi}")
    if t.choice_sharp <= 0.5 or t.done_yes < 0.9:
        raise ValueError("choice_sharp must be above 0.5 and done_yes at least 0.9")


def load(path: Path = GRAPH_MD) -> Graph:
    return parse(path.read_text())
