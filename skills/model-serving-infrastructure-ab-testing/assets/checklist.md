# Pre-Flight Checklist — Champion-Challenger A/B Test

## Before data collection

- [ ] `test_mode` set explicitly to `TestMode.LIVE_SPLIT` or `TestMode.SHADOW`? (`'shadow'` is rejected — the comparison is case-sensitive on purpose.)
- [ ] `traffic_split_ratio` set as the fraction routed to the **champion**, in $[0, 1]$?
- [ ] `min_sample_size` and `significance_level_alpha` **pre-registered and recorded** before the first sample? (Changing either after seeing an interim result invalidates the $p$-value.)
- [ ] `request_key` is a stable allocation unit (`symbol`, `account_id`) and not a per-order UUID?
- [ ] Each experiment has its own `experiment_id`, so concurrent experiments allocate independently?

## Before evaluating

- [ ] Both arms reached the pre-registered horizon, without stopping early on a favourable interim read?
- [ ] Every `ModelExecutionResult` carries the `model_id` that actually produced it, and the two lists are passed champion-first?
- [ ] Returns are realised per-trade returns in basis points — not shadow-mode counterfactuals compared against live fills?
- [ ] Observations are independent (no overlapping holding periods, no one signal fanned across correlated symbols)?

## Reading the report

- [ ] `status` is `AB_TEST_COMPLETED`, not `AB_TEST_INVALID_DATA` or `AB_TEST_INSUFFICIENT_SAMPLES`?
- [ ] $p$-value came from the $t$ distribution with the reported `degrees_of_freedom` (never the normal approximation)?
- [ ] Statistics reported as `None` are understood as *not computed*, not as zero?
- [ ] The mean difference is economically material after transaction costs and borrow, not merely statistically significant?

## Before promoting

- [ ] `recommended_action` treated as **advisory** — no automated pipeline promotes on it unsupervised?
- [ ] Promotion authorised and recorded by a named person under your change-control process, with a timestamp?
- [ ] Rollback path to the incumbent champion tested and ready?
- [ ] Retest / conformance obligations for a material change to a live algorithm confirmed for **your** jurisdiction? (See `references/standards.md` — the EU/RTS 6 material there does not apply to every entity.)
