# GitHub Copilot Instructions for Algo-Trading-Skills

This repository contains **504 structured algorithmic trading skills** following the `agentskills.io` standard located in the `skills/` directory.

When generating code, analyzing trading infrastructure, or assisting with quant strategies:

- **Skill Discovery**: Scan `index.json` or `skills/*/SKILL.md` frontmatter to locate relevant skill modules (e.g. `order-placement-idempotency`, `kill-switch-and-drawdown-circuit-breakers`, `lookahead-bias-elimination`).
- **Follow Workflows**: Strictly adhere to the step-by-step `Workflow` and `Common Pitfalls` defined in `skills/<skill-name>/SKILL.md`.
- **Reference Standards**: Consult `references/standards.md` for regulatory compliance (SEC Rule 15c3-5, Reg NMS, MiFID II, FCA, SEBI) and exchange specifications.
- **Verification**: Ensure generated code passes tests using `python -m unittest discover -s skills/<skill-name>/scripts`.
