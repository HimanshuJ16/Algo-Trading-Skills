# Pre-Flight Checklist

## Keyword Integrity

- [ ] Has the keyword been screened for ambiguity in both directions — company name with another meaning ("Apple", "Amazon"), and ticker that is also a common word (`GPS`, `CAR`, `ALL`)?
- [ ] Is a Trends *topic* used instead of a raw search term where one exists, and is that choice recorded?
- [ ] Are geography and comparison set fixed for the life of the series?
- [ ] Was the keyword set held out or multiple-testing corrected, rather than chosen by backtest performance?

## Point-in-Time Integrity

- [ ] Is `as_of` supplied on every backtest call, so the publication lag is enforced and not just recorded?
- [ ] Is `publication_lag_hours` a **measured** property of this pipeline, rather than the placeholder default?
- [ ] Is `dropped_unobservable_points` non-zero somewhere in the run — i.e. is the filter demonstrably doing work?
- [ ] Are all timestamps timezone-aware, with 7-day pulls (local time) distinguished from 30-day-plus pulls (UTC)?
- [ ] Is the bucket timestamp stored separately from the retrieval timestamp?

## Baseline Statistics

- [ ] Does the baseline window strictly precede the observation being standardized (a series of $N$ points supports a baseline of at most $N-1$)?
- [ ] Is the reported `rolling_std_dev_svi` the observed value, never a floor or substitute?
- [ ] Do degenerate (flat) baselines emit `INSUFFICIENT_DATA` rather than a signal?
- [ ] For spike-prone keywords, has a median/MAD baseline been considered given that mean/σ baselines are contaminated by earlier spikes?

## Data Handling

- [ ] Is the series sorted by timestamp and free of duplicate buckets before scoring?
- [ ] Are 0–100 values from different requests, ranges or geographies kept apart, or re-normalized on an overlap before splicing?
- [ ] Is `svi_scale_max` set correctly for the source (100 for UI/pytrends, `None` for the consistently-scaled official API)?
- [ ] Are raw pulls persisted immutably, given that a re-pull will not reproduce them?

## Signal Quality

- [ ] Have `lookback_window` and `z_score_threshold` been recalibrated out-of-sample instead of inherited from the defaults?
- [ ] Has the sign and horizon of the relationship been estimated, rather than read off the `BULLISH_`/`BEARISH_` labels?
- [ ] Is `is_attention_spike` checked alongside `signal_type`, so an undirected spike (momentum exactly 0) is not mistaken for a quiet period?
- [ ] Is this feature combined with a return model, cost model and sizing rule before it is allowed to size anything?
