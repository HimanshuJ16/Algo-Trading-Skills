# AGENTS.md — Global AI Agent Guidance & Skill Protocol

This repository contains structured algorithmic trading skills following the
[agentskills.io](https://agentskills.io) open specification, located in `skills/`.

## 🤖 Instructions for AI Coding Agents

When tasked with generating, reviewing, testing, or refactoring algorithmic trading, market data, backtesting, financial ML, or broker integration code:

1. **Skill Discovery**:
   - Query `index.json` (one object with a `skills` array and a `subdomains` count map) or
     inspect frontmatter across `skills/*/SKILL.md`.
   - Every skill's `description` starts with "Use when …". Match on the situation you are in
     (order placement after a timeout, a backtest that reads a bar's own close), not on the
     general subject area.

2. **Standard Workflow Execution**:
   - Read `skills/<skill-name>/SKILL.md`.
   - Check `## When NOT to Use` **first** — it names the cases the skill does not cover and
     hands each to the skill that does. Applying a correct playbook to the wrong problem is
     its own failure mode.
   - Follow the `## Workflow` section in order.
   - Cross-check `## Common Pitfalls` to prevent duplicate fills, lookahead bias, unhandled
     network timeouts, or capital over-exposure.

3. **Reference Implementation & Regulatory Specs**:
   - Consult `skills/<skill-name>/references/standards.md` for regulatory touchpoints
     (SEC Rule 15c3-5, Reg NMS, EU MiFID II / RTS 6, UK FCA, SEBI, ISDA) with their sources.
   - Refer to helper implementations in `skills/<skill-name>/scripts/` for reusable Python
     patterns. Each helper is standalone — it imports nothing from other skills.

4. **Validation & Verification**:
   - Validate against `skills/<skill-name>/assets/checklist.md`.
   - Run unit tests: `python -m unittest discover -s skills/<skill-name>/scripts`.
   - Verify repo structural integrity: `python tools/validate_skills.py`.

## ⚙️ Repository Tools & Commands
- `python tools/validate_skills.py` — frontmatter, required sections, cross-references,
  scripts layout and plugin packaging for every skill.
- `skills-ref validate skills/<skill-name>` — the agentskills.io reference validator.
- `python tools/build_index.py` — rebuilds `index.json` (`--check` verifies it is current).
- `python tools/build_marketplace.py` — rebuilds the per-domain plugin marketplace.
- `python tools/run_all_tests.py` — every skill's unittest suite, each in its own subprocess.
  Add `--skill <name>` to run one while iterating.
- `python -m unittest discover -s tests` — core repository tests.

## 🔒 Non-negotiables when writing trading code
- Order placement, modification and cancellation are idempotent via a client-supplied order ID
  plus a local ledger. A broker timeout means **unknown**, never **failed**; so does a
  transport-level error such as a `NetworkException`, gateway timeout or 5xx. Only an explicit
  broker rejection means failed. Never retry an ambiguous order state in an unbounded loop.
- Risk limits (drawdown, position size, message rate) live in a module with veto power over
  the strategy engine, never inside the strategy function they constrain. Clearing a halt is
  an audited operation with an operator and a reason.
- Backtests must not use a bar's own close before that bar completes; feature engineering must
  be point-in-time correct.
