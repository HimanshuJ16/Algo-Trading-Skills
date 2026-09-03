# Checklist for Firm Licensing Thresholds

## Input aggregation

- [ ] `off_exchange_volume_usd` counts every execution otherwise than on a national securities exchange of which the firm is a member, classified by execution venue.
- [ ] `exempt_off_exchange_volume_usd` includes **only** volume evidenced as Rule 15b9-1(c)(1) exchange-routed Rule 611 / Options OPP flow or (c)(2) stock-leg flow. Unevidenced volume is left at 0.0.
- [ ] For any (c)(2) claim, written policies and procedures exist, are enforced, and are preserved three years consistent with Rule 17a-4.
- [ ] `peak_orders_per_second` is the highest order count in a single **calendar clock second, per exchange** — not a rolling window. It feeds the SEBI TOPS check only; it is not an EU input.
- [ ] Article 19 averages are computed per instrument over its relevant trading hours (limb a) and summed across the venue (limb b), restricted to liquid instruments, excluding DEA-client and client-order messages.
- [ ] Unmeasured Article 19 averages are passed as `None`, never as `0.0`.
- [ ] `is_retail_api_algo_flow` is set only for a retail investor's API-routed algorithm.
- [ ] `has_customers` is reviewed by Compliance before each run; once `True`, prior clear history is re-examined.
- [ ] The aggregation layer rejects non-finite, negative and mistyped metrics before construction; dataclass validation is a backstop only.

## Configuration

- [ ] `LicensingThresholdEvaluator` runs on published defaults unless a stricter override is approved and recorded in the policy registry.
- [ ] No override loosens a threshold past the published figure (TOPS 10, Article 19 limbs 2.0 and 4.0, off-exchange floor 0.00 USD).
- [ ] Any `0` override is confirmed to be intentional — it is honoured, not treated as unset.

## Outcome handling

- [ ] `requires_registration = True` pages the CCO and General Counsel, and the offending activity is throttled, rerouted or stopped.
- [ ] `manual_review_required = True` is routed to counsel and is **never** filed as compliant — nor treated as a confirmed breach.
- [ ] Reports with `rule_id = None` (unrecognised jurisdiction) go to manual legal review.
- [ ] A multi-jurisdiction firm has one report per jurisdiction, retained independently.
- [ ] Each report is archived immutably with its input snapshot, `evaluated_at`, `schema_version`, `violations` and `manual_review_items`.
- [ ] Customer funds are not commingled with proprietary capital at desk level without explicit compliance sign-off.

## Cadence and drift

- [ ] The EU Article 19 self-assessment runs **at least monthly**, per ESMA guidance.
- [ ] Published thresholds are re-verified against source on a documented cadence, and reports are backfilled when one changes.

## Operational controls

- [ ] Unit test suite passes: `python -m unittest discover -s scripts` (58 tests).
- [ ] Repository validator passes: `python tools/validate_skills.py`.

## Sign-off

- Chief Compliance Officer (CCO): ___________________________
- Date: ___________________________
