# Standards — position-limit-reporting-cftc-large-trader

Jurisdiction: **United States, CFTC only.** Nothing here applies to EU, UK,
Indian or other regimes. MiFID II commodity derivative position limits (Article
57) and their reporting are a separate framework with different arithmetic and
are not covered by this skill.

Nothing in this skill hard-codes a reporting level or a limit level. Both tables
are amended over time; resolve them at the evaluation date and archive what you
used, or the audit is not reproducible.

## Regulatory basis (verified against primary sources)

| Claim | Source | Status / applicability |
|---|---|---|
| A "reportable position" is any open contract position that at the close of the market on any business day "equals or exceeds the quantity specified in § 15.03" in either "any one future of any commodity on any one reporting market" or "long or short put or call options that exercise into the same futures contract" | [17 CFR 15.00(p)(1)](https://www.law.cornell.edu/cfr/text/17/15.00) | Mandatory definition. Fixes the test as **gross, per contract month, per side, inclusive (`>=`)**. |
| A "special account" is "any commodity futures or option account in which there is a reportable position" | 17 CFR 15.00(r) | Mandatory definition. |
| Reporting levels are set in the § 15.03(b) table; "Crude Oil, Sweet" is listed at **350 contracts** | [17 CFR 15.03](https://www.law.cornell.edu/cfr/text/17/15.03) | Mandatory. Level verified. Note the table lists "S&P 500 Stock Price Index" at 1,000 — an "E-mini S&P 500" line does not appear under that name; check the current table before assuming a level for a specific contract. |
| "When a special account is reported for the first time, the futures commission merchant, clearing member, or foreign broker shall identify the special account to the Commission on Form 102" | [17 CFR 17.01(a)](https://www.law.cornell.edu/cfr/text/17/17.01) | Mandatory. **The filer is the carrying firm, not the position-holding entity, and it is a first-report/change event, not a daily trader filing.** |
| Form 102A covers position-based special accounts in futures; Form 102B covers volume threshold accounts; Form 102S covers swaps consolidated accounts. "Futures commission merchants (FCMs), clearing members, foreign brokers, and certain reporting markets may have reporting obligations on Form 102A." | [CFTC, Ownership and Control Reports final rule fact sheet](https://www.cftc.gov/sites/default/files/idc/groups/public/@newsroom/documents/file/ocr_factsheet_final.pdf) | Official CFTC publication. Confirms the A/B/S naming (the CFR text says "Form 102"). |
| A trader holding a reportable position files Form 40 "after a special call upon such trader by the Commission or its designee" | [17 CFR 18.04(a)](https://www.law.cornell.edu/cfr/text/17/18.04) | Mandatory. This — not Form 102A — is the trader-side obligation, and it is call-driven. |
| Part 150 prohibits holding or controlling positions in the spot month, and in a single month or all-months-combined, "net long or net short, in excess of the levels specified by the Commission" | [17 CFR 150.2](https://www.law.cornell.edu/cfr/text/17/150.2) | Mandatory. Fixes the limit test as **net, per limit type, exclusive (`>`)**. |
| Federal limits apply to 25 physically-settled core referenced futures contracts; each spot-month limit is "set at or below 25% of estimated deliverable supply"; "only the Legacy Contracts and their associated Referenced Contracts are subject to federal speculative position limits outside of the spot month" | [CFTC, Position Limits for Derivatives](https://www.cftc.gov/IndustryOversight/MarketSurveillance/SpeculativeLimits/index.htm) | Official CFTC page. **The reason `CFTCLimitSpec` carries three independently optional limits.** Nine legacy agricultural contracts have all three; the other sixteen have spot-month only. |
| Some spot-month limits step down within the spot month — the CFTC's published Live Cattle limit is "600 contracts at the close of trading on the first business day following the first Friday of the contract month; 300 contracts … prior to the last five trading days; and 200 contracts … prior to the last two trading days" | ibid. | Official. **A spot-month limit is not a constant across the spot month.** Re-resolve the level per business day. |
| Federal limit levels are published in Appendix E to Part 150 | [17 CFR Part 150, Appendix E](https://www.law.cornell.edu/cfr/text/17/appendix-E_to_part_150) | Mandatory. Authoritative location for the numeric levels. This skill does not reproduce them. |
| Aggregation: "all positions in accounts for which any person, by power of attorney or otherwise, directly or indirectly controls trading or holds a 10 percent or greater ownership or equity interest must be aggregated", subject to the exemptions in § 150.4(b) | [17 CFR 150.4](https://www.law.cornell.edu/cfr/text/17/150.4) | Mandatory. Both control **and** a 10% ownership interest trigger aggregation. Eight exemption categories exist (limited partners, independently operated owned entities, FCM discretionary accounts, independent account controllers, underwriting, broker-dealer activity, information-sharing restrictions, affiliate notice filings). |
| Bona fide hedging positions are exempt from Part 150 limits | 17 CFR 150.3 | Mandatory. **Applies to limits only.** § 15.00(p) contains no hedging carve-out, so a hedge position is still reportable — which is how this engine models it. |
| The Commission sunset the routine position-reporting requirements of Part 20 (large trader reporting for physical commodity swaps) — "clearing organizations, clearing members, and swap dealers will no longer be required to file the daily and event-based position reports" — effective upon Federal Register publication, 21 July 2026. "The recordkeeping and special-call provisions of Part 20" are retained. | [CFTC Release 9269-26, 17 July 2026](https://www.cftc.gov/PressRoom/PressReleases/9269-26) | **Current as of this writing.** Any material citing routine Part 20 position reports as a live obligation is out of date. Part 20 is *not* the OCR rule — OCR is Part 17. |
| The 2024 Part 17 final rules replace the enumerated data elements with an appendix, add a Part 17 Guidebook, and "remove the outdated 80-character data submission standard … replaced by a FIXML standard"; reporting firms comply "two years after publication in the Federal Register" | [CFTC Release 8902-24, 30 April 2024](https://www.cftc.gov/PressRoom/PressReleases/8902-24) | Mandatory for **reporting firms** (FCMs, clearing members, foreign brokers). Affects the format of the daily Part 17 large trader reports, not this engine's arithmetic. Relevant if you are the carrying firm. |

## Engineering standards enforced by this skill

| Concern | Standard |
|---|---|
| Aggregation level | Positions MUST be aggregated per legal entity across all accounts under common ownership/control (§ 150.4), and the engine MUST refuse a position attributed to a different entity or a different commodity rather than absorbing it. |
| Reporting test | MUST be gross, per `(contract_month, instrument_class)` bucket, each side independently, using `>=`. Sides MUST NOT be summed or netted; months MUST NOT be pooled; options MUST NOT be pooled with futures. |
| Limit test | MUST be net, per configured limit type, using strict `>`. |
| Absent limits | A limit that does not exist for the contract MUST be `None` and MUST NOT be tested, and the report MUST name it in `limits_not_tested`. Silently substituting zero or infinity is prohibited. |
| Configured-but-unrunnable controls | A configured `spot_month_limit` with no `spot_month` MUST raise. A control that silently does not run is worse than a refusal. |
| Level provenance | Levels MUST be caller-supplied and resolved at the evaluation date. No level may be hard-coded in the engine. |
| Double counting | The same `(account_id, contract_month, instrument_class)` supplied twice MUST raise. |
| Data integrity | Negative, non-finite, or non-numeric legs MUST raise. A negative short leg silently flips the net sign and turns a breach into a clean report. |
| Timing | Limit audits MUST run intraday (§ 150.2 prohibits *holding or controlling* an excess position). The reporting flag is only meaningful on an end-of-day snapshot (§ 15.00(p): "at the close of the market"). |

## Known limitations

- **No futures-equivalent conversion.** § 150.2 combines futures with futures-equivalent options and economically equivalent swaps. The engine performs no delta conversion; option positions must arrive already converted for the limit tests to be meaningful. Reporting buckets are counted in raw option contracts, which is what § 15.00(p)(1)(ii) tests.
- **No exemption adjudication.** `is_bona_fide_hedge` is asserted by the caller. Whether a position qualifies under § 150.3, and whether a § 150.4(b) aggregation exemption applies, are legal determinations outside this engine.
- **No swaps.** Economically equivalent swaps count toward Part 150 limits but are not modelled.
- **No exchange-set limits or accountability levels.** Outside the spot month, non-legacy contracts are governed by DCM rules, not federal limits.
- **No filing.** No Form 102/40 payload is generated and nothing is transmitted.
- **`contract_month` is an opaque string.** Inconsistent spelling across accounts silently splits a bucket and can hide a reportable position. Normalise upstream.
- **Contract counts are floats.** Contracts are integral; fractional inputs are accepted and reported to two decimals rather than rejected, since some upstream systems carry futures-equivalent fractions. Both boundaries are therefore exposed to floating-point accumulation: a book of fractional legs summing to a level "exactly" can land a fraction either side of it. Feed whole contracts wherever the source data is whole.
- **Aggregate figures and breach figures have different populations.** `aggregated_net_position` and the aggregated gross legs cover the whole book including bona fide hedges, because that is what the reporting test runs on. Each `LimitBreach.net_position` excludes hedges, because that is what § 150.3 leaves subject to limits. They will not tie out when `hedge_exempt_contracts_excluded` is non-zero; that is correct, not an inconsistency.

## Category

`Regulatory Compliance & Risk Controls` — see top-level `mappings/` directory.
