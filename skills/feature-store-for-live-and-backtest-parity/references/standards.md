# Standards & Conventions — feature-store-for-live-and-backtest-parity

## Pipeline contract

| Mode | Input source | Window construction | State | Parity expectation |
|---|---|---|---|---|
| Offline batch | Historical bar matrix | `bars[i-lookback+1 : i+1]`, right-closed at the labelled bar | Stateless | Reference |
| Online streaming | Live bar stream | Bounded ring buffer, `maxlen = lookback` | Ring buffer + last-timestamp watermark | Exactly 0.0 difference against batch when both call one shared core |

The tolerance is a property of the *implementation*, not of the problem. With a genuinely
shared calculation core the two pipelines evaluate identical float operations on identical
inputs, so the correct assertion is `tolerance = 0.0`. A non-zero tolerance is only
warranted when the batch side has been re-implemented — vectorized NumPy/Pandas, a
different summation order, or GPU inference — and it should then be set from the
least-scaled feature, because an absolute threshold is far stricter in relative terms for
RSI (0–100) than for a one-bar return (order 1e-2). The reference engine's default of
`1e-6` is headroom for that case, not a target to relax toward.

## Numerical conventions that must be pinned explicitly

These are the concrete divergence sources. Each is silent: no exception, no NaN, just
values that are slightly and consistently wrong on one side.

| Convention | Library defaults in conflict | Reference engine | Evidence |
|---|---|---|---|
| Standard deviation divisor | `pandas.rolling().std()` → `ddof=1`; TA-Lib `TA_VAR` computes `sum_sq/n - mean²` → population; `pandas_ta.bbands` defaults `ddof=0` to match TA-Lib | `stddev_ddof=0` (population), overridable to 1 | TA-Lib `src/ta_func/ta_VAR.c`; pandas_ta `bbands` docs |
| RSI smoothing | Wilder (1978) uses recursive smoothing (RMA), so the value depends on where the history starts; Cutler's variant substitutes a simple moving average precisely to remove that path dependence | Cutler's RSI — the only one derivable from a bounded window | Wilder, *New Concepts in Technical Trading Systems* (1978); Cutler's RSI variant |
| RSI when gains and losses are both zero | Undefined (0/0). TA-Lib emits `0.0`; many platforms emit 50 or NaN | `50.0` (neutral), named constant | TA-Lib `src/ta_func/ta_RSI.c` zero-denominator branch |
| Bollinger band width | 2 standard deviations of the closing price over the same window as the moving average | `2.0 × σ`, window = 20 | Bollinger's canonical definition |
| Output precision | — | Full float precision; no rounding inside the shared core | Rounding to 4 dp makes any tolerance below ~5e-5 unenforceable |

## What replay parity does and does not prove

`validate_parity()` streams one bar series through both pipelines. It proves the two code
paths agree **on identical input**. It cannot see input-side skew: vendor bar revisions,
late or dropped ticks, a different bar-consolidation rule between the historical API and
the live socket, or a corporate-action adjustment applied to stored history but not to the
live feed. The complement is serving-time feature logging — record the vectors actually
served and reconcile them against the batch recomputation for the same timestamps.

Primary guidance: Zinkevich, *Rules of Machine Learning: Best Practices for ML
Engineering* — Rule #32 ("re-use code between your training pipeline and your serving
pipeline") is the mechanism this skill implements; Rule #29 ("save the set of features
used at serving time and pipe those features to a log to use them at training time") is
the data-side check replay cannot substitute for.
<https://developers.google.com/machine-learning/guides/rules-of-ml>

## Category

`financial-ml` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & operational notes

Model risk management guidance is relevant where a supervised institution is involved, and
its applicability is narrower than "any quant fund":

- **US banking organizations.** Federal Reserve **SR 26-2**, *Revised Guidance on Model
  Risk Management*, issued **17 April 2026** jointly by the Federal Reserve, OCC and FDIC,
  **supersedes and replaces SR 11-7** (4 April 2011) and SR 21-8. The OCC companion is
  Bulletin 2026-13, which also rescinds OCC Bulletin 2011-12. The Federal Reserve states
  the letter is "expected to be most relevant to banking organizations with over $30
  billion in total assets regulated by the Federal Reserve."
  <https://www.federalreserve.gov/supervisionreg/srletters/SR2602.pdf> ·
  <https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html>
  The relevant hook for this skill is process verification: evidence that the model as
  deployed computes what the validated model computes. A stored, dated parity report is
  that evidence.
- **Anyone else.** A proprietary trading firm, hedge fund, or individual is not subject to
  SR 26-2 or OCC 2026-13. Do not cite them as a compliance obligation outside the
  supervised perimeter — batch/live parity is an engineering control there, and its value
  is that the backtest keeps describing the system that is actually trading.

Non-regulatory but load-bearing: `## Verification` in `SKILL.md` is the acceptance gate,
and the parity assertion belongs in CI, not in a promotion checklist executed once.
