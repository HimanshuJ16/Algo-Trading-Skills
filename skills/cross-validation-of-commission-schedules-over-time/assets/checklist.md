# Pre-Flight / Sign-off Checklist — cross-validation-of-commission-schedules-over-time

## Schedule sourcing

- [ ] Commission schedule sourced from the **executing broker's own** published historical pricing (not a generic table, not another broker's rates).
- [ ] Every rate change in the backtest window has its own tier with the correct effective date.
- [ ] Tier *structure* matches what the broker actually charged (flat ticket / per-share with floor and cap / percent-of-value) — no invented composite.
- [ ] Any tier boundary that could not be sourced is recorded in the tier's `source` field and flagged in the tearsheet.

## Coverage and safety

- [ ] Schedule spans the entire backtest window; no trade date falls in a gap.
- [ ] Construction rejected overlapping tiers (verified, not assumed).
- [ ] A trade dated outside the schedule **raises** rather than being priced at $0.00.
- [ ] An unparseable timestamp **raises** rather than falling through to the latest tier.
- [ ] Trade timestamps converted to the schedule's timezone before lookup.
- [ ] Share quantities passed as positive numbers with an explicit `side`.

## Fee computation

- [ ] Minimum per-order charge enforced (verified on a deliberately tiny order).
- [ ] Percent-of-value cap enforced where the broker publishes one (verified on a low-priced, high-share-count order).
- [ ] Zero-commission cutover verified at the exact boundary date (day before vs day of).

## Regulatory pass-through fees (US equities)

- [ ] SEC Section 31 and FINRA TAF schedules populated for the backtest window from regulator sources, **or** their absence explicitly disclosed.
- [ ] Regulatory fees applied to **sales only**.
- [ ] FINRA TAF per-trade maximum applied.
- [ ] `regulatory_fees_modeled` flag read and reported — an unmodelled cost is never presented as a zero cost.

## Audit

- [ ] Historical vs modern flat-fee drag audit generated and the P&L delta reported alongside backtest results.
- [ ] Automated Testing: run `python -m unittest discover -s skills/cross-validation-of-commission-schedules-over-time/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
