# Standards for Point-in-Time Fundamentals Data Joins

| Metric | Engineering Standard |
|---|---|
| As-Of Join Condition | `filing_date <= as_of_date` AND `period_end_date <= as_of_date`. |
| Restatement Isolation | Future restatements MUST NOT overwrite historical as-reported values. |
| Timezone Standard | UTC or exchange local time (EST/EDT for SEC filings). |
