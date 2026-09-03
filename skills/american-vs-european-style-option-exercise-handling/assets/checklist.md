# Checklist for Holder-Side American Option Exercise Handling

## Contract and scope
- [ ] **Exercise style verified per contract:** confirmed American-style. European options cannot be exercised early — skip this skill.
- [ ] **Style not inferred from settlement:** OEX is American-style *and* cash-settled; XEO on the same index is European-style. Neither fact follows from the other.
- [ ] **Position side is long:** this is the holder's screen. Short positions go to `early-exercise-assignment-risk-management`.

## Inputs
- [ ] **Quote convention:** `market_price` is the executable **bid**, not the mid and not the last trade.
- [ ] **Quote freshness and size:** the bid is live and of usable size, not stale, crossed or one-sided.
- [ ] **Spot is cum-dividend** while the underlying has not yet gone ex.
- [ ] **Dividend session is correct:** `is_ex_dividend_tomorrow=True` only on the session *before* the ex-date (under T+1 the ex-date is the record date).
- [ ] **Input validation:** `OptionState` rejects negative, NaN, infinite, boolean and non-numeric prices, and rejects an unrecognised `option_type` rather than defaulting it.
- [ ] **Config immutability:** `OptionState` is a frozen dataclass.

## Decision logic
- [ ] **Single rule applied:** exercise iff `intrinsic_value > market_price`, for calls and puts alike.
- [ ] **Dividend is not a trigger:** confirm no code path exercises because `dividend_amount` exceeds the quoted time value. The cum-dividend bid already prices the dividend; that comparison counts it twice and forfeits `TV_cum` per share.
- [ ] **Non-dividend call block:** early exercise of an American call with no imminent dividend is never optimal (Merton 1973) whenever the bid is at or above intrinsic.
- [ ] **Below-parity detection:** calls and puts quoted below intrinsic return `should_exercise=True`.
- [ ] **Parity boundary:** exact parity does **not** exercise, and the reason warns that holding is worth less than either selling or exercising.
- [ ] **`False` is not "do nothing":** downstream routing reads the reason, not only the boolean.
- [ ] **Intrinsic value:** call vs put intrinsic logic confirmed correct; OTM/ATM short-circuits before every other rule.

## Model cross-check (optional)
- [ ] **Exact condition used:** `dividend_capture_test` applies `D > p_ex + K(1 − e^{−rτ})`, not the necessary-only textbook form `D > K(1 − e^{−rτ})`.
- [ ] **Misuse rejected:** the test raises `ValueError` for a put and for a state with no pending dividend.
- [ ] **Disagreement handled as data quality:** a model/quote disagreement triggers a quote investigation, never an exercise against a live sellable market.

## Operational
- [ ] **Feasibility confirmed:** cash or margin for `strike × 100` per call contract; a locate for the short stock an exercised put creates.
- [ ] **Cut-off known:** the carrying firm's early-exercise cut-off for the session is on file. For *expiring* options the rule-fixed deadline is 5:30 p.m. ET (FINRA Rule 2360(b)(23)(A)); for early exercise on an ordinary session there is no rule-fixed deadline.
- [ ] **Frictions considered:** commissions, exercise fees, the equity leg's spread and the tax consequence, none of which the engine models.

## Test suite
- [ ] **Run:** `python -m unittest discover -s skills/american-vs-european-style-option-exercise-handling/scripts` — 38 tests, 100% pass rate.
- [ ] **Regression pinned:** the v1.x case (intrinsic 10.00, bid 11.00, dividend 2.00) returns `False`.
- [ ] **Invariant pinned:** no input combination exercises while `market_price >= intrinsic_value`.
- [ ] **Independent oracle:** the engine's verdicts agree with a Black-Scholes continuation value computed outside the module.

## Sign-off
- Head of Quantitative Derivatives: ___________________________
- Date: ___________________________
