---
name: american-vs-european-style-option-exercise-handling
description: Quantitative decision engine for determining optimal early exercise conditions
  for American options vs holding to expiration (European style).
domain: multi-asset-derivatives
subdomain: options-pricing
tags:
- options
- derivatives
- early-exercise
- quantitative-finance
brokers_frameworks:
- generic
version: "1.2.0"
author: System
license: MIT
---

## When to Use

Use this skill when managing a portfolio of American-style options (where early exercise is legally permitted). The decision to exercise early is a classic quantitative problem boundary. Exercising early destroys the option's "Time Value" (optionality), which is generally suboptimal. However, deep In-The-Money (ITM) puts, or calls immediately preceding an ex-dividend date, may require early exercise to maximize yield.

**American vs European distinction**: European-style options can only be exercised at expiration — there is no early-exercise decision to make. This skill is strictly for American-style options. If the option is European-style, early exercise is not possible and this skill does not apply.

## When NOT to Use

- **European-style options**: European options cannot be exercised before expiration. There is no early-exercise decision — skip this skill entirely.
- **Index options with cash settlement**: Many index options (e.g., SPX) are European-style and cash-settled. Verify the exercise style before applying this logic.
- **Options near expiration with no open interest**: If the option has no liquidity to sell, exercise-vs-hold is moot — assignment is automatic at expiration if ITM.
- **Exotic/structured options**: Binary, barrier, or Asian options have non-standard exercise features that require bespoke pricing models, not this general-purpose engine.

## Prerequisites

- Python 3.9+
- Real-time options pricing (Market Value).
- Real-time underlying spot pricing.
- Dividend schedules for the underlying asset.

## Workflow

1. **State Construction**: Feed the current `OptionState` (Spot, Strike, Market Price, Time to Expiry, Dividend Info) into the engine. The dataclass validates inputs (non-negative finite prices, valid option type) at construction time.
2. **Intrinsic vs Continuation**: The engine compares the Intrinsic Value (immediate payoff) against the Continuation Value (current market price of the option).
3. **Hard Rules Application**:
   - **Call Options**: Enforces the mathematical rule that an American Call should *never* be early exercised on a non-dividend paying stock when `market_price >= intrinsic_value`. It only evaluates early exercise if a dividend is imminent or the call is trading below parity (illiquid).
   - **Put Options**: Evaluates if the intrinsic value strictly exceeds the market continuation value (deep ITM scenario where the interest on cash outpaces time value).
4. **Action**: Returns a boolean `should_exercise` flag with a quantitative justification.

## Common Pitfalls

- **Exercising Non-Dividend Calls**: Retail traders often exercise ITM calls early to "lock in profits." This is a mathematical error that destroys the time value premium. The optimal move is to sell the call in the open market, not exercise it.
- **Ignoring Dividends**: Failing to exercise a deep ITM call the day before a massive ex-dividend date, thereby forfeiting the dividend yield to the option writer.
- **Below-Parity Blind Spot**: If a call or put is trading below its intrinsic value (illiquid, wide spreads), selling captures less than exercising. The engine detects this condition and exercises.
- **Exercising at Dividend == Time Value**: When the dividend exactly equals the time value, the trader is indifferent. The engine defaults to **not exercising** (requires strictly greater dividend) — a conservative choice that preserves optionality.
- **Assuming All Equity Options are American**: Index options (SPX, NDX) are European-style. Verify the exercise style before running the engine.

## Verification

- Run `python scripts/test_american_vs_european_style_option_exercise_handling.py` to assert that non-dividend calls are strictly blocked from early exercise, while deep ITM puts and dividend-captured calls correctly trigger the exercise logic.
- Verify below-parity calls (market < intrinsic) trigger exercise even without a dividend.
- Verify the boundary case (dividend == time_value) does not exercise.
- Verify invalid inputs (negative prices, invalid option type) raise `ValueError` at construction.

## Related Skills

- `options-pin-risk-management-at-expiry`
- `early-exercise-assignment-risk-management`
---
