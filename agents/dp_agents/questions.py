"""
Jev questions: fixed outcomes in code, wording in questions.yaml.

`build()` turns a fork into the typesafe-sdk question dicts. `validate_rewrite()` is what N9
must pass: a new questions.yaml may change wording only, never a fork, a type or a level count.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_YAML = ROOT / "questions.yaml"

# The fixed shape of every fork. Code owns this.
FORK_TYPES = {"specified": "noul", "target_file": "choice", "retry": "noul", "done": "noul", "risk": "score"}
RISK_LEVELS = 5


def load(path: Path = QUESTIONS_YAML) -> dict:
    data = yaml.safe_load(path.read_text())
    validate(data)
    return data


def _normalize(data: dict) -> None:
    """YAML reads unquoted `true:`/`false:` keys as booleans; noul criteria use the strings."""
    for q in (data.get("forks") or {}).values():
        if isinstance(q, dict) and isinstance(q.get("criteria"), dict):
            q["criteria"] = {(str(k).lower() if isinstance(k, bool) else k): v for k, v in q["criteria"].items()}


def validate(data: dict) -> None:
    _normalize(data)
    forks = data.get("forks") or {}
    if set(forks) != set(FORK_TYPES):
        raise ValueError(f"questions.yaml forks must be exactly {sorted(FORK_TYPES)}")
    for name, kind in FORK_TYPES.items():
        q = forks[name]
        if q.get("type") != kind:
            raise ValueError(f"fork {name} must be type {kind}")
        if not str(q.get("instructions", "")).strip():
            raise ValueError(f"fork {name} needs instructions")
        if kind == "noul" and set((q.get("criteria") or {})) - {"true", "false"}:
            raise ValueError(f"fork {name}: noul criteria may only describe true/false")
        if kind == "score" and len(q.get("levels") or []) != RISK_LEVELS:
            raise ValueError(f"fork {name} must keep exactly {RISK_LEVELS} levels")


def validate_rewrite(old: dict, new: dict) -> None:
    validate(new)
    if int(new.get("version", 0)) != int(old.get("version", 0)) + 1:
        raise ValueError("a rewrite must bump version by exactly 1")


def noul(q: dict) -> dict:
    out = {"type": "noul", "instructions": q["instructions"].strip()}
    if q.get("criteria"):
        out["criteria"] = {k: q["criteria"][k] for k in ("true", "false") if k in q["criteria"]}
    return out


def choice(q: dict, options: list[str]) -> dict:
    if not 2 <= len(options) <= 255:
        raise ValueError("a choice needs 2 to 255 options")
    return {"type": "choice", "instructions": q["instructions"].strip(), "criteria": {o: None for o in options}}


def score(q: dict) -> dict:
    return {"type": "score", "instructions": q["instructions"].strip(), "criteria": list(q["levels"])}
