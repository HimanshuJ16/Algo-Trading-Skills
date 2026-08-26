# Standards for JSE Equity Market Integration

Source of record: JSE Limited, *Volume 00E — Trading and Information Overview for
Equity Market*, **v4.09 (28 May 2026)**, the contract specification for the JSE
equity market on the LSEG Millennium Exchange platform. Tables below are
transcribed from that document and cross-checked against the JSE Trading
Operations webinar *JSE @ Your Service 6 — Circuit Breakers, Price Bands &
Self-Match Prevention* (25 June 2026). Verified 25 August 2026.

| Metric | Engineering Standard |
|---|---|
| Trading Platform | JSE equity market runs on LSEG Millennium Exchange (MillenniumIT), migrated under the JSE's Integrated Trading and Clearing (ITaC) programme. |
| Trading Currency | **ZAC (South African Cents)**. "The trading currency for the instrument... will be ZAC (South African Cents) for the JSE markets." ZAR 1 = 100 ZAC. |
| Tick Size | **1 for every instrument.** "Tick Size: The minimum possible price/price increment which can be used for an order. This is set to 1 for the JSE and NSX markets." There is **no** price-tiered tick ladder on the JSE equity market. |
| Limit Price | "This value should be greater than zero and a multiple of the instrument's 'tick' size" — i.e. a whole number of ZAC. |
| Lot Size | **1 for every instrument.** Order Quantity "should be a whole number that is greater than zero and must be a multiple of the instrument's Lot Size". |
| Maximum Order Size | $99{,}999{,}999$ shares, identical across every JSE and NSX equity segment. |
| Instrument Symbol | "The JSE alpha code assigned to the instrument." Volume 00E publishes **no** length or character rule. Codes are alphanumeric: `NPN`, `AGL`, `S32` (South32), `ETFSWX`. |
| Price Bands | ZA01 only, $\pm 90\%$ of the **static** reference price. Orders outside are **rejected on entry**. |
| Circuit Breakers | Per segment **and** per session. A breach triggers a Volatility Auction Call session — it does **not** reject the order. |
| Half-tick (0.5 ZAC) | System-applied price improvement for pegged hidden order executions only. Not a submittable order price. |

## Trading Segments (Volume 00E s4)

| Segment | Name | Contents | Max Order Size |
|---|---|---|---|
| ZA01 | Top Companies | Top 40 constituents; JSE/UK dual listed | 99,999,999 |
| ZA02 | Medium Liquid | Liquidity-rated 1/2 non-Top-40; debentures; preference shares; Kruger Rands | 99,999,999 |
| ZA03 | Less Liquid | Remaining liquidity-rated 1/2/3 instruments; nil paid letters; AltX; BEE | 99,999,999 |
| ZA04 | Specialist Products | Warrants; investment products; other securities | 99,999,999 |
| ZA06 | Exchange Traded Products | ETFs; ETNs | 99,999,999 |
| ZA11 / ZA12 | NSX Local / NSX-JSE Dual Listed | Namibian Stock Exchange instruments | 99,999,999 |

## Reference Prices

- **Static Reference Price** — the previous day's closing price or the last
  auction price. Anchors the ZA01 price band and the static circuit breaker.
- **Dynamic Reference Price** — the last traded price. Anchors the dynamic
  circuit breaker.
- Volume 00E defines the general *Reference Price* as "the last auction or
  automated trade price or the previous closing price, whichever is the most
  recent, or in the absence of [either], a price as determined by the JSE".

## Price Bands (order rejection)

> "Instruments trading in the Equity Market (ZA01) will be subject to Price
> Bands. These are designed to prevent far-off orders from disproportionately
> influencing the indicative Auction Price (IAP) and to maintain orderly price
> formation. Orders entered outside of these defined thresholds will be rejected
> by the trading system."

| Segment | PB Outer Limit |
|---|---|
| ZA01 | $\pm 90\%$ from the static reference price |

No price band is published for ZA02, ZA03, ZA04, ZA06 or the NSX segments. The
engine therefore enforces none there — a narrower client-side band would reject
orders the JSE accepts. Price bands also do **not** restrict the execution of
off-book (reported) trades; the trading system flags a far-away reported trade
via the `PBBreached` field on the Trade Capture Report rather than blocking it.

## Circuit Breakers (volatility auction, NOT rejection)

> "Circuit breaker tolerance is defined as a percentage in relation to the
> Static Reference Price and / or Dynamic Reference Price. If the difference
> between the price of the next trade and the Static Reference Price or Dynamic
> Reference Price is **equal or greater** than that permitted by the circuit
> breaker tolerance defined for the relevant session the instrument will
> automatically be moved into a Volatility Auction Call session."

Two breakers exist, "the more restrictive of which will always take precedence".
The threshold is inclusive: exactly at the tolerance is a breach.

Tolerances are stated as **static % / dynamic %** (Volume 00E v4.09 s8.6.5):

| Trading Session | ZA01 | ZA02 | ZA03 | ZA04 | ZA06 |
|---|---|---|---|---|---|
| Opening Auction Call | 8 / 6 | 20 / 10 | 50 / 25 | 70 / 50 | 20 / 8 |
| Continuous Trading | 10 / 3 | 15 / 5 | 50 / 25 | 70 / 50 | 15 / 5 |
| Intraday Auction Call | N/A | N/A | 50 / 25 | N/A | N/A |
| Closing Auction Call | 4 / 2 | 10 / 5 | 50 / 25 | N/A | 20 / 8 |
| FCO Auction Call | 15 / 2 | 30 / 4 | 50 / 25 | 70 / 50 | 30 / 4 |
| Re-Opening Auction Call | 8 / 6 | 20 / 10 | 50 / 25 | 70 / 50 | 20 / 8 |

`N/A` means the JSE applies no circuit breaker to that segment/session pair —
not that the tolerance is unlimited in some other sense. The JSE publishes no
EQM tolerance table for the NSX segments (ZA11/ZA12).

**These values change.** Volume 00E carries the footnote "These values may be
reviewed by the JSE from time to time", and the document's own change log
records amendments in 2014, Nov 2018, Mar 2019, and May 2026 (ZA03 and ZA04).
The 25 June 2026 webinar deck still showed the pre-v4.09 `N/A` cells for ZA04
continuous trading and the ZA03/ZA04 FCO auction; the v4.09 specification is the
later and more authoritative of the two and is what the engine encodes.
Re-verify against the current Volume 00E before relying on these constants in
production.

## Consequences of a Circuit Breaker Breach

- Instrument moves to a Volatility Auction Call session (5 minutes).
- The remainder of the aggressing order is **added to the order book** if its
  time in force is persistent, or **expired** if it is not.
- IOC and Market orders (including Market IOC) expire immediately, with the
  execution report reason `Expired (circuit breaker breached)`.
- Orders carrying the `EHL` attribute are expired at the start of the session.
- A `Circuit Breaker Breach` news message is published, and Drop Copy Gateway
  users can opt into real-time circuit breaker alerts for their own traders.

Because the JSE evaluates the price of the **next trade**, a client-side engine
can only approximate the check using the order's limit price. That is a
conservative proxy: an aggressing buy executes at or below its limit, an
aggressing sell at or above it, so the limit price bounds the worst-case
deviation the order can cause. A resting order that never aggresses may never
trade at that price at all.

## Self-Match Prevention (out of scope for this engine)

SMP is optional, enabled per CompID with an exchange-issued unique key, and
available in Continuous Trading and the Closing Price Cross only. On a
self-match the **resting** order is cancelled. Circuit breaker checks are
performed **before** SMP is applied — market protection takes priority. This
engine does not model SMP; see `exchange-self-match-prevention-configuration`.

## Sources

- JSE Limited, *Volume 00E — Trading and Information Overview for Equity Market*
  v4.09, 28 May 2026 (tick size, lot size, ZAC currency, Maximum Order Size,
  trading segments, s8.6.5 Circuit Breakers and Price Bands, s8.6.6 Self-Match
  Prevention):
  <https://clientportal.jse.co.za/Content/JSE%20Contract%20Specification%20Items/Volume%2000E%20-%20Trading%20and%20Information%20Overview%20for%20Equity%20Market%20v4.09.pdf>
- JSE Limited, *JSE @ Your Service 6 — Circuit breakers, Price Bands, and
  Self-Match Prevention*, JSE Trading Operations, 25 June 2026 (price band vs
  circuit breaker distinction, EQM circuit breaker table, order-handling
  consequences of a breach):
  <https://clientportal.jse.co.za/Content/Trading%20Webinars/JSE%20@%20Your%20Service%206-%20Circuit%20Breakers,%20Price%20Bands%20&%20Self-Match%20Prevention.pdf>
- JSE Limited, Service Hotline 062/2020, *Updated: Summary of JSE Circuit
  Breakers and Auctions*, 23 March 2020 (static/dynamic reference price
  definitions; volatility auction duration):
  <https://clientportal.jse.co.za/Content/JSEHotlinesItems/JSE%20Service%20Hotline%2006220%20EQM%20and%20EDM%20-%20Upgrade%20Summary%20of%20JSE%20Circuit%20Breakers%20and%20Auctions.pdf>
- JSE Limited, instrument page for South32 Limited, alpha code `S32` (evidence
  that JSE alpha codes are alphanumeric, not letters-only):
  <https://www.jse.co.za/jse/instruments/3851>
- JSE Client Portal, *Trading and Market Data Documentation* (index of the
  current Volume 00E, Native Trading Gateway and FIX 5.0 SP2 gateway specs):
  <https://clientportal.jse.co.za/technical-library/trading-and-market-data-documentation>
- Mondo Visione, *Johannesburg Stock Exchange Successfully Migrates Equity & FX
  Derivatives To LSEG Technology Platform* (secondary source for the Millennium
  Exchange platform lineage):
  <https://mondovisione.com/media-and-resources/news/johannesburg-stock-exchange-successfully-migrates-equity-and-fx-derivatives-to-lse-2019612/>
