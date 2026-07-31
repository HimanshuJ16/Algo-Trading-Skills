# Workflows for Point-in-Time Fundamentals Data Joins

1. **Filing Date Filtering**:
   - Filter fundamental records enforcing filing_date <= as_of_date.
2. **Latest Record Selection**:
   - Select the most recently published record as of the query date.
3. **Restatement Audit**:
   - Audit if naive period-end join would have introduced restatement lookahead bias.
4. **Audit Report Generation**:
   - Output structured PIT fundamentals report.
