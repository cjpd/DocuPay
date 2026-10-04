"""
Ask a node's forks: one Jev call for all of them, sharp answers decided by code, split ones by
Opus (one call for all split forks), every fork logged with its probabilities and route.
"""
import json
import time
from dataclasses import dataclass
from typing import Any, Optional

from . import opus as opus_mod
from . import questions as q
from . import router


@dataclass
class Fork:
    name: str                   # e.g. "done:2" (base name before the colon picks the question)
    question: dict              # typesafe-sdk question dict
    outcomes: list              # the fixed outcomes Opus must choose from on a split
    options: Optional[list[str]] = None   # for choice forks


def noul_fork(name: str, ctx) -> Fork:
    return Fork(name, q.noul(ctx.questions["forks"][name.split(":")[0]]), [True, False])


def choice_fork(name: str, ctx, options: list[str]) -> Fork:
    return Fork(name, q.choice(ctx.questions["forks"][name.split(":")[0]], options), list(options), options)


def score_fork(name: str, ctx) -> Fork:
    return Fork(name, q.score(ctx.questions["forks"][name.split(":")[0]]), ["low", "mid", "high"])


def _opus_schema(split: list[Fork]) -> dict:
    props = {}
    for f in split:
        if f.outcomes == [True, False]:
            props[f.name] = {"type": "boolean"}
        else:
            props[f.name] = {"type": "string", "enum": [str(o) for o in f.outcomes]}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def ask(ctx, *, ticket: Optional[int], node: str, state: Any, forks: list[Fork]) -> dict[str, Any]:
    """Return {fork name: decision}. A decision is always one of the fork's fixed outcomes."""
    call_id = time.time_ns()
    result = ctx.jev.ask(state, {f.name: f.question for f in forks})
    decisions, split = {}, []
    per_fork_latency = result.latency_ms  # one call answers all forks of the node
    for f in forks:
        answer = result.answers.get(f.name)
        route = router.for_fork(f.name, answer, ctx.graph.thresholds, f.options)
        fork_id = ctx.store.fork(
            ticket=ticket, node=node, fork=f.name, state=state if isinstance(state, (dict, list)) else {"text": str(state)[:4000]},
            question=f.question, outcomes=[str(o) for o in f.outcomes], probs=answer.probs if answer else None,
            answer=json.dumps(route.value) if route.sharp else None,
            route="jev" if route.sharp else ("opus-fallback" if result.error else "opus"),
            jev_call=call_id, latency_ms=per_fork_latency, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            questions_version=ctx.questions.get("version"),
        )
        if route.sharp:
            decisions[f.name] = route.value
        else:
            split.append((f, fork_id, route.reason))
    if split:
        lines = "\n".join(
            f"- {f.name}: {f.question.get('instructions')} Outcomes: {[str(o) for o in f.outcomes]}. Jev: {why}"
            for f, _, why in split
        )
        data = opus_mod.call(
            ctx.opus, ctx.store, ticket=ticket, node=node, purpose=f"decide {node} forks",
            prompt=("Jev was not sure about these decisions. Decide each one. Pick only from the listed outcomes.\n"
                    f"{lines}\n\nState:\n{opus_mod.untrusted(json.dumps(state, default=str)[:12000])}"),
            schema=_opus_schema([f for f, _, _ in split]), cwd=str(ctx.repo), tools=opus_mod.READ_TOOLS,
        )
        for f, fork_id, _ in split:
            value = data[f.name]
            if f.outcomes != [True, False] and value not in [str(o) for o in f.outcomes]:
                raise ValueError(f"Opus answered {value!r} outside the fixed outcomes of {f.name}")
            decisions[f.name] = value
            ctx.store.exec("UPDATE forks SET answer=? WHERE id=?", (json.dumps(value), fork_id))
    return decisions
