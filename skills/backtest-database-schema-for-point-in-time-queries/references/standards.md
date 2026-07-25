# Standards — backtest-database-schema-for-point-in-time-queries

| Requirement | Specification | Enforcement |
|---|---|---|
| Temporal Column | `known_at` (ISO timestamp) | Mandatory on all PIT tables |
| Temporal Filter | `known_at <= as_of_date` | Enforced at database view level |
| Restatement Handling | Preserve prior rows; insert new row with updated `known_at` | Never overwrite historical records |

## Category

`backtesting-methodology`
