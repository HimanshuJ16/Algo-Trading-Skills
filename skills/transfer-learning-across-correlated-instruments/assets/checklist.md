# Transfer Learning Sign-Off Checklist

Work through this before a transferred model takes capital. Thresholds named below are
this skill's operational defaults, not standards — record the values **your** desk
calibrated and why. See `references/standards.md` §0.

Target instrument: ______________  Source instrument: ______________
Evaluated by: ______________  Date: ______________

## 1. Data and alignment

- [ ] Source and target features use **identical definitions, lag structures and
      sampling frequency**. Differences listed here, or none: ______________
- [ ] Both datasets carry **strictly increasing integer timestamps in the same unit**,
      from the same trading calendar.
- [ ] `correlation_overlap` matches the number of shared bars you expect. A near-empty
      overlap is a clock or calendar mismatch, not a weak relationship.
- [ ] No non-finite values, no ragged rows, no duplicate bars. (The engine raises on all
      three; confirm the raise was fixed at the source, not worked around.)
- [ ] No constant or near-constant feature columns.

## 2. Leakage controls

- [ ] The target was split **chronologically**, never shuffled. Split timestamp:
      ______________
- [ ] The source was truncated to bars strictly before that timestamp. Rows dropped:
      ______________ (from `audit_trail`)
- [ ] `lambda` was tuned on a validation slice carved from the **end of the fit window**,
      not on the held-out window.
- [ ] No feature is constructed from information unavailable at its own bar. (See
      `feature-engineering-without-leakage`.)

## 3. Negative-transfer screening

- [ ] Aligned correlation: ______ against floor ______.
- [ ] Fisher-z 95% lower bound (`correlation_ci95_low`): ______. If it sits far below the
      floor, the gate was decided by too few bars — raise `min_correlation_overlap`
      rather than lowering the floor.
- [ ] Mean SMD: ______ against ceiling ______.
- [ ] `max_feature_domain_shift` is **set**, not left at `None`. Worst feature:
      ______________ at ______.
- [ ] The source instrument was chosen for a shared economic driver you can name, not
      because it maximised the correlation over a search. Driver: ______________

## 4. Out-of-sample evidence

- [ ] `transfer_model_r2` (vs the fit-window historical mean): ______ — must be **> 0**.
- [ ] `direct_target_r2`: ______ , or **not identified**. If `None`, this is recorded as
      "no baseline existed", not as a baseline of zero.
- [ ] `transfer_gain_r2`: ______ — positive where a baseline exists.
- [ ] Held-out window size: ______ bars. Enough that the R-squared is not one or two
      observations.
- [ ] The gain is large enough to survive transaction costs at the intended size. (See
      `backtesting-ml-models-against-transaction-costs`.)
- [ ] `rejection_reasons` is empty. If not, every listed reason was resolved on its merits
      — not by loosening the threshold that flagged it.

## 5. Reproducibility and archival

- [ ] Re-running the evaluation on the same inputs reproduces every figure exactly.
- [ ] `audit_trail`, the full `TransferConfig`, both dataset fingerprints, and the fitted
      `ModelParameters` (weights, bias, **and the source scaler**) are archived together.
- [ ] The fine-tuned model is recorded as carrying the **source** scaler. Any downstream
      code that re-standardizes with target statistics is a defect.
- [ ] A re-evaluation cadence is scheduled — the correlation that justified this transfer
      is a measured quantity that decays.
- [ ] A rollback path to the target-only model (or to no model) is defined and tested.

## 6. Stated limitations acknowledged

- [ ] The estimator is **linear**; non-linear structure is not captured.
- [ ] The shift metric is a **standardized mean difference**, blind to changes in
      dispersion or distribution shape — it is not a Wasserstein distance.
- [ ] Nothing in the pipeline establishes that `P(Y|X)` is shared between the two
      instruments. That remains an assumption, challenged only by the held-out result.
- [ ] Shrinkage toward the source does **not** decay as target history accumulates;
      `lambda` must be re-calibrated as the target matures.

Sign-off: ______________________  Date: ______________
