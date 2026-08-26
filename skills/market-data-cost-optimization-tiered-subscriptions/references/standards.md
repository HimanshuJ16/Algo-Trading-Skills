# Standards for Market Data Cost Optimization

These are engineering standards for this skill, plus the sourced fee-structure facts
they rest on. Nothing below is a regulatory obligation on the *firm* except where a
jurisdiction is named; exchange and SIP fee schedules are contractual.

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Tradeable-symbol floor | A symbol with an open position **or** a live signal MUST be recommended to a real-time tier (TIER2 or TIER1). Delayed data cannot carry an order. |
| Inactive demotion rule | A symbol with no position, no live signal, and no fill within the configured inactivity threshold MAY be demoted to TIER3 delayed/EOD. |
| Unknown input | An unrecognised `current_tier`, a duplicate symbol, or a negative day count MUST raise rather than be defaulted — a defaulted tier manufactures baseline spend and therefore phantom savings. |
| Cost schedule provenance | The tier cost schedule MUST come from the firm's own contract. The shipped `TIER_COSTS` values are illustrative placeholders with no market basis. |
| Savings denominator | A reported data-spend reduction MUST be quoted against total spend (metered + fixed), not against the symbol-metered slice alone. |
| Demotion cadence | Tier changes MUST NOT be churned inside a billing period, because exchange and SIP fees are not prorated. |
| Spend audit frequency | Entitlements and tier assignments SHOULD be re-audited every billing cycle, aligned to the vendor's usage-reporting deadline. |

## Sourced fee-structure facts

| Fact | Source |
|---|---|
| Nasdaq TotalView: Professional/Corporate **$80.50 per subscriber/month** effective 2025-01-01 ($84.00 for 2026, $86.00 for 2027); the entitlement's "Security Coverage" is *"Nasdaq, NYSE, and Other Regional Issues"* — the whole market, not a symbol list. | [Nasdaq US Equities Price List 2025-2027](https://www.nasdaqtrader.com/content/ProductsServices/PriceList/Nasdaq_US_Equities_Price_List_2025_2026_2027.pdf) |
| Nasdaq Depth Non-Display (Direct Access Only): **$396.00 per subscriber** for 1-39 subscribers, then flat per firm — $15,840 (40-99), $31,680 (100-249), $75,000 (250+), effective 2025-01-01. | [Nasdaq US Equities Price List 2025-2027](https://www.nasdaqtrader.com/content/ProductsServices/PriceList/Nasdaq_US_Equities_Price_List_2025_2026_2027.pdf) |
| UTP Plan: Real-Time Direct Access **$2,500/month per firm**; Real-Time Non-Display Use on own behalf **$3,500/month per firm**; Non-Display for an Electronic Trading System **$3,500/month per Electronic Trading System**; Delayed External Redistributor $250/month per firm. | [UTP Data Policies, September 2023, Fee Schedule](https://www.utpplan.com/DOC/datapolicies.pdf) |
| UTP Plan: *"End-of-Day Usage is not currently fee liable."* | [UTP Data Policies, September 2023, Fee Schedule](https://www.utpplan.com/DOC/datapolicies.pdf) |
| UTP Plan: *"All fees are subject to change and fees will not be prorated."* | [UTP Data Policies, September 2023, Fee Schedule](https://www.utpplan.com/DOC/datapolicies.pdf) |
| UTP Per Query Policy — the one genuinely per-security charge: *"Vendors are to charge the applicable per query rate for each data pull associated with one security"*, **$0.0075 per query**, capped at 3,200 quotes / **$24 per month** (Professional Subscriber) or 134 quotes / **$1 per month** (Nonprofessional). Capping is not permitted where simultaneous access is allowed or per-subscriber query tracking is impossible. | [UTP Data Policies, September 2023, Per Query Policy](https://www.utpplan.com/DOC/datapolicies.pdf) |
| CME Group publishes website quotes delayed **at least 10 minutes**. | [CME Group — Delayed Quotes](https://www.cmegroup.com/market-data/browse-data/delayed-quotes.html) |
| EU: MiFIR Article 13(2) requires trading venues, APAs and systematic internalisers to make the Article 13(1) data available *"free of charge 15 minutes after publication in a format that is machine-readable and usable for all users, including retail investors."* | [ESMA Interactive Single Rulebook — MiFIR Article 13](https://www.esma.europa.eu/publications-and-data/interactive-single-rulebook/mifir/article-13-obligation-make-pre-trade-and) |
| LSEG (Refinitiv) DACS is the entitlement **enforcement and usage-reporting** system within the LSEG Real-Time Distribution System — it applies and reports entitlements, it does not price them. | [LSEG — Real-Time Data Access Control System (DACS)](https://www.lseg.com/en/training/learning-centre/learning-paths/learning-path-for-data-solutions/learn-about-real-time-market-data/learn-about-real-time-data-access-control-system-dacs) |
| Bloomberg B-PIPE publishes no per-symbol rate card; pricing is contract-negotiated over data fields, exchanges, redistribution rights, and consuming applications. | [Bloomberg — Real-Time Market Data Feed (B-PIPE)](https://professional.bloomberg.com/products/data/enterprise-catalog/real-time-data-feed/) |

## Not verified

- **No published per-symbol monthly rate** for depth-of-book or consolidated real-time
  data was found at any exchange, SIP, or major vendor. Treat any per-symbol tier cost
  as firm-specific contract data, not an industry rate.
- Fee amounts above are point-in-time (schedules effective 2025-2027, UTP policies
  published September 2023). Re-check against the current schedule before relying on a
  figure; exchange fee changes are filed with the SEC and take effect on notice.
