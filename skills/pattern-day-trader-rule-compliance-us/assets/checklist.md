# Pre-Flight / Sign-off Checklist — pattern-day-trader-rule-compliance-us

Use this before considering the skill's implementation complete.

## Regime and policy

- [ ] **Broker policy confirmed and dated:** the broker has stated in writing
      whether it still applies a count-based day-trading restriction to this
      account during the phase-in ending 2027-10-20, and the answer is recorded
      on `DayTradePolicy` with `source` and `source_as_of`.
- [ ] **No rule asserted that no longer exists:** nothing in code, logs or
      operator-facing messages claims FINRA currently requires four-in-five
      counting or $25,000 minimum equity. Rule 4210(f)(8)(B) was deleted
      effective 2026-06-04.
- [ ] **Unconfirmed fails closed:** with `confirmed_with_broker=None` the gate
      still blocks and the decision carries the currency warning.
- [ ] **Intraday margin covered:** the account's intraday margin deficit is
      monitored under Rule 4210(d)(2), with the 5th and 15th business-day
      deadlines dated and the de minimis carve-out (lesser of 5% of equity or
      $1,000) applied.

## Classification and counting

- [ ] **Timezone-aware timestamps:** executions carry tz-aware timestamps and the
      trading date is derived in the exchange timezone. A late-session round trip
      expressed in UTC still lands on one trading date.
- [ ] **Quantity-aware matching:** scale-ins are retained; a closing execution
      offsets same-day lots before overnight lots; a reversal leaves an
      opposite-side lot open.
- [ ] **Overnight carve-out:** an overnight long sold the next day before any new
      purchase is not counted; a sale after a new same-day purchase is.
- [ ] **Business-day window with holidays:** an exchange holiday calendar is
      supplied, so no decision carries the "no holiday calendar" warning.
- [ ] **Explicit as-of date:** every count and gate call passes the session date;
      no code path relies on wall-clock "today" in a backtest.

## Gate and state

- [ ] **Pre-submission block:** the 4th day trade on a sub-threshold account is
      blocked before the order is sent, not after a broker rejection.
- [ ] **Threshold boundary:** equity exactly at the threshold is permitted; one
      cent below is not.
- [ ] **Sticky designation:** an account that reached the limit stays designated
      after the window empties, and is blocked again if equity falls back below
      the threshold.
- [ ] **Broker reconciliation:** local and broker counts agree over a paper
      period; an absent broker counter is recorded as unverified, never as zero.

## Evidence and testing

- [ ] **Auditable decisions:** every blocked or allowed day trade is logged via
      `PDTGateDecision.as_log_record()` with count, equity, threshold, policy
      name, policy source and warnings.
- [ ] **Automated testing:** run `python -m unittest test_pdt_tracker` from
      `scripts/` and confirm a 100% pass rate.
- [ ] **Re-verification scheduled:** a dated review of the broker policy and the
      underlying rule is on the calendar; this area changed in 2026.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Broker and policy as-of date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
