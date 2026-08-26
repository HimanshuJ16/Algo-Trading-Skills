# Pre-Flight Checklist — ICE Futures US & EU Integration

## Contract resolution
- [ ] Is the order keyed on the **ICE product contract code**, and does the
      resolved contract's name and currency match what you meant? (`T` is ICE
      WTI, not Dutch TTF — that is `TFN`.)
- [ ] Is the delivery month in the contract's **listed series**? (`DX` quarterly;
      `SB` March/May/July/October.)

## Identifiers
- [ ] Is FIX Tag 207 `SecurityExchange` set to the correct MIC (`IFEU` / `IFUS`)?
- [ ] Is Tag 200 `MaturityMonthYear` a well-formed `YYYYMM` from a **four-digit**
      year — and is it, not the `BZ26` display label, what identifies the
      contract month downstream?
- [ ] Is Tag 55 / Tag 48 populated from the **ICE FIX specification for your
      session**, rather than from a symbol formatter?
- [ ] Is Tag 54 `Side` the enumerated value (`1`/`2`)?
- [ ] If a reporting regime needs a **segment** MIC, are you using it rather than
      the operating MIC?

## Valuation
- [ ] Is the price in the contract's own quotation convention? (Sugar No. 11 in
      **cents** per pound, not dollars.)
- [ ] Is the notional reported **with its currency**? (TTF is EUR.)
- [ ] Does `tick_size × contract_size × currency_per_price_unit` reproduce ICE's
      published tick value (Brent $10, Sugar No. 11 $11.20, DX $5.00)?
- [ ] For TTF, is the lot size supplied **per delivery period** rather than
      hard-coded?

## Price controls
- [ ] Is order entry gated on the **Reasonability Limit** — not on the No
      Cancellation Range?
- [ ] Is the reference the **Exchange-set anchor price**, not the BBO or the mid?
- [ ] Is the check **directional** (buy above upper / sell below lower), so deep
      passive orders are not falsely rejected?
- [ ] Are RL and NCR carried in **price units** from the published tables, with
      `limits_source` and `limits_as_of` — and never as an invented tick count?
- [ ] Is any Exchange widening (2× volatile conditions, IFUS pre-open up to 3×)
      passed explicitly rather than baked into the stored level?
- [ ] Does a **missing anchor price fail closed** instead of falling back to the
      order book?

## Arithmetic and input
- [ ] Are prices and ticks compared with `Decimal`?
- [ ] Is price positivity checked **separately** from tick alignment?
- [ ] Are non-positive quantity, unknown side, and non-finite price rejected
      before any valuation happens?

## Interpretation
- [ ] Is the error-trade exposure reported **separately** from the accept/reject
      verdict, and understood as a bound assuming a fill at the limit price?
- [ ] Is a local pass treated as "checks passed", never as "ICE accepted it"?
- [ ] Are the unmodelled controls (IPL/TPL, stop and market protection limits,
      min/max order value, instrument state) covered elsewhere in your order path?
- [ ] On a request timeout, is the order's state resolved through the venue with
      the original client order ID rather than blindly resubmitted?
