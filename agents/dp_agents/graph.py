"""Read the gates block from graph.md and check it."""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GRAPH_MD = ROOT / "graph.md"


@dataclass(frozen=True)
class Graph:
    risk_flag_at: int
    human_gates: dict
    raw_block: str


def gates_block(text: str) -> str:
    """The fenced yaml block under '## Gates'."""
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
    risk = (data.get("review") or {}).get("risk_flag_at")
    if not isinstance(risk, int) or not 1 <= risk <= 4:
        raise ValueError("graph.md: review.risk_flag_at must be an integer from 1 to 4")
    gates = data.get("human_gates") or {}
    if set(gates) != {"G1", "G2", "G3", "G4"}:
        raise ValueError("graph.md: human_gates must be exactly G1..G4")
    return Graph(risk, gates, block)


def load(path: Path = GRAPH_MD) -> Graph:
    return parse(path.read_text())
