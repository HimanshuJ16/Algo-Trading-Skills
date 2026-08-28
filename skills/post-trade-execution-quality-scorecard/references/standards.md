# Standards for Post-Trade Execution Quality Scorecard

## 1. What is actually mandatory, and on whom

Nothing in this skill is a regulatory filing tool. The table separates rules that bind
from thresholds this repository treats as house convention.

| Item | Jurisdiction | Instrument | Status | Binds whom |
|---|---|---|---|---|
| SEC Rule 605, 17 CFR 242.605 (as amended, Rel. 34-99679) | US | Regulation NMS | Mandatory. Effective 14 June 2024; compliance date extended to **1 August 2026** by Rel. 34-104147 (effective 2 Oct 2025) | Market centres; broker-dealers introducing or carrying **100,000 or more** customer accounts; single-dealer platforms |
| SEC Rule 606 | US | Regulation NMS | Mandatory | Broker-dealers routing customer orders (order-routing disclosure — not execution quality statistics) |
| MiFID II Art. 27(1) — best execution | EU | Directive 2014/65/EU | Mandatory, in force | Investment firms executing client orders |
| MiFID II Art. 27(7) + Art. 66 of Del. Reg. (EU) 2017/565 — **monitor execution arrangements, review policy at least annually** | EU | Directive 2014/65/EU | Mandatory, in force | Investment firms |
| MiFID II Art. 27(3) / RTS 27 (Del. Reg. (EU) 2017/575) — venue execution quality reports | EU | — | **Deleted** by Directive (EU) 2024/790 | Nobody. Do not build these |
| MiFID II Art. 27(6) / RTS 28 (Del. Reg. (EU) 2017/576) — top-five venue reports | EU | — | **Deleted** by Directive (EU) 2024/790; ESMA deprioritised supervision from 13 Feb 2024 (ESMA35-335435667-5871) | Nobody. Do not build these |

Directive (EU) 2024/790 entered into force 29 March 2024 with a Member State
transposition deadline of **29 September 2025**, now past. A codebase still generating
RTS 27/28 reports is generating reports no one is required to receive.

**Art. 27(7) is the clause this skill serves.** The RTS 27/28 *reporting* obligations
were removed; the underlying obligation to *monitor* execution quality and review the
execution policy was not. A scorecard is one defensible way to evidence that monitoring.

## 2. Rule 605 metric definitions this engine mirrors

Only two. Both are reproduced faithfully so the output is comparable in kind to a
published Rule 605 report; neither makes this engine a report generator.

| Metric | Rule 605 definition | This engine |
|---|---|---|
| Effective spread | Twice the signed difference between the execution price and the consolidated midpoint **at the time of order receipt** | `2 * side_sign * (avg_fill_price - arrival_midquote)` |
| Average effective / average quoted spread ("E/Q") | Ratio of the **share-weighted average** effective spread to the **share-weighted average** quoted spread, expressed as a percentage | `eqr_ratio_of_averages`, reported as a ratio (multiply by 100 to compare with a filing) |

The reference point matters: an effective spread measured against the *execution-time*
midpoint rather than the *receipt-time* midpoint is a different statistic, and it makes
a slow execution into a drifting market look tight.

`avg_eqr_ratio` — the notional-weighted mean of per-order ratios — is deliberately
reported alongside and is **not** the Rule 605 construct. Mean-of-ratios and
ratio-of-means diverge whenever quoted spreads vary across orders.

Not implemented, and required for an actual filing: notional order size categories,
fractional/odd-lot/round-lot classification, price and size improvement statistics,
realized spread at 50 ms / 1 s / 15 s / 1 min / 5 min, time-to-execution buckets from
sub-100-microsecond upward, monthly aggregation, and the CSV+PDF summary report.

## 3. Implementation shortfall

Perold, A. F. (1988), "The Implementation Shortfall: Paper versus Reality", *Journal of
Portfolio Management* 14(3), 4–9. IS is the shortfall of the realised portfolio against
a paper portfolio filled entirely at the decision price, and decomposes into delay,
explicit, implicit (execution) and **opportunity** cost. This engine computes the
execution and opportunity components against the arrival price; explicit costs
(commissions, fees, taxes, borrow) and decision-to-arrival delay are out of scope and
must be added downstream before the number is called a full shortfall.

## 4. House thresholds — configurable, not mandated

No regulator, exchange or standards body prescribes any of the values below. They are
defaults a desk is expected to recalibrate against its own cost model; see
`execution-cost-model-recalibration-cadence`.

| Setting | Default | Nature |
|---|---|---|
| `benchmark_target_is_bps` | 10.0 bps | House target. Arrival slippage below it accrues no penalty. |
| `is_penalty_per_bps` | 2.0 | House scoring weight. |
| `eqr_penalty_per_unit` | 20.0 | House scoring weight, applied to $E/Q$ above 1.0. |
| `fill_penalty_per_pct` | 1.0 | House scoring weight, per percentage point unfilled. |
| `min_venue_notional_for_grade` | 0.0 | Below this a venue is reported but graded `NR`. |
| Grade boundaries $A \ge 90$, $B \ge 80$, $C \ge 70$, $D \ge 60$, else $F$ | — | House convention. |

An $E/Q$ at or below 1.0 means the execution landed at or inside the quoted spread —
a useful sanity check, not a compliance test. Note the population it was defined for:
Rule 605 computes $E/Q$ over *individual marketable orders* against the receipt-time
quote. A large parent order worked over minutes walks the book, so $E/Q$ of 5–15 is
structural, not a broker failing, and `eqr_penalty_per_unit` must be recalibrated (or
zeroed) before grading such a book. A fill rate target is a commercial
expectation set in a broker agreement, not a regulatory floor.

## 5. Sources

- 17 CFR 242.605 — https://www.law.cornell.edu/cfr/text/17/242.605
- SEC Release No. 34-99679, *Disclosure of Order Execution Information* (adopted 6 Mar 2024) — https://www.sec.gov/newsroom/press-releases/2024-32
- SEC Release No. 34-104147, *Extension of Compliance Date for Disclosure of Order Execution Information* (2 Oct 2025) — https://www.sec.gov/files/rules/final/2025/34-104147.pdf
- Directive (EU) 2024/790 amending Directive 2014/65/EU — https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX:32024L0790
- ESMA35-335435667-5871, public statement on deprioritisation of supervisory actions on RTS 28 reporting (13 Feb 2024) — https://www.esma.europa.eu/sites/default/files/2024-02/ESMA35-335435667-5871_Public_Statement_on_deprioritisation_of_supervisory_actions_on_RTS_28_reporting.pdf
- Commission Delegated Regulation (EU) 2017/565, Art. 66 (execution policy review) — https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0565
- Perold, A. F. (1988), *Journal of Portfolio Management* 14(3), 4–9.
