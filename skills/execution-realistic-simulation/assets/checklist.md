# Pre-Flight / Sign-off Checklist — execution-realistic-simulation

Use this before considering the skill's implementation complete.

- [ ] **Bid-Ask Spread Direction:** Confirm BUY fills occur at/beyond the ask and SELL fills at/beyond the bid — never at mid.
- [ ] **Side Validation:** Confirm an unrecognised side string raises rather than defaulting to a direction.
- [ ] **Square-Root Market Impact:** Confirm quadrupling order size exactly doubles modelled impact (a linear model quadruples it; a flat model leaves it unchanged).
- [ ] **Participation Sanity:** Confirm orders above ~10% of ADV are flagged as extrapolation rather than silently priced.
- [ ] **Rate Currency:** Confirm every statutory rate carries an effective date and that no venue's rate predates its most recent change. For NSE F&O, confirm the STT rates in use are the ones effective 1 April 2026 (0.15% options sell on premium, 0.05% futures sell), not the 2024 or pre-2024 rates.
- [ ] **Complete Fee Stack Verification:** Confirm STT/CTT, exchange transaction charge, SEBI turnover fee, stamp duty and GST match the published rate card **component by component**, not just in total.
- [ ] **GST Base:** Confirm GST is applied to brokerage + exchange + SEBI charges only, and that stamp duty is charged on the buy side only.
- [ ] **No Fee Fall-Through:** Confirm every instrument/market type has its own explicit fee schedule and none inherits another market's rates.
- [ ] **Partial Fill Handling:** Confirm order sizes exceeding available depth produce partial fills, that impact and fees are charged on the executed quantity, and that a zero-quantity fill incurs no per-order brokerage.
- [ ] **Fail-Loud Inputs:** Confirm zero/unknown ADV, NaN volatility, non-positive prices, and a half-spread wider than the mid all raise rather than being clamped or substituted.
- [ ] **Latency Source Quote:** Confirm the mid/half-spread passed in comes from the post-latency moment, not from the signal bar.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/execution-realistic-simulation/scripts` and confirm a 100% pass rate.
- [ ] **Live Recalibration:** Confirm a plan exists to regress modelled impact against realized fills and refit $\gamma$ once paper/live fills are available.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Rate card verified against source on: ___________________________
- Reviewed by: ___________________________
