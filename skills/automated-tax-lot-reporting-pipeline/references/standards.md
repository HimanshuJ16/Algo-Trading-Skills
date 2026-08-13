# Standards for Tax Lot Accounting

| Strategy | Description | Common Use Case |
|---|---|---|
| **FIFO** | First-In, First-Out (Sells oldest lots first). | The standard default IRS method. Simple to compute. |
| **HIFO** | Highest-In, First-Out (Sells most expensive lots first). | Tax optimization (Minimizes realized capital gains by maximizing the cost basis deduction). |

## Category
`tax-accounting-reporting-global`

## Detailed Standards Reference

### Regulatory Framework

#### United States (IRS)
- **FIFO Default**: Under IRS regulations, FIFO is the default method for identifying stock or securities sold unless specific identification is used (IRS Publication 550)
- **Specific Identification**: Taxpayers can use specific identification if they adequately identify the securities being sold (date of acquisition, cost basis)
- **HIFO Permissibility**: While not the default, HIFO is permissible under specific identification rules if properly documented
- **Wash Sale Rules**: Losses from wash sales (acquiring substantially identical stock within 30 days before/after sale) are disallowed and added to basis of new stock

#### International Considerations
- **Canada**: CRA allows average cost method for identical properties, FIFO for ad hoc identification
- **UK**: HMRC requires share matching rules (same-day, 30-day, then pooling)
- **EU**: Varies by country; many follow FIFO or average cost principles
- **Australia**: ATO allows FIFO or average cost for shares

### Exchange and Broker Considerations

#### Data Quality Requirements
- **Timestamp Precision**: Millisecond precision recommended to ensure proper chronological ordering
- **Trade Identification**: Unique trade IDs required for audit trails and error reconciliation
- **Price Accuracy**: Execution prices should reflect actual transaction costs including fees/ commissions where applicable
- **Quantity Precision**: Support for fractional shares increasingly important with DRPs and ETFs

#### Reporting Requirements
- **Form 8949**: Requires detailed reporting of each transaction with columns for:
  - Description of property
  - Date acquired
  - Date sold
  - Proceeds
  - Cost basis
  - Gain/loss
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

#### Regulatory Sources
- IRS Publication 550: Investment Income and Expenses
- IRS Instructions for Form 8949 and Schedule D
- Treasury Regulation §1.1012-1 (Specific identification)
- FINRA Rules on Trade Reporting and Record Keeping

#### Industry Standards
- FIX Protocol Standards for trade reporting
- ISO 20022 for financial messaging
- CFA Institute Guidelines on Investment Performance Calculation (GIPS)

#### Academic Resources
- "Tax Lot Optimization Strategies" - Journal of Financial Planning
- "Algorithmic Trading and Tax Efficiency" - Quantitative Finance Papers
- "Lot Level Accounting in Portfolio Management" - CFA Curriculum Materials

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
- Special considerations for cryptocurrency lot accounting
- Varying treatment of forks, airdrops, and staking rewards
- Increasing regulatory guidance on digital asset taxation