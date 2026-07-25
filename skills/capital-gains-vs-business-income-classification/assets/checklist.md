# Pre-Flight Checklist

- [ ] Are all options and futures trades explicitly mapped to `AssetClass.DERIVATIVE` so they aren't misclassified as STCG?
- [ ] Is the engine correctly identifying Intraday trades (same-day open and close) as `SPECULATIVE_BUSINESS`?
- [ ] Have you ensured that algorithm hosting costs, data feeds, and execution fees are only deducted against the Business Income buckets, not Capital Gains?