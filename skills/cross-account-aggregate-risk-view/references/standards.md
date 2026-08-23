# Standards for Cross-Account Aggregate Risk View

| Metric | Engineering Standard |
|---|---|
| Firm-Wide GMV Cap | Aggregate Gross Market Value across all accounts MUST NOT exceed the firm risk mandate (e.g. $10,000,000). GMV is gross across symbols but netted within each symbol: $\text{GMV} = \sum_s \lvert Q_{net}(s) \cdot P(s)\rvert$. |
| Internal Offsetting Alert | Concurrent long/short positions in the same asset across sub-accounts MUST be flagged for internal netting. This flags capital friction; it does not assert a regulatory wash-trade violation. |
| Consolidated Margin Limit | Total firm-wide margin utilization MUST NOT exceed 80% of aggregate margin capacity (internal mandate; tune per firm). Margin used against zero/unconfigured capacity MUST violate — never report 0% utilization. |
| Pre-Trade Margin Projection | Margin requirements are broker/product-specific (Reg T, portfolio margin, SPAN) and are NOT modelled here — only the broker-reported `margin_used_usd` / `margin_limit_usd` are consolidated. A pre-trade check projects margin only when the caller supplies `additional_margin_usd`; otherwise the margin cap gates existing balances only. |
| Fail-Closed Valuation | Every held symbol MUST have a valid market price (> 0, finite). Missing or invalid prices MUST produce an `unvalued_symbols` violation and block pre-trade approval rather than valuing positions at $0.00. |

## Regulatory Touchpoints

- **SEC Rule 15c3-5 (17 CFR 240.15c3-5)** — the Market Access Rule requires broker-dealers providing market access to maintain financial risk management controls that reject orders exceeding pre-set credit or capital thresholds **set in aggregate** for each customer and the firm, plus erroneous-order controls (price/size parameters). The obligations rest on the sponsoring broker-dealer, not directly on buy-side funds, but a firm-wide pre-trade GMV cap is the buy-side implementation of the same aggregate-threshold principle.
- **Jurisdiction note**: this skill encodes internal risk-mandate mechanics, not a specific legal obligation. Thresholds (GMV cap, 80% margin utilization) are examples to be calibrated to the firm's own mandate and prime-broker agreements.
