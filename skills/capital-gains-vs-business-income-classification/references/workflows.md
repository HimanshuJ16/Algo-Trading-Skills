# Workflows for Tax Classification

1. **End-of-Day Reconciliation**: Download raw fill logs from the broker and reconstruct "Closed Trades" (paired entries and exits).
2. **Execution**: Pass the list of `ClosedTrade` objects to the `TaxClassificationEngine.classify_portfolio()`.
3. **Segregation**: 
   - Route `TaxCategory.SPECULATIVE_BUSINESS` PnL to the speculative tax bucket (cannot offset non-speculative).
   - Route `TaxCategory.NON_SPECULATIVE_BUSINESS` PnL to the general business bucket (can deduct server/data expenses).
   - Route `TaxCategory.SHORT_TERM_CAPITAL_GAINS` and `LONG_TERM_CAPITAL_GAINS` to the investment bucket.
4. **Reporting**: Generate the final aggregated ledger for the firm's accountant.