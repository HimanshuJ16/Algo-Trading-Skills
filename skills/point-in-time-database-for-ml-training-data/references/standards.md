# Standards — point-in-time-database-for-ml-training-data

| Requirement | Join Rule | Enforcement |
|---|---|---|
| Temporal Availability | `available_at <= label_timestamp` | Strict inequality join filter |
| Revision Handling | As-of join returns latest version available on target date | Restatements ignored until published |

## Category

`financial-ml`
