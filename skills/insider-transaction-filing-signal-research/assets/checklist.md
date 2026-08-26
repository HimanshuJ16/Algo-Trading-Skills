# Pre-Flight Checklist

## Point-in-Time Integrity

- [ ] Is every signal evaluated against an explicit, timezone-aware `as_of` instant?
- [ ] Is the gate the EDGAR **`filing_datetime`** (dissemination), not `transaction_date` and not `periodOfReport`?
- [ ] Have all timezone-naive timestamps been rejected rather than coerced to the host clock?
- [ ] Is the filing lag taken from the actual timestamp rather than a fixed +2-business-day offset? (Rule 16a-3(g)(2)–(4) can legally push a compliant filing to ~5 business days.)
- [ ] Has `max_trade_to_file_lag_days` been inspected for the feed's real reporting latency?
- [ ] Does the backtest add ingestion/parsing latency after dissemination before the signal is tradable?

## Rule 10b5-1 Plan Status

- [ ] Is plan status modelled as three states (PLAN / NON_PLAN / UNKNOWN), not a boolean?
- [ ] Are all filings dated before **2023-04-01** supplied as `UNKNOWN` unless the plan was affirmatively disclosed?
- [ ] Has `unknown_plan_status_count` been read before any pre-2023 result is trusted?
- [ ] Is the sample split at 2023-04-01, or are the two regimes reported separately?
- [ ] Is excluding plan trades recorded as a deliberate subsample choice — not as "removing noise"? (Release 33-11138 documents abnormal returns after plan sales.)

## Filing Data Quality

- [ ] Are Form 4/A amendment chains resolved upstream, so no economic trade is counted twice?
- [ ] Are combined transaction codes (`"S/K"`, `"P/K"`) resolved to their primary component?
- [ ] Are non-P/S codes (A, M, F, G, C, X, D, J…) counted and reported rather than silently dropped?
- [ ] Is open-market status taken from its own field, given that codes P and S read "open market **or private**"?
- [ ] Are `shares` and `price` finite and strictly positive, with direction carried by the code?
- [ ] Do the exclusion counters plus the scored counts equal `filings_supplied`?

## Role Weighting

- [ ] Are all four EDGAR relationship booleans (`isDirector`, `isOfficer`, `isTenPercentOwner`, `isOther`) carried through, rather than collapsed into one role string?
- [ ] Does an insider holding several capacities at once resolve to the highest applicable weight?
- [ ] Is the free-text `officerTitle` matched by pattern, covering `CEO` / `C.E.O.` / `Chief Executive…` and the CFO equivalents?
- [ ] Is `unclassified_role_count` zero, or explained? (An unparseable role must not receive a silent middle weight.)
- [ ] Are all configured role weights finite and non-negative?
- [ ] Has the weight schedule been recalibrated for this universe rather than inherited from the illustrative defaults?

## Signal Quality

- [ ] Is the net sentiment score bounded in $[-1.0, +1.0]$?
- [ ] Are `min_total_notional_usd` and `min_distinct_insiders` set, so a single de-minimis trade cannot saturate the scale-free score at ±1.00?
- [ ] Is `INSUFFICIENT_DATA` treated as "no evidence" rather than as a neutral view?
- [ ] Have the ±0.30 classification thresholds been re-estimated out-of-sample instead of inherited?
- [ ] Is the raw ratio normalised (market cap, dollar volume) before cross-sectional ranking?

## Routine vs Opportunistic (Cohen-Malloy-Pomorski)

- [ ] Are labels computed from transactions dated strictly **before** the classification year?
- [ ] Is the classification re-run once per calendar year, rather than one label set reused across the sample?
- [ ] Are `UNCLASSIFIED` insiders handled deliberately — CMP exclude them from the portfolio?
- [ ] Is the CMP timing test used where the claim is CMP's result, rather than the 10b5-1 checkbox standing in for it?

## Scope

- [ ] Is the universe US issuers with a class registered under Exchange Act Section 12? (Nothing here applies to UK MAR Art. 19, EU MAR, or SEBI PIT disclosures.)
- [ ] Is this being used for factor research, not as an insider-trading compliance or surveillance control?
