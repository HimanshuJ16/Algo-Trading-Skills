# Standards for Point-in-Time Index Constituent Tracking

| Metric | Engineering Standard |
|---|---|
| PIT Membership Rule | `add_date <= T` AND (`del_date IS NULL` OR `del_date > T`). |
| Survivorship Bias Elimination | Delisted and bankrupt constituents MUST be included in historical backtest windows. |
| Rebalance Effective Date | Membership changes MUST take effect on official exchange effective dates. |
