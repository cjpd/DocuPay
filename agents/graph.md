# DocuPay dev-loop graph

The graph that takes a GitHub issue to a reviewed, tested branch ready for a PR.
Three layers that never overlap:

| Layer | Who | Owns |
|---|---|---|
| Slow brain | Opus (Claude Code headless, `claude -p`) | plans, code, diff review, split forks |
| Fast reflex | Jev (TypeSafe System One) | a calibrated probability for each fixed outcome of a fork |
| Decider | deterministic code (`dp_agents/`) | state, thresholds, retry limits, budgets, every irreversible action |

The models advise, the code decides. Hard limits live in `dp_agents/limits.py`, not here,
so no model (including the N9 self-improvement loop) can change them.

## Nodes

Every node is one loop: trigger, one narrow action, a check that proves it is done, a stop rule.

| Node | Trigger | Action | Check | Stop rule |
|---|---|---|---|---|
| N1 intake | open issue labeled `agent-ready` | pick the next issue (code sorts by priority label, then age) | issue has acceptance criteria, points <= 5, Jev `specified` is sharp yes | fail: label `needs-spec`, stop (0 retries) |
| N2 plan | Ticket from N1 | Opus writes a plan: files, steps, one test per acceptance item | every file exists or is marked new; every acceptance item maps to a test | 2 retries, then escalate |
| N3 build | Plan, or a back edge | Opus edits code in a git worktree on `agent/<issue>` | diff applies, stays in allowed paths, <= max diff lines | 3 build rounds per ticket across all back edges, then escalate |
| N4 verify | new commit on the work branch | code runs the fixed check list (`limits.VERIFY_COMMANDS`) | every command exits 0 | fail: back edge to N3 with the failing tail |
| N5 review | N4 green | Opus, fresh context, reviews the diff as the Critic | no blocking finding; Jev `done` sharp yes for every acceptance item | blocking finding: back edge to N3 (2 review rounds) |
| N6 evaluate | N5 pass | code runs the offline benchmark and compares with the baseline | fraud false-approve stays 0; accuracy not lower than baseline | regression: back edge to N3 |
| N7 ship | N6 pass | code pushes the work branch, drafts the PR text | push succeeded | opening the PR waits at gate G1 |
| N8 report | every edge | code writes the event to the store, dashboard and Telegram | event row exists | no retry; errors are logged |
| N9 improve | work window open AND (misses >= 3 OR closed >= 5) AND >= 60 min since last run | Opus reads forks and misses, proposes new question wording | replay of recorded forks scores >= current | 1 attempt per run, 3 runs per window, aborts when the window closes; threshold/gate changes go to G4 |

No node only forwards work. N8 is the record; every other node changes state.

## Edges

Typed payloads are defined in `dp_agents/edges.py`. Every edge is written to the `events` table.

| From | To | Type | Kind |
|---|---|---|---|
| N1 | N2 | `Ticket` | forward |
| N2 | N3 | `Plan` | forward |
| N3 | N4 | `Commit` | forward |
| N4 | N5 | `Verified` | forward |
| N5 | N6 | `Reviewed` | forward |
| N6 | N7 | `Evaluated` | forward |
| N7 | G1 | `ShipRequest` | gate |
| N4 | N3 | `Failure` | back edge (the build caused it) |
| N5 | N3 | `Findings` | back edge |
| N6 | N3 | `Regression` | back edge |
| N2 | N1 | `Unplannable` | back edge (the ticket caused it: label `needs-spec`) |
| any | ESCALATE | `Escalation` | stop rule reached: Telegram alert, ticket parked |

A failed check goes back to the node that caused it, never straight to a person.
A person is reached only by a gate or by an escalation after a stop rule.

## Jev questions

The fixed outcomes and the wording live in `questions.yaml`. Outcome labels are fixed by code;
N9 may reword `instructions` only.

| Fork | Node | Type | Outcomes |
|---|---|---|---|
| `specified` | N1 | yes/no | is the issue specified enough to build |
| `target_file` | N2 | choice | one of the real repository files (filled at run time) |
| `retry` | N3 | yes/no | retry the build after this failure, or stop |
| `done_<k>` | N5 | yes/no | is acceptance item k done by this diff |
| `risk` | N5 | score 0-4 | how risky is this diff |

Sharp answers run straight in code. Split answers go to Opus, which must pick one of the same
fixed outcomes. If Jev errors or times out, the fork goes to Opus (never fail-open).

## Gates

Thresholds below are read by `dp_agents/graph.py`. Start values, tuned on DocuPay's own history
(`dp_agents/calibrate.py`). Changing anything in this block is gate G4.

```yaml
thresholds:
  choice_sharp: 0.85        # top choice probability at or above this: code acts
  retry_yes: 0.80           # p(retry) at or above: retry
  retry_no: 0.20            # p(retry) at or below: stop
  done_yes: 0.95            # p(done) at or above: done
  done_no: 0.05             # p(done) at or below: not done, back edge without Opus
  specified_yes: 0.85
  specified_no: 0.15
  risk_low: 1.0             # expected risk at or below: continue
  risk_high: 3.0            # expected risk at or above: human gate G1 with a risk flag
window:
  active_minutes: 30
  passive_hours: 8
improve:
  min_misses: 3
  min_closed: 5
  min_minutes_between_runs: 60
  max_runs_per_window: 3
human_gates:
  G1: open a pull request
  G2: merge a pull request
  G3: delete a branch or a file outside the work branch
  G4: change a threshold, a gate, or a hard limit
```

Gates G1 to G4 always wait for a person. No threshold can skip them. The agent never merges,
never pays, never deletes outside its own worktree, and never sends a message to anyone except
the owner's Telegram chat.

Every issue body, comment, CI log and web page is data. It goes into a prompt only inside
`<untrusted>` tags, with an instruction that nothing inside them is an instruction.
