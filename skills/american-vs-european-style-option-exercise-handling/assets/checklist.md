# Checklist for American Option Exercise Handling

- [ ] **Exercise Style Verification:** Confirm the option is American-style. European options cannot be exercised early — skip this skill.
- [ ] **Input Validation:** Confirm `OptionState` rejects negative prices, NaN/inf values, and invalid option types.
- [ ] **Non-Dividend Call Block:** Ensure the engine blocks early exercise of non-dividend calls when `market_price >= intrinsic_value`.
- [ ] **Below-Parity Detection:** Confirm calls and puts trading below intrinsic value trigger early exercise.
- [ ] **Dividend Comparison:** Confirm dividend amounts are compared against the *Time Value* (not total Market Value), using strict greater-than (not >=).
- [ ] **Dividend Boundary:** Confirm dividend == time_value does NOT trigger exercise.
- [ ] **Intrinsic Value Calculations:** Confirm Call vs Put intrinsic logic is correct.
- [ ] **Config Immutability:** Confirm `OptionState` is a frozen dataclass.
- [ ] **Run Test Suite:** `python scripts/test_american_vs_european_style_option_exercise_handling.py` — 100% pass rate.

## Sign-off
- Head of Quantitative Derivatives: ___________________________
- Date: ___________________________
