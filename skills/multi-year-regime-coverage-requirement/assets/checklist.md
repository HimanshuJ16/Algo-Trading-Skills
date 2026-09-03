# Pre-Flight / Sign-off Checklist — multi-year-regime-coverage-requirement

Use this before signing off a backtest for promotion to live capital.

## Inputs

- [ ] **Bar frequency declared:** `bars_per_year` matches the actual bar frequency (252 for daily). It is *not* left at the default for intraday data.
- [ ] **Series is gap-free:** duration is a bar count, not a calendar span — confirm no missing months are being counted as elapsed time.
- [ ] **Alignment confirmed:** `strategy_returns[i]` is the return over the bar ending at `prices[i]`, and the two series are the same length.
- [ ] **Excess returns:** if the risk-free rate is non-zero, it has been subtracted upstream; otherwise the "Sharpe" is a return-to-variability ratio.
- [ ] **Data cleaned:** no non-finite or non-positive prices, no non-finite returns, no return at or below $-100\%$. (The engine raises on all of these — a clean run is the evidence.)

## Coverage

- [ ] **Warm-up accounted for:** `unclassified_bars` reviewed; those bars are excluded from every metric.
- [ ] **Duration gate:** `total_years >= min_required_years`, checked unrounded.
- [ ] **Regime gate:** `unique_regimes_covered` has at least `min_required_regimes` entries.
- [ ] **Thin regimes reviewed:** anything in `regimes_observed` but not in `unique_regimes_covered` has been looked at — a regime present for 5 bars is not coverage, and the report says so.
- [ ] **Episode counts checked:** a regime spanning one episode is weaker evidence than the same bar count spread across several.

## De-averaged Performance

- [ ] **Per-regime breakdown reviewed**, not the aggregate — the worst regime is the finding.
- [ ] **Undefined Sharpe rendered as undefined:** `sharpe_ratio is None` is shown as "not measurable", never coerced to 0 or dropped from the report.
- [ ] **Correct drawdown quoted:** `max_drawdown_pct` (within-episode, experienced) is the one used for the decision; `concatenated_drawdown_pct` is labelled as synthetic wherever it appears.

## Decision

- [ ] **`is_promotable` read together with `vetoed_regimes`:** an empty `vetoed_regimes` means no drawdown breach, whatever else failed.
- [ ] **Failure reasons distinguished:** insufficient duration, insufficient regimes, and a drawdown veto are recorded as separate findings.
- [ ] **Labels not overstated externally:** `BEAR_MARKET` here is a 20-bar $-3\%$ bucket, not the SEC's 20%-over-two-months definition. No external document claims otherwise.
- [ ] **Parameters archived with the report:** `window_size`, `bars_per_year`, `min_bars_per_regime`, `high_vol_annualized_threshold`, `trend_threshold_pct`, and all three gates. Without them the audit is not reproducible.
- [ ] **Complementary gates run:** this audit is necessary, not sufficient — look-ahead, walk-forward, and capacity checks completed separately.

## Automated Testing

- [ ] Run `python -m unittest discover -s skills/multi-year-regime-coverage-requirement/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Engine parameters used: ___________________________
