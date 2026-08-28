# Standards for Options Chain Expiry Cycle Conventions by Exchange

All entries were verified against the cited primary sources in **August 2026**.
Exchanges change contract specifications, sometimes without prior notification.
Re-verify before relying on any row; the code carries the same `source` and
`source_as_of` values on every `ContractConvention`.

## Bundled contract registry

| Exchange | Symbol | Expiry rule | Settlement | Exercise | Delivery | Last trading day |
|---|---|---|---|---|---|---|
| Cboe | `SPX` | 3rd Friday | `AM_SETTLED` (SOQ / SET) | European | Cash | Preceding business day |
| Cboe | `SPXW` | Not calendar-derivable (weekly / EOM) | `PM_SETTLED` | European | Cash | Expiration date |
| Cboe | `NDX` | 3rd Friday | `AM_SETTLED` (NOOP-based) | European | Cash | Preceding business day |
| Cboe | `NDXP` | Not calendar-derivable | `PM_SETTLED` | European | Cash | Expiration date |
| Cboe | `RUT` | 3rd Friday | `AM_SETTLED` (SOQ / RLS) | European | Cash | Preceding business day |
| Cboe | `RUTW` | Not calendar-derivable | `PM_SETTLED` | European | Cash | Expiration date |
| Cboe | `XSP` | 3rd Friday | `PM_SETTLED` | European | Cash | Expiration date |
| Cboe | `VIX` | Wednesday 30 days before the 3rd Friday of the **following** month | `AM_SETTLED` (SOQ) | European | Cash | Preceding business day |
| CME | `ES` (quarterly) | 3rd Friday | `AM_SETTLED` (SOQ) | **American** | **Futures** | Expiration date |
| Eurex | `ODAX` | 3rd Friday, else preceding exchange day | `AUCTION_SETTLED` (Xetra intraday auction, from 13:00 CET) | European | Cash | Expiration date |
| Deribit | `BTC`, `ETH` | **Last** Friday | `FIXED_TIME_SETTLED` (08:00 UTC delivery price) | European | Cash | Expiration date |

Declared-asset-class default (used only when the caller passes
`asset_class="EQUITY"` or `"ETF"`, never inferred from a ticker):

| Class | Expiry rule | Settlement | Exercise | Delivery |
|---|---|---|---|---|
| US listed equity / ETF option (OCC cleared) | 3rd Friday | `PM_SETTLED` | American | Physical |

## Rules that the "third Friday, AM, European, cash" shorthand gets wrong

| Claim | Reality | Source |
|---|---|---|
| "Monthly options expire on the third Friday." | Deribit monthlies expire the **last** Friday of the month at 08:00 UTC. The published Deribit Q1 2026 expiry was 2026-03-27; the third Friday was 2026-03-20. | Deribit Support — *Settlement*; *Contract Introduction Policy* |
| "…and VIX is no exception." | Cboe VIX options expire on the **Wednesday 30 days before the third Friday of the following calendar month**. 30 days before a Friday is always a Wednesday, so a VIX expiry is never a Friday. | [Cboe VIX Options specifications](https://www.cboe.com/tradable-products/vix/vix-options/specifications/) |
| "The third Friday is always the expiration date." | "…the third Friday of the expiration month **or the immediately preceding business day if the Exchange is not open on that Friday**." Good Friday fell on the third Friday in **April 2022** (expiry moved to Thu 14 Apr) and **April 2025** (moved to Thu 17 Apr). | [Cboe RUT/RUTW specifications](https://cboe.com/tradable_products/ftse_russell/russell_2000_index_options/rut_specifications/); [Eurex ODAX specifications](https://www.eurex.com/ex-en/markets/idx/dax/DAX-Options-139884) |
| "An option trades until its expiration date." | "Trading in SPX options will ordinarily cease on the **business day (usually a Thursday) preceding** the day on which the exercise-settlement value (i.e., the expiration date) is calculated." Applies to every AM-settled monthly. | [Cboe SPX Options specifications](https://www.cboe.com/tradable_products/sp_500/spx_options/specifications/) |
| "Index options are AM-settled." | `XSP` (Mini-SPX) is an index option, European and cash-settled, but **PM-settled**. So are `NDXP`, `RUTW` and `SPXW`. | [Cboe XSP factsheet](https://cdn.cboe.com/resources/xsp/XSP_Options_Fact_Sheet.pdf); [Nasdaq NDX/NDXP factsheet](https://www.nasdaq.com/nasdaq-100-options-xnd-ndx) |
| "Index options are European; equity options are American." | CME **quarterly** `ES` options are **American**-style and exercise into the underlying futures contract. CME separately lists European-style Third-Friday Monthlies on the same underlying — a different product. | [CME Group — Understanding listings and expirations](https://www.cmegroup.com/education/courses/understanding-micro-futures-contracts-at-cme-group/micro-e-mini-options/understanding-listings-and-expirations) |
| "Settlement is either AM or PM." | Eurex determines the DAX final settlement price from **Xetra intraday auction** prices (from 13:00 CET) — neither an opening nor a closing value. Deribit settles at a **fixed 08:00 UTC** delivery price. | [Eurex ODAX specifications](https://www.eurex.com/ex-en/markets/idx/dax/DAX-Options-139884); Deribit Support — *Settlement* |
| "Equity options expire on the Saturday after the third Friday." | Historic. US listed equity option expiration is the third Friday itself; exercise settles share delivery on the normal cycle. | [OCC — Equity options product specifications](https://www.theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications) |

## Settlement-type vocabulary

| Value | Meaning |
|---|---|
| `AM_SETTLED` | Settlement value struck from component **opening** prices on the expiration date (Special Opening Quotation). |
| `PM_SETTLED` | Settlement value is the **closing** value on the expiration date. |
| `AUCTION_SETTLED` | Settlement value from a scheduled **intraday auction** (Eurex/Xetra, from 13:00 CET). |
| `FIXED_TIME_SETTLED` | Settlement value struck at a **fixed wall-clock time** (Deribit, 08:00 UTC). |

## Delivery-type vocabulary

| Value | Meaning |
|---|---|
| `CASH` | Cash difference, delivered on the business day following expiration. |
| `PHYSICAL` | Delivery of the underlying security. |
| `FUTURES` | Exercise delivers a position in the underlying **futures** contract (CME options on futures). |

## Engineering standards

| Requirement | Standard |
|---|---|
| Third-Friday derivation | Arithmetic from `date.weekday()`. Never `calendar.monthcalendar()`, whose column layout depends on the process-global `calendar.setfirstweekday()`. |
| Unknown contract | Fail closed with `UnknownContractError`. Never infer conventions from a ticker string. |
| Non-derivable cycle | Fail closed with `UnsupportedCycleError`. Never substitute the monthly anchor for a weekly series. |
| Holiday calendar | Injected, per exchange. Absent ⟹ unadjusted **plus** an explicit `report.warnings` entry — never a fabricated calendar, never another venue's. |
| Continuously-traded venues | `observes_exchange_holidays=False`; no roll-back is applied to Deribit. |
| DTE | Signed. Negative means expired; `is_expired` is reported alongside. |
| Provenance | Every registry entry carries `source` and `source_as_of`, propagated onto every report. |
