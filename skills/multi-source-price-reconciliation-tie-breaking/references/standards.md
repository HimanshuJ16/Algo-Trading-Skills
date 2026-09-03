# Standards — multi-source-price-reconciliation-tie-breaking

## Engineering standards

These are **this skill's engineering requirements**, not regulatory mandates. No
regulator, exchange or vendor publishes a cross-vendor reconciliation rule; see
*Not verified here* at the foot of this file.

| Rule | Requirement |
|---|---|
| Usable vs corroborated | A result MUST carry two separate claims: a `status` (was a price produced, and how) and `is_cross_verified` (did two or more independent sources agree within tolerance). A tie-broken price is usable and uncorroborated; these are not the same thing. |
| Input validation | Non-finite (`NaN`, `±inf`) and non-positive prices MUST be rejected before entering the filter. `NaN` compares `False` against every threshold, so an unchecked `NaN` is classified valid and becomes the canonical price. |
| Instrument identity | Every quote's `symbol` MUST match the batch symbol. Cross-instrument reconciliation yields a confident price for the wrong security. |
| Source independence | Duplicate `vendor_id` values MUST be rejected. One vendor counted twice biases the median and the composite, and reports a quorum of independent sources that does not exist. |
| Attribution quorum | Median-distance outlier rejection MUST NOT be applied to fewer than three usable quotes. With two, the median is equidistant from both, so no outlier is attributable. |
| Filter deadlock | Where the median filter rejects every quote (clusters straddling the median), the result MUST be reported as unresolved and unverified. The rejection record MUST NOT be silently reset to zero outliers. |
| Filter denominator | The agreement tolerance MUST be evaluated against the median of the **surviving** quotes, not the median of all quotes including those just rejected. |
| Tick floor | Both the deviation bound and the agreement tolerance MUST be floored at one minimum price increment expressed in percent. A tolerance below one tick makes every lawful one-tick disagreement a breach. |
| Boundary behaviour | Callers MUST NOT calibrate to the exact threshold. Decimal thresholds are not exactly representable in binary floating point, so equality at the boundary is not reproducible across differently-derived inputs. |
| Clock discipline | Staleness MUST be measured on a single local receipt clock against an explicit `as_of` reading. Vendor and exchange event timestamps MUST NOT be used — the resulting "age" contains inter-vendor clock skew. |
| Blackout detection | `as_of` MUST NOT default to the freshest quote in the batch. That anchor makes the newest quote zero seconds old by construction and can never detect the outage where every vendor has stopped updating. |
| Staleness ordering | Stale quotes MUST be excluded before the median, the outlier filter and the composite. A frozen quote inside the deviation bound is structurally invisible to outlier logic. |
| No price | Where every quote is stale, the result MUST carry no price rather than the last known value. |
| Determinism | Tie-breaker keys MUST form a total order (ending in `vendor_id`), and composite summation MUST run in a pinned order. Otherwise the canonical price depends on the caller's iteration order and is irreproducible. |
| Configuration | An unrecognised tie-breaker method MUST raise. Falling back to "first quote in the list" converts a typo into order-dependent pricing. |
| Weights | `reliability_weight` MUST be strictly positive. Zero weights collapse the composite denominator; negative weights place the composite outside the quote range. |
| Precision | Output rounding MUST be configurable and default to none. A hardcoded four-decimal round returns `0.0` for a token quoted at 0.00002181. |
| Blending | A composite computed across quotes that breach tolerance MUST be flagged unverified. It is a manufactured price that no vendor quoted. |

## Default parameters

**Starting points for calibration, not standards.** Re-derive each from recorded
quote history for the specific vendor set and instrument.

| Parameter | Default | Notes |
|---|---|---|
| `max_deviation_pct` | 0.01 (1%) | Distance from the median at which a quote is attributed as an outlier. Must be wide enough to survive genuine fast markets and narrow enough to catch a decimal-shifted or stale-cache quote. |
| `tolerance_pct` | 0.0005 (5 bps) | Spread within which survivors are treated as agreeing. **Narrower than one $0.01 tick for any NMS stock quoted between $1.00 and $20.00** — floor it. |
| `min_absolute_tolerance` | 0.0 | The tick floor, in price units, applied to *both* bounds. Set it to the instrument's minimum price increment. Left at 0.0 the percentage bounds apply unfloored. |
| `max_quote_age_seconds` | `None` (off) | Enable it. When set, `as_of` becomes mandatory. Must exceed the instrument's own quiet periods or an illiquid symbol is permanently stale between genuine ticks. |
| `min_sources_for_outlier_filter` | 3 | Cannot be lowered — see the attribution quorum rule above. |
| `price_precision` | `None` (no rounding) | Set per instrument from venue metadata. `None` is the crypto-safe default. |
| `tie_breaker_method` | `PRIORITY` | Records an operator preference, not a detection of which vendor was wrong. |

## Verified facts

| Fact | Detail | Source |
|---|---|---|
| Tick size bounds the tolerance | Rule 612 sets the minimum pricing increment for NMS stocks at $0.01 for quotations at or above $1.00 and $0.0001 below $1.00. The September 2024 amendments add a $0.005 increment for tick-constrained stocks, but compliance has been deferred by temporary exemptive relief — most recently, as of this writing, to the **first business day of November 2027**. Confirm the current status before relying on either increment. | [17 CFR § 242.612 (Cornell LII)](https://www.law.cornell.edu/cfr/text/17/242.612); [SEC, *Regulation NMS: Minimum Pricing Increments, Access Fees, and Transparency of Better Priced Orders*, Release No. 34-101070 (Sept. 18, 2024)](https://www.sec.gov/files/rules/final/2024/34-101070.pdf); [Order granting temporary exemptive relief, FR doc. 2026-11997 (June 15, 2026), Release No. 34-105656](https://www.federalregister.gov/documents/2026/06/15/2026-11997/order-granting-temporary-exemptive-relief-pursuant-to-section-36a1-of-the-securities-exchange-act-of) |
| Vendors legitimately differ | In adopting the Market Data Infrastructure rule the SEC described the two-tiered structure in which participants who pay for exchange proprietary depth-of-book feeds and the associated connectivity "receive more content-rich data faster" than those who do not. Cross-vendor divergence is therefore expected behaviour, not prima facie corruption, and a feed that is merely *ahead* is not an outlier. | [SEC, *Market Data Infrastructure*, Release No. 34-90610 (Dec. 9, 2020)](https://www.sec.gov/files/rules/final/2020/34-90610.pdf) |
| Two sources cannot attribute an outlier | Arithmetic, not policy: for quotes `a < b` the median is `(a+b)/2`, so `|a − m| = |b − m| = (b−a)/2`. Any symmetric distance test passes both or fails both. Verified by test. | Reference implementation, `test_two_sources_are_never_filtered_because_neither_can_be_attributed`. |
| A median filter can reject everything | For an even number of quotes split into two clusters (100, 100, 105, 105) the median falls in the gap and every quote exceeds the bound. For an odd count the median *is* a quote, so at least one always survives. | Reference implementation, `test_bimodal_split_reports_a_deadlock_rather_than_zero_outliers`. |

## Regulatory context

Nothing in this skill is legal, accounting or compliance advice, and **none of the
provisions below regulates real-time cross-vendor price reconciliation directly**.
They are the obligations that a multi-source pricing control most often supports,
listed with their actual scope so they are not over-claimed.

| Jurisdiction | Provision | What it actually says | Applies to |
|---|---|---|---|
| EU | **CRR Article 105(8)** (Regulation (EU) No 575/2013) | Institutions must perform **independent price verification** in addition to daily marking to market or marking to model. Verification of market prices and model inputs must be performed by a person or unit independent from those who benefit from the trading book, **at least monthly**, or more frequently depending on the nature of the market or trading activity. Where independent pricing sources are unavailable or more subjective, prudent measures such as valuation adjustments may be appropriate. | EU credit institutions and CRR-scope investment firms holding trading-book positions. Mandatory — but it is a **periodic valuation control**, not a tick-level feed requirement. This engine is a mechanism that can feed such a process; it does not by itself discharge the obligation. |
| US | **17 CFR 270.2a-5(a)(4)** ("Evaluate pricing services") | Determining fair value in good faith requires "[o]verseeing pricing service providers, if used, including establishing the process for approving, monitoring, and evaluating each pricing service provider and initiating price challenges as appropriate." A cross-vendor divergence report is a natural trigger for such a challenge. | Registered investment companies and their designated valuation designees. **Not** a rule about a proprietary trading firm's feed handler. |
| US | **17 CFR 270.2a-5(c)** (readily available market quotations) | "A market quotation is readily available only when that quotation is a quoted price (unadjusted) in active markets for identical investments that the fund can access at the measurement date, provided that a quotation will not be readily available if it is not reliable." Reliability is therefore part of the definition, not an optional overlay. | As above. |
| US | **17 CFR 242.612** (Reg NMS Rule 612) | Minimum pricing increment for NMS stocks. Referenced here **only** as the arithmetic floor for the agreement tolerance; it imposes no reconciliation obligation. | Exchanges, ATSs, vendors, brokers and dealers quoting NMS stocks. |

## Not verified here

No source was located for any of the following, and none should be presented to a
reviewer as an external requirement:

- A regulator-, exchange- or vendor-published **cross-vendor divergence tolerance**.
- A published **outlier deviation threshold** for vendor price validation.
- A published **maximum quote age** or staleness timeout for a trading firm.
- A published **minimum number of independent pricing sources** for a non-fund,
  non-CRR trading firm.
- Any mandate that a proprietary trading firm run multi-vendor price reconciliation
  at all.

Treat every parameter in this skill as an engineering choice you must be able to
justify from your own recorded data, not as a compliance floor.

## Behaviour changes in 2.0.0

The 1.x engine's defects and the fixes are enumerated in `references/workflows.md`.
Three changes are visible at the API boundary:

- `canonical_price` is now `Optional[float]` and is `None` when every quote is stale.
- `status` now takes four values, not one. Only `RECONCILIATION_SUCCESS` implies
  corroboration; check `is_cross_verified` rather than truth-testing the status string.
- Output rounding is off by default. Set `price_precision=4` to restore 1.x output.

## Cross-references

- Two-source arbitration with latency-skew handling: `market-data-feed-arbitration-across-vendors`
- Vendor failover and promotion policy: `vendor-outage-fallback-data-source-hierarchy`
- Receipt-clock discipline: `clock-skew-correction-for-tick-timestamps`
- Escalation once a price is untrusted: `graduated-response-to-data-quality-degradation`
- Which source is authoritative for reference data: `reference-data-golden-source-designation`
