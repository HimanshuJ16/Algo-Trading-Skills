# Checklist for American Option Exercise Handling

- [ ] Ensure the engine correctly blocks the early exercise of non-dividend paying Call options.
- [ ] Confirm dividend amounts are correctly compared against the *Time Value* (not the total Market Value).
- [ ] Ensure Intrinsic Value calculations correctly isolate Call vs Put logic.
- [ ] Run test suite: `python scripts/test_american_vs_european_style_option_exercise_handling.py`.

## Sign-off
- Head of Quantitative Derivatives: ___________________________
- Date: ___________________________
