# Standards — market-data-feed-arbitration-across-vendors

## Engineering standards

| Rule | Requirement |
|---|---|
| Trust vs verification | A result MUST carry two separate flags: *usable* (`is_trusted`) and *cross-verified by two fresh, simultaneous, agreeing feeds* (`is_cross_verified`). A failover price is usable and unverified; these are not the same claim. |
| Clock discipline | Staleness MUST be measured on a single local receipt clock. Vendor or exchange event timestamps MUST NOT be used, because the resulting "age" includes inter-vendor clock skew. |
| Tick validation | Non-finite (`NaN`, `±inf`) and non-positive prices MUST be rejected at the boundary, before entering arbitration state. |
| Ordering | A tick older than that vendor's last observation MUST NOT overwrite it. |
| Blackout detection | Feed health MUST be evaluable **without** an incoming tick, on a supervisor timer at an interval well below the stale threshold. Tick-driven staleness alone cannot detect a total blackout. |
| Comparability | Only observations within a bounded age of each other may be compared or averaged. A disagreement between non-simultaneous observations MUST NOT be attributed to a vendor. |
| Divergence tolerance | The tolerance MUST be at least the instrument's minimum price increment expressed in percent, and SHOULD be calibrated from recorded cross-vendor history per instrument. |
| Attribution | A vendor MUST NOT be quarantined on a single divergent tick. Evidence-based attribution (a frozen feed) MUST be preferred; a reference-vendor fallback MUST be reported as operator policy, never as outlier detection. |
| Unresolved state | A price emitted while a divergence is unresolved MUST be flagged untrusted rather than published as consensus. |
| Quarantine release | Release MUST require N consecutive clean comparisons (hysteresis). |
| Quarantine promotion | A quarantined feed that becomes the only surviving feed MUST NOT be silently promoted to trusted. |
| No price | When every feed is stale, the result MUST carry no price rather than the last known value. |
| Divergence reporting | A result where no comparison was performed MUST report divergence as *unknown*, never as `0.0`. |
| Logging | Feed-state logging MUST be emitted on transitions, not per tick. |
| Concurrency | State MUST be safe for concurrent access: feed handlers typically run one thread per vendor session. |

## Default parameters

These are **starting points for calibration, not standards**. No regulator, exchange or vendor publishes a cross-vendor divergence tolerance; the figures below come from this skill's reference implementation and must be re-derived per instrument and per vendor pair from recorded data.

| Parameter | Default | Notes |
|---|---|---|
| `max_divergence_pct` | 0.05 (5 bps) | **Floor it at one minimum price increment.** For a US NMS stock quoted in $0.01 increments, one tick exceeds 5 bps below $20. |
| `max_stale_seconds` | 2.0 | Must exceed the instrument's own quiet periods, or an illiquid symbol is permanently "stale" between genuine ticks. |
| `max_comparison_age_seconds` | 0.25 | Above this the two observations are not treated as simultaneous. |
| `divergence_confirmation_seconds` | 1.0 | Hold-down before policy fallback. Zero reproduces quarantine-on-first-tick and flaps in fast markets. |
| `recovery_consecutive_ticks` | 3 | Hysteresis on quarantine release. |
| `frozen_price_seconds` | 5.0 | Evidence threshold for "still ticking, no longer moving". |

## Feed-topology facts (verified against primary sources)

| Fact | Detail | Source |
|---|---|---|
| A/B lines are not independent vendors | CME MDP 3.0 disseminates every packet on both UDP Feed A and UDP Feed B; "UDP Feed A and UDP Feed B should be used for arbitration", and the redundancy exists to cover UDP packet loss. Two copies of one stream share a sequence space and are arbitrated losslessly by packet sequence number — not by price comparison. | [CME MDP 3.0 — Dissemination, CME Group Client Systems Wiki](https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/MDP+3.0+-+Dissemination) |
| Feeds legitimately differ in latency | In adopting the Market Data Infrastructure rule the SEC described the two-tiered structure in which participants buying exchange proprietary depth-of-book feeds and the associated connectivity "receive more content-rich data faster" than consumers of the consolidated tapes. Cross-vendor divergence is therefore expected behaviour, not prima facie corruption. | [SEC, *Market Data Infrastructure*, Release No. 34-90610 (Dec. 9, 2020)](https://www.sec.gov/files/rules/final/2020/34-90610.pdf) |
| Tick size bounds the tolerance | Reg NMS Rule 612 sets the minimum pricing increment for NMS stocks quoted at or above $1.00 at $0.01. The 2024 amendments add a $0.005 increment for tick-constrained stocks, but compliance has been deferred by temporary exemptive relief — as of this writing to the first business day of **November 2027**. Confirm the current status before relying on either increment. | [SEC, *Regulation NMS: Minimum Pricing Increments, Access Fees, and Transparency of Better Priced Orders*, Release No. 34-101070 (Sept. 18, 2024)](https://www.sec.gov/files/rules/final/2024/34-101070.pdf); [Order granting temporary exemptive relief from Rules 610(c), 612 and 600(b)(89)(i)(F)](https://www.federalregister.gov/documents/2025/11/17/2025-19926/order-granting-temporary-exemptive-relief-pursuant-to-section-36a1-of-the-securities-exchange-act-of) |

## Regulatory context

Nothing in this skill is legal or compliance advice, and none of the provisions below regulates feed arbitration directly. They are the obligations a broken feed most often causes a firm to breach.

| Jurisdiction | Provision | What it actually says | Applies to |
|---|---|---|---|
| EU | **RTS 6, Article 14(2)(b)** (Commission Delegated Regulation (EU) 2017/589) | Business continuity arrangements must cover "a range of possible adverse scenarios relating to the operation of the algorithmic trading systems, including the unavailability of systems, staff, work space, **external suppliers** or data centres or loss or alteration of critical data". Article 14(4) requires annual review and testing. Vendor-feed loss is squarely an external-supplier scenario. | Investment firms engaged in algorithmic trading in the EU. Mandatory. |
| EU | **RTS 6, Article 16** | Real-time monitoring of algorithmic trading activity, with alerts to staff: "Real-time alerts shall be generated within five seconds after the relevant event." | As above. Mandatory. The five-second ceiling governs monitoring of *trading activity*, not feed health as such, but it is the operative alert-latency benchmark for an EU firm. |
| UK | Assimilated RTS 6 (FCA Handbook) | The same Articles 14 and 16 apply as assimilated law. | UK investment firms. Mandatory. |
| US | **Exchange Act Rule 15c3-5(c)(1)(ii)** | Controls reasonably designed to prevent the entry of erroneous orders, by rejecting orders exceeding appropriate price or size parameters. A corrupted price feed is a common upstream cause of an erroneous order. | Broker-dealers with market access. It is **not** a data-pipeline rule and does not bind a proprietary trading firm's feed handler directly. |
| US | Regulation SCI (17 CFR 242.1000 *et seq.*) | Systems capacity, integrity and business-continuity obligations for market data systems. | **SCI entities only** — SROs, plan processors, certain large ATSs. It does **not** apply to an ordinary trading firm; do not cite it as an obligation on your own feed handler. |

**Not verified here:** any regulator-published numeric threshold for cross-vendor price divergence, feed-staleness timeouts, or mandatory feed redundancy for non-SCI trading firms. No such figure was located; treat every parameter in this skill as an engineering choice you must justify, not a compliance floor.

## Category

`real-time-architecture` — see top-level `mappings/` directory.

## Cross-references

- Sequence-space arbitration of identical lines: `sequence-number-gap-detection-for-feeds`
- Three-or-more-source outlier attribution: `multi-source-price-reconciliation-tie-breaking`
- Clock discipline for receipt timestamps: `clock-skew-correction-for-tick-timestamps`
- Escalation once a feed is untrusted: `graduated-response-to-data-quality-degradation`
