---
name: backtest-outlier-and-bad-tick-filtering
description: >-
  Use when ingesting raw tick or bar files that contain fat-finger prints, test
  messages, zero prices and out-of-sequence ticks, so one bad print does not create a
  phantom signal. Every purge also removes a price a stop might genuinely have hit.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: backtesting-methodology
  tags: backtesting-methodology, bad-tick-filtering, outlier-detection, data-cleaning, median-filter, price-spikes
  brokers_frameworks: "Outlier Bad Tick Filter Engine; Python; NIST/SEMATECH e-Handbook; FINRA Clearly Erroneous Transactions"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill during data ingestion prior to backtesting. Raw exchange tick files frequently contain anomalous data points: fat-finger bad prints ($10.00$ on a $100.00$ stock), test messages, corrupt quotes during opening auction, or zero-price trades. If unfiltered, a single erroneous low print can trigger a false $+900\%$ momentum signal or hit a stop loss in backtest simulation that could never occur in reality.

## When NOT to Use

- **Not for stop-loss, liquidation, or margin-call realism.** Every purge deletes a price the backtest will never see. If a print was genuinely executable, removing it means your simulated stop never fires — the mirror image of the bias this skill exists to prevent. Filter the signal series, keep the raw series for execution modelling.
- **Not a substitute for the venue's own erroneous-trade record.** Exchanges break clearly erroneous trades and publish the list. Where that record exists it is authoritative; a statistical filter is a fallback for data you cannot reconcile.
- **Not for detecting stale or out-of-sequence ticks.** Despite the description's wording, this implementation reads prices only. It has no timestamps, so it cannot detect a repeated stale quote, a sequence gap, or an out-of-order print. Use `sequence-number-gap-detection-for-feeds` and `clock-skew-correction-for-tick-timestamps`.
- **Not for low-frequency bar series.** A rolling window of 21 daily bars spans a month; the median will not track a trend, and legitimate moves get flagged. This is a tick and high-frequency-bar tool.
- **Not a returns-based test.** It measures deviation from a rolling *level*, so a fast directional move drifts away from the trailing median and produces a low but non-zero false-positive rate. Measure that rate on a known-clean segment before trusting the settings.

## Prerequisites

- Raw price time series (ticks or high-frequency bars), chronologically ordered.
- Rolling window size $W$ (e.g., 21 ticks) and outlier threshold $Z_{\text{max}}$ (e.g., 5.0 MAD deviations).
- The instrument's minimum price variation (tick size), for the `min_deviation` floor.
- Any array parallel to the prices — timestamps, volumes, venue codes — that must be realigned after filtering using `report.kept_indices`.

## Workflow

1. **Compute Rolling Median & Median Absolute Deviation (MAD)** over a strictly **trailing** window of already-accepted prices, so the decision for tick $i$ never reads tick $i+1$:
   $$\text{MAD} = \text{median}(|P_i - \text{median}(P)|)$$

2. **Evaluate Modified Z-Score** (Iglewicz–Hoaglin, per NIST §1.3.5.17):
   $$Z_i = \frac{0.6745 \cdot |P_i - \text{median}(P)|}{\text{MAD}}$$

3. **Filter Outliers**: Purge non-positive prices, non-finite prices, single-tick jumps beyond $\Delta_{\text{max}}\%$, and prices where $Z_i > Z_{\text{max}}$. The test is applied in price units with an additive floor, $|P_i - \text{median}| > Z_{\text{max}} \cdot \text{MAD}/0.6745 + \gamma$, following Brownlees & Gallo (2006). Set $\gamma$ (`min_deviation`) to the tick size — **at $\gamma = 0$ the MAD test is skipped entirely whenever MAD is zero**, which is every flat window, and `report.mad_test_skipped_count` tells you how often that happened.

4. **Decide Whether a Run of Outliers Is a Level Shift**: After `max_consecutive_drops` consecutive flags, the run is reinterpreted as a genuine gap (split, news, halt reopen) rather than bad prints. The prints purged on the way in are then **restored**, because the evidence says they were real, and the MAD window restarts at the new level. Set `restore_ticks_on_regime_change=False` for a strictly causal pass matching a live streaming filter — at the cost of permanently losing the ticks at each shift.

5. **Generate Data Cleanliness Report**: Report total raw ticks, bad ticks purged, and percent dataset sanitized. Realign every parallel array with `report.kept_indices` before using the cleaned series.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Mean/StdDev Instead of Median/MAD**: Outliers heavily distort mean and standard deviation, masking subsequent bad prints.
- **Over-filtering Real Volatility Spikes**: Setting $Z_{\text{max}}$ too low (e.g. 2.0) and truncating genuine flash crash price moves.
- **NaN Passing Every Test**: Every comparison against NaN is False, so an unguarded filter accepts it, and one NaN then corrupts every subsequent median. Non-finite values must be rejected explicitly, before any comparison.
- **Trusting a Flat Window**: When more than half a window shares one value, MAD is exactly zero and the modified Z-score is undefined. Skipping the test there silently leaves quiet stretches unscreened; dividing anyway raises `ZeroDivisionError`. Use the $\gamma$ floor.
- **A Bad First Print Anchoring the File**: A trailing filter has no history before the first tick, so an erroneous opening print is accepted and becomes the reference for the jump test. Validate the first $W$ ticks against an external reference, or discard the warm-up.
- **Deleting Real Data at Every Genuine Gap**: A naive consecutive-drop rule purges the leading ticks of every split or news gap. Worse, if the rolling window is not restarted at the new level it stays contaminated for $W$ more ticks and keeps re-flagging good prints — measured at 22 real ticks lost from a single level shift before this was fixed.
- **Losing Timestamp Alignment**: The function returns bare prices. Reusing the original timestamp array against the cleaned series silently shifts every observation. Use `kept_indices`.
- **Treating 20% as a Standard**: It is this implementation's default, not a published threshold. For US OTC equity securities FINRA's clearly-erroneous guidelines run from 20% (sub-$1) down to 3% ($1,000+), and LULD bands for NMS stocks above $3.00 are 5% (Tier 1) or 10% (Tier 2). A flat 20% is far too loose for a liquid equity. See `references/standards.md`.

## Verification

- Inject bad tick ($P=10.0$ into $100.0$ series), verify bad tick detection and removal.
- Inject `float("nan")` and `float("inf")` and assert neither appears in the cleaned series.
- Feed a permanent level shift and assert no genuine tick is lost and exactly one regime change is reported.
- Assert the hand-computed MAD boundary: window $[10,11,12,13,14]$ has median $12$ and MAD $1$, so with $Z_{\text{max}}=5$ the limit is $5/0.6745 = 7.4129$; $19.42$ must be purged and $19.41$ kept.
- Run the filter over a segment known to be clean and confirm the purge rate is acceptably low.
- Run `python -m unittest discover -s skills/backtest-outlier-and-bad-tick-filtering/scripts` and confirm 100% pass rate.

## Related Skills

- `data-vendor-cross-validation-for-backtests`
- `backtest-determinism-and-reproducibility`
- `multi-source-price-reconciliation-tie-breaking`
- `sequence-number-gap-detection-for-feeds`
---
