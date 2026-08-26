# LME Order Gateway — Pre-Flight Checklist

Levels below are as at 2026-08-25 and are revised by LME notice. Confirm against
`references/standards.md` before signing off.

## Reference data

- [ ] Metal code resolved against the catalog (`AH`, `AA`, `NA`, `CA`, `PB`,
      `NI`, `SN`, `ZS`) — not defaulted.
- [ ] Lot size taken **per metal**: 25 MT for `CA`/`AH`/`PB`/`ZS`, 20 MT for
      `AA`/`NA`, **6 MT for `NI`**, **5 MT for `SN`**.
- [ ] Outright tick taken **per metal**: $0.50/MT, except **$5.00/MT for `NI`
      and `SN`**.
- [ ] Tick sizes and Daily Price Limits carry a source and a retrieval date, and
      have been refreshed since the last LME notice.

## Order payload

- [ ] `lots` is a positive whole number; `0`, negatives and fractional lots are
      rejected, not clamped.
- [ ] `side` is `BUY` or `SELL`; anything else is rejected.
- [ ] Price is positive and finite (`NaN`/`Inf` rejected), and positivity is
      checked **separately** from tick alignment.
- [ ] Price and notional arithmetic uses `Decimal`, not `float`.
- [ ] Exposure is expressed in **tonnes** (`lots × lot_size_mt`), not lots.

## Prompt date

- [ ] Prompt is a rolling designator (`CASH`, `TOM`, `3M`) or an explicit date —
      no free-text passthrough.
- [ ] Explicit date is after the trade date.
- [ ] Explicit date is within the contract's furthest listed monthly prompt
      (`SN` 15, `AA`/`NA` 27, `PB`/`ZS`/`NI` 63, `CA`/`AH` 123 months).
- [ ] Non-Wednesday weekly / non-third-Wednesday monthly prompts are **flagged
      for calendar confirmation, not hard-rejected** — the LME publishes
      substitute prompt dates.
- [ ] Where correctness matters, an LME prompt-date calendar is supplied and
      `prompt_date_confirmed` is True.

## Daily Price Limit

- [ ] Reference price is the previous Business Day's **Closing Price for the
      3-month contract** — not the mid, top of book, or LME Official Price.
- [ ] Percentage is per metal: 12% for `AH`/`CA`/`PB`/`ZS`, 15% for
      `NI`/`SN`/`AA`/`NA`.
- [ ] Band is applied **symmetrically to both sides** — no ICE-style directional
      Reasonability Limit logic.
- [ ] A missing reference price fails closed (`NO_DPL_REFERENCE_PRICE`,
      `ready_to_send` False), never silently passes.

## Before going live

- [ ] Callers know `ready_to_send` means "passed the checks modelled here", not
      "LMEselect accepted the order".
- [ ] Rejection paths from the controls **not** modelled here are handled: Price
      Bands, Exchange-/Member-set maximum order size limits, and the order
      throttle.
- [ ] Behaviour on a DPL Multiple Day Framework suspension is defined.
- [ ] Timeouts do **not** trigger a blind resubmission — order state is resolved
      through the venue against the original client order ID.
- [ ] One validated order has been sent in conformance and accepted on both
      instrument identification and price.
