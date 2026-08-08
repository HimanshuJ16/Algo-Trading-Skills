---
name: Algo-Trading-Skills Rule
description: Rule for auto-discovering and executing algorithmic trading skills in Continue
globs: ["**/*.py", "**/*.json", "**/*.md", "skills/**"]
---

# Continue Rule for Algo-Trading-Skills

When developing algorithmic trading systems, backtest loops, broker integrations, or risk controls:

1. Search `index.json` or frontmatter in `skills/*/SKILL.md` for matching domain skills.
2. Follow the `Workflow`, `Common Pitfalls`, and `Verification` sections in the matching `SKILL.md`.
3. Consult `skills/<skill-name>/references/` for regulatory compliance standards (SEC 15c3-5, Reg NMS, MiFID II, FCA, SEBI).
4. Verify execution by running `python tools/run_all_tests.py`.
