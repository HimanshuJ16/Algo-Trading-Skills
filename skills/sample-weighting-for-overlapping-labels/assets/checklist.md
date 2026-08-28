# Pre-Flight Checklist — Sample Weighting for Overlapping Labels

Sign off before a weighted training run is used to make a capital decision.

## Inputs

- [ ] Every training row has a label span with **both endpoints as inclusive bar
      indices** (not timestamps, not epoch seconds, not exclusive ends).
- [ ] Spans whose vertical barrier ran past the end of the sample were truncated
      by the labeller, not left open or dropped silently.
- [ ] `sample_id` is unique across the set and is the same key the training
      matrix is indexed by.
- [ ] No inverted span (`end < start`), no non-finite `realized_return` — the
      engine raises on both; confirm the pipeline does not swallow the error.
- [ ] For exact return attribution: `bar_log_returns` are **log** returns and
      cover every bar of every span.

## Weighting

- [ ] Concurrency $c_t$ computed over the same span set the weights are for.
- [ ] Dataset average uniqueness $\bar{u}$ recorded, and the effective sample
      size $\bar{u}N$ — not $N$ — used when judging statistical significance.
- [ ] Weighting method chosen deliberately: `UNIQUENESS_ONLY` for classification
      outcomes, `RETURN_ATTRIBUTED` when label magnitude matters, `TIME_DECAY`
      when older regimes should fade.
- [ ] If `RETURN_ATTRIBUTED`: `report.return_attribution_is_exact` checked. If
      `False`, the $u_i|r_i|$ approximation is a deliberate choice, recorded as
      such — not an unnoticed default.
- [ ] If `TIME_DECAY`: `time_decay_last_weight` is documented and inside
      $(-1, 1]$; zero-weighted oldest observations (negative settings) are an
      intended exclusion.
- [ ] `report.degenerate_uniform_fallback` is `False`. If `True`, the run is a
      data failure, not a weighting result.
- [ ] Normalised weights sum to $N$; nothing downstream rounds them before use.

## Hand-off

- [ ] Weights joined to the training matrix **by `sample_id`**, never by row
      position, and the join produced no nulls.
- [ ] `sample_weight` passed to `fit(...)` **and** to the scoring/metric call.
- [ ] Bagged learners: `max_samples` set to the average uniqueness (AFML §4.4),
      or sequential bootstrapping used.
- [ ] Class-imbalance weights, if any, multiplied in and the $\sum w_i = N$
      normalisation restored afterwards.

## Leakage control (separate from weighting)

- [ ] Cross-validation is **purged and embargoed**. Sample weighting is not a
      substitute and does not make an unpurged fold honest.
- [ ] Decision recorded on whether weights were computed per training fold or
      once over the full sample.
- [ ] `python -m unittest discover -s skills/sample-weighting-for-overlapping-labels/scripts`
      passes at 100%.
