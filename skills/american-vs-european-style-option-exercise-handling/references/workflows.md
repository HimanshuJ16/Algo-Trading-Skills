# Workflows for Holder-Side Early Exercise Decisions

## Daily assessment of long American positions

1. **Market data**: ingest the option's NBBO and the underlying price. Take the **bid** for the exercise-vs-sell comparison — the holder's alternative to exercising is selling, and a sale realises the bid. Record the quote's timestamp; a stale or one-sided market changes what the comparison means (step 5).
2. **Exercise-style gate**: confirm the contract is American-style. European-style contracts have no early-exercise decision and drop out here. Do not infer the style from the settlement method — OEX is American-style and cash-settled, XEO on the same index is European-style.
3. **Corporate-action check**: before the close, screen the calendar for ex-dividend dates. Under US T+1 settlement (since 28 May 2024) the ex-date is the record date, so the decision session for dividend capture is the **session before the ex-date**. Set `is_ex_dividend_tomorrow=True` and `dividend_amount` on that session only.
4. **Evaluation**: for every ITM long American option, construct an `OptionState` and call `EarlyExerciseEvaluator.evaluate()`.
   - `should_exercise=True` → exercising realises more than selling. Submit the exercise notice before the carrying firm's cut-off for the session.
   - `should_exercise=False` → do not exercise. This is **not** the same as "do nothing": if the reason reports the quote at parity, close the position (sell or exercise, they are worth the same) rather than holding into the ex-date.
5. **Quote-quality gate**: a below-parity bid on an option with no pending dividend is unusual and is far more often a stale, crossed or one-sided market than a real arbitrage. Verify the quote is executable — and verify the size — before routing an irrevocable instruction against it.
6. **Model cross-check (calls, optional)**: where the quote cannot be trusted, call `dividend_capture_test(state, same_strike_put_price, risk_free_rate, years_to_expiry)` for the exact condition `D > p_ex + K(1 − e^{−rτ})`. Given a fair quote it is algebraically the same test as step 4; a disagreement is evidence about the *quote*, not a reason to exercise into a market you could have sold into.
7. **Feasibility check**: before submitting, confirm the account can actually carry the result — cash or margin for `strike × 100` per call contract, and a locate for the short stock position an exercised put creates when the shares are not held.
8. **Submission**: route the exercise notice to the clearing broker before **that firm's** cut-off. Once processed by OCC the exercise is final; before the firm's cut-off, whether an instruction can still be amended is a firm-level question.

## Deadlines — what is fixed by rule and what is not

- **Expiring options (US):** FINRA Rule 2360(b)(23)(A) gives holders until **5:30 p.m. ET** on expiration day to make a final exercise decision. Members may set earlier cut-offs but may not accept instructions after it. Under an announced modified close of trading, the deadline becomes 1 hour 30 minutes after that close. Note "ET", not "EST" — the rule follows the prevailing Eastern offset.
- **Early exercise on an ordinary session:** no rule-fixed deadline. The holder must notify the brokerage firm before *that firm's* cut-off for the session, and firms commonly set theirs well before the exchange deadline. This engine models no cut-off and takes no time input; the calendar belongs to the operator.
- **Expiration-day mechanics:** OCC's exercise-by-exception procedure exercises equity options in the money by $0.01 or more against the closing price unless the clearing member instructs otherwise. It is a default, not an automation — contrary instructions are always available.

## Edge cases

- **Below-parity quotes.** Exercising realises intrinsic; selling realises the bid. When the bid is below intrinsic the engine returns `True` regardless of dividend status — but see the quote-quality gate above. An illiquid option with a $0.00 bid trivially satisfies the test; that is correct (exercising realises intrinsic, selling realises nothing) and is exactly the case where the size and executability of the quote matter most.
- **Exact parity.** Selling and exercising are worth the same and the engine returns `False`, preferring the sale: it avoids taking delivery, avoids finding the cash for the strike, and leaves no stock position to manage. Holding, however, is worth strictly less than either — an ITM option quoted at parity has no time value left to protect. The reason string flags this case explicitly.
- **Dividend larger than the quoted time value.** Not a trigger. The bid is cum-dividend and already prices the coming drop, so `D > TV_cum` counts the dividend twice; the band `0 ≤ TV_cum < D` is precisely where exercising forfeits `TV_cum` per share against simply selling. See `references/standards.md`.
- **A dividend pending on a put.** It makes early exercise *less* attractive, not more — the exercising put holder gives up the stock and the dividend with it. `dividend_capture_test` rejects puts for this reason rather than returning a meaningless number.
- **OTM/ATM options.** Zero intrinsic value short-circuits to `False` before any other rule, including a $0.00 bid on a worthless OTM contract.
- **European-style options.** Out of scope entirely; there is no early-exercise decision to evaluate.
