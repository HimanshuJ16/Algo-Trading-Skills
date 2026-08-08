# AGENTS.md — Global AI Agent Guidance & Skill Protocol

This repository contains **504 structured algorithmic trading skills** following the [agentskills.io](https://agentskills.io) open specification located in `skills/`.

## 🤖 Instructions for AI Coding Agents

When tasked with generating, reviewing, testing, or refactoring algorithmic trading, market data, backtesting, financial ML, or broker integration code:

1. **Skill Discovery**:
   - Query `index.json` or inspect frontmatter across `skills/*/SKILL.md`.
   - Match user requirements (e.g. order idempotency, kill switches, slippage modeling, wash trade detection) to target skill modules.

2. **Standard Workflow Execution**:
   - Read `skills/<skill-name>/SKILL.md`.
   - Strictly follow the `## Workflow` section to execute domain procedures in correct order.
   - Cross-check `## Common Pitfalls` to prevent duplicate fills, lookahead bias, unhandled network timeouts, or capital over-exposure.

3. **Reference Implementation & Regulatory Specs**:
   - Consult `skills/<skill-name>/references/standards.md` for regulatory touchpoints (SEC Rule 15c3-5, Reg NMS, EU MiFID II / RTS 6, UK FCA, SEBI, ISDA).
   - Refer to helper implementations in `skills/<skill-name>/scripts/` for reusable Python code patterns.

4. **Validation & Verification**:
   - Validate skill compliance with `skills/<skill-name>/assets/checklist.md`.
   - Run unit tests: `python -m unittest discover -s skills/<skill-name>/scripts`.
   - Verify repo structural integrity: `python tools/validate_skills.py`.

## ⚙️ Repository Tools & Commands
- `python tools/validate_skills.py` — Verifies YAML frontmatter and standard sections for all 504 skills.
- `python tools/build_index.py` — Rebuilds `index.json` search index.
- `python tools/run_all_tests.py` — Executes unit test suites across all skill helper modules.
- `python -m unittest discover -s tests` — Executes core repository behavioral tests.
