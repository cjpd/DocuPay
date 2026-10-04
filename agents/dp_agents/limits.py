"""
Hard limits. Owned by code only.

Nothing in this file is read from graph.md, questions.yaml, a model answer or the environment,
so no model (including the N9 improve loop) can loosen it. Change it by hand, in a reviewed commit.
"""

# Never touched by the agent.
PROTECTED_BRANCHES = frozenset({"main", "master"})
WORK_BRANCH_PREFIX = "agent/"

# Paths a build diff may touch. Anything else fails the N3 check.
ALLOWED_PATHS = ("backend/", "frontend/", "agents/tests/")
# Paths that are allowed but always send the ticket to a person at G1 with a flag.
SENSITIVE_PATHS = (
    "backend/config/",
    "backend/apps/users/",
    "backend/apps/documents/migrations/",
    "backend/apps/organizations/migrations/",
    ".github/",
    ".env",
)
# Never allowed, even in a sensitive diff.
FORBIDDEN_PATHS = ("agents/dp_agents/", "agents/graph.md", "agents/questions.yaml", ".env")

MAX_DIFF_LINES = 600
MAX_TICKET_POINTS = 5

# Stop rules (retries are counted by code, per ticket).
MAX_PLAN_ATTEMPTS = 3          # 1 try + 2 retries
MAX_BUILD_ROUNDS = 3           # across all back edges into N3
MAX_REVIEW_ROUNDS = 2

# Budgets.
MAX_OPUS_CALLS_PER_TICKET = 25
MAX_USD_PER_TICKET = 5.0
MAX_USD_PER_DAY = 20.0
MAX_MINUTES_PER_TICKET = 60
OPUS_CALL_TIMEOUT_S = 900
JEV_TIMEOUT_S = 5.0
JEV_SLOW_MS = 1000             # logged as slow above this

# N9 improve.
MAX_REPLAY_FORKS = 200         # Jev calls per N9 replay, per version

# The fixed check list for N4 verify (cwd = repo root of the worktree).
VERIFY_COMMANDS = (
    ("backend tests", "cd backend && .venv/bin/python -m pytest -q -o addopts=''"),
    ("migrations", "cd backend && DJANGO_SETTINGS_MODULE=config.settings_test .venv/bin/python manage.py makemigrations --check --dry-run"),
    ("frontend typecheck", "cd frontend && npx tsc --noEmit"),
    ("frontend lint", "cd frontend && npm run lint --silent"),
    ("frontend build", "cd frontend && npm run build --silent"),
)
VERIFY_TIMEOUT_S = 900
BENCHMARK_COMMAND = "backend/.venv/bin/python backend/benchmark/run.py --out {out}"
# Fraud categories whose false auto-approve rate must stay exactly 0 in N6.
FRAUD_CATEGORIES = ("wrong_vendor_tax_id", "changed_bank_account")
