---
name: early-exercise-assignment-risk-management
description: >-
  Use when a book carries short American-style options that can be assigned before
  expiration, screening ex-dividend capture against the call's remaining time value and
  flagging at-parity in-the-money shorts.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: early-exercise, assignment-risk, american-options, ex-dividend, extrinsic-value, covered-calls, option-greeks
  brokers_frameworks: "CBOE Options; OCC; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a book carries **short American-style option positions** — covered call income programs, cash-secured puts, credit spreads, or any multi-leg structure with a short leg that can be assigned before expiration. Two economically distinct drivers put a writer at risk:

- **Ex-dividend capture on short calls.** A long call holder who exercises on the last cum-dividend session captures the dividend. Since the US move to T+1 settlement on 28 May 2024 the ex-dividend date and the record date are the same day, so an exercise on the session *before* the ex-date settles on the record date and makes the exerciser a holder of record. The assigned writer is short the stock over the ex-date and owes the dividend.
- **Carry-driven exercise on deep ITM short puts.** Exercising a put frees the strike as cash, which then earns interest for the option's remaining life. The higher the rate and the longer the remaining life, the sooner a put's extrinsic value collapses to the exercise boundary.

## When NOT to Use

- **European-style options.** SPX, NDX, XEO and similar cannot be exercised before expiration; the engine short-circuits them to `LOW_RISK`. Their expiry-day exposure is *pin risk*, a different problem — see `options-pin-risk-management-at-expiry`.
- **As a settlement-method classifier.** Exercise style and settlement method are independent. OEX (S&P 100) is American-style *and* cash-settled; XEO on the same index is European-style. Do not infer one from the other — see `physical-vs-cash-settlement-handling`.
- **As a probability model.** The engine emits an ordinal `assignment_risk_score` (0–100), not a probability. Whether *your* account is assigned depends on OCC allocation to your clearing member and then the member's own FIFO / random / equally-random allocation method (FINRA Rule 2360(b)(23)(C)) applied across the open interest — none of which is an input here.
- **As an execution or cutoff scheduler.** The engine takes day counts you supply. It does not know your broker's early-exercise cutoff, holiday calendar, or session clock.
- **On long positions.** This is a writer's screen. For the holder-side exercise-vs-sell decision use `american-vs-european-style-option-exercise-handling`.

## Prerequisites

- Short position details: `position_id`, `symbol`, `option_type` (`CALL`/`PUT`), `exercise_style` (`AMERICAN`/`EUROPEAN`), `strike`, `option_market_price`, `underlying_price`, `contracts_qty` (positive count), `days_to_expiry`, `contract_multiplier` (100 for standard US equity options).
- **`option_market_price` should be the bid, not the mid or last.** A rational holder exercises only when exercising beats *selling*, and selling realises the bid. Feeding the mid overstates extrinsic value and therefore understates assignment risk.
- Declared dividend details where applicable: `upcoming_dividend_usd` (per share) and `days_to_ex_div`. Leave `days_to_ex_div` at its `+inf` default when no dividend is scheduled.
- Optional but strongly recommended: `same_strike_put_price` (same strike, same expiry) and an engine `risk_free_rate`. Supplying them upgrades the call test from a conservative screen to the exact condition.

## Workflow

1. **Intrinsic & Extrinsic Decomposition**:
   - $\text{Intrinsic}_{\text{call}} = \max(0, S - K)$, $\text{Intrinsic}_{\text{put}} = \max(0, K - S)$; $\text{Extrinsic} = \max(0,\ P - \text{Intrinsic})$.
   - Decision point: if $P < \text{Intrinsic}$ the quote is **below parity**. The reported extrinsic is clamped at zero, but the condition is preserved as `quoted_below_parity` and a `QUOTE_BELOW_INTRINSIC` data-quality flag — exercising already beats selling, and the mark may equally be stale or crossed. Verify the quote before acting on it.
2. **Exercise-Style Gate**: European-style positions return `LOW_RISK` immediately. An unrecognised `option_type` or `exercise_style` is **rejected**, never defaulted — a typo must not silently reclassify a call as a put or an American contract as European.
3. **Ex-Dividend Test (short calls only)**:
   - First check relevance: a dividend whose ex-date falls *after* this option expires cannot be captured by exercising it. Such a dividend is ignored and flagged `DIVIDEND_AFTER_EXPIRY_IGNORED`.
   - **Exact test** when `same_strike_put_price` is supplied — exercise immediately before the ex-date is optimal exactly when
     $$D > \text{TV}_{ex} = p_{ex} + K\left(1 - e^{-r\tau}\right)$$
     where $\text{TV}_{ex}$ is the call's time value at the *ex-dividend* underlying price and $p_{ex}$ is the same-strike, same-expiry put (Merton 1973).
   - **Fallback screen** when it is not: $D > \text{Extrinsic}_{\text{cum-div}}$. This is deliberately **conservative, not exact** — by put-call parity $\text{Extrinsic}_{\text{cum-div}} = \text{TV}_{ex} - PV(D)$, so the screen fires whenever $\text{TV}_{ex} < D + PV(D)$, a strict superset of the exact condition. It over-flags and does not under-flag, which is the correct direction for a writer, but it is not evidence that exercise is certain. The report records which test ran in `exercise_test_used`.
   - Windowing: test satisfied **and** $\text{DaysToExDiv} \le 1.0 \implies$ `CRITICAL_ASSIGNMENT_RISK` + `CLOSE_OR_ROLL_SHORT_CALL`. Test satisfied inside the wider 3.0-day pre-warning window $\implies$ `ELEVATED_ASSIGNMENT_RISK` + `MONITOR`.
4. **At-Parity Test (calls and puts)**:
   - ITM **and** $\text{Extrinsic} \le \max(\$0.05,\ 0.0005 \times K) \implies$ `HIGH_ASSIGNMENT_RISK` + `CLOSE_OR_ROLL_SHORT_{CALL,PUT}`. The relative term matters: a flat \$0.05 floor is a meaningful test on a \$100 strike and a meaningless one on a \$5,000 strike.
   - This rule is dividend-independent, so it catches carry-driven put exercise, hard-to-borrow call exercise, and any short pinned at parity — cases the ex-dividend rule alone never sees.
5. **Severity Resolution**: the reported `risk_level` is the **most severe rule that fired**, and every firing rule contributes its own sentence to `risk_summary`. A critical ex-dividend verdict keeps the call close/roll directive even when the parity rule also fires.
6. **Audit Report Generation**: output the structured `EarlyExerciseAuditReport`, carrying `assignment_risk_score`, `exercise_test_used`, `early_exercise_edge_usd`, `assigned_share_notional_usd`, `dividend_liability_usd`, `quoted_below_parity`, and `data_quality_flags`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading the risk score as a probability of assignment.** Assignment reaches an individual account only through OCC allocation to the clearing member and then the member's FIFO / random / equally-random allocation across its short open interest (FINRA Rule 2360(b)(23)(C); Regulatory Notice 11-35). Rational exercise by holders is necessary for assignment, not sufficient for *your* assignment — and a low probability of being picked is no comfort when the loss is a dividend liability plus an unhedged short stock position.
- **Comparing the dividend to the mid.** Extrinsic computed from the mid can double the apparent buffer on a wide market. The holder's alternative to exercising is selling at the bid; screen on the bid.
- **Comparing the dividend to the wrong time value.** The desk rule "dividend > time value" and the exact rule "dividend > put + interest on strike" are not the same test. The former is a conservative screen on the *cum-dividend* extrinsic; treat a hit as a reason to look, not as proof exercise is optimal.
- **Counting a dividend the option will never see.** An ex-date after expiration carries no assignment risk for that contract. Screening on the dividend alone flags 0DTE and short-dated positions that cannot be exercised into the record date.
- **Watching only the ex-dividend window on calls.** A call pinned at parity — hard-to-borrow name, deep ITM, no dividend anywhere near — is exercisable on any session. The original version of this engine only looked at calls inside a one-day ex-dividend window and scored these `LOW_RISK`.
- **Applying an absolute extrinsic floor across all strikes.** \$0.05 of extrinsic on a \$5,000-strike index option is not a comparable condition to \$0.05 on a \$100 strike.
- **Expecting a dividend to raise short put risk.** A dividend before expiry makes put early exercise *less* attractive, not more — the put holder who exercises gives up the stock and the dividend with it. The engine subtracts it in the put carry edge.
- **Conflating American with European, or exercise style with settlement.** European-style index options cannot be assigned early; American cash-settled index options (OEX) can. Neither fact follows from the other.
- **Acting after the cutoff.** FINRA Rule 2360(b)(23)(A) fixes 5:30 p.m. ET on expiration day as the final decision deadline for *expiring* options, and members may set earlier deadlines. For early exercise on an ordinary session the deadline is your clearing member's own cutoff. Closing or rolling "before the ex-date" means before that cutoff on the last cum-dividend session, not the next morning.

## Verification

- Instantiate `EarlyExerciseRiskEngine()`. Submit a short American call (Strike \$100, Underlying \$105, Bid \$5.20 → Intrinsic \$5.00, Extrinsic \$0.20, 10 contracts) with a \$1.00 dividend 0.5 days out. Confirm `risk_level == "CRITICAL_ASSIGNMENT_RISK"`, `recommended_action == "CLOSE_OR_ROLL_SHORT_CALL"`, `exercise_test_used == "EXTRINSIC_SCREEN"`, and `dividend_liability_usd == 1000.0`.
- Re-submit the same position with `same_strike_put_price=0.60`, `upcoming_dividend_usd=0.75` on an engine with `risk_free_rate=0.05`. The exact test gives $\text{TV}_{ex} = 0.60 + 100(1 - e^{-0.05 \times 15/365}) = \$0.8053 > \$0.75$, so the verdict is `LOW_RISK` — a position the conservative screen would have flagged.
- Confirm a deep ITM short call at parity with no dividend anywhere (Strike \$100, Underlying \$140, Bid \$40.01) still returns `HIGH_ASSIGNMENT_RISK`.
- Run `python -m unittest discover -s skills/early-exercise-assignment-risk-management/scripts`.

## Related Skills

- `american-vs-european-style-option-exercise-handling`
- `options-pin-risk-management-at-expiry`
- `physical-vs-cash-settlement-handling`
- `corporate-action-event-calendar-integration`
- `dividend-futures-and-forward-modeling`
- `options-chain-data-normalization-across-vendors`
