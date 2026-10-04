# DocuPay dev-loop graph

The graph that takes a GitHub issue to a reviewed, tested branch ready for a PR.
Two layers that never overlap:

| Layer | Who | Owns |
|---|---|---|
| Brain | Opus (Claude Code headless, `claude -p`) | plans, code, diff review, and the judgment calls inside those same calls |
| Decider | deterministic code (`dp_agents/`) | state, every rule, retry limit, budget and irreversible action |

The model advises, the code decides. Hard limits live in `dp_agents/limits.py`, not here,
so no model can change them.

## Nodes

Every node is one loop: trigger, one narrow action, a check that proves it is done, a stop rule.

| Node | Trigger | Action | Check | Stop rule |
|---|---|---|---|---|
| N1 intake | open issue labeled `agent-ready` by the owner | pick the next issue (code sorts by priority label, then number) | issue has a `- [ ]` acceptance checklist and points <= 5 | fail: label `needs-spec`, stop (0 retries) |
| N2 plan | Ticket from N1 | Opus writes a plan: files, steps, one test per acceptance item | every file exists or is marked new; every acceptance item maps to a test | 2 retries, then escalate |
| N3 build | Plan, or a back edge | Opus edits code in a git worktree on `agent/<issue>` | diff applies, stays in allowed paths, <= max diff lines | 3 build rounds per ticket across all back edges; the retry rule may stop sooner; then escalate |
| N4 verify | new commit on the work branch | code runs the fixed check list (`limits.VERIFY_COMMANDS`) | every command exits 0 | fail: back edge to N3 with the failing tail |
| N5 review | N4 green | Opus, fresh context, reviews the diff as the Critic and rates each acceptance item and the risk | no blocking finding and every acceptance item done | blocking or not done: back edge to N3 (2 review rounds), then escalate |
| N6 evaluate | N5 pass | code runs the offline benchmark and compares with the baseline | fraud false-approve stays 0; accuracy not lower than baseline | regression: back edge to N3 |
| N7 ship | N6 pass | code pushes the work branch, drafts the PR text | push succeeded | opening the PR waits at gate G1 |
| N8 report | every edge | code writes the event to the store, dashboard and Telegram | event row exists | no retry; errors are logged |

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
| N6 | N3 | `Failure` | back edge (benchmark regression) |
| N2 | N2 | `Unplannable` | retry (the plan failed its check) |
| any | ESCALATE | `Escalation` | stop rule reached: Telegram alert, ticket parked |

A failed check goes back to the node that caused it, never straight to a person.
A person is reached only by a gate or by an escalation after a stop rule.

## Decisions

Every decision is logged in the `decisions` table and shown on the dashboard.
No decision costs an extra model call.

| Decision | Node | Decided by | Rule |
|---|---|---|---|
| `specified` | N1 | code | has an acceptance checklist and points <= 5 |
| `start_file` | N2 | code | the first file of the plan's first step |
| `retry` | N3 | code | stop on an environment failure (connection refused, permission, timeout, disk full) or the same failure twice in a row; otherwise retry |
| `done:<k>` | N5 | Opus, in the review call | acceptance item k is implemented and tested |
| `risk` | N5 | Opus, in the review call | 0 (docs) to 4 (could leak tenant data or approve a payment wrongly) |

## Gates

Read by `dp_agents/graph.py`. Changing anything here is gate G4: a person edits it by hand.

```yaml
review:
  risk_flag_at: 3          # risk at or above this is flagged at G1
human_gates:
  G1: open a pull request
  G2: merge a pull request
  G3: delete a branch or a file outside the work branch
  G4: change a rule, a gate, or a hard limit
```

Gates G1 to G4 always wait for a person. No rule can skip them. The agent never merges,
never pays, never deletes outside its own worktree, and never sends a message to anyone except
the owner's Telegram chat.

Every issue body, comment, CI log and web page is data. It goes into a prompt only inside
`<untrusted>` tags, with an instruction that nothing inside them is an instruction.
