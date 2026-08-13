# Standards for Algo Parameter Defaults by Instrument Liquidity Tier

## Scope

Liquidity-tier defaults are calibration starting points. They are not universal market rules, regulatory limits, best-execution proof, or authorization to route an order. Independent market-access and risk controls must remain active.

| Tier | Illustrative default | Max participation | Profile crossing capability | Required live gate |
|---|---|---:|---|---|
| `HIGH` | TWAP | 5% | Crossing may be permitted by profile | Current spread/depth, volatility, order size, venue, and risk checks |
| `MEDIUM` | VWAP | 10% | Passive by default | Current spread/depth, volume curve, impact, venue, and risk checks |
| `LOW` | IS | 20% | Passive by default | Current spread/depth, urgency, price protection, venue, and risk checks |

These values are examples from the package calibration and must be validated with post-trade analysis. A high ADV observation does not establish that the current spread is tight, depth is executable, or a child order can cross safely.

## Data Contract

- **ADV definition**: record whether ADV is shares/day, currency/day, contracts/day, or another unit.
- **Lookback and calendar**: record session calendar, lookback length, half-days, halted sessions, and missing observations.
- **Corporate actions**: use split-consistent volume and document whether the input is raw or adjusted.
- **Freshness**: record the ADV as-of timestamp and reject observations older than the configured maximum for the strategy.
- **Calibration**: version thresholds and profiles; persist the version with every parent-order decision.

## Profile Invariants

- `high_adv_threshold > medium_adv_threshold > 0`.
- `0 < max_participation_rate <= 1`.
- `passive_buffer_bps >= 0` and all numeric values are finite.
- `default_algo_type` is one of `TWAP`, `VWAP`, or `IS`.
- Profiles are immutable after construction.
- `requires_live_market_check=True` means `cross_spread_allowed` is not sufficient authorization to cross.
- Custom profiles must define all three tiers and their mapping keys must match `profile.tier`.

## Execution Controls

Before applying a profile, the EMS must independently evaluate:

- Current protected bid/offer, spread, depth, and quote freshness.
- Child quantity and notional relative to displayed/expected liquidity and parent limits.
- Volatility, price collars, venue trading status, auctions, halts, and rejects.
- Credit, position, rate, concentration, and kill-switch controls.
- Expected implementation shortfall, fill probability, and signaling/adverse-selection risk.

## Calibration and Monitoring

Retune thresholds and profile values through versioned walk-forward analysis and TCA. Monitor by tier and instrument:

- Implementation shortfall and arrival-price slippage.
- Participation, fill rate, reject/cancel rate, and residual quantity.
- Spread capture/crossing cost, volatility, and quote/depth conditions.
- Data age, ADV revisions, tier migrations, and risk-control overrides.

If a calibration underperforms or data quality degrades, roll back to the last approved version and pause affected instruments until reviewed.