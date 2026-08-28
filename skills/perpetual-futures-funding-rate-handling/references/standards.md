# Standards for Perpetual Futures Funding Rate Handling

Every row below is sourced. Where venues differ, the difference is stated rather than
averaged into a false universal. Caps, intervals and formulas are changed by venues
without much notice — re-verify against the linked page before relying on a number.

## Funding mechanics by venue

| Venue | Settlement | Notional basis | Proration | Source |
|---|---|---|---|---|
| Binance USDS-M | 00:00 / 08:00 / 16:00 UTC by default; **switches to hourly** when the previous settlement reached the cap or floor (since Jan 2025). Up to ~15s deviation in actual charge time. | "Nominal Value of Positions = Mark Price × Size of a Contract" | None — only positions open **at** the funding time pay or receive; close before it and nothing is charged. | [Introduction to Binance Futures Funding Rates](https://www.binance.com/en/support/faq/detail/360033525031) |
| Binance COIN-M | Same schedule | Notional = (contracts × contract multiplier) / mark price; fee settles in the **base coin**, not quote currency | Same | [What Fees are Generated in Binance Futures Trading?](https://www.binance.com/en/support/faq/what-fees-are-generated-in-binance-futures-trading-98488a516eb84e3eb34605683dffd554) |
| Bybit | Every 8h by default (00:00 / 08:00 / 16:00 UTC); **interval is per symbol** and may be adjusted live when last price and mark price diverge | Rate applied to mark-priced position value | Charged at the funding timestamp | [Funding Fee Calculation](https://www.bybit.com/en/help-center/article/Funding-fee-calculation) |
| OKX | 8h default; 4-hour contracts exist, and the settlement frequency is adjusted automatically per market conditions | Rate applied to position value | Charged at the funding timestamp | [Perpetual funding fee mechanism](https://www.okx.com/en-us/help/perps-funding-fee-mechanism) |
| Deribit | Rate is **quoted** as an 8-hour rate but accrues **continuously** (transferred every few seconds) | `payment = rate × position size × (elapsed / 8h)` | Inherently prorated — a partially-held interval is charged pro rata | [Perpetual Swap Funding](https://insights.deribit.com/education/perpetual-swap-funding/) |

## Rate construction and caps

| Item | Detail | Source |
|---|---|---|
| Binance funding rate | `F = [avg Premium Index P + clamp(interest rate − P, −0.05%, +0.05%)] / (8 / N)`, `N` = interval hours | [Binance FAQ 360033525031](https://www.binance.com/en/support/faq/detail/360033525031) |
| OKX funding rate | `clamp([avg premium index + clamp(interest rate − avg premium index, −0.05%, +0.05%)] / (8 / N), floor, cap)`; interest rate 0.01% per 8h settlement, 0.015% for 4h contracts | [Revision of the Funding Rate Formula for OKX Perpetual Futures](https://www.okx.com/en-us/help/important-update-revision-of-the-funding-rate-formula-for-okx-perpetual) |
| Binance caps | ±2% for most contracts; majors capped at 0.75 × maintenance margin ratio (≈ ±0.3% for BTCUSDT). Per-symbol adjusted values are published as `adjustedFundingRateCap` / `adjustedFundingRateFloor` | [Binance FAQ 360033525031](https://www.binance.com/en/support/faq/detail/360033525031), [Get Funding Rate Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info) |
| Deribit clamp | Documented as ±0.5% (BTC) and ±1% (ETH), expressed as 8-hour rates; a ±0.025% dead zone around the index yields a zero rate | [Perpetual Swap Funding](https://insights.deribit.com/education/perpetual-swap-funding/) |
| OKX caps | Set per contract and adjustable in real time — read them per symbol rather than hard-coding | [Adjustment of Perpetual Swap Funding Rules](https://www.okx.com/en-us/help/adjustment-of-perpetual-swap-funding-rules) |

## API fields this module consumes

| Field | Venue endpoint | Note |
|---|---|---|
| `fundingIntervalHours` | Binance `GET /fapi/v1/fundingInfo` | Only returned for symbols with an adjusted cap/floor/interval; absent means the 8h default applies. Shares a 500/5min/IP limit with `GET /fapi/v1/fundingRate`. |
| `lastFundingRate`, `nextFundingTime`, `markPrice` | Binance `GET /fapi/v1/premiumIndex` | `nextFundingTime` is epoch **milliseconds** — convert with `funding_timestamp_from_epoch_ms`. |
| `positionAmt`, `positionSide`, `entryPrice` | Binance `GET /fapi/v2/positionRisk` | `positionSide` is `BOTH` in one-way mode and carries no direction; derive it from the sign of `positionAmt`. |

## Engineering standards enforced by this skill

| Standard | Rule |
|---|---|
| Sign convention | Payment, APR and APY are all signed from the **position's** perspective: positive = cost, negative = income. Positive `F` means longs pay shorts on every venue above. |
| Notional | `|position_qty| × mark_price`, quote currency, linear contracts only. Entry price never enters the calculation. |
| Simple annualization (APR) | `F × (8760 / IntervalHours) × 100%` — a hypothetical, not a forecast. |
| Compounded annualization (APY) | `((1 + F) ^ (8760 / IntervalHours) − 1) × 100%`. Materially larger than the APR at high rates (109.5% vs 198.8% at 0.1%/8h). |
| Interval | Read per symbol per settlement. Never assumed. Non-positive values are rejected, never coerced. |
| Rate hygiene | Non-finite rates rejected; rates beyond the plausibility guard (default 5% per interval, far above every published cap) rejected as probable percent/decimal confusion. |
| Instrument integrity | A position and a funding print for different symbols are rejected, not silently combined. |
| Determinism | The engine reads no clock. Time-to-funding is computed only from a caller-supplied timezone-aware `now_utc`. |
