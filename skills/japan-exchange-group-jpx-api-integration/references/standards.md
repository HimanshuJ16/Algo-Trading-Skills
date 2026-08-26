# Standards for JPX / TSE Cash Equity Integration

Source of record: the Japan Exchange Group "Trading Rules of Domestic Stocks"
pages and the Securities Identification Code Committee (SICC). The tick size
page was last updated by JPX on **6 August 2026** and the daily price limits
page on **24 August 2026**; both were transcribed here on **25 August 2026**.
The trading system is **arrowhead4.0**, live since **5 November 2024** (fourth
generation since arrowhead launched on 4 January 2010).

| Metric | Engineering Standard |
|---|---|
| Base Currency | Japanese Yen (JPY). Prices are **not** whole yen — the minimum tick is JPY 0.1. |
| Securities Code Format | Four characters. Positions 1 and 3 are digits; positions 2 and/or 4 may be a digit or one of 19 uppercase letters. Codes assigned before January 2024 remain all-numeric. |
| Tick Size Rules | Order prices MUST be an exact multiple of the tick from the table applicable **to that issue**, with the band selected by the **order price**. |
| Trading Unit | Domestic stocks: 100 shares, standardised on 1 October 2018. ETFs, ETNs, REITs and leveraged products: 1 or 10. |
| Daily Price Limits | Set in **absolute yen** against the base price (基準値段). Never a percentage. |
| Trading Hours | Morning session 09:00–11:30; afternoon session 12:30–15:30 (extended from 15:00 on 5 November 2024). Orders accepted from 08:00 and 12:05. A closing auction runs from 15:25, with the Itayose at 15:30. |
| Scope | These rules govern the auction (立会) cash equity market. ToSTNeT off-auction trading and OSE/TOCOM derivatives on J-GATE are out of scope. |

## Securities Codes (SICC)

The Securities Identification Code Committee — formed by the five Japanese
exchanges and JASDEC — began including letters in specific name codes for
stocks on **1 January 2024**, because the numeric range `1300`–`9999` available
for general companies is running out.

- Letters appear in **either or both of the 2nd and 4th positions only**; the
  1st and 3rd positions remain digits. Examples: `9A76`, `987A`, `9A7A`.
- **19 uppercase letters** are used. `B`, `E`, `I`, `O`, `Q`, `V` and `Z` are
  excluded, giving the allowed set `ACDFGHJKLMNPRSTUWXY`.
- The first such code was `130A`. Fourth-position letters are assigned first;
  second-position letters begin at `1A00` once those are exhausted. Codes with
  letters in *both* positions are reserved but not yet assigned.
- **Codes set before January 2024 are unchanged** — JPX itself remains `8697`.

A four-digit validator therefore rejects every issue listed since January 2024.

## Tick Size (呼値の単位)

Band bounds are **inclusive** — JPX publishes them as 「1,000円以下」, "1,000 yen
or less" — so a price exactly on a boundary takes the finer tick of the lower
band. TSE publishes three tables; which one applies is a property of the issue.

### TOPIX500 Constituents (TOPIX100 + TOPIX Mid400)

Applied under **Rule 14, Paragraph 3, Item 1-b of the TSE Business
Regulations**, and also to ETFs, ETNs and leveraged products with a trading
unit of 10 or above.

| Price per share (JPY) | Tick size (JPY) |
|---|---|
| up to 1,000 | 0.1 |
| up to 3,000 | 0.5 |
| up to 10,000 | 1 |
| up to 30,000 | 5 |
| up to 100,000 | 10 |
| up to 300,000 | 50 |
| up to 1,000,000 | 100 |
| up to 3,000,000 | 500 |
| up to 10,000,000 | 1,000 |
| up to 30,000,000 | 5,000 |
| over 30,000,000 | 10,000 |

### ETFs, ETNs and Leveraged Products With a Trading Unit of One

| Price per share (JPY) | Tick size (JPY) |
|---|---|
| up to 10,000 | 1 |
| up to 30,000 | 5 |
| up to 100,000 | 10 |
| up to 300,000 | 50 |
| up to 1,000,000 | 100 |
| up to 3,000,000 | 500 |
| up to 10,000,000 | 1,000 |
| up to 30,000,000 | 5,000 |
| over 30,000,000 | 10,000 |

### Other Issues

| Price per share (JPY) | Tick size (JPY) |
|---|---|
| up to 3,000 | 1 |
| up to 5,000 | 5 |
| up to 30,000 | 10 |
| up to 50,000 | 50 |
| up to 300,000 | 100 |
| up to 500,000 | 500 |
| up to 3,000,000 | 1,000 |
| up to 5,000,000 | 5,000 |
| up to 30,000,000 | 10,000 |
| up to 50,000,000 | 50,000 |
| over 50,000,000 | 100,000 |

**Applicability is announced per issue.** TSE publishes "Handling of Tick Sizes
from *date* onward" notices listing the issues entering or leaving the TOPIX500
tick size table on a given date. Do not infer the applicable table from an index
constituent snapshot; use the published applicability list.

## Daily Price Limits (制限値幅)

TSE limits the intraday price range in **absolute yen**, keyed to the base price
(基準値段) — normally the previous day's closing price or the last special
quote. Band bounds are **exclusive** (「100円未満」), the opposite convention to
the tick table above.

| Base price (JPY) | Daily price limit (JPY) |
|---|---|
| less than 100 | ±30 |
| less than 200 | ±50 |
| less than 500 | ±80 |
| less than 700 | ±100 |
| less than 1,000 | ±150 |
| less than 1,500 | ±300 |
| less than 2,000 | ±400 |
| less than 3,000 | ±500 |
| less than 5,000 | ±700 |
| less than 7,000 | ±1,000 |
| less than 10,000 | ±1,500 |
| less than 15,000 | ±3,000 |
| less than 20,000 | ±4,000 |
| less than 30,000 | ±5,000 |
| less than 50,000 | ±7,000 |
| less than 70,000 | ±10,000 |
| less than 100,000 | ±15,000 |
| less than 150,000 | ±30,000 |
| less than 200,000 | ±40,000 |
| less than 300,000 | ±50,000 |
| less than 500,000 | ±70,000 |
| less than 700,000 | ±100,000 |
| less than 1,000,000 | ±150,000 |
| less than 1,500,000 | ±300,000 |
| less than 2,000,000 | ±400,000 |
| less than 3,000,000 | ±500,000 |
| less than 5,000,000 | ±700,000 |
| less than 7,000,000 | ±1,000,000 |
| less than 10,000,000 | ±1,500,000 |
| less than 15,000,000 | ±3,000,000 |
| less than 20,000,000 | ±4,000,000 |
| less than 30,000,000 | ±5,000,000 |
| less than 50,000,000 | ±7,000,000 |
| 50,000,000 or more | ±10,000,000 |

**This is not a percentage band.** The implied percentage is roughly 30% at the
bottom of the table, falls to roughly 15–17% in the JPY 5,000–10,000 region,
and rises again to 25% in places. Any model of the form "±X% of the previous
close" will be wrong in both directions at different price levels.

**Broadening.** For domestic stocks, TSE broadens the limit from the following
business day (the third business day) if, for two consecutive business days,
either (1) the price reached the upper (or lower) limit, no closing auction at
the limit price was carried out, and volume was zero; or (2) no shares traded
until the end of the afternoon session, but shares traded at the afternoon
closing auction at the limit with bids (or offers) remaining at the limit. For
ETFs, ETNs and leveraged products, closing the auction at the limit price is
sufficient to broaden the limit from the next business day. Multiply-listed
foreign issues are excluded from the broadening operation. Affected issues are
published by TSE in Market News. **The engine does not model broadening** — the
widened figure cannot be derived from the schedule, so pass it explicitly via
`daily_price_limit_override_jpy`.

**No published floor.** For base prices below JPY 30 the published table yields
a negative theoretical lower bound. TSE does not publish a floor in this table,
so the engine reports the bound unclamped; the effective floor is whatever your
gateway enforces. Verify with your broker before relying on it.

## Announced Changes — Verify Before They Bite

- **1 March 2027 — tick size regime change.** TSE replaces the current index
  classification approach (is the issue a TOPIX500 constituent?) with a
  **liquidity-based** approach, assigning tick sizes from each issue's
  Spread-to-Tick Ratio (STR). The revised tables are published on the JPX tick
  size page. `JpxStockExchangeApiEngine` accepts a `tick_schedules` override so
  the new tables can be adopted without editing the module, but the *selection
  logic* will also need to change from a table name to an STR-derived tier.
- **12 October 2027 — random closing.** TSE plans to randomise the time of the
  afternoon closing Itayose within a window each business day, to deter gaming
  of the closing price. This does not affect tick, unit or limit validation,
  but it does affect any scheduler that assumes a deterministic 15:30 close.

## Not Modelled Here

Trading halts and suspensions; special quotes (特別気配) and their renewal
price ranges; the pre-opening and closing auction phases; short-sale price
restrictions; the order-to-trade ratio and other participant-level controls;
ToSTNeT off-auction trading; and any credit, margin or position check. These
are enforced by arrowhead and by your trading participant, not by this engine.

## Sources

- JPX, *Tick Size | Trading Rules of Domestic Stocks* (three tick tables, band
  inclusivity, 1 March 2027 STR change; updated 6 Aug 2026):
  <https://www.jpx.co.jp/english/equities/trading/domestic/07.html> —
  Japanese original (「以下」 bounds):
  <https://www.jpx.co.jp/equities/trading/domestic/07.html>
- JPX, *Daily Price Limits | Trading Rules of Domestic Stocks* (absolute-yen
  schedule, broadening conditions; updated 24 Aug 2026):
  <https://www.jpx.co.jp/english/equities/trading/domestic/06.html> —
  Japanese original (「未満」 bounds):
  <https://www.jpx.co.jp/equities/trading/domestic/06.html>
- JPX, *Trading Unit | Trading Rules of Domestic Stocks* (100 shares for
  domestic stocks, standardised 1 Oct 2018):
  <https://www.jpx.co.jp/english/equities/trading/domestic/03.html>
- JPX, *Overview | Trading Rules of Domestic Stocks* (session and order
  acceptance times): <https://www.jpx.co.jp/english/equities/trading/domestic/01.html>
- JPX SICC, *Securities Codes will Include Letters* (positions 2 and 4, the 19
  permitted letters, assignment order, `130A`):
  <https://www.jpx.co.jp/english/sicc/code-pr/index.html>
- JPX, *Services (arrowhead)* (arrowhead4.0 live 5 Nov 2024, fourth
  generation): <https://www.jpx.co.jp/english/systems/equities-trading/01.html>
- JPX, *Strengthening the Functions of the Cash Equity Market* (trading-hours
  extension and closing auction from 5 Nov 2024; random closing planned for
  12 Oct 2027):
  <https://www.jpx.co.jp/english/equities/trading/strengthening/index.html>
- JPX, *Handling of Tick Sizes from May 18 onward* (example of the per-issue
  TOPIX500 tick table applicability notices; cites Rule 14, Paragraph 3,
  Item 1-b of the Business Regulations):
  <https://www.jpx.co.jp/english/news/1030/20260514-01.html>
