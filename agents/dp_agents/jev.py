"""
Jev, the fast reflex. One call per fork, all questions in that call, probabilities kept.

The `jev` decorator package discards probabilities, so this uses typesafe-sdk directly.
The key comes from TYPESAFE_API_KEY (an env file, or a gateway credential); it is never logged.
"""
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from . import limits


@dataclass
class Answer:
    kind: str                       # noul | choice | score
    probs: dict[str, float]         # noul: {"yes": p}; choice: label -> p; score: level -> p
    value: Any                      # noul: p(yes); choice: top label; score: expected score
    confidence: Optional[float] = None


@dataclass
class JevResult:
    answers: dict[str, Answer]
    latency_ms: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    model: str = ""
    error: Optional[str] = None
    extra: dict = field(default_factory=dict)


class Jev(Protocol):
    def ask(self, state: Any, questions: dict[str, dict]) -> JevResult: ...


class TypeSafeJev:
    def __init__(self, model: str = "jev-latest"):
        from typesafe_sdk import RetryPolicy, TypeSafeClient  # imported lazily: tests use FakeJev

        self.model = model
        # One quick retry only: a slow fork goes to Opus rather than waiting.
        self.client = TypeSafeClient(
            # With a gateway credential the key is added on the wire, so the local value is a placeholder.
            api_key=os.environ.get("TYPESAFE_API_KEY") or "injected-by-gateway",
            retry=RetryPolicy(max_retries=1, timeout=limits.JEV_TIMEOUT_S))

    def ask(self, state, questions):
        started = time.monotonic()
        try:
            r = self.client.system_one(state=state, questions=questions, model=self.model,
                                       timeout=limits.JEV_TIMEOUT_S)
        except Exception as exc:  # any failure routes the fork to Opus; never fail-open
            return JevResult({}, (time.monotonic() - started) * 1000, error=f"{type(exc).__name__}")
        latency = (time.monotonic() - started) * 1000
        answers = {}
        for name, a in r.answers.items():
            if a.type == "noul":
                answers[name] = Answer("noul", {"yes": a.noul}, a.noul)
            elif a.type == "choice":
                answers[name] = Answer("choice", dict(a.probabilities), a.choice, a.confidence)
            elif a.type == "score":
                answers[name] = Answer("score", {str(k): v for k, v in a.probabilities.items()}, a.score, a.confidence)
        return JevResult(answers, latency, r.usage.input_tokens, r.usage.output_tokens, r.model)


class FakeJev:
    """Deterministic Jev for tests and dry runs: returns the scripted answers, else unsure."""

    def __init__(self, script: Optional[dict[str, Answer]] = None, latency_ms: float = 5.0, fail: bool = False):
        self.script = script or {}
        self.latency_ms = latency_ms
        self.fail = fail
        self.calls: list[tuple[Any, dict]] = []

    def ask(self, state, questions):
        self.calls.append((state, questions))
        if self.fail:
            return JevResult({}, self.latency_ms, error="FakeFailure")
        answers = {}
        for name, q in questions.items():
            base = name.split(":")[0]
            if name in self.script or base in self.script:
                answers[name] = self.script.get(name) or self.script[base]
            elif q["type"] == "noul":
                answers[name] = Answer("noul", {"yes": 0.5}, 0.5)
            elif q["type"] == "choice":
                labels = list(q["criteria"])
                p = 1 / len(labels)
                answers[name] = Answer("choice", {l: p for l in labels}, labels[0], p)
            else:
                n = len(q["criteria"])
                answers[name] = Answer("score", {str(i): 1 / n for i in range(n)}, (n - 1) / 2, 1 / n)
        return JevResult(answers, self.latency_ms, 10, 0, "fake")


def get_jev() -> Jev:
    if os.environ.get("DP_AGENTS_FAKE_JEV") == "1":
        return FakeJev()
    return TypeSafeJev(os.environ.get("JEV_MODEL", "jev-latest"))
