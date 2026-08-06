# Workflows for Synthetic Continuous Futures Contract Construction

1. **Multi-Contract Data Ingestion**:
   - Ingest OHLCV data for all contract expirations.
2. **Roll Condition Monitoring**:
   - Monitor volume/OI crossover between front and back contracts.
3. **Back-Adjustment Execution**:
   - Subtract cumulative roll gap from historical front contract prices.
4. **Continuous Series Serialization**:
   - Export continuous DataFrame with active contract identifier tags.
