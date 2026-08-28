# SGX Pre-Flight Checklist

Sign-off before an SGX-bound strategy trades live. Figures and citations:
`references/standards.md`.

## Routing and connectivity

- [ ] Is every order tagged with the engine it belongs to — **Titan-DT** for
      derivatives, **Reach-ST** for securities — rather than a single "SGX API"?
- [ ] Is the Titan interface you actually use (OUCH, FIX order entry, ITCH/GLIMPSE for
      data) named in the design, and has SGX conformance testing been completed for it?
- [ ] Is there a plan for the **Iris-ST** securities engine replacing Reach-ST in
      H2 2027 (new order types, new pre-trade risk controls)?
- [ ] Is the validator kept separate from transport, so a validation pass is never
      reported as an order that reached the exchange?

## Contract reference data

- [ ] Does every contract specification in use carry a `source` and a `verified_on`
      date, and is the reconciliation cadence written down and owned?
- [ ] Was the table reconciled against the **current** SGX specification, not an
      archived PDF? (The A50 tick has been **1 index point since 5 October 2020**; SGX's
      2018 PDF still shows 2.5.)
- [ ] Does the refresh process detect a specification change **under an unchanged
      product code**? (`NS` went from JPY 100 x index / 1-point tick to JPY 10 x index /
      2.5-point tick on 22 June 2026.)
- [ ] Are retired contracts absent rather than stale? (`TW`, MSCI Taiwan, was replaced
      by `TWN`, FTSE Taiwan, US$40 per index point, 0.25 point tick, on 20 July 2020.)
- [ ] Are resting orders purged and re-derived after any increment change?

## Price validation

- [ ] Is tick alignment computed in `Decimal` with **no tolerance**, never
      `price % tick` in binary float?
- [ ] Is the increment selected by **trade type** — outright, calendar spread, T@IC,
      NLT — and not by contract alone? (`NK`: 5 / 1 / 0.25 index points.)
- [ ] Does an unpublished increment **raise** rather than fall back to the outright tick?
- [ ] Does an unknown product code **raise** rather than skip validation?
- [ ] Are both the limit price and the stop trigger price validated on a stop-limit?

## Singapore securities specifics

- [ ] Is the SGX-ST minimum bid size derived from the **order's price** on every order,
      not cached per symbol? (0.001 below S$0.20, 0.005 from S$0.20, 0.01 from S$1.00
      for ordinary shares; structured warrants stay at 0.005 to S$1.995.)
- [ ] Is the security class passed, and do ETFs/ETNs come from reference data rather
      than a guessed 0.01?
- [ ] Are foreign-currency counters kept off the SGD scale (home-market bid size
      alignment for HKD, RMB and JPY was removed on 15 July 2026)?
- [ ] Is quantity sized against the correct board lot, including the **5 October 2026**
      move to price-tiered board lots (10 units above S$10, 1 unit above S$100 for the
      instruments SGX specifies)?

## Gates beyond tick legality

- [ ] Are the SGX-ST Forced Order Range, the circuit breaker band and Clearing Member
      pre-execution value limits enforced elsewhere in the path?
- [ ] Are contract daily price limits checked (A50: ±10% and ±15% with a cooling-off
      period)?
- [ ] Is order submission idempotent, with an ambiguous-timeout reconciliation path?
- [ ] Are multi-currency exposures (`CN` USD, `NK` JPY, `FEF` USD, equities SGD)
      converted before aggregation against any notional limit?
