# Pre-Flight / Sign-off Checklist — backtest-outlier-and-bad-tick-filtering

Use this before considering the skill's implementation complete.

## Configuration

- [ ] **Jump threshold calibrated to the instrument**, not left at the 20.0 default. Checked against the venue's volatility band and erroneous-trade guideline (see `references/standards.md`).
- [ ] **`min_deviation` set to the instrument's tick size.** If left at 0, confirm `report.mad_test_skipped_count` is acceptably small — otherwise the series was screened by the jump rule alone.
- [ ] **`z_threshold` choice recorded**, with the reason for departing from the 3.5 NIST/Iglewicz–Hoaglin recommendation.
- [ ] **Window size suits the sampling frequency.** 21 ticks is not 21 daily bars.
- [ ] **Restoration mode chosen deliberately.** Default restores real ticks at a level shift; `restore_ticks_on_regime_change=False` is strictly causal and loses them.

## Data Integrity

- [ ] **Rolling median & MAD computed over a trailing window** — decision for tick $i$ reads nothing after $i$.
- [ ] **Modified Z-score implemented** as $0.6745\,|P_i-\tilde{x}|/\text{MAD}$, evaluated in price units so MAD $=0$ cannot divide by zero.
- [ ] **Single-tick price jump filter operational**, referenced to the last *accepted* price.
- [ ] **Non-finite rejection verified:** inject NaN and $\pm\infty$; confirm neither reaches the cleaned series.
- [ ] **Warm-up validated externally.** The first `window_size` ticks get no MAD screening; cross-checked or discarded.
- [ ] **Timestamps realigned via `report.kept_indices`** — never by reusing the raw timestamp array.
- [ ] **Report invariants hold:** `cleaned + purged == total`, and `kept_indices` and `purged_indices` partition the input range.

## Bias Control

- [ ] **False-positive rate measured** on a segment known to be clean, not assumed to be zero.
- [ ] **Genuine level shifts survive:** a split or news gap is preserved, with `regime_changes_detected` matching the number of real gaps.
- [ ] **Raw series retained** alongside the cleaned series, with purged indices and the filter configuration, so every deletion is reviewable and the pass is replayable.
- [ ] **Execution modelling uses the raw series.** Confirmed that stop-loss, liquidation, and margin logic is not reading a series with real crash prints removed.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/backtest-outlier-and-bad-tick-filtering/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Instrument / venue and thresholds used: ___________________________
