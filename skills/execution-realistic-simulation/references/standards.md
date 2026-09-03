# Venue & Framework Coverage — execution-realistic-simulation

| Exchange / Regulatory Framework | Relevance to this skill | Modelled in `scripts/fill_model.py` |
|---|---|---|
| NSE / BSE — equity intraday, delivery, futures, options | Statutory stack: STT/CTT, exchange transaction charge (IPFT included), SEBI turnover fee, stamp duty, GST 18% on brokerage + exchange + SEBI charges | Yes — four dated `FeeSchedule` entries |
| US equities (SEC / FINRA) | SEC Section 31 fee on sales, assessed as a rate on notional | Section 31 only. FINRA's Trading Activity Fee is charged **per share sold** and cannot be expressed as a fraction of turnover, so it is not modelled |
| Crypto spot venues | Maker/taker tiers, per-venue and per-volume-band | Placeholder 0.1% taker fee only — replace with the venue's schedule before use |
| Backtesting engines (Backtrader, Zipline, Lean) | Each exposes a custom slippage/commission hook the model in this skill can be dropped into | Not integrated — the helper is framework-agnostic |

## Rate provenance

Every rate in `DEFAULT_FEE_SCHEDULES` carries an `effective_from` date and a `source`
string, and the module carries `FEE_SCHEDULES_VERIFIED_ON` (2026-08-24). Statutory
rates change and are jurisdiction-specific:

- Indian F&O STT changed twice in eighteen months — 0.0625%/0.0125% (options/futures)
  to 0.10%/0.02% on 1 October 2024, then to 0.15%/0.05% on 1 April 2026 (Budget 2026).
  Equity delivery (0.1%, both sides) and intraday (0.025%, sell side) were unchanged.
- NSE transaction charges were restructured on 1 October 2024 to a uniform, non-slab
  basis following SEBI's July 2024 circular on charges levied by market infrastructure
  institutions, and have been revised since.
- The SEC Section 31 rate is adjusted at least annually; USD 20.60 per million took
  effect 4 April 2026.

Re-verify against the exchange's and the tax authority's current published rates before
using cost figures in any decision, and update `FEE_SCHEDULES_VERIFIED_ON` when you do.

## Not modelled

Charges that are not a fraction of turnover, and therefore cannot be derived from this
helper's arguments: FINRA Trading Activity Fee (per share sold), depository/DP charges
on Indian delivery sells (per scrip per day), clearing member charges, STT on exercised
or assigned options (charged on intrinsic value, not premium turnover), and account-level
charges such as AMC.

## Regulatory & Operational Notes

Cost realism in a backtest is a research-integrity control, not only an accuracy concern:
a strategy approved on understated costs is a strategy approved on a result that was never
achievable. Where backtest results are used in disclosure to investors or to a regulator,
the cost assumptions and their effective dates are part of the record — see
`backtest-audit-trail-for-regulatory-review`.
