---
name: american-vs-european-style-option-exercise-handling
description: >-
  Use when a book holds long American-style options and today's question is exercise now
  or sell, comparing intrinsic value against the executable bid with the Merton
  ex-dividend condition as a cross-check.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: options, derivatives, early-exercise, american-options, ex-dividend, put-call-parity, quantitative-finance
  brokers_frameworks: generic
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a book holds **long American-style options** and something has to be decided about them today: exercise now, or sell? Early exercise destroys the option's remaining time value, so it is the wrong answer almost always — but "almost always" is not "always", and the exceptions (an ITM call on the last cum-dividend session, a deep ITM put whose extrinsic value has collapsed under carry, any option whose bid has fallen below parity) are the ones that cost real money when missed.

The rule the engine applies is one comparison:

> Exercising realises exactly the intrinsic value — stock worth `spot` against payment of `strike`, and nothing more. Selling realises the bid. **Exercise if and only if intrinsic value exceeds the bid.**

That comparison needs no interest rate, no volatility and no time to expiry, because all three are already priced into the quote the holder can sell into. The same argument from the other side: a holder who genuinely wants the shares can sell the call and buy the stock for `spot − bid`, which beats the `strike` paid on exercise exactly when `bid > intrinsic`.

**American vs European distinction**: European-style options can only be exercised at expiration — there is no early-exercise decision to make. This skill is strictly for American-style options.

## When NOT to Use

- **European-style options.** SPX, NDX and XEO cannot be exercised before expiration. Verify the exercise style before running anything here. Exercise style and settlement method are independent: OEX (S&P 100) is American-style *and* cash-settled, while XEO on the same index is European-style — see `physical-vs-cash-settlement-handling`.
- **Short positions.** This is the holder's screen. For the writer's assignment-risk view — which deliberately uses a *conservative, over-flagging* version of the ex-dividend test — use `early-exercise-assignment-risk-management`.
- **As an options pricing model.** The engine consumes a quote; it does not produce one. `dividend_capture_test` needs a put price and a rate supplied from outside.
- **As an exercise scheduler.** It knows nothing about your carrying firm's cut-off time, the holiday calendar or the session clock. It tells you *what* to do, never *by when*.
- **On expiration day itself.** Expiring ITM contracts are handled by OCC's exercise-by-exception procedure and by pin risk, a different problem — see `options-pin-risk-management-at-expiry`.
- **Exotic/structured options.** Binary, barrier and Asian options have non-standard exercise features that need bespoke models.

## Prerequisites

- Python 3.9+ (standard library only; no third-party dependencies).
- **The option's bid, not the mid and not the last trade.** The alternative to exercising is selling, and a sale realises the bid. At the true early-exercise boundary an American option's fair value sits exactly at parity, so a mid-based comparison systematically misses live exercise decisions, and a last-trade-based one invents them out of stale prints.
- Current underlying price, cum-dividend while the underlying has not yet gone ex.
- Declared dividend per share and a flag for the last cum-dividend session. Under T+1 settlement (US, since 28 May 2024) the ex-date *is* the record date, so an exercise submitted on the session **before** the ex-date settles onto the record-date books; an exercise on the ex-date itself is too late.
- Optional, for the exact ex-dividend cross-check: the same-strike same-expiry put price, a risk-free rate, and time to expiry in years.
- Your carrying firm's early-exercise cut-off time for the session. It is a firm-level parameter, not a rule-fixed one.

## Workflow

1. **State construction**: build an `OptionState` from the option type, spot, strike and **bid**, plus the dividend fields where one is pending. The frozen dataclass validates at construction — an unrecognised option type is rejected rather than defaulted, and prices must be real, finite and non-negative (`True` is rejected too, so a stray boolean cannot silently become `1.00`).
2. **Decide on the quote**: `EarlyExerciseEvaluator.evaluate(state)` returns `(should_exercise, reason)`. Zero intrinsic value short-circuits to `False`. Otherwise the verdict is `intrinsic > bid`.
3. **Read `False` correctly.** It means *do not exercise*; it does not mean *do nothing*. When the quote sits exactly at parity there is no time value left to protect, and holding through an ex-date surrenders the dividend in exchange for the smaller ex-dividend time value — the reason string says so explicitly in that case. Route on the reason, not on the boolean alone.
4. **Cross-check only if the quote is suspect**: if the market is stale, crossed, one-sided or absent, call `dividend_capture_test(state, same_strike_put_price, risk_free_rate, years_to_expiry)`. It applies the exact condition `D > p_ex + K(1 − e^{−rτ})` (Merton 1973). It rejects puts and states with no pending dividend rather than returning a meaningless verdict.
5. **Treat disagreement as a data-quality finding.** Given a fair, executable quote the two routes are algebraically the same test. If they disagree, the call quote is stale or crossed — investigate the quote. That is not a licence to exercise on the model against a live market you could have sold into.
6. **Check the exercise is operationally possible before submitting.** A call exercise needs the cash or margin to pay `strike × 100` per contract. A put exercise on stock you do not own creates a short position that needs a locate and carries borrow cost and recall risk. Neither is modelled here; both can make a marginal exercise the wrong trade.
7. **Act before the cut-off.** An exercise notice must reach the carrying firm before *that firm's* cut-off for the session. Firms set their own, and they are typically earlier than any exchange or clearing deadline.

> Full procedure: see `references/workflows.md`.
> Standards and sources: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Comparing the dividend against the option's quoted time value.** This is the classic desk rule, and for a *holder* it is wrong. The bid is cum-dividend and already prices the coming drop: by put-call parity the cum-dividend time value is `TV_cum = TV_ex − PV(D)`, so testing `D > TV_cum` counts the dividend twice and fires across the whole band `0 ≤ TV_cum < D`. Everywhere in that band selling realises `intrinsic + TV_cum` while exercising realises only `intrinsic`. Version 1.x of this engine did exactly this: a call with intrinsic 10.00, a 11.00 bid and a 2.00 dividend was told to exercise, throwing away 1.00 per share — 100.00 per contract. The over-flagging is deliberate and harmless on the *writer's* side (see `early-exercise-assignment-risk-management`); on the holder's side it is a standing instruction to give money away.
- **Screening on the mid or the last trade.** The holder's alternative is selling at the bid. A mid overstates what selling realises and hides live exercise decisions; a stale last print invents them.
- **Exercising an ITM call to "lock in profits".** With no imminent dividend, early exercise of an American call is never optimal (Merton 1973). Sell it — that captures the intrinsic *and* the time value.
- **Reading `should_exercise=False` as "hold".** At parity there is nothing left to hold. Sell or exercise; doing nothing is the one strictly worse choice.
- **Applying the 5:30 p.m. ET deadline to early exercise.** FINRA Rule 2360(b)(23)(A) fixes 5:30 p.m. ET on expiration day as the final decision deadline for **expiring** options, and members may set earlier ones. It says nothing about early exercise on an ordinary session, where the operative deadline is the carrying firm's own cut-off. "EST" is also wrong for roughly eight months of the year; the rule is stated in ET.
- **Assuming expiring ITM options are exercised automatically.** OCC's exercise-by-exception procedure exercises equity options in the money by $0.01 or more against the closing price *unless the clearing member instructs otherwise*. OIC is explicit that this is "not, strictly speaking, 'automatic'".
- **Assuming all equity-index options are American.** Verify the exercise style per contract; do not infer it from the settlement method or the underlying.
- **Forgetting what exercising leaves you holding.** Cash or margin for the strike on a call, a short stock position needing a locate on a put, plus a taxable event and the ex-date price drop on the shares you now own.

## Verification

- Run `python -m unittest discover -s skills/american-vs-european-style-option-exercise-handling/scripts` — 38 tests, all passing.
- **Regression, v1.x behaviour**: `OptionState("CALL", spot=110, strike=100, market_price=11.00, is_ex_dividend_tomorrow=True, dividend_amount=2.00)` must return `should_exercise=False` with `early_exercise_edge == -1.00`. v1.x returned `True` here.
- **Invariant**: no combination of option type, price level or dividend size may return `True` while `market_price >= intrinsic_value`.
- **True exercise preserved**: the same contract quoted at 9.95 (below parity) must return `True`, and the reason must name the pending ex-dividend date and the cut-off.
- **Independent oracle**: for spot 110, strike 100, a 2.00 dividend, τ = 90/365, σ = 25%, r = 4%, the Black-Scholes continuation value on the ex-dividend underlying (108) exceeds the 10.00 realised by exercising, so holding is optimal — and the engine, fed that fair value as the bid, must decline to exercise. Shorten τ to 30/365 and the continuation falls below 10.00, the American value pins at parity, and the engine must exercise on a below-parity bid.
- **Exact condition**: `dividend_capture_test` with `K=100`, `p_ex=0.60`, `r=5%`, `τ=15/365` must give `time_value_ex_dividend == 0.8052684` and `is_exercise_optimal=False` against a 0.75 dividend.
- **Misuse rejection**: `dividend_capture_test` must raise `ValueError` for a put and for a state with no pending dividend; `OptionState` must raise `ValueError` for a negative, NaN, infinite, boolean or non-numeric price and for an unrecognised option type.

## Related Skills

- `early-exercise-assignment-risk-management`
- `options-pin-risk-management-at-expiry`
- `physical-vs-cash-settlement-handling`
- `corporate-action-event-calendar-integration`
