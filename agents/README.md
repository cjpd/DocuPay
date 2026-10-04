# DocuPay agent graph

A graph that takes a GitHub issue to a tested, reviewed branch and a draft PR that waits for you.

- **Opus** (Claude Code headless) plans, writes code and reviews diffs.
- **Jev** (TypeSafe System One) answers every fork with calibrated probabilities, in one call per node.
- **Code** owns the state, every threshold, every retry limit and every irreversible action.

Read [`graph.md`](graph.md) first: nodes, edges, back edges, Jev questions and gates.
Hard limits are in [`dp_agents/limits.py`](dp_agents/limits.py). No model can change them.

## How a ticket moves

1. You label an issue `agent-ready`. It needs a `- [ ]` checklist (the acceptance criteria) and 5 points or fewer (`points:3` label).
2. N1 → N7 run: plan, build in a worktree on `agent/<issue>`, verify, review, benchmark, push.
3. A failed check goes back to the node that caused it, never to you. A stop rule parks the ticket and alerts you.
4. Gate G1 asks you on Telegram (or the dashboard) to open a draft PR. You merge it yourself (G2).

Only issues **you** labeled are picked up. Issue text, comments and logs are data, never instructions.

## Run it on a VPS, 24/7

Tested layout: Ubuntu 24.04, 4 vCPU, 8 GB RAM (the frontend build needs it), 40 GB disk.

### 1. User and tools

```sh
sudo adduser --disabled-password dpagent
sudo apt-get update && sudo apt-get install -y git curl python3.11 python3.11-venv build-essential
curl -LsSf https://astral.sh/uv/install.sh | sudo -u dpagent sh
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash - && sudo apt-get install -y nodejs
sudo npm install -g @anthropic-ai/claude-code
sudo mkdir -p /srv/docupay && sudo chown dpagent: /srv/docupay
```

### 2. The repository and its environments

```sh
sudo -iu dpagent
git clone https://github.com/cjpd/DocuPay.git /srv/docupay && cd /srv/docupay
git config user.name "DocuPay agent" && git config user.email "agent@docupay.local"
~/.local/bin/uv venv backend/.venv --python 3.11 && ~/.local/bin/uv pip install --python backend/.venv/bin/python -r backend/requirements.txt
(cd frontend && npm ci)
~/.local/bin/uv venv agents/.venv --python 3.11 && ~/.local/bin/uv pip install --python agents/.venv/bin/python -r agents/requirements.txt
agents/.venv/bin/python -m pytest -q agents/tests    # 48 tests, no network
```

### 3. Opus: sign Claude Code in once

```sh
claude          # as dpagent; run /login and finish in the browser, then /exit
```

### 4. Telegram bot

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name. Copy the token.
2. Send any message to your new bot.
3. Get your chat id: `curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates"` and copy `message.chat.id`.

The bot reads only your chat. Commands: `/approve <gate>`, `/reject <gate>`. Any message counts as activity.

### 5. GitHub token

Make a **fine-grained** token for `cjpd/DocuPay` only, with Contents, Issues and Pull requests set to read/write.
The agent pushes only `agent/*` branches, never force-pushes, and opens PRs as drafts after G1.

### 6. The env file (the only place for keys)

```sh
sudo cp /srv/docupay/agents/deploy/docupay-agents.env.example /etc/docupay-agents.env
sudo chown dpagent: /etc/docupay-agents.env && sudo chmod 600 /etc/docupay-agents.env
sudo -u dpagent nano /etc/docupay-agents.env     # fill in the keys; DASHBOARD_TOKEN=$(openssl rand -hex 24)
```

Keys never go in code, in git, in the chat, or in logs.

### 7. Calibrate, then the final check

```sh
cd /srv/docupay && export DP_AGENTS_ENV=/etc/docupay-agents.env PYTHONPATH=agents
agents/.venv/bin/python -m dp_agents.cli calibrate     # scores DocuPay's own history; proposes thresholds at G4
agents/.venv/bin/python -m dp_agents.cli check         # must end with CLEAN
```

The service runs the same `check` before every start and refuses to run unattended until it is clean.

### 8. Start it with automatic restart

```sh
sudo cp agents/deploy/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now docupay-agents docupay-agents-dashboard
journalctl -u docupay-agents -f
```

`Restart=always` restarts it 30 s after a crash. If it fails 5 times in 10 minutes, systemd stops and Telegram tells you.

### 9. The dashboard

It listens on `127.0.0.1:8765` only. From your laptop:

```sh
ssh -L 8765:127.0.0.1:8765 dpagent@your-vps
# then open http://localhost:8765/login?token=<DASHBOARD_TOKEN>
```

Or put the VPS on Tailscale and set `DASHBOARD_HOST` to its Tailscale IP. Do not expose the dashboard to the internet.
An open, visible dashboard tab counts as "active" for the N9 work window.

## Alerts you get on Telegram

| Event | Message |
|---|---|
| Gate G1 or G4 opens | 🚦 with `/approve N` and `/reject N` |
| Ticket parked (stop rule, budget, error) | ⚠️ with the node and the reason |
| Unclear issue | the reason and the `needs-spec` label |
| Daemon tick error | ❗ |
| Service stopped | ⛔ from systemd |
| Daily report at `REPORT_HOUR` UTC | 📊 loops, forks, hours saved, Opus calls, Jev latency and cost |

## Commands

```sh
python -m dp_agents.cli run          # the daemon
python -m dp_agents.cli tick         # one pass
python -m dp_agents.cli dashboard
python -m dp_agents.cli report --send
python -m dp_agents.cli check
python -m dp_agents.cli calibrate --dry-run
python agents/scripts/demo.py agents/data/demo.sqlite3    # fills a demo DB with fakes, for the dashboard
```
