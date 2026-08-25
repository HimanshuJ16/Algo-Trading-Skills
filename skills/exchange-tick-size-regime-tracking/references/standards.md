# Standards — exchange-tick-size-regime-tracking

All regulatory statements below were verified on **2026-08-24**. Tick regimes change by
rule amendment and by exchange circular; re-verify before relying on any row.

## US NMS stocks — SEC Rule 612 (17 CFR 242.612)

Source: [17 CFR § 242.612, Minimum pricing increment](https://www.law.cornell.edu/cfr/text/17/242.612)
(Regulation NMS). Adopting release for the 2024 amendments: SEC Release No. 34-101070,
*Regulation NMS: Minimum Pricing Increments, Access Fees, and Transparency of Better
Priced Orders*, adopted 2024-09-18, effective 2024-12-09.

| Fact | Detail |
|---|---|
| What the rule constrains | Displaying, ranking, or **accepting** a bid or offer, an order, or an indication of interest in an NMS stock. |
| What it does **not** constrain | Execution prices. Sub-penny executions from price improvement or midpoint matching remain permissible. |
| Increment, price $\ge \$1.00$ | $\$0.01$ (default). |
| Increment, price $< \$1.00$ | $\$0.0001$. |
| Amended tick-constrained increment | $\$0.005$ where the Time Weighted Average Quoted Spread over the Evaluation Period is $\le \$0.015$. |
| Evaluation Periods | January–March and July–September. The **primary listing exchange** measures each NMS stock's TWAQS over the period; the resulting increment is assigned per symbol and operative for six months. |
| Newly listed stocks | Assigned the $\$0.01$ increment initially. |
| **Compliance status of the $\$0.005$ tier** | **Not operative.** Temporary exemptive relief granted 2025-10-31 (to November 2026) was extended by the Chairman's statement of **2026-06-11 to the first business day of November 2027**, covering Rules 600(b)(89)(i)(F), 610(c) and 612. |

Because the $\$0.005$ increment is a **per-symbol assignment**, it cannot be derived from
price. The engine therefore returns $\$0.01$ for prices $\ge \$1.00$ unless the caller
passes `tick_constrained=True` from real reference data.

Mechanics of the amendment (secondary, law-firm summaries of Release 34-101070):
[Sidley Austin](https://www.sidley.com/en/insights/newsupdates/2024/10/sec-adopts-rules-modifying-minimum-pricing-increments-access-fee-caps-and-order-transparency) ·
[Orrick](https://www.orrick.com/en/Insights/2024/09/SEC-Amends-Regulation-NMS-to-Provide-for-Half-Penny-Quoting-in-Securities-and-Reduction).

Sources for the compliance-date history:
[SEC press release 2025-130 (exemptive order, 2025-10-31)](https://www.sec.gov/newsroom/press-releases/2025-130-sec-issues-exemptive-order-regarding-compliance-certain-rules-under-regulation-nms) ·
[Chairman's statement, 2026-06-11](https://www.sec.gov/newsroom/speeches-statements/atkins-statement-minimum-pricing-increments-access-fee-caps-061126) ·
[Morrison Foerster client alert, 2026-06-12](https://www.mofo.com/resources/insights/260612-sec-proposes-landmark-rollback-of-core-regulation).

## EU shares, depositary receipts and ETFs — MiFID II RTS 11

Source: **Commission Delegated Regulation (EU) 2017/588** (RTS 11), Annex; ADNT
thresholds per Article 3. Reproduced from the exchange publications of the table:
[Wiener Börse — Tick size & liquidity band](https://www.wienerborse.at/en/trading/trading-information/tick-size/).

Liquidity bands are defined on the **average daily number of transactions (ADNT)**
published by ESMA / the relevant NCA and applied from the annual application date:

| Band | ADNT |
|---|---|
| 1 | $0 \le \text{ADNT} < 10$ |
| 2 | $10 \le \text{ADNT} < 80$ |
| 3 | $80 \le \text{ADNT} < 600$ |
| 4 | $600 \le \text{ADNT} < 2{,}000$ |
| 5 | $2{,}000 \le \text{ADNT} < 9{,}000$ |
| 6 | $\text{ADNT} \ge 9{,}000$ |

The Annex table (19 price ranges × 6 bands) is reproduced verbatim in
`scripts/exchange_tick_size_regime_tracking.py` as `RTS11_TICK_TABLE`. Selected cells,
which the unit tests assert against:

| Price range | Band 1 | Band 3 | Band 6 |
|---|---|---|---|
| $0 \le P < 0.1$ | 0.0005 | 0.0001 | 0.0001 |
| $1 \le P < 2$ | 0.01 | 0.002 | 0.0002 |
| $20 \le P < 50$ | 0.2 | 0.05 | 0.005 |
| $100 \le P < 200$ | 1 | 0.2 | 0.02 |
| $P \ge 50{,}000$ | 500 | 100 | 10 |

Notes:

- The regime applies to **shares, depositary receipts and ETFs** traded on EU trading
  venues — not to bonds, structured products or derivatives.
- For ETFs whose underlyings are exclusively instruments in scope of the regime, RTS 11
  Article 3 fixes the applicable liquidity band at the highest one (band 6).
- The Annex value is a **minimum**: RTS 11 requires venue ticks "equal to or greater
  than" the table value, so a venue may publish a coarser step.
- Post-Brexit UK venues are outside this Regulation and follow the FCA's onshored
  regime; do not apply an RTS 11 band to a UK-only listing without checking.

## DFM (Dubai Financial Market) — AED price steps

Source: **DFM Circular 02/2026, "Revision to Tick Size Structure – DFM Listed
Securities"** (dated 2026-02-03), effective **2026-04-06**, listed on the
[DFM circulars page](https://www.dfm.ae/the-exchange/regulation/circulars).

| Price range (AED) | Tick |
|---|---|
| $P < 1$ | 0.001 |
| $1 \le P < 10$ | 0.01 |
| $10 \le P < 50$ | 0.02 |
| $50 \le P < 100$ | 0.05 |
| $P \ge 100$ | 0.10 |

Scope: listed equities, ETFs and REITs. On the effective date DFM validated **existing
open orders** against the new structure, rejecting or requiring amendment of those that
no longer complied — a live-order migration hazard, not just a new-order rule.

Secondary confirmations of the table:
[Emirates NBD Securities investor update](https://www.emiratesnbdsecurities.com/en/investor-updates/revision-to-tick-size-structure) ·
[Voice of Emirates, 2026-03-31](https://www.voiceofemirates.com/en/business/2026/03/31/dubai-financial-market-announces-revision-to-minimum-tick-size-for-listed-securities-effective-april-6-2026/).

## Engineering standards enforced by this skill

| Requirement | Enforcement |
|---|---|
| No silent venue fallback | `resolve_venue()` raises `UnknownVenueError`; there is no default tick. |
| No inferred RTS 11 liquidity band | `LiquidityBandRequiredError` when a band-dependent venue is queried by price alone. |
| Exact decimal arithmetic | All comparisons and alignment use `Decimal`; on-tick means an exact zero remainder, not a tolerance. |
| Directional safety | `PASSIVE` rounding (BUY down / SELL up) is available and required for live limit orders; `PASSIVE`/`AGGRESSIVE` refuse to run without a `side`. |
| Post-alignment band re-check | The tick reported is the one governing the *aligned* price, and `crossed_price_band` flags the move. |
| Regulatory floor vs venue tick | `venue_assigned_tick` may be coarser than the table; a finer value is rejected. |
| Table integrity at registration | `register_venue()` rejects gapped, overlapping, unordered, or upper-bounded tick tables. |

## Category

`market-data` / `exchange-reference-data`
