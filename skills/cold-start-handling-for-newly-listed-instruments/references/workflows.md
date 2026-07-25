# Deep Workflow Reference — cold-start-handling-for-newly-listed-instruments

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Check History Maturity**: Compare $N_{\text{bars}}$ vs minimum required history threshold $N_{\text{min}}$.
2. **Apply Cold-Start Substitution**: If $N_{\text{bars}} < N_{\text{min}}$, substitute NaN/missing features with sector peer proxy averages.
3. **Scale Position Sizing**: Apply `cold_start_size_scale` ($25\%$ max allocation) to limit unproven asset risk.
4. **Transition to Native Model**: Once $N_{\text{bars}} \ge N_{\text{min}}$, fully enable native ML model predictions.

## Production Implementation Reference

- Reference code: `scripts/cold_start_handler.py` (`ColdStartHandler`, `ColdStartEvaluation`).
- Automated unit tests: `scripts/test_cold_start_handler.py`.
