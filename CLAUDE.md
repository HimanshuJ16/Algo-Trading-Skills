# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

**Algo-Trading-Skills** is a content library, not an application. It holds self-contained
algorithmic-trading skills under `skills/<skill-name>/`, each following the
[agentskills.io](https://agentskills.io) standard. There is no runtime, no service, and no
shared Python package — the only executable code is the scripts in `tools/`, the root
`tests/`, the per-skill helper + test modules in `skills/*/scripts/`, and the three runnable
cookbook scripts in `examples/`.

Two distinct kinds of work happen here, and they have different rules:

1. **Using the skills** — writing/auditing/refactoring trading code (in this repo or elsewhere)
   by discovering and following the relevant skill playbooks.
2. **Authoring the skills** — adding or improving a skill directory so it passes the
   machine-enforced structural contract and the repo's quality bar.

## Commands

```bash
pip install -r requirements-dev.txt                      # pyyaml, numpy, pandas, scipy, pyotp, pytest
python tools/validate_skills.py                          # frontmatter + sections + cross-refs + scripts layout + packaging
agentskills validate skills/<skill-name>                  # agentskills.io reference validator (pip install skills-ref; needs 3.11+)
python tools/build_index.py                              # regenerate index.json (--check verifies it is current)
python tools/build_marketplace.py                        # regenerate the per-domain plugin marketplace (--check too)
python tools/run_all_tests.py                            # every skill suite, isolated per subprocess
python tools/run_all_tests.py --skill <skill-name>       # just one skill, while iterating
python -m unittest discover -s skills/<skill-name>/scripts  # the command every SKILL.md quotes
python -m unittest discover -s tests                      # root repo tests
python -m pytest                                          # same root tests (pytest.ini pins testpaths)
```

CI (`.github/workflows/validate-skills.yml`) runs all of the above on Python 3.10, 3.12 and
3.13 for every push and PR to `main`, plus the three `examples/` scripts. Everything must pass.

Things to know when reading the output:
- `run_all_tests.py` runs each skill's suite in its own subprocess and prints one
  `[PASS]`/`[FAIL]` line per skill. Helper log noise (`KILL SWITCH CALLBACK FAILED`,
  `Cold Storage Audit FAILED`, …) is captured and replayed **only** for a failing suite,
  so a green run is quiet.
- `index.json` and `.claude-plugin/marketplace.json` are **generated artifacts**. Regenerate
  them with `build_index.py` / `build_marketplace.py` after any frontmatter change rather
  than hand-editing. Neither carries a timestamp, so regenerating unchanged sources produces
  no diff, and CI fails if either is stale.

## Skill discovery workflow (using the skills)

1. **Query `index.json`** (or grep `skills/*/SKILL.md` frontmatter) to find skills matching the
   task — e.g. `order-placement-idempotency`, `kill-switch-and-drawdown-circuit-breakers`,
   `lookahead-bias-elimination`. Every description starts with "Use when …", so match on the
   situation, not the subject.
2. **Read `skills/<skill-name>/SKILL.md`** and check `## When NOT to Use` first — it names the
   cases the skill does not cover and hands each to the skill that does. Then follow
   `## Workflow` in order and check `## Common Pitfalls` for the named failure modes.
3. **Go deeper only when implementing**: `references/standards.md` (regulatory/exchange
   mandates and broker coverage table), `references/workflows.md` (full procedure detail),
   `scripts/` (working reference implementation).
4. **Verify** against `assets/checklist.md` and the command in `## Verification`.

Cross-cutting maps live in `mappings/broker-api-coverage.md` and
`mappings/regulatory-coverage.md`; the system architecture the skills were extracted from is
in `docs/architecture.md`; `examples/` shows several skills composed into one script by
importing their real helper modules.

## Skill anatomy (authoring the skills)

`tools/validate_skills.py` is the enforced contract — read it before changing skill structure.
`docs/skill-anatomy.md` documents the same contract in prose. Every `skills/<skill-name>/`
must have:

```
SKILL.md                  agentskills.io frontmatter + Markdown body
references/standards.md   broker/framework coverage + regulatory touchpoints
references/workflows.md   deep technical procedure
scripts/<helper>.py       working reference implementation
scripts/test_<helper>.py  unittest suite for it
assets/checklist.md       sign-off checklist derived from the Verification section
```

### Frontmatter

The agentskills.io spec allows only `name`, `description`, `license`, `compatibility`,
`allowed-tools` and `metadata` at the top level, so every repo-specific field lives under
`metadata:` **as a string** (the spec defines `metadata` as a string-to-string map):

```yaml
---
name: order-placement-idempotency
description: >-
  Use whenever a bot places, modifies, or cancels live orders and must guarantee it
  never double-executes an order due to retries, timeouts, or reconnects
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, idempotency, client-order-id, order-ledger, retry-safety
  brokers_frameworks: Fyers API v3; Zerodha Kite Connect; Upstox API v2; IBKR API
  version: "2.0.0"
  author: algo-trading-skills-contributors
---
```

Validator rules that commonly bite:
- **No repo field at the top level.** `tags:`, `version:` and friends outside `metadata:`
  fail both this validator and the agentskills.io reference validator.
- **`metadata` values are strings, never lists.** `tags` is comma-separated;
  `brokers_frameworks` is semicolon-separated, because vendor names contain commas.
  Quote `version` (`version: "2.0.0"`) — an unquoted `1.10` is a YAML float and silently
  becomes `1.1`.
- **`description` starts with `Use ` and is at most 280 characters**, contains no marketing
  words ("institutional", "production-grade"). It is the only thing an agent sees before
  opening the skill, and every installed skill's description costs context every session.
- **`name` matches the directory**, is kebab-case, at most 64 characters.
- **`metadata.subdomain` is one of the 16 domains** listed in `docs/ROADMAP_500.md`;
  `metadata.domain` is always `algorithmic-trading`; `license` is always `Apache-2.0`;
  `metadata.author` is always `algo-trading-skills-contributors`.
- The body must contain all seven `## When to Use`, `## When NOT to Use`,
  `## Prerequisites`, `## Workflow`, `## Common Pitfalls`, `## Verification`,
  `## Related Skills` headings, as exact level-2 headings (a `###` fails).
- Every backticked slug in `## Related Skills` must be a real skill directory — and so must
  every slug quoted in README, `mappings/`, `docs/`, `examples/` and `llms*.txt`.
- `references/standards.md`, `references/workflows.md` and `assets/checklist.md` must exist
  by name; `scripts/` must hold at least one helper module **and** at least one `test_*.py`
  suite. A helper whose own filename starts with `test_` fails validation, because
  `run_all_tests.py` would collect it as a suite.
- Every test command quoted in a skill's Markdown must be the repo-root form
  `python -m unittest discover -s skills/<name>/scripts` (a trailing `-v` is fine), or
  `python tools/run_all_tests.py`. `SKILL.md` must quote its own command at least once,
  and no `cd skills/...` line may precede it (the repo-root path breaks after the cd).
- No skill document may claim a Python floor below 3.10 ("Python 3.9+" fails).
- Line endings do not matter to the validator (it reads in text mode), but `.gitattributes`
  normalises the repository to LF. Preserve a file's existing line endings when editing it
  (read and write with `newline=""`) to keep diffs minimal.

Script conventions: each `scripts/` helper is a **standalone module** with no imports from
other skills or from a shared package. Its sibling test imports it by bare module name;
`run_all_tests.py` runs each suite in its own subprocess, so two skills may safely use the
same module name (two of them ship a `circuit_breaker.py`). This is also why a bare
`pytest` over `skills/` breaks and `pytest.ini` restricts `testpaths` to `tests/`.
Prefer stdlib (`dataclasses`, `typing`, `enum`, `decimal`, `logging`, `math`) and reach for
numpy/pandas/scipy only when the computation genuinely needs it — most helpers are
dependency-free by design. The floor is **Python 3.10** (some helpers use
`dataclass(slots=True)`; several use `zoneinfo`).

`docs/ROADMAP_500.md` lists every skill by domain; `CONTRIBUTING.md` holds the quality bar
(would following this skill have prevented a real production bug; is the Verification
section actually checkable).

## Domain mandates for generated trading code

- Enforce pre-trade risk controls (SEC Rule 15c3-5, EU MiFID II RTS 6).
- Order placement/modify/cancel must be idempotent via a client-supplied order ID plus a local
  ledger — a broker timeout means *unknown*, never *failed*. A transport-level error
  (`NetworkException`, a gateway timeout, a 5xx) is also *unknown*; only an explicit broker
  rejection is *failed*. Never retry an ambiguous order state in an unbounded loop.
- Risk limits (drawdown, position size, message rate) live in a module with veto power over the
  strategy engine, never inside the strategy function they constrain. Clearing a halt is an
  audited operation with an operator and a reason, never a bare attribute assignment.
- Backtests must not use a bar's own close before that bar completes, and feature engineering
  must be point-in-time correct.
- Regulatory and broker-behavior claims in skill content must be verifiable against
  authoritative sources; a missing claim is better than a wrong or fabricated one. Do not
  narrate the content's own revision history in published files — state the current fact.

## Sibling agent-instruction files

`AGENTS.md`, `GEMINI.md`, `CONVENTIONS.md` (Aider), `.cursor/rules/algo-trading-skills.mdc`,
`.continue/rules/algo-trading-skills.md`, `.clinerules`, `.windsurfrules`,
`.github/copilot-instructions.md` and `llms.txt` all restate the discovery workflow above for
other tools. If the protocol or the skill count changes, update them together.
