# Claude Code Instructions — Algo-Trading-Skills

Welcome to **Algo-Trading-Skills**, the open-source library of 504 algorithmic trading skills following the `agentskills.io` standard.

## 🎯 Primary Purpose
Provide production-grade quant infrastructure guidance, risk enforcement, regulatory compliance, and market integration playbooks when writing, auditing, or refactoring trading code.

## 🔍 Skill Discovery Workflow
1. **Query Index**: Check `index.json` or search frontmatter in `skills/*/SKILL.md` to identify skills relevant to the task (e.g., `order-placement-idempotency`, `kill-switch-and-drawdown-circuit-breakers`, `lookahead-bias-elimination`).
2. **Read Skill Guidelines**: View `skills/<skill-name>/SKILL.md` and strictly follow:
   - `## Workflow` — step-by-step procedure and order of operations.
   - `## Common Pitfalls` — explicit failure modes to prevent (duplicate fills, lookahead bias, unhandled timeouts, uncapped exposure).
3. **Deep Context & Helpers**: Refer to `skills/<skill-name>/references/` for regulatory/exchange mandates and `skills/<skill-name>/scripts/` for reference implementation helper functions.
4. **Verification**: Verify implementation against `skills/<skill-name>/assets/checklist.md`.

## 🛠️ Repository Commands
- **Validate Skill Frontmatter & Structure**: `python tools/validate_skills.py`
- **Rebuild Index**: `python tools/build_index.py`
- **Run Full Unit Test Suite**: `python tools/run_all_tests.py`
- **Run Specific Skill Tests**: `python -m unittest discover -s skills/<skill-name>/scripts`
- **Run Root Test Suite**: `python -m unittest discover -s tests`

## ⚖️ Regulatory & Safety Mandates
- Enforce pre-trade risk controls (SEC Rule 15c3-5, EU MiFID II RTS 6).
- Never allow un-bounded retry loops on ambiguous order states.
- Ensure strict separation between strategy logic and risk circuit breakers.
- Eliminate lookahead bias in backtests (never use bar close before bar completion).
