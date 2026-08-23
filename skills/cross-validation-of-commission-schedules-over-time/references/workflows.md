# Deep Workflow Reference — cross-validation-of-commission-schedules-over-time

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Construct the time-varying fee schedule

Build a `List[CommissionTier]`, one tier per contiguous period during which the
broker's published rate did not change. Each tier carries inclusive
`effective_start` / `effective_end` ISO dates.

Decision points:

- **Which broker?** Commission schedules are broker-specific. If the backtest is
  broker-agnostic, pick one broker and say so in the tearsheet — do not blend rates
  from several.
- **What structure?** Flat per-ticket (`fixed_ticket_fee` alone), per-share
  (`per_share_fee` with `min_trade_fee` and, if the broker publishes one,
  `max_pct_of_value`), or percent-of-value (`pct_value_fee`). Do not combine a
  ticket fee with a per-share fee unless the broker actually charges both —
  that composite describes no real US retail schedule.
- **Coverage.** The schedule must span the entire backtest window. It is validated
  at construction: overlapping tiers are rejected outright (the applicable rate
  would be ambiguous); gaps are permitted but logged, and any trade landing in one
  raises rather than being priced.

### 2. Resolve the tier for each trade date

`calculate_trade_commission` parses the timestamp to a calendar date (accepting
`date`, `datetime`, and ISO-8601 strings with or without a time component) and
selects the covering tier.

**It never falls back to a default tier.** A timestamp that cannot be parsed, or a
date outside the schedule, raises `CommissionScheduleError`. This is deliberate:
the previous fallback-to-latest-tier behaviour silently charged $0.00 — applying
today's zero-commission rate to a trade the model could not classify, which is
exactly the bias this skill exists to prevent.

If your schedule's effective dates are expressed in exchange-local time, convert
the trade timestamp to that timezone before passing it in; the date component is
taken verbatim, so a UTC timestamp near a session boundary can land on the wrong
calendar day and therefore the wrong tier.

### 3. Compute the trade commission

```
raw   = fixed_ticket_fee + shares * per_share_fee + trade_value * pct_value_fee
floor = max(raw, min_trade_fee)
final = min(floor, trade_value * max_pct_of_value)     # only when a cap is set
```

The cap is applied **after** the minimum, so the cap dominates. This matches the
IBKR Fixed structure ($0.005/share, $1.00 minimum per order, maximum 1% of trade
value). Model a broker whose minimum is not overridden by a cap with
`max_pct_of_value=None`.

`shares` must be a positive quantity — direction is carried by the `side`
argument. Signed quantities are rejected, because a negative quantity would
silently reduce or invert a per-share fee.

### 4. Add regulatory pass-through fees (US equities)

Supply a `List[RegulatoryFeeTier]` to model the SEC Section 31 fee and the FINRA
Trading Activity Fee. Both apply to **sales only**:

```
section_31 = trade_value * sec_fee_per_million / 1_000_000
taf        = min(shares * taf_per_share, taf_max_per_trade)
```

Rates and effective dates come from the SEC fee-rate advisories and FINRA
Schedule A — see `references/standards.md`. No default history ships with this
module. When `regulatory_schedule` is `None`, every result and report is flagged
`regulatory_fees_modeled=False`; read that flag before quoting a total cost.

### 5. Audit the fee-schedule impact

`audit_impact` prices every trade twice — once under the historical schedule, once
under a flat `modern_baseline` (defaulting to zero commission) applied
retroactively — and reports the delta in dollars and as a percentage of starting
capital. That delta is the P&L inflation a naive backtest would have booked.

Regulatory fees are reported separately and excluded from the delta, since they
apply under both schedules.

## Production Implementation Reference

- Reference code: `scripts/commission_schedule_modeler.py`
  (`HistoricalCommissionModeler`, `CommissionTier`, `RegulatoryFeeTier`,
  `TradeCommissionResult`, `FeeScheduleImpactReport`, `CommissionScheduleError`).
- Reference schedules: `DEFAULT_SCHWAB_RETAIL_SCHEDULE`, `IBKR_FIXED_US_EQUITY_TIER`.
- Automated unit tests: `scripts/test_commission_schedule_modeler.py`.

## Known Limitations

- US cash equities only. Options per-contract fees, futures exchange/clearing fees,
  and non-US levies (UK stamp duty, India STT, exchange transaction charges) are
  not modelled.
- Volume-tiered commission schedules (rate depending on trailing 30-day volume) are
  not modelled here; see `exchange-fee-tier-and-rebate-structure-analysis`.
- Maker/taker rebates are not modelled; see
  `post-only-and-maker-taker-fee-optimization`.
- Payment-for-order-flow-era execution-quality differences are an implicit cost not
  captured by any commission schedule; see
  `transaction-cost-analysis-tca-integration`.
