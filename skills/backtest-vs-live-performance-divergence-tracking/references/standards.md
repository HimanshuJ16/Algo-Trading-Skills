# Backtesting Methodology Standards — backtest-vs-live-performance-divergence-tracking

## Configuration Defaults — Not Published Standards

No regulator, exchange, or standards body prescribes backtest-vs-live divergence limits.
A search for an authoritative threshold found only vendor blogs and rules of thumb
("discount backtest Sharpe by 50%"), none of which is a standard. **Every number below
is an implementation default**, chosen to be plausible, and every one is a constructor
argument. Calibrate them against the divergence your own strategy population actually
exhibits before wiring the output to a suspension workflow.

| Divergence Metric | Comparison Basis | Warning Default | Critical Default |
|---|---|---|---|
| Sharpe Decay | relative decay % | $\ge 20\%$ | $\ge 50\%$ |
| Drawdown Blow-Up | ratio to backtest | $\ge 1.5\times$ | $\ge 2.0\times$ |
| Win Rate Decay | relative decay % | $\ge 10\%$ | $\ge 25\%$ |
| Fill Rate Gap | percentage-point gap | $\ge 5$ pts | $\ge 15$ pts |
| Slippage Amplification | ratio to backtest | $\ge 2.0\times$ | $\ge 4.0\times$ |

Thresholds are **inclusive**. A value exactly equal to a threshold classifies at that
level — fail-closed is the correct direction for a control that gates live capital.

## Three Comparison Bases, One Scale Per Metric

The five metrics are not commensurable, which was previously invisible in the output:
`divergence_pct` held a percentage for every metric while `threshold_warning` held a
multiplier for two of them, so a dashboard comparing $80.0$ against $1.5$ was comparing
nothing.

Each `DivergenceMetric` now carries:

- `comparison_value` — the quantity actually classified, on the same scale as both
  thresholds. **This is the field to compare against a threshold.**
- `comparison_basis` — one of `relative decay %`, `ratio to backtest`,
  `percentage-point gap`.
- `divergence_pct` — a human-readable percentage for display. Not on the threshold scale
  for the two ratio metrics.
- `notes` — why a comparison could not be formed, when applicable.

Note the deliberate asymmetry: **win rate uses relative decay, fill rate uses an absolute
percentage-point gap.** A fill rate falling from 99% to 94% is 5 points, which matters
operationally regardless of the base; a win rate falling from 80% to 76% is a 5% relative
move, which is what matters statistically. Both are percentages; they are not compared
the same way.

## Undefined Comparisons Are Never Acceptable

A ratio or relative decay cannot be formed against a zero or non-positive baseline. The
rule is:

| Baseline | Live | Result |
|---|---|---|
| positive | any | normal comparison |
| non-positive, equal to live | equal | `ACCEPTABLE`, nothing diverged |
| non-positive, differs from live | differs | `WARNING` + `notes`, never `ACCEPTABLE` |

This exists because the degenerate cases are the dangerous ones, not the harmless ones:

- **Zero backtest slippage** is the single most common backtest omission. Defaulting the
  amplification ratio to $1\times$ certified unlimited live execution cost as acceptable.
- **Zero backtest drawdown** means the drawdown dimension was never modelled at all.
- **Non-positive backtest Sharpe** means the strategy should not have been promoted; the
  relative-decay question is secondary.

## Drawdown Sign Convention

Either convention is accepted — $-15.0$ or $15.0$ — and **magnitudes are compared**. Both
snapshots must use the same one; opposite non-zero signs raise, because that indicates the
two snapshots were sourced from different systems rather than that anything diverged.

This matters inside this repository: `backtest-reporting-standardized-tearsheet` reports
`Max Drawdown` as a **negative** fraction. A guard of the form
`if backtest.max_drawdown_pct > 0` silently skips the entire comparison when fed that
output, and a live drawdown five times the backtest reports `ACCEPTABLE`.

## Rounding Before Classification

The classified value is rounded to 6 decimal places before comparison. Without it, a
Sharpe of $2.0$ decaying to $1.6$ — exactly $20\%$ — computes in binary floating point as
$19.999999999999996$ and lands on the opposite side of a $20\%$ threshold from the $20.0$
the report displays. For an auditable control, the number shown and the number compared
must be the same number.

## Rejected Inputs

Rejected with `DivergenceTrackerError` rather than classified:

- non-finite metric values — `max(0.0, nan)` returns `0.0` and `nan >= threshold` is
  `False`, so an unguarded NaN reported no divergence and no suspension on every metric
  it touched;
- win rate or fill rate outside $[0, 100]$ — a win rate passed as the fraction `0.55`
  would be read as $0.55\%$ and produce a $99\%$ decay;
- opposite drawdown sign conventions between the two snapshots;
- a warning threshold above its critical counterpart, which inverts the severity ladder;
- non-positive `observation_periods`, and a blank strategy name.

## Sample Adequacy

`min_live_observations` defaults to `0` (disabled). When set, and when both snapshots
carry `observation_periods`, a live sample below the minimum sets
`is_sample_adequate=False` and prefixes the message.

**Severity is deliberately not downgraded.** A short sample makes the verdict unreliable
in both directions: it can invent a divergence that is not there, and it can hide one that
is. Downgrading would turn an unreliable signal into a false all-clear.

No minimum is prescribed here, because the statistically adequate sample depends on the
strategy's trade frequency and return distribution. The parameter exists so the caller can
apply their own.

## Regulatory Scope

This tool produces a **periodic** comparison of two snapshots. It is not real-time
monitoring and does not detect intra-session misbehaviour.

EU investment firms engaged in algorithmic trading are subject to a real-time monitoring
obligation under Article 16 of RTS 6 (Commission Delegated Regulation (EU) 2017/589),
which requires monitoring of algorithmic trading activity for signs of disorderly trading
during the hours orders are being sent. A divergence report does not satisfy that
obligation, and the two address different risks on different timescales.

**Sourcing note:** EUR-Lex did not return retrievable content during this review across
three attempts (HTML, ELI and PDF endpoints). The article number and title above are
corroborated from secondary reproductions of RTS 6, including a regulator-hosted copy,
but were **not read from the primary text**. Verify against EUR-Lex before relying on the
citation. Applicability is EU/UK-assimilated investment firms; the US analogue is SEC Rule
15c3-5. Neither imposes requirements on the arithmetic in this module. Nothing here is
legal advice — see `mifid-ii-algo-trading-compliance-eu` and
`sec-rule-15c3-5-risk-controls-us`, which own this surface.

## Scope Boundary

This module compares two supplied snapshots. It does not compute the metrics, source
them, verify that the two windows are comparable, attribute a cause to any divergence,
correct for the multiple strategies being monitored, or suspend anything. It emits a
recommendation; the suspension workflow is the caller's.

## Category

`backtesting-methodology` — see top-level `mappings/` directory.
