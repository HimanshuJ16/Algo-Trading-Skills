# Standards for Euronext Optiq Market Data Integration

| Metric | Engineering Standard |
|---|---|
| Optiq SBE Protocol | Market data feeds MUST use Optiq Simple Binary Encoding (SBE) templates. |
| Multicast Arbitration | Production feed handlers MUST implement Line A/B multicast arbitration. |
| Trading Halt Reaction | Quoting MUST freeze within $< 1\text{ms}$ of receiving `HALTED` SymbolStatus. |
