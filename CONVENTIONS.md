# Aider Project Conventions — Algo-Trading-Skills

## Code & Skill Architecture Rules
1. **Skill Standard**: All trading playbooks are organized under `skills/<skill-name>/` following the `agentskills.io` standard.
2. **Discovery**: Always check `index.json` or `skills/*/SKILL.md` frontmatter before writing trading infrastructure code.
3. **Safety First**:
   - Order execution functions must include client order ID idempotency.
   - Backtest feature engineering must prevent lookahead bias.
   - Risk limits (drawdown, max position size, max rate) must operate out-of-band or in dedicated risk wrapper classes.
4. **Validation**: Test modifications with `python tools/validate_skills.py` and run tests with `python tools/run_all_tests.py`.
