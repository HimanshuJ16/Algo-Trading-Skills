---
name: perpetual-futures-funding-rate-handling
description: >-
  Turning a published perpetual-swap funding print into the cash flow it actually costs this position: mark-priced notional, a payment signed from the position's side rather than the rate's, simple and compounded annualization off the symbol's real settlement interval (never an assumed 8 hours), and an adverse-carry verdict that refuses NaN rates, mismatched symbols and percent-for-decimal unit errors instead of computing through them.
domain: Crypto Derivatives & Perpetual Swaps
subdomain: Funding Rate Mechanics & Carry Yield
tags: ["perpetual-futures", "funding-rate", "crypto-derivatives", "binance-futures", "okx-perpetuals", "carry-trade", "funding-arbitrage", "linear-perpetuals"]
brokers_frameworks: ["Binance USDS-M Futures API", "Bybit / OKX Perpetual Swap API", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when you hold — or are about to hold — a **linear (USDT/USDC-margined)** crypto perpetual position across a funding settlement and need the honest carry number: BTCUSDT, ETHUSDT, a cash-and-carry short against spot, a basis trade, or a directional position whose thesis has to survive the funding bill.

Perpetual swaps have no expiry, so nothing forces convergence to spot. Instead the venue settles a periodic payment between longs and shorts to tether the mark price to the index: when the rate is positive, longs pay shorts; when it is negative, shorts pay longs. Binance computes the fee against "Nominal Value of Positions = Mark Price × Size of a Contract", Bybit applies the rate to mark-priced position value, and the direction rule is identical on both. This engine takes one published print and one position and returns the signed payment, the annualized cost from *that position's* point of view, and a verdict against an operator carry ceiling.

The two things it is most useful for: catching a directional position whose funding drag quietly exceeds its expected edge, and pricing the carry leg of a delta-neutral basis trade before the capital is committed.

## When NOT to Use

- **On inverse / COIN-margined contracts.** There, notional is `contracts × contract_multiplier / mark_price` and the fee settles in the base coin, not in quote currency. Feeding a COIN-M position to `|qty| × mark_price` produces a number with the wrong magnitude *and* the wrong unit — it is not a rounding error, it is a different formula.
- **As a funding-rate forecaster.** This consumes a rate the venue has already published. It does not compute the premium index, does not predict the next print, and the annualized figures are not projections — see the pitfall below.
- **On a continuous-funding venue, as the amount charged.** Binance, Bybit and OKX charge the whole interval to whoever holds the position at the funding timestamp, with no proration. Deribit quotes an 8-hour rate but accrues it continuously (`payment = rate × size × elapsed / 8h`). On Deribit this engine's per-interval figure is an upper bound on a partially-held interval, not the debit.
- **As a portfolio-level carry view.** One symbol, one print, one position. No netting across sub-accounts, no cross-margin aggregation — see `capital-efficiency-across-cross-margined-strategies`.
- **As a risk control.** `recommended_action` is a string in a report. It closes nothing. Position-flattening on adverse carry belongs behind an independent breaker — see `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- Position at the funding timestamp: `symbol`, `position_qty`, `side` (`LONG`/`BUY` or `SHORT`/`SELL`), `entry_price` (context only — funding ignores it), `mark_price` **at the funding timestamp**, not the last trade price.
- The published print: `funding_rate` as a **per-interval decimal** (`0.0001` = `+0.01%`, never `0.01`), and `next_funding_timestamp_utc` as ISO-8601 UTC (`funding_timestamp_from_epoch_ms` converts Binance/Bybit epoch-ms fields).
- **`funding_interval_hours` read from the venue for this symbol** — Binance `GET /fapi/v1/fundingInfo` → `fundingIntervalHours`. The default of 8 is a convenience, not a fact about your symbol.
- A carry policy: `max_adverse_funding_apr` (decimal annualized cost ceiling, e.g. `0.25`).

## Workflow

1. **Resolve direction before anything else.**
   - `side` is authoritative; `position_qty` supplies magnitude. A `LONG` carrying a negative quantity is a contradiction and raises rather than being reinterpreted.
   - **Decision point — Binance one-way mode reports `positionSide="BOTH"`.** That token carries no direction and is rejected. Derive `LONG`/`SHORT` from the sign of `positionAmt` first; guessing flips the sign of a real cash flow.

2. **Notional and signed payment:**
   $$V_{\text{notional}} = |Q| \times P_{\text{mark}}, \qquad \text{Payment} = d \cdot V_{\text{notional}} \cdot F, \quad d = \begin{cases} +1 & \text{LONG} \\ -1 & \text{SHORT}\end{cases}$$
   Positive payment = outflow (you pay). Negative = inflow (you are paid).
   - **Decision point — use the mark price, not entry and not last.** Funding is charged on mark-priced notional; a position 20% in profit pays funding on the *marked* value, not on what it cost.

3. **Annualize off the symbol's real interval:**
   $$n = \frac{8760}{\text{IntervalHours}}, \qquad \text{APR} = d \cdot F \cdot n \cdot 100\%, \qquad \text{APY} = \left[(1 + d F)^{n} - 1\right] \cdot 100\%$$
   - **Decision point — check the interval before believing the APR.** Binance switches a symbol to *hourly* settlement when the previous rate reaches the cap/floor; OKX runs 4-hour contracts; Bybit sets it per symbol and adjusts live. The same `0.01%` print is 10.95% APR on 8h and 87.60% on 1h. An assumed 8 understates a capped symbol's drag eightfold.
   - **Decision point — APR and APY are not interchangeable.** At `+0.1%` per 8h they are 109.5% and 198.8%. Quote the simple APR for a single held interval; quote the compounded APY only if the carry is genuinely being rolled — and then do not call it an APR.
   - Both figures are signed like the payment: **positive means this position is paying**, so a short earning funding reports a negative APR.

4. **Adverse drag audit.** Breach when the position is paying *and* `APR > max_adverse_funding_apr × 100`. The comparison is strict — sitting exactly on the ceiling is not a breach. Income never breaches, however large.

5. **Report.** `FundingRateReport` carries the payment, both annualized figures, the interval and periods actually used, optional `hours_to_next_funding` (only when you pass `now_utc`), and an advisory `recommended_action`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming the 8-hour interval.** It is a default on Binance, Bybit and OKX, and all three run symbols on shorter intervals — Binance moves a symbol to hourly settlement precisely when the rate is at the cap, i.e. exactly when the drag matters most. Read the interval per symbol per settlement.
- **Reading the rate's sign instead of the position's.** A `+0.01%` print is a cost to a long and income to a short. Code that treats `funding_rate > 0` as "we are paying" is wrong for every short, and wrong in the direction that makes a losing carry look profitable.
- **Closing one minute late.** Funding liability on Binance, Bybit and OKX attaches to whoever holds the position *at* the timestamp, with no proration: exit at 07:59 UTC and you pay nothing; exit at 08:00:01 and you pay the whole interval. Binance additionally documents up to a 15-second deviation in actual settlement timing, so "one second early" is not a plan.
- **Extrapolating one print to an APR and treating it as a forecast.** The annualized figure answers "what if this print repeated unchanged for a year". Funding rates mean-revert, flip sign, and are capped. Use the APR to compare *this* interval against a policy ceiling, not to project a year of carry.
- **Passing the rate in percent.** `0.01` meaning "0.01%" is a 100× fee error. The engine's plausibility guard rejects gross cases (`0.75` handed over meaning `0.75%`) but **cannot** catch this one: `0.01` is a legitimate 1% print, inside Binance's ±2% cap. Validate units at the API boundary.
- **Letting a NaN rate through.** `NaN > 0` is `False`, so a naive implementation classifies a corrupt rate as *income* — the safest-looking status the report can produce, off the worst possible data. Non-finite rates are rejected here.
- **Pairing a position with another symbol's print.** A BTCUSDT position and an ETHUSDT funding update compute cleanly and mean nothing; the engine rejects the mismatch.
- **Backtesting perpetual strategies at zero holding cost.** A `+0.01%` 8-hour print is ~10.95% simple annualized. A long-biased strategy backtested without funding overstates multi-month profitability by roughly that much, before fees.

## Verification

- Instantiate `PerpetualFuturesFundingRateHandlingEngine()`. Feed 10 BTC **LONG** @ `$50,000` mark with `+0.0001` (`+0.01%`) on an 8-hour interval: verify `$500,000` notional, `funding_payment_usd == +50.00` (outflow), `annualized_funding_apr ≈ +10.95`, `annualized_funding_apy ≈ +11.57`, `periods_per_year == 1095.0`.
- Same position with `-0.0002`: verify `funding_payment_usd == -100.00` (inflow) and status `FUNDING_INFLOW_INCOME`.
- Flip to **SHORT** at `+0.0001`: verify `-50.00` and `annualized_funding_apr ≈ -10.95` — sign agreement between payment and APR is the property to check, not the magnitude.
- Interval sensitivity: the same `+0.0001` print gives `+21.90%` APR at 4h and `+87.60%` at 1h, while the per-interval payment stays `$50.00`.
- Boundary: `+0.0002` on 8h is exactly `21.90%` APR; against `max_adverse_funding_apr=0.219` it must **not** breach.
- Negative checks — each must raise `FundingInputError`: `side="LNG"`, `side="BOTH"`, `LONG` with a negative quantity, a zero quantity, a non-positive `mark_price`, a `NaN`/`inf` rate, `funding_interval_hours` of `0` or `-8`, a BTCUSDT position against an ETHUSDT print, a rate of `0.75`, and a naive (non-tz-aware) `now_utc`.
- Run `python -m unittest discover -s skills/perpetual-futures-funding-rate-handling/scripts` and confirm a 100% pass rate.

## Related Skills

- `crypto-exchange-api-integration`
- `bybit-derivatives-api-integration`
- `okx-unified-account-api`
- `capital-efficiency-across-cross-margined-strategies`
- `kill-switch-and-drawdown-circuit-breakers`
- `execution-venue-fee-tier-optimization`
