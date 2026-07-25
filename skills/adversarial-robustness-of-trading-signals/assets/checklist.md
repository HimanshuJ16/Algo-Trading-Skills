# Checklist for Adversarial Robustness of Trading Signals

- [ ] Model `predict()` function wrapped correctly.
- [ ] `epsilon` configured relative to the true market microstructure noise level.
- [ ] `worst_case_sign` perturbation used for adversarial boundary testing.
- [ ] Tests passed: `python scripts/test_signal_adversarial_tester.py`
- [ ] Model rejected if flip rate exceeds 5.0%.

## Sign-off
- Quant Reviewer: ___________________________
- Date: ___________________________
