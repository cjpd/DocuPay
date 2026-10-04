"""Typed handoffs between nodes. Every edge payload is one of these models."""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Ticket(BaseModel):
    number: int
    title: str
    body: str
    acceptance: list[str] = Field(min_length=1)
    points: int
    priority: int  # lower is more urgent
    labeled_by: Optional[str] = None


class PlanStep(BaseModel):
    description: str
    files: list[str]


class Plan(BaseModel):
    summary: str
    steps: list[PlanStep] = Field(min_length=1)
    files: list[str] = Field(min_length=1)
    new_files: list[str] = []
    tests: dict[str, str]  # acceptance item -> test that proves it


class Commit(BaseModel):
    branch: str
    sha: str
    diff_lines: int
    files: list[str]
    sensitive: bool = False


class Failure(BaseModel):
    check: str
    log_tail: str


class Finding(BaseModel):
    severity: Literal["blocking", "should_fix", "nit"]
    file: str
    line: int = 0
    message: str


class Findings(BaseModel):
    findings: list[Finding]


class Review(BaseModel):
    """The N5 Critic's answer: findings, plus its own rating of each acceptance item and the risk."""
    findings: list[Finding]
    done: list[bool]
    risk: int = Field(ge=0, le=4)


class Verified(BaseModel):
    sha: str
    checks: list[str]


class Reviewed(BaseModel):
    sha: str
    risk: float
    done: dict[str, bool]


class Regression(BaseModel):
    metric: str
    baseline: float
    now: float


class Evaluated(BaseModel):
    sha: str
    metrics: dict[str, float]


class ShipRequest(BaseModel):
    branch: str
    sha: str
    title: str
    body: str
    risk: float
    sensitive: bool


class Escalation(BaseModel):
    node: str
    reason: str


class Unplannable(BaseModel):
    reason: str


EDGE_TYPES = {cls.__name__: cls for cls in (
    Ticket, Plan, Commit, Failure, Findings, Verified, Reviewed, Regression, Evaluated,
    ShipRequest, Escalation, Unplannable,
)}
