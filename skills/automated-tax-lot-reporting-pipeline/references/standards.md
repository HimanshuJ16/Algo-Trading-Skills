# Standards for Tax Lot Accounting

| Strategy | Description | Common Use Case |
|---|---|---|
| **FIFO** | First-In, First-Out (Sells oldest lots first). | The standard default IRS method. Simple to compute. |
| **HIFO** | Highest-In, First-Out (Sells most expensive lots first). | Tax optimization (Minimizes realized capital gains by maximizing the cost basis deduction). |

## Category
`tax-accounting-reporting-global`

## Detailed Standards Reference

### Regulatory Framework

#### United States (IRS) — securities
- **FIFO Default**: Treas. Reg. §1.1012-1(c)(1) — where a taxpayer sells shares purchased on different dates or at different prices "and the taxpayer does not adequately identify the lot from which the stock is sold or transferred, the stock sold or transferred is charged against the earliest lot." FIFO is the fallback, not an election.
- **Specific Identification**: §1.1012-1(c) permits identifying the particular shares delivered. §1.1012-1(c)(8) treats "a standing order or instruction for the specific identification of stock" as an adequate identification made at the time of sale.
- **HIFO Permissibility**: HIFO is not a distinct statutory method. It is one *standing instruction* under specific identification ("always sell the highest-cost shares"), and is only respected if that identification exists as a contemporaneous record. Computing HIFO after the fact, from a batch of executions, is not by itself an adequate identification — the identification must exist no later than the sale.
- **Wash Sale Rules**: IRC §1091 — a loss is disallowed where substantially identical stock or securities are acquired within 30 days before or after the sale, and the disallowed loss is added to the basis of the replacement shares. This engine applies no §1091 adjustment; see `wash-sale-rule-tracking-us`.

#### United States (IRS) — digital assets
Digital assets diverge from securities and the differences change how this engine must be deployed.
- **Per-wallet, per-account basis**: Treas. Reg. §1.1012-1(j) (final regulations T.D. 10000, 89 FR 56480, published 9 July 2024) provides the ordering rules for units of the same digital asset, applied to the units held in **each wallet or account** — not universally across a taxpayer's holdings. §1.1012-1(h) and (j) apply to acquisitions and dispositions on or after **1 January 2025**. Run one engine instance per wallet/account; a single ledger pooling several venues does not produce a compliant result for periods on or after that date.
- **FIFO default for digital assets**: absent an adequate identification, §1.1012-1(j)(3)(i) charges units against the earliest acquired units held in that broker's custody.
- **Transition relief for pre-2025 basis**: Rev. Proc. 2024-28 provides a safe harbour for allocating unused basis across wallets/accounts held as of 1 January 2025, for taxpayers moving off a universal (multi-wallet) methodology.
- **Temporary identification relief**: Notice 2025-7, extended by Notice 2026-20, relieves taxpayers from the §1.1012-1(j)(3)(ii) requirement to specify units *to the custodial broker*, for dispositions from 1 January 2025 through **31 December 2026**. During that window an identification recorded in the taxpayer's own books and records — including a standing order recorded before the units are sold — is accepted. This is the window in which a HIFO standing instruction driving this engine can constitute the identification; it is temporary and its status after 31 December 2026 should be confirmed against current IRS guidance before relying on it.

#### International Considerations

Jurisdiction matters more than it looks: in two of the four below, FIFO/HIFO output from this engine is **not** a valid basis for the local return.

- **Canada**: the CRA requires the **adjusted cost base (ACB)** of *identical properties* (shares of the same class, units of the same fund) to be computed as a running weighted average — "you have to calculate the average cost of each property in the group at the time of each purchase." There is no FIFO or specific-identification election for identical properties. **Neither FIFO nor HIFO output from this engine is a valid basis for a Canadian T1 capital gains calculation.**
- **UK**: HMRC share identification runs in a fixed order — same day (TCGA 1992 s.105(1)), then acquisitions in the 30 days *following* the disposal (s.106A(5), the "bed and breakfasting" rule), then the s.104 pooled holding at its average pooled cost. The 30-day rule takes priority over everything except the same-day rule. **This engine models none of these three steps**; its FIFO output is not UK share matching.
- **EU**: no single rule — each member state legislates its own basis method (FIFO and average cost both appear). Confirm the specific member state before applying this engine's output.
- **Australia**: the ATO accepts specific identification where the shares can be identified from the taxpayer's records, and accepts FIFO "as a reasonable basis of identification" where disposed shares are unidentifiable (TR 96/4 and the ATO CGT guidance on identifying shares or units sold). An **average cost method is generally not acceptable** for CGT, because the CGT provisions require a date of acquisition and cost base for a *particular* asset — only a narrow exception applies. FIFO output from this engine is usable here; a HIFO selection requires the supporting records.

### Exchange and Broker Considerations

#### Data Quality Requirements
- **Timestamp Precision**: Millisecond precision recommended to ensure proper chronological ordering
- **Trade Identification**: Unique trade IDs required for audit trails and error reconciliation
- **Price Accuracy**: Execution prices should reflect actual transaction costs including fees/ commissions where applicable
- **Quantity Precision**: Support for fractional shares increasingly important with DRPs and ETFs

#### Reporting Requirements
- **Form 8949**: Requires detailed reporting of each transaction, columns (a)-(h):
  - (a) Description of property
  - (b) Date acquired — `RealizedGainRecord.acquired_timestamp_ms`
  - (c) Date sold or disposed of — `RealizedGainRecord.disposed_timestamp_ms`
  - (d) Proceeds — `quantity_sold * sell_price`
  - (e) Cost or other basis — `quantity_sold * cost_basis_price`
  - (f)/(g) Adjustment code and amount — **not produced by this engine** (wash sales, corporate actions)
  - (h) Gain or loss — `realized_pnl`, before any column (g) adjustment
- **Holding period split**: Form 8949 separates Part I (short-term) from Part II (long-term). Short-term is a holding period of one year or less; long-term is more than one year, counting from the day *after* acquisition through the disposal date. The engine carries both timestamps but does **not** classify: converting epoch milliseconds to a calendar date requires a timezone the engine deliberately does not assume. The consumer must apply it.
- **Schedule D**: Aggregates Form 8949 totals
- **Foreign Accounts**: FBAR/FATCA reporting may apply to foreign holdings

### Institutional Best Practices

#### Data Management
- **Immutable Trade Records**: Store original trade data without modification for auditability
- **Chronological Processing**: Enforce strict time-ordered processing to maintain lot integrity
- **Gap Detection**: Implement mechanisms to detect missing trades in sequence
- **Reconciliation**: Regular reconciliation with broker statements and custodial records

#### Computational Precision
- **Floating Point Considerations**: Use decimal or fixed-point arithmetic for financial calculations to avoid rounding errors
- **Rounding Rules**: Follow jurisdiction-specific rounding conventions (typically to nearest cent)
- **Large Number Handling**: Ensure engine can handle large volumes without performance degradation

#### Performance Optimization
- **Lot Sorting**: Maintain lots in sorted order rather than re-sorting on each transaction
- **Memory Management**: Implement efficient data structures for lot storage and retrieval
- **Batch Processing**: Support for processing trades in batches while maintaining chronological integrity
- **Caching**: Cache frequently accessed lot data for high-frequency scenarios

### Implementation Guidelines

#### Error Handling Philosophy
- **Fail Fast**: Invalid inputs should raise descriptive exceptions immediately
- **Partial Failure Isolation**: Errors in individual trades should not block processing of subsequent trades
- **Audit Trail**: All errors should be logged with sufficient context for debugging
- **Recovery Procedures**: Clear procedures for recovering from engine failures or data corruption

#### Monitoring and Observability
- **Metrics**: Track trades processed, lots created/consumed, calculation errors, processing latency
- **Health Checks**: Verify engine state consistency (lot counts match expectations)
- **Logging**: Structured logging with correlation IDs for trade traceability
- **Alerting**: Threshold-based alerts for anomalous conditions (unexpected lot growth, error spikes)

#### Security Considerations
- **Input Validation**: Strict validation of all trade parameters to prevent injection attacks
- **Access Control**: Role-based access for different operations (trading vs reporting vs admin)
- **Data Protection**: Encryption of sensitive trade data at rest and in transit
- **Audit Logging**: Comprehensive audit trail of all lot matching decisions

### References and Further Reading

#### Regulatory Sources (primary)
- Treas. Reg. §1.1012-1(c) — identification of shares sold; (c)(1) FIFO default, (c)(8) standing orders: https://www.law.cornell.edu/cfr/text/26/1.1012-1
- Treas. Reg. §1.1012-1(h) and (j) — digital asset basis and unit ordering rules, applicable to acquisitions and dispositions on or after 1 Jan 2025 (T.D. 10000, 89 FR 56480)
- Rev. Proc. 2024-28 — transition from universal to wallet-by-wallet digital asset basis allocation: https://www.irs.gov/pub/irs-drop/rp-24-28.pdf
- IRS Notice 2026-20 — extension of temporary relief under §1.1012-1(j)(3)(ii) through 31 Dec 2026: https://www.irs.gov/pub/irs-drop/n-26-20.pdf (extends Notice 2025-7)
- IRS Instructions for Form 8949 and Schedule D: https://www.irs.gov/instructions/i8949
- IRS Publication 550: Investment Income and Expenses
- IRC §1091 — wash sales of stock or securities
- CRA, "Special rules and other transactions" — identical properties and ACB averaging: https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/about-your-tax-return/tax-return/completing-a-tax-return/personal-income/line-12700-capital-gains/special-rules-other-transactions.html
- HMRC Capital Gains Manual CG51560 — share identification rules, TCGA 1992 s.105/s.106A: https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51560
- ATO TR 96/4 and the ATO CGT guide, "Identifying shares or units sold"

#### Industry Standards
- FIX Protocol Standards for trade reporting
- ISO 20022 for financial messaging
- CFA Institute Guidelines on Investment Performance Calculation (GIPS)

## Broker-Specific Nuances

### Interactive Brokers
- Provides trade data via API with millisecond timestamps
- Supports fractional share trading
- Offers built-in tax lot optimization tools (but external engines may provide more control)

### Fidelity
- Offers cost basis tracking with multiple methods
- Provides year-end tax statements
- May have specific requirements for lot ID reporting

### Charles Schwab
- Comprehensive cost basis reporting
- Supports multiple identification methods
- Provides detailed transaction history

### Vanguard
- Primarily focused on mutual fund accounting
- ETF trading follows stock lot accounting rules
- Conservative approach to tax lot methods

### Crypto Exchanges
- **US basis tracking is per wallet/account** for dispositions on or after 1 Jan 2025 (Treas. Reg. §1.1012-1(j)); instantiate one engine per wallet or account rather than pooling venues into one ledger.
- Forks, airdrops and staking rewards are acquisitions with their own basis and acquisition date. This engine only ingests BUY/SELL, so those must be normalised into BUY records upstream with the correct basis, or the lots will be missing.
- Fractional quantities are the norm, which is why lot exhaustion here is epsilon-based rather than an exact zero comparison — see `_QUANTITY_EPSILON` in the helper.
- See `crypto-transaction-tax-lot-tracking` for the wallet-scoped variant of this engine.