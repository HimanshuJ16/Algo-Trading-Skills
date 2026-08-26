# Pre-Flight / Sign-off Checklist — leverage-limit-enforcement-across-instruments

Use this before considering the skill's implementation complete.

- [ ] **Exposure Basis:** Confirm exposures are signed underlying-equivalent notionals — netted per symbol, summed gross across symbols — and that options carry underlying notional with `exposure_delta`, never premium paid.
- [ ] **De-Risking Is Never Blocked:** Confirm a closing order nets against the existing position (a $\$100\text{k}$ sell against a $\$300\text{k}$ long projects $2.0\times$, not $4.0\times$), and that a reducing order on an already-over-cap book returns `APPROVED_RISK_REDUCING_WHILE_OVER_LIMIT`.
- [ ] **Reversals Are Not Remediation:** Confirm a same-size position reversal (every ratio unchanged) is vetoed, not approved as risk-reducing.
- [ ] **Unrounded Comparisons:** Confirm a projected $3.00499\times$ against a $3.0\times$ cap is rejected, and that an exactly $3.0\times$ projection is approved.
- [ ] **Input Validation:** Confirm `side="LONG"`, a negative or non-finite notional, a NaN or non-positive equity, a malformed position row, and a symbol declared under two asset classes each raise `ValueError`.
- [ ] **Fail-Closed Asset Classes:** Confirm an *opening* order in an asset class with no configured cap returns `REJECTED_UNKNOWN_ASSET_CLASS`, that a *reducing* order in that class is still approved, and that `default_asset_class_limit` is set explicitly if any fallback is intended.
- [ ] **Limits Are Calibrated, Not Inherited:** Confirm `max_net_leverage < max_gross_leverage` (otherwise the net gate can never bind), and that each `asset_class_limits` value was chosen for this mandate — the shipped `CRYPTO` default of $3.0\times$ exceeds the EU retail CFD limit of 2:1.
- [ ] **Margin Is Covered Separately:** Confirm a margin/liquidation control is in place alongside this one; passing a leverage cap says nothing about maintenance margin.
- [ ] **Concurrency:** Confirm check-then-place is serialized at the caller, and that the remainder of a partially filled parent is re-evaluated.
- [ ] **Data Freshness:** Confirm position notionals are marked from a current price source before the gate runs.
- [ ] **Audit Trail:** Confirm every decision — approvals included — is persisted with its projected ratios and applied limits.
- [ ] **Automated Testing:** Run `python scripts/test_leverage_limit_enforcer.py` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
