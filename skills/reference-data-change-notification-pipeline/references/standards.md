# Standards & Sources for Reference Data Change Notification Pipeline

## There is no published detection-latency standard for instrument reference data

Rules of the form changes "MUST be detected within 5 minutes of snapshot update" and
consumers "MUST be notified within 1 minute of detection" are not standards. **No
regulator, exchange, standards body or vendor publishes either figure.** Presenting an
unsourced number as a MUST makes an arbitrary choice look like a compliance obligation.

The detection cadence you actually need is set by the publication cadence of the source
and by the deadline of whatever consumes the data — for example, a MIC list that changes
monthly does not need five-minute polling, whereas an intraday trading-status field
does. Derive it; do not inherit it.

## Identifier stability — the fact this skill is built on

| Claim | Source | Verified | Impact here |
|---|---|---|---|
| Meta Platforms' Class A common stock "will begin trading on NASDAQ under the ticker symbol 'META' prior to market open on June 9, 2022", and "its CUSIP number will remain unchanged." | Meta Platforms press release, 31 May 2022 ([investor.atmeta.com](https://investor.atmeta.com/investor-news/press-release-details/2022/Meta-Platforms-Inc.-to-Change-Ticker-Symbol-to-META-on-June-9/default.aspx); filed as Exhibit 99.1 to the company's Form 8-K) | Quoted from the primary source | A ticker change does **not** by itself imply an identifier change. `symbol` and `cusip` must be diffed independently; reacting to a rename by re-resolving every identifier is unnecessary churn, and watching only ISIN misses the rename entirely. |
| An ISIN is 12 characters: an ISO 3166-1 alpha-2 country code, a 9-character NSIN, and a check digit. In North America the numbering agency is the CUSIP organization, so a CUSIP becomes an ISIN by prefixing the country code and appending a check digit. When the NSIN changes, the ISIN changes. | ISO 6166; structure per [ISO 6166 overview](https://en.wikipedia.org/wiki/ISO_6166). ANNA is the ISO 6166 Maintenance Agency. | Secondary source for the structure; the ISO standard itself is paywalled | Combined with the row above: because Meta's CUSIP was unchanged, its US ISIN was unchanged. ISIN is therefore *derived* stability, not independent stability — a CUSIP change propagates into the ISIN. |
| "FIGIs... never change, are never reused, and are permanent, allowing users to maintain data integrity over a time period of multiple corporate actions and changes." "Tickers and other market identifiers can change as a result of corporate actions, which often results in the need to remap content." | [OpenFIGI — Features](https://www.openfigi.com/about/features) (FIGI is an Object Management Group open standard) | Quoted from the standard's own site | The recommended primary key for an instrument master. Because a FIGI is never reused, it is also immune to the ticker-recycling hazard below. |

**Ticker reuse.** Exchanges may reassign a freed ticker to a different company after a
delisting or rename. Published, citable waiting periods per venue were **not found**
during this skill's research, so no duration is asserted here — verify against the
listing venue's own symbol-reservation policy before relying on one. The engineering
consequence stands regardless: a join on ticker without a date qualifier can splice two
different issuers into one series.

## Change publication carries an effective date

| Claim | Source | Impact here |
|---|---|---|
| "The MIC list is published on the second Monday of the month or the following business day if it falls on a public holiday in the country of the ISO 10383 Registration Authority (RA)." "The modifications become effective on the fourth Monday of the month." The RA for ISO 10383 is S.W.I.F.T. SC. | [ISO 20022 — Market Identifier Codes](https://www.iso20022.org/market-identifier-codes) (the Registration Authority's publication page) | A change is **visible** roughly two weeks before it is **effective**. This engine reports what a snapshot contains; it does not know an effective date. Never wire `CRITICAL` detection straight into a routing-table swap — gate the application of the change on its effective date. |
| Meta's ticker change was announced 31 May 2022 and effective 9 June 2022. | Press release above | Same pattern in equities: announcement precedes effect by days. |

## Regulatory touchpoints — jurisdiction-scoped, read before citing

These are **context**, not obligations this engine discharges. None of them mandates a
change-detection pipeline; they establish why instrument reference data is treated as a
controlled record in the jurisdictions named.

| Jurisdiction | Instrument | What it covers | Applicability caveat |
|---|---|---|---|
| EU | Commission Delegated Regulation (EU) [2017/585](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0585) (RTS 23), supplementing MiFIR (Regulation (EU) No 600/2014) | Data standards and formats for **financial instrument reference data**, and the arrangements by which trading venues and systematic internalisers submit it to competent authorities and ESMA (published via FIRDS). Reference data is submitted daily, by 21:00 CET, for instruments admitted to trading or traded before 18:00 CET that day. | Binds **trading venues and systematic internalisers**, not a buy-side firm's internal instrument master. The full consolidated text on EUR-Lex could not be retrieved during this skill's research, so no article-level citation is asserted; verify article numbers and current timings against the consolidated text before quoting them in a compliance document. The UK operates an assimilated version post-Brexit — do not assume the EU and UK texts are identical. |
| US | SEC Rule 15c3-5 (Market Access Rule) | Requires broker-dealers with market access to maintain risk-management controls, including pre-set credit and capital thresholds, reasonably designed to prevent erroneous orders. | Cited here only because those controls are evaluated **against reference data** — a stale lot size or multiplier degrades a control that is itself mandatory. The rule says nothing about reference-data change detection. See `sec-rule-15c3-5-risk-controls-us`. |

## Engineering conventions this module actually enforces

These are decisions made in this repository, not external standards. They are testable
and are covered by the skill's unit tests.

| Convention | Rationale |
|---|---|
| Identity/routing fields (`symbol`, `exchange`, `mic`, `status`, `currency`, `isin`, `cusip`, `sedol`, `figi`) classify as `CRITICAL`. | A stale value misroutes an order to the wrong instrument or venue. |
| Order-construction fields (`lot_size`, `tick_size`, `contract_multiplier`, `min_order_qty`, `max_order_qty`, `price_precision`, `quantity_precision`, `expiry`, `strike`, `settlement_date`) classify as `WARNING`. | A stale value produces a rejected or mis-sized order against the correct instrument. |
| Field-name matching is case-insensitive. | A vendor publishing `Symbol` must not be silently downgraded to `INFO`. |
| A removal is floored at `WARNING` regardless of field. | A vendor dropping a column is a data-quality incident in its own right. An addition is not escalated. |
| Absence and `None` are tracked separately (`old_present` / `new_present`). | They are different facts: "stopped publishing" versus "published as unknown". |
| A value whose comparison raises is treated as **changed**. | An uncomparable value is not evidence of stability. |
| Notification delivery is attempted once per (consumer, notification), failures isolated and returned. | One dead sink must not silence the others; retry policy belongs to the transport, which alone knows whether the sink is idempotent. |
| `enabled=False` reports `ENGINE_DISABLED`, never `NO_CHANGES`. | "We did not look" is not "there was nothing to find". |
