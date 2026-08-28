# Standards for Supply Chain Data for Earnings Prediction

These are engineering standards for this skill, **not** regulatory requirements. No
regulator, exchange or standard-setter publishes a supplier blend weight, an
inventory drag weight, a surprise Z-score threshold, or a minimum chain coverage.
"MUST" below means "MUST, to produce a defensible estimate with this module".
Every numeric default in the table is a house value and must be recalibrated per
name and per sector before live use.

The two items that *are* externally anchored — the 10% concentration line and the
SEC periodic-report deadlines — are marked as such and cited in **Sources**.

| Parameter / rule | Engineering standard | Basis |
|---|---|---|
| Units of the consensus | `consensus_revenue_growth_pct` MUST be a **revenue** growth consensus in the same period-over-period convention and percentage units as the supplier and customer inputs. It MUST NOT be an EPS consensus. | The engine produces a top-line growth estimate. Differencing it against an EPS expectation subtracts two different quantities; translating revenue into EPS requires incremental-margin and share-count assumptions the engine does not make. Version 1.0.0 made this subtraction and named the output field `consensus_eps_gap_pct`; the field is now `consensus_revenue_gap_pct`. The engine cannot detect which consensus it was handed — this is a caller contract. |
| Surprise denominator | `consensus_dispersion_pct` MUST be supplied per call, MUST be finite and strictly positive, and MUST be either the cross-analyst dispersion of the revenue estimates for this company-quarter or the historical standard deviation of this model's own realized gap for this name. There is no default. | Standardized-surprise measures divide the surprise by an *estimated* dispersion (Mendenhall-style SUE uses the standard deviation of analyst forecasts; Foster, Olsen & Shevlin (1984) use a time-series model of the surprise). A hard-coded constant — version 1.0.0 divided every gap by 5.0 — makes the output a rescaled gap wearing a Z-score's clothes. The two choices are not interchangeable, and `surprise_z_threshold` must be calibrated against whichever one is used. |
| Degenerate dispersion | A zero, negative, NaN or infinite `consensus_dispersion_pct` MUST raise. It MUST NOT be absorbed into $Z = 0$ / `NEUTRAL`. | A confident `NEUTRAL` produced by a broken configuration is indistinguishable in the audit record from a measured agreement between chain and consensus. |
| Non-finite inputs | Any NaN or infinite growth rate, weight or threshold MUST raise. | `nan >= 1.0` and `nan <= -1.0` are both False, so an unguarded banding chain falls through to `NEUTRAL`. Missing data must never present as a measurement. |
| Availability timestamps | Every observation MUST carry `available_from_iso` — the instant the figure became **public** (earnings release or SEC filing acceptance), never the fiscal period end — and it MUST be timezone-aware ISO-8601. Naive values MUST be rejected, not assumed UTC. | Form 10-Q is due 40 days after quarter end for large accelerated and accelerated filers and 45 days for all others; Form 10-K is due 60 / 75 / 90 days after year end for large accelerated / accelerated / non-accelerated filers (Exchange Act Rule 12b-2 filer categories). Using the period end as the usable date advances every observation by up to a quarter, in the direction that flatters the backtest. |
| Point-in-time cutoff | `as_of_iso` MUST be passed on every call, in research and in production. Observations published after it MUST be excluded and counted, never silently dropped. | A cutoff that is recorded but not applied produces a backtest that appears point-in-time correct and is not. `as_of` also stamps the output, so the call is deterministic; version 1.0.0 stamped results with `pd.Timestamp.now()`. |
| Staleness | `max_observation_age_days` SHOULD be set to a defensible bound. When it is `None`, `stale_observations_excluded_count` reading zero MUST be read as "nothing was checked", not "everything is fresh". The engine records this in `audit_notes`. | Aligning a target quarter to a supplier figure from four quarters ago is a data-join defect the engine cannot otherwise see. |
| Concentration weighting | Suppliers MUST be weighted by `supplier_share_of_target_cogs_pct` and customers by `customer_share_of_target_revenue_pct`. An unweighted mean over disclosed links MUST NOT be used. | An unweighted mean is dominated by whichever links the vendor happened to capture rather than by the ones that explain the target. Version 1.0.0 documented concentration weighting in three places and implemented it in none — its `SupplierCustomerLink` type was never read. |
| Read-through screen | Suppliers whose `target_share_of_supplier_revenue_pct` is below `min_read_through_share_pct` MUST be excluded, not down-weighted. Default `10.0`. | A supplier's *total* revenue growth is only informative about the target to the extent the target drives it. The 10% default is anchored **by analogy** to the ASC 280-10-50-42 materiality line for customer concentration; ASC 280 sets no floor for this calculation and the number remains a house value. |
| Coverage gate | Below `min_supplier_coverage_pct` of the target's input spend, the engine MUST emit `INSUFFICIENT_DATA` with `surprise_z_score = None`. A number MUST NOT be substituted. Default `10.0`. | Normalizing by the observed weight total assumes the unobserved suppliers grew like the observed ones. At 3% coverage that assumption carries the entire estimate. Same ASC 280 analogy, same caveat: a house floor, not a standard. |
| Weight arithmetic | `supplier_share_of_target_cogs_pct` MUST sum to at most 100% across a batch, `customer_share_of_target_revenue_pct` likewise, and duplicate tickers MUST raise. | A supplier set cannot exceed the target's whole input spend; a duplicated link double-weights itself in the concentration mean. |
| Blend weights | `supplier_blend_weight` (default `0.70`) and `inventory_blend_weight` (default `0.30`) MUST be non-negative and MUST NOT both be zero. Their *values* are unvalidated house calibrations. | The *direction* of the damping is supported — Lee, Padmanabhan & Whang (1997) show order variance exceeds sales variance and amplifies upstream, so a supplier's swing overstates the target's end demand. The *magnitude* is not: no published source establishes 0.70/0.30, and version 1.0.0's reference table presented them as a "Standard Parameter", which they are not. Re-estimate them by regressing realized target revenue growth on the two weighted terms. |
| Band decision | Directional bands MUST be decided on the unrounded $Z$ and are inclusive at the edge; only the reported figure is rounded. | `round(0.99996, 4) == 1.0` promotes `NEUTRAL` to a directional signal on a value the data does not support. |
| Threshold is read | `surprise_z_threshold` (default `1.0`) MUST be honoured by the banding logic. | Version 1.0.0 exposed `surprise_z_threshold=1.5`, never read it, and hard-coded $\pm 1.0$ — so the documented configuration surface and the actual behaviour disagreed silently. |
| Signal vocabulary | `INSUFFICIENT_DATA` MUST be distinct from `NEUTRAL` in the return value and in every downstream consumer, and `None` MUST NOT be coerced to `0.0`. | `NEUTRAL` is a measured agreement between the chain and the consensus; `INSUFFICIENT_DATA` is an absence of evidence. Collapsing them invites a consumer to act on the second. |
| Signal scope | The output MUST NOT be described as an order instruction. `BUY_EARNINGS_SURPRISE` is a directional bias on a fundamental estimate. | Sizing, stops and exposure limits are owned by other skills. See the Related Skills in `SKILL.md`. |
| Reproducibility | The full `SupplyChainEarningsSignal` record MUST be persisted **with the configuration that produced it**. | Every threshold is a parameter, so the Z-score is not reproducible from the inputs alone. `declared_lead_time_months` is echoed into the record for the same reason. |

## Graph completeness is structurally limited

This is the single largest source of error in the estimate and no code change fixes it.

- **ASC 280-10-50-42** requires a public entity to disclose the fact and the total
  amount of revenue from any single external customer at or above **10%** of its
  revenues, and the segment reporting it. It states explicitly that "the public
  entity need not disclose the identity of a major customer." Entities under
  common control count as one customer; each of the federal, a state, a local and
  a foreign government counts as one customer.
- **Relationships below 10% need not be disclosed at all**, so the long tail of a
  supply chain is invisible in filings.
- **Regulation S-K Item 101(c)** was amended by SEC Release **33-10825**
  (adopted 26 August 2020, effective 9 November 2020), which replaced the
  prescriptive requirement to name customers accounting for 10% or more of
  revenue with a principles-based description of any dependence on major
  customers. Named counterparties therefore became *scarcer* after that date, and
  coverage of a filings-derived graph is not stable through time.

The practical consequences: vendor graphs are incomplete, counterparty names are
often inferred rather than disclosed, and a coverage figure computed on 2015 data
is not comparable to one computed on 2025 data. `supplier_coverage_pct` reports
what was actually observed for exactly this reason.

## Compliance notes

- **Alternative supply-chain data can carry material non-public information.**
  Filings-derived relationship data normally does not, but shipping manifests,
  logistics feeds and expert-network colour can. Those controls belong to
  `insider-trading-controls-for-alternative-data-usage`, not to this module.
- **Entitlement is separate from access.** What a supply-chain data vendor returns
  and what its contract permits you to store, redistribute or trade on are
  different questions. See `alternative-data-vendor-due-diligence-checklist` and
  `data-vendor-contractual-usage-restriction-tracking`.

## Sources

- FASB ASC 280-10-50-42, *Segment Reporting — Entity-Wide Disclosures about Major
  Customers*: disclosure of revenues from a single external customer at or above
  10% of revenues, with the identity of the customer explicitly not required.
- SEC, *Modernization of Regulation S-K Items 101, 103, and 105*, Release
  No. 33-10825 (adopted 26 August 2020; effective 9 November 2020).
  <https://www.sec.gov/files/rules/final/2020/33-10825.pdf> — Item 101(c) moved to
  a principles-based description of dependence on major customers, dropping the
  prescriptive naming of 10%+ customers.
- SEC Exchange Act Rule 12b-2 (accelerated and large accelerated filer
  definitions) and the General Instructions to Forms 10-K and 10-Q: 10-Q due 40
  days after quarter end for large accelerated and accelerated filers, 45 days for
  all others; 10-K due 60 / 75 / 90 days after year end for large accelerated /
  accelerated / non-accelerated filers.
- Lee, H. L., Padmanabhan, V. & Whang, S. (1997). "Information Distortion in a
  Supply Chain: The Bullwhip Effect." *Management Science* 43(4), 546–558.
  Order variance exceeds sales variance and the distortion increases moving
  upstream; four causes — demand signal processing, the rationing game, order
  batching, price variation.
- Cohen, L. & Frazzini, A. (2008). "Economic Links and Predictable Returns."
  *The Journal of Finance* 63(4), 1977–2011.
  <https://doi.org/10.1111/j.1540-6261.2008.01379.x> — stock prices do not promptly
  incorporate news about economically linked firms; a long-short strategy on
  customer news yields monthly alphas above 150 bp.
- Menzly, L. & Ozbas, O. (2010). "Market Segmentation and Cross-predictability of
  Returns." *The Journal of Finance* 65(4), 1555–1580.
  <https://doi.org/10.1111/j.1540-6261.2010.01578.x> — economically related supplier
  and customer industries cross-predict each other's returns; the effect declines
  with analyst coverage and institutional ownership.
- Thomas, J. K. & Zhang, H. (2002). "Inventory Changes and Future Returns."
  *Review of Accounting Studies* 7, 163–187.
  <https://doi.org/10.1023/A:1020221918065> — the Sloan (1996) accrual anomaly is
  driven mainly by inventory changes; inventory build predicts lower subsequent
  returns.
- Pandit, S., Wasley, C. E. & Zach, T. (2011). "Information Externalities along the
  Supply Chain: The Economic Determinants of Suppliers' Stock Price Reaction to
  Their Customers' Earnings Announcements." *Contemporary Accounting Research*
  28(4), 1304–1343 — customers' earnings announcements revise expectations about
  suppliers' future earnings and cash flows. Note this documents the *reverse*
  direction from the one this module reads: it establishes that supply-chain
  earnings information transfers between linked firms, not that supplier results
  predict customer results. Menzly & Ozbas (2010) is the source for
  bidirectionality, and it is measured at the industry level.
- Foster, G., Olsen, C. & Shevlin, T. (1984). "Earnings Releases, Anomalies, and
  the Behavior of Security Returns." *The Accounting Review* 59(4), 574–603 — the
  standardized-unexpected-earnings construction, in which the surprise is divided
  by an estimated dispersion rather than a constant.
