# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

**Algo-Trading-Skills** is a content library, not an application. It holds 504 self-contained
algorithmic-trading skills under `skills/<skill-name>/`, each following the
[agentskills.io](https://agentskills.io) standard. There is no runtime, no service, and no
shared Python package — the only executable code is the three scripts in `tools/`, the root
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
python tools/validate_skills.py                          # structure + frontmatter + cross-ref + scripts-layout + test-command validation (must report 504 passed)
python tools/build_index.py                              # regenerate index.json from SKILL.md frontmatter
python tools/run_all_tests.py                            # every skills/*/scripts/test_*.py (~20,300 tests, 504 files, ~30-85s)
python -m unittest discover -s skills/<skill-name>/scripts  # one skill's tests
python -m unittest discover -s tests                      # root repo tests (shell out to the tools; also rewrite index.json)
```

CI (`.github/workflows/validate-skills.yml`) runs `validate_skills.py` then `run_all_tests.py`
on every push/PR to `main`. Both must pass.

Things to know when reading the output:
- `run_all_tests.py` streams the helpers' own log lines (`KILL SWITCH CALLBACK FAILED`,
  `Cold Storage Audit FAILED`, …). Those are expected test fixtures, not failures — only the
  per-file `[PASS]/[FAIL]` lines and the final `Summary`/`[SUCCESS]` matter.
- `index.json` is a **generated artifact** with a `generated_at` timestamp. Regenerate it with
  `build_index.py` after any frontmatter change rather than hand-editing it, and expect it to
  show as modified in `git status` after running the root tests.

## Skill discovery workflow (using the skills)

1. **Query `index.json`** (or grep `skills/*/SKILL.md` frontmatter) to find skills matching the
   task — e.g. `order-placement-idempotency`, `kill-switch-and-drawdown-circuit-breakers`,
   `lookahead-bias-elimination`.
2. **Read `skills/<skill-name>/SKILL.md`** and follow `## Workflow` in order; check
   `## Common Pitfalls` for the named failure modes it exists to prevent.
3. **Go deeper only when implementing**: `references/standards.md` (regulatory/exchange
   mandates and broker coverage table), `references/workflows.md` (full procedure detail),
   `scripts/` (working reference implementation).
4. **Verify** against `assets/checklist.md`.

Cross-cutting maps live in `mappings/broker-api-coverage.md` and
`mappings/regulatory-coverage.md`; the system architecture the skills were extracted from is
in `docs/architecture.md`; `examples/` shows several skills composed into one script.

## Skill anatomy (authoring the skills)

`tools/validate_skills.py` is the enforced contract — read it before changing skill structure.
Every `skills/<skill-name>/` must have:

```
SKILL.md                  YAML frontmatter + Markdown body
references/standards.md   broker/framework coverage + regulatory touchpoints
references/workflows.md   deep technical procedure
scripts/<helper>.py       working reference implementation
scripts/test_<helper>.py  unittest suite for it
assets/checklist.md       sign-off checklist derived from the Verification section
```

Validator rules that commonly bite:
- Frontmatter must carry all of `name, description, domain, subdomain, tags,
  brokers_frameworks, version, author, license`.
- `name` must equal the directory name exactly (kebab-case).
- `version` must match `^\d+\.\d+(\.\d+)?$`. Quote it (`version: "2.0.0"`) — an unquoted
  `1.10` is parsed by YAML as the float `1.1` and silently changes the version.
- The body must contain all seven `## When to Use`, `## When NOT to Use`,
  `## Prerequisites`, `## Workflow`, `## Common Pitfalls`, `## Verification`,
  `## Related Skills` headings. `## When NOT to Use` states the skill's scope boundaries
  and hands each excluded case to the skill that does own it; it was a 100%-adopted
  convention before it was enforced, and the casing is exact.
- Every backticked slug longer than 3 chars in `## Related Skills` must be a real skill
  directory — broken cross-references fail the build.
- `references/`, `scripts/`, `assets/` must each exist and be non-empty.
- `scripts/` must hold at least one helper module **and** at least one `test_*.py` suite. A
  helper whose own filename starts with `test_` fails validation: `run_all_tests.py` globs
  `test_*.py`, so such a helper is collected as if it were a suite.
- Every test command quoted in a skill's `SKILL.md`, `references/*.md` or `assets/*.md` must
  be the repo-root form `python -m unittest discover -s skills/<name>/scripts` (a trailing
  `-v` is fine), or `python tools/run_all_tests.py`. Relative forms such as
  `python scripts/test_x.py` fail outright from the repository root, which is where every
  other documented command in this repo runs. `SKILL.md` must quote its own command at least
  once, so the Verification section is always actionable.
- Line endings do not matter to the validator. Its regex is written against `---\n`, but it
  reads with `open(..., encoding="utf-8")` in text mode, so Python's universal-newline
  translation turns CRLF into LF before the match. This machine has `core.autocrlf=true` and
  ~130 `SKILL.md` files are CRLF in the working copy; all of them validate. Do preserve a
  file's existing line endings when editing it (read and write with `newline=""`), purely to
  keep diffs minimal.

Script conventions: each `scripts/` helper is a **standalone module** with no imports from
other skills or from a shared package. Its sibling test imports it by bare module name —
`tools/run_all_tests.py` inserts each script dir onto `sys.path` and purges modules between
files, so two skills may safely use the same module name. Prefer stdlib (`dataclasses`,
`typing`, `enum`, `decimal`, `logging`, `math`) and reach for numpy/pandas/scipy only when the
computation genuinely needs it — most helpers are dependency-free by design.

`docs/skill-anatomy.md` documents this in prose; `docs/ROADMAP_500.md` tracks scope for every
skill; `CONTRIBUTING.md` holds the quality bar (would following this skill have prevented a
real production bug; is the Verification section actually checkable).

## Domain mandates for generated trading code

- Enforce pre-trade risk controls (SEC Rule 15c3-5, EU MiFID II RTS 6).
- Order placement/modify/cancel must be idempotent via a client-supplied order ID plus a local
  ledger — a broker timeout means *unknown*, never *failed*. Never retry an ambiguous order
  state in an unbounded loop.
- Risk limits (drawdown, position size, message rate) live in a module with veto power over the
  strategy engine, never inside the strategy function they constrain.
- Backtests must not use a bar's own close before that bar completes, and feature engineering
  must be point-in-time correct.
- Regulatory and broker-behavior claims in skill content must be verifiable against
  authoritative sources; a missing claim is better than a wrong or fabricated one.

## Sibling agent-instruction files

`AGENTS.md`, `GEMINI.md`, `CONVENTIONS.md` (Aider), `.cursor/rules/algo-trading-skills.mdc`,
`.github/copilot-instructions.md` and `llms.txt` all restate the discovery workflow above for
other tools. If the protocol or the skill count changes, update them together.
