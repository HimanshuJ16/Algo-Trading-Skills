# Standards for Cross-Strategy Tax Lot Optimization

| Metric | Engineering Standard |
|---|---|
| Tax-Lot Selection Method | Sell orders MUST use Specific Lot Identification / HIFO to minimize taxable gains. |
| Wash Sale Interception | 61-day window ($[-30, +30]$ days) MUST be audited for cross-strategy wash sale triggers. |
| Cross-Strategy Netting | Offsetting buys and sells MUST be netted internally prior to external broker routing. |