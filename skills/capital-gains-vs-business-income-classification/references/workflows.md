# Workflows for Tax Classification

1. **End-of-Day Reconciliation**: Download raw fill logs from the broker and reconstruct
   "Closed Trades" (paired entries and exits). Carry the delivery/settlement flag and the
   listing status through from the contract note — reconstructing them later from
   timestamps alone is what produces the misclassifications this skill exists to prevent.

2. **Pin the Jurisdiction and Elections**: Build one `TaxClassificationEngine` per
   jurisdiction, passing a `TaxElections` object recording the positions the taxpayer has
   actually taken for the year. Do not infer an election from trade frequency. If a
   taxpayer files in more than one jurisdiction, see
   `multi-jurisdiction-tax-residency-implications` — running the same ledger through two
   engines does not by itself resolve treaty or residency questions.

3. **Execution**: Pass the list of `ClosedTrade` objects to
   `TaxClassificationEngine.classify_portfolio()`, which returns a `TradeClassification`
   per trade carrying the category, the `Decimal` PnL, the holding period in days, and a
   `rationale` string naming the provision applied. Use `aggregate_pnl()` for the
   per-bucket totals.

4. **Persist the Rationale**: Store the `rationale` alongside each classified trade. When
   an assessment arrives a year or two later, the audit question is not what the total was
   but why a given trade landed in a given bucket.

5. **Segregation** — the valid buckets differ by jurisdiction:
   - *India*: route `SPECULATIVE_BUSINESS` PnL to the speculative pool (s.73: it can only
     be set off against speculative business income). Route `NON_SPECULATIVE_BUSINESS` to
     the general business pool, against which server and data expenses may be deducted.
     Route `SHORT_TERM_CAPITAL_GAINS` and `LONG_TERM_CAPITAL_GAINS` to the investment pool.
   - *United States*: `SHORT_TERM_CAPITAL_GAINS` and `LONG_TERM_CAPITAL_GAINS` to
     Schedule D / Form 8949; `BUSINESS_INCOME` (a s.475(f) election is in force) to
     Form 4797; `SECTION_1256_60_40` to Form 6781 via
     `section-1256-contract-tax-treatment-us-futures`.
   - *Canada*: `CAPITAL_GAINS` to Schedule 3; `BUSINESS_INCOME` is fully taxable rather
     than subject to the inclusion rate. There is no long-term bucket to route to.

6. **Apply the Adjustments This Engine Does Not**: wash sales / superficial losses, lot
   selection (FIFO vs specific identification), and currency conversion of foreign-currency
   PnL all happen outside this skill and can change the figures materially.

7. **Reporting**: Generate the final aggregated ledger for the firm's accountant, stating
   the jurisdiction and the elections assumed on the face of the report so that a wrong
   assumption is visible rather than buried in a config file.
