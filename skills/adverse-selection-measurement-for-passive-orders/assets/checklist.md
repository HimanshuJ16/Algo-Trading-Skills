# Pre-Promotion / Sign-off Checklist — adverse-selection-measurement-for-passive-orders

Use this before acting on a toxicity verdict or scaling a passive strategy's
capital based on its markout curve.

---

## 1. Fill data integrity

- [ ] **Passive only** — market/aggressing fills filtered out upstream; every
      row passed to the engine is a resting fill.
- [ ] **Side correct** — `BUY`/`SELL` matches the ledger (a flipped side
      inverts the entire sell curve).
- [ ] **Price/quantity positive & finite** — `PassiveFill` validates; verify
      no zero/negative rows slipped through pre-filtering.
- [ ] **Clock base** — fill timestamps share the epoch and time base of the
      market-data series.

## 2. Market-data window

- [ ] **Strictly ascending timestamps** — engine validates; preserve if
      pre-processing externally.
- [ ] **Forward coverage** — window extends to `max(fill_ts) + max(horizons)`;
      `stats[h].truncated == 0` on a full-day sample.
- [ ] **Backward coverage** — window starts at or before `min(fill_ts)`;
      `missing_pre_fill == 0` on a full-day sample.
- [ ] **Mid quality** — mids are finite, positive, from a clean L1 source
      (`multi-source-price-reconciliation-tie-breaking` if gappy).
- [ ] **Mid freshness** — `stale_asof_mid == 0` and `stats[h].stale == 0`; any
      non-zero count was fixed at the feed, not by relaxing the bound.
- [ ] **Resolution supports the shortest horizon** — the mid series updates
      more often than `min(horizons_sec)`. A horizon below the quote-update
      cadence resolves to the same prevailing mid as the next one up and cannot
      be measured, however cleanly it reports.

## 3. Configuration

- [ ] **Horizons span ≥ 2 orders of magnitude** (e.g. 100 ms, 1 s, 10 s) — a
      single horizon cannot diagnose latency vs directional toxicity.
- [ ] **Includes a sub-second horizon** — microstructure toxicity lives in
      ms; an EOD-only or 1-min-only curve hides it.
- [ ] **`markout_basis` chosen deliberately** — `fill_to_mid` for execution
      quality, `arrival_to_mid` to isolate adverse drift; run both and compare.
- [ ] **`quantity_weighted`** — True for notional-aware aggregation (default
      for production); record unweighted for comparison.
- [ ] **`require_asof_mid = True`** — never disabled in backtests. It guards
      both bases, not just `arrival_to_mid`.
- [ ] **`max_mid_staleness_sec` set deliberately** — derived from this
      instrument's quote-update cadence and recorded with the run. Leaving it
      `None` means an over-age mid is scored as if it were current.

## 4. Verdict interpretation

- [ ] **`has_sufficient_data` is True** — checked *before* `is_toxic`. With
      `evaluable_horizons == 0` the verdict is "not measured", not "healthy",
      and must not be acted on or forwarded to a gate.
- [ ] **`evaluable_horizons == len(horizons_sec)`** — otherwise the curve is
      not comparable day-over-day, because `toxicity_ratio` is a share of the
      evaluable horizons only.
- [ ] **Read the distribution, not just the mean** — `median`, `p25`, `p75`
      reported per horizon; a mean-negative / median-positive split is not
      robustly toxic. Note these quantiles are **unweighted** even when
      `quantity_weighted=True`, which weights `mean_bps` only.
- [ ] **`count ≥ 30` per horizon** — otherwise gate on the median, not the
      mean; report the IQR.
- [ ] **`toxicity_ratio` read alongside `is_toxic`** — one negative horizon
      among six is not "toxic"; a uniformly negative curve is.
- [ ] **Curve shape diagnosed** — short-horizon negative (latency) vs long-
      horizon negative (directional) route to different remediation skills.

## 5. Reproducibility

- [ ] **Frozen fill ledger + market snapshot** — same inputs reproduce the
      same `as_dict()`.
- [ ] **Report persisted** to the day's TCA / model card with `horizons_sec`,
      `markout_basis`, `quantity_weighted`, `fills_used`, `missing_pre_fill`.
- [ ] **Day-over-day comparison** — a regression in the curve (e.g. 1 s
      markout -8 → -15 bps) is investigated before it hits P&L.

## 6. Action

- [ ] **Latency-shape toxicity** routed to `tick-to-trade-latency-measurement`.
- [ ] **Directional toxicity** routed to signal/alpha review.
- [ ] **Persistently toxic curve** considered as a strategy-level kill-switch
      trigger (`kill-switch-and-drawdown-circuit-breakers`).
- [ ] **Any automated gate reading `is_toxic` also reads `has_sufficient_data`**
      — verified in the consuming code, not assumed.

## Sign-off

- Execution Quant: ___________________________
- Date: ___________________________
- Symbol / strategy: ___________________________
- `MarkoutConfig` snapshot (paste JSON): ___________________________
- `AdverseSelectionReport.as_dict()` snapshot: ___________________________
- `missing_pre_fill` / `stale_asof_mid` / `truncated` / `stale` audit (all must
  be 0): ___________________________
- `evaluable_horizons` / `has_sufficient_data`: ___________________________
