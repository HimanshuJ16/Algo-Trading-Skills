# Standards for Insider Filing Factor Research

| Metric | Engineering Standard |
|---|---|
| Rule 10b5-1 Filtering | Pre-arranged Rule 10b5-1 trades MUST be filtered out ($w = 0.0$). |
| Role Weighting | CEO/CFO trades MUST carry highest weight ($w = 1.0$). |
| Open Market Enforcement | Non-open-market option grants MUST be excluded from sentiment scores. |