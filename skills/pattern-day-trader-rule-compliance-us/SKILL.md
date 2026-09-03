---
name: pattern-day-trader-rule-compliance-us
description: >-
  Use when a bot day trades US equities or equity options in a margin account
  and must not have its trading frozen by day-trading margin rules. Covers the
  pattern day trader regime (four day trades in five business days, $25,000
  minimum equity) that FINRA deleted effective 2026-06-04 and that brokers may
  still apply as house policy through the 2027-10-20 phase-in, and the Rule
  4210(d)(2) intraday margin standard that replaced it.
domain: algorithmic-trading
subdomain: regulatory-compliance-global
tags:
- regulatory-compliance-global
- finra-rule-4210
- intraday-margin
- pattern-day-trader
brokers_frameworks:
- FINRA Rule 4210 (Margin Requirements)
- SR-FINRA-2025-017 / SEC Release 34-105226
- FINRA Regulatory Notice 26-10
- Alpaca Trading API
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this when a bot opens and closes the same US equity or equity option
position within one session, in a **margin** account, and a trading freeze would
break the strategy. Two regimes are in play at once, and conflating them is the
most common way to get this wrong:

| | Pattern day trader regime | Intraday margin regime |
|---|---|---|
| **Rule** | FINRA Rule 4210(f)(8)(B) | FINRA Rule 4210(a)(17)–(19), (d)(2) |
| **Status** | **Deleted effective 2026-06-04** by SR-FINRA-2025-017 (SEC approval 34-105226, 2026-04-14) | In force from 2026-06-04 |
| **Trigger** | 4+ day trades in 5 business days, and more than 6% of total trades | An intraday margin deficit following an IML-reducing transaction |
| **Constraint** | $25,000 minimum equity, maintained at all times | Satisfy the deficit "as promptly as possible" |
| **Sanction** | No day trading until equity is restored | 90 *calendar* days of no new short positions or debit balances, for a customer who makes a practice of failing and misses the 5th business day |
| **Still binding on your bot?** | Only as **broker house policy** — see below | Yes, via your broker |

The count-based regime has not simply vanished from practice. FINRA permits
members "for an interim period to continue to apply the current day trading
margin requirements where they deem appropriate — for example, by account" while
they implement the new provisions; that phase-in ends **2027-10-20**. Separately,
Rule 4210(d)(1) lets a member set stricter house requirements indefinitely. So
the correct question is no longer "what does FINRA require" but **"what does my
broker currently apply to this account, and when did I last confirm it"**.

Verify the parameters against your broker before trading; both this skill and
the reference implementation date-stamp their sources so a stale assumption is
visible rather than silent.

## When NOT to Use

- **Cash accounts.** The day-trading provisions applied to margin accounts.
  Cash accounts are constrained by Regulation T settlement and free-riding
  instead — a different failure mode with different remedies.
- **Non-US venues.** Nothing here transfers to another jurisdiction.
- **As the authoritative day-trade count.** The broker's count governs; the
  local count exists to catch drift *before* the broker acts on it. Where the
  broker no longer publishes a counter, the local number is unverified and the
  helper says so rather than implying agreement.
- **As a margin engine.** `intraday_margin_deficit()` implements the Rule
  4210(a)(19) definition over snapshots *you* supply. Rule 4210(d)(2)(B) lets
  the member apply sweep-balance, market-value, "as of" and simultaneity
  policies this skill knows nothing about, and to assume worst-case ordering
  where sequence cannot be demonstrated. Treat the output as your estimate, not
  the broker's number.
- **To engineer around a restriction.** Splitting orders across accounts or
  mislabelling round trips to suppress a count is a compliance problem, not an
  implementation one.

## Prerequisites

- The broker's current policy, in writing and dated: does it still apply a
  count-based day-trading restriction to this account, with what threshold, and
  when does it migrate to intraday margin?
- Timezone-aware execution timestamps. The trading date is the fill instant in
  the **exchange's** timezone; a 19:30 America/New_York fill stored as UTC lands
  on the next calendar date.
- An exchange holiday calendar for the business-day window — see
  `global-exchange-holiday-calendar-handling`. Weekend-only arithmetic is wrong
  in every week containing a market holiday.
- Account equity and maintenance margin requirement as the broker computes them,
  not as the bot's position tracker infers them.

## Workflow

1. **Establish which regime binds this account, with a date.** Ask the broker
   whether it still applies day-trading requirements during the phase-in. Record
   the answer as policy data (`DayTradePolicy.confirmed_with_broker`), not as a
   constant in code. Unconfirmed is not the same as "not applicable": leave the
   gate blocking and let the decision carry the warning, because failing open on
   an unverified compliance control is the worse error.
2. **Classify round trips from executions, quantity-aware and FIFO-ordered.**
   Match a closing execution against same-day opening lots *first*, then
   overnight lots. That ordering is what implements the former carve-out — a
   long held overnight and sold the next day **prior to any new purchase** of
   the same security was not a day trade, but a sale *after* a new same-day
   purchase was. A single-slot-per-symbol model silently discards scale-ins and
   then books phantom day trades out of the resulting position drift.
3. **Count over business days, ending on the as-of date.** The window is the
   as-of business day plus the preceding four. Pass the as-of date explicitly:
   anchoring on "the last recorded trade" keeps a months-old history inside the
   window forever, and anchoring on wall-clock "today" mis-evaluates a backtest.
4. **Gate the next day trade before submission, not after rejection.** Block
   when equity is below the threshold and either the account is already
   designated or this would be the 4th day trade in the window. Discovering the
   restriction from a broker rejection means the strategy is already holding a
   position it planned to exit intraday.
5. **Treat the designation as sticky.** The minimum equity had to be maintained
   "at all times", so an account that crosses back below the threshold is
   restricted again even though the rolling window has emptied. Clear the flag
   only on the broker's confirmation.
6. **Reconcile against the broker every session.** Where the broker publishes a
   counter, a mismatch means the local classifier is wrong — investigate before
   the next day trade. Where it does not (Alpaca removed `daytrade_count`,
   `pattern_day_trader`, `last_daytrade_count`, `daytrading_buying_power` and
   `last_daytrading_buying_power` by 2026-07-06), record the count as unverified
   rather than assuming agreement.
7. **Monitor intraday margin deficits, which is the constraint that now bites.**
   Compute the worst negative intraday margin level following an IML-reducing
   transaction, satisfy any deficit as promptly as possible, and date the 5th and
   15th business day deadlines. A deficit at or under the lesser of 5% of equity
   or $1,000 cannot establish a "practice" of failing to satisfy promptly, and
   expiry after the 15th business day is not a safe harbour — the freeze applies
   "without regard to its expiration".
8. **Log every gate decision with its inputs and its provenance.** Rolling
   count, equity, threshold, policy name, policy source and warnings, so a
   blocked order can be reconstructed months later.
9. **Re-verify the rule itself on a schedule.** This area moved in 2026 after
   two decades of stability; treat the parameters as data with an as-of date. See
   `regulatory-change-monitoring-service-integration`.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Asserting the PDT rule as current FINRA law.** It was deleted effective
  2026-06-04. A bot that blocks a trade "because FINRA requires $25,000" is
  citing a rule that no longer exists; it may still be right that *the broker*
  blocks it, which is a different claim needing a different source.
- **Assuming the opposite — that the constraint is gone.** Members may keep
  applying the old requirements through 2027-10-20, per account, and house
  requirements under Rule 4210(d)(1) have no expiry.
- **Conflating the two former sanctions.** Falling below $25,000 while
  designated stopped day trading until equity was restored; the 90-day
  cash-available restriction applied to a pattern day trader who failed to meet
  a *special maintenance margin call* within five business days. They were
  different triggers with different remedies, and the new (d)(2)(D) 90-day
  freeze is a third thing again.
- **Deriving the trade date from a UTC or naive timestamp.** A late-session
  round trip splits across two dates and the day trade disappears from the
  count — the dangerous direction of error.
- **Dropping same-side executions.** Ignoring a scale-in leaves the tracked
  position smaller than the real one; the next day's closing sale then looks
  like an opening trade and a phantom day trade appears.
- **Counting five calendar days**, or five weekdays across a market holiday.
- **Letting the count decay the designation.** The rolling window emptying did
  not undo the designation or the maintenance obligation.
- **Reading an absent broker counter as agreement.** A missing
  `daytrade_count` field is not a zero.
- **Retrying a blocked order against a different account or symbol to keep
  trading.** That is circumvention, not error handling.

## Verification

- Replay a session in which a scale-in precedes a partial close, then close the
  remainder the next morning; confirm exactly one day trade is recorded and the
  next-day sale is not counted.
- Replay a same-day round trip whose fills straddle midnight UTC; confirm both
  legs land on one trading date and the day trade is counted.
- Drive the rolling count to 3 on a sub-threshold account and confirm the 4th
  day trade is blocked *before* submission, with the decision record naming the
  count, the equity and the policy source.
- Cross a business-day boundary with an exchange holiday inside the window and
  confirm the window extends to the correct fifth business day.
- Confirm the local count matches the broker's counter over a paper-trading
  period, and that an absent counter is reported as unverified rather than
  reconciled.
- Confirm a deficit equal to the lesser of 5% of equity or $1,000 is treated as
  de minimis and one cent more is not, and that the 5th and 15th business day
  deadlines land on the dates a manual count gives.
- Run `python -m unittest discover -s skills/pattern-day-trader-rule-compliance-us/scripts`.

## Related Skills

- `global-exchange-holiday-calendar-handling`
- `broker-account-margin-call-handling`
- `leverage-limit-enforcement-across-instruments`
- `kill-switch-and-drawdown-circuit-breakers`
- `regulatory-change-monitoring-service-integration`
- `paper-to-live-promotion-checklist`
