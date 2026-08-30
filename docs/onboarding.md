---
sidebar_position: 2
title: Onboarding
---

# Onboarding

Ten minutes from clone to a dry-run cycle. Nothing to install but Docker.

## 1. Access

- Ask for collaborator access to `bill-mccormick-dg/alpaca-hackathon` (private
  until submission).
- Create **your own** Alpaca paper trading account at
  [alpaca.markets](https://alpaca.markets) and generate API keys. Never use or
  ask for the official account's keys; they live only on CT 108, and
  `secrets.yaml` rejects an `official` entry outright.
- Featherless API key: use the shared team key (ask), or your own.
- Maintainer only: the `test` account's real keys come from the private
  `homenetwork` repo's ansible vault (`ansible-vault view vault.yml`, vars
  `vault_alpaca_hackathon_test_*`). Not the bare `vault_alpaca_*` vars and not
  `pass alpaca/Key` — those are CT 107's alpaca-trader, a different project.

## 2. Run it

```bash
git clone git@github.com:bill-mccormick-dg/alpaca-hackathon.git
cd alpaca-hackathon
cp secrets.example.yaml secrets.yaml   # your TEST paper keys + Featherless key
docker compose build
docker compose run --rm bot -m unittest discover -s tests     # ~300 tests, no keys needed
docker compose run --rm bot                                   # = run_cycle.py --dry-run --force
docker compose run --rm bot run_cycle.py --dry-run --force --verbose
docker compose run --rm bot status.py
docker compose up docs                                        # this site at http://localhost:3000
```

`--dry-run` prints what would be ordered and sends nothing. `--force` skips
the market-open / trading-window / entry-cutoff gates so you can rehearse on a
weekend. Drop `--dry-run` to place real paper orders **on the account in your
`secrets.yaml`**.

Everything writes to `./logs` (journal, halt files, overrides), which is
bind-mounted and git-ignored. `config.yaml` is bind-mounted read-only, so config
edits need no rebuild.

Want the challenger config? `docker compose run --rm bot run_cycle.py --config config-test.yaml --dry-run --force --verbose`
- that's the variant with research tools, the Kalshi prior and the learning
loop switched on.

## 3. How we ship

Small branches, one idea each; every change goes through the same pipeline
the whole project was built with (58 PRs so far):

```
git checkout -b feat/short-name        # off main
# ... change, add/adjust tests ...
python -m unittest discover -s tests   # or the docker compose form above
ruff check .
git push -u origin feat/short-name
gh pr create                           # CI runs lint + tests on the PR
gh pr merge --squash --delete-branch   # only when green
```

Merging to `main` deploys to CT 108 automatically (self-hosted runner,
rsync into `/opt/alpaca-hackathon`, `logs/` and `.venv/` untouched). Never
commit to `main` directly; never commit `secrets.yaml`, `.env`, `logs/`, or
credentials.

Conventions: `ruff` is the linter (config in `pyproject.toml`); tests are
`unittest`, credential-free, and live in `tests/test_<module>.py`; anything
that talks to Alpaca or Featherless is wrapped so a fake can stand in (see
`FakeMCPClient` in `tests/test_snapshot.py`, `ScriptedClient` in
`tests/test_decide.py`).

## 4. Where to look first

| You want to... | Read / run |
|---|---|
| understand one cycle end to end | [Architecture](architecture), then `run_cycle.py` top to bottom (~250 lines) |
| change what the model is told | `strategy_notes` in `config.yaml`; `bot/decide.py::PROMPT_TEMPLATE` |
| change a guardrail | `bot/risk.py::check_order` and its tests - the tests are the spec |
| see what happened today | `status.py`, `logs/journal.jsonl`, `eod_review.py --no-model` |
| try a different model | `override.py set model <id>` (expires 16:00 ET) or `config-test.yaml` |
| add a research tool | `bot/research.py::TOOLS` + `to_mcp_call` - allowlist, read-only only |

## 5. Things that will bite you

- Alpaca's free options feed has **no Greeks**; ours are derived per contract
  (`bot/greeks.py`) and are not internally consistent. The prompt says so.
- Newer Featherless models are **thinking models**: without
  `model_params: {chat_template_kwargs: {enable_thinking: false}}` they return
  empty content. `config-test.yaml` has it set.
- The network CT 108 sits on has intermittently flaky DNS. MCP calls retry
  transient connect errors; if a manual script dies on "name resolution", just
  rerun it.
- Never leave root-owned `__pycache__` inside `/opt/alpaca-hackathon` on CT 108
  (it breaks the deploy rsync). Use `PYTHONDONTWRITEBYTECODE=1` when running
  things there by hand; cron already does.
