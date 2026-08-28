# Workflows for New Strategy Onboarding Checklist

## 0. Assemble the package — the step that decides whether any of this is worth anything

The engine evaluates attestations. Who supplies each one is the difference between a
governance control and a formality.

| Field | Should be supplied by | Evidence the attester should be holding |
|---|---|---|
| `walk_forward_score`, `regimes_covered`, `backtest_sharpe` | Research, reproduced by a second person | The walk-forward run and the regime breakdown, re-executed from committed code and pinned data |
| `paper_trading_days`, `paper_trading_errors` | Operations / whoever owns the paper deployment | The paper run's logs and its error/exception record, not a recollection |
| `kill_switch_integrated` | Risk | A record of the switch actually being fired against this strategy in the paper environment |
| `model_card_completed` | Model risk | The model card itself, reviewed against `model-card-documentation-for-trading-models` |
| `compliance_approved` | Compliance | The sign-off, dated and attributable |

`author` records who **submitted** the package. It does not record who attested to any
individual claim; if that distinction matters for your audit trail, carry it alongside
the report.

## 1. Validate before gating

Malformed input raises `ValueError`; it does not produce a verdict.

- Attestation flags must be real `bool`. Strings, `0`/`1`, `None` and containers are
  rejected rather than coerced. `bool("false")` is `True` — a coerced payload would
  pass the gate that exists to stop it.
- `walk_forward_score` and `backtest_sharpe` must be finite. `NaN` fails every
  comparison, so a corrupt metric would read as a weak strategy rather than as corrupt
  data. `+inf` is what a zero-variance return series produces and would clear any floor.
- Counts must be non-negative `int`. `bool` is rejected explicitly: `True` is an `int`
  in Python and would silently mean "one day".
- `strategy_id`, `strategy_name` and `author` must be non-blank. An audit record keyed
  by an empty identifier is not an audit record.

## 2. Gate 1 — Backtest robustness

Walk-forward score, regime coverage and backtest Sharpe, each against an inclusive
floor (exactly at the threshold passes).

Read `failed_criteria`, not just `passed`. The gate bundles three independent
conditions, and "BACKTEST_GATE failed" spans everything from a Sharpe of 1.49 to a
backtest that only ever saw a bull market.

On the Sharpe floor specifically: it screens out visibly broken strategies. It is not
evidence of edge, and tightening it selects harder for over-fitting — see
`references/standards.md`.

## 3. Gate 2 — Operational runtime

Paper-trading duration, critical execution errors, kill-switch integration.

- Duration is not coverage. Fourteen calm days demonstrate that the plumbing holds,
  not that the strategy survives stress. If the paper window contained no volatile
  session, record that in the sign-off instead of letting the day count stand as the
  finding.
- A zero error count inherits the reliability of the error detection behind it. Zero
  from a system that never logged anything is not a clean run.
- Kill-switch **integration** is not kill-switch **proof**. A switch that has never
  been fired against this strategy in this deployment has been wired, not validated.

## 4. Gate 3 — Model risk documentation

A completed model card exists. Existence only — the engine cannot read the document.
Whether it records parameter limits, decay conditions and known failure modes is a
human review against `model-card-documentation-for-trading-models`.

## 5. Gate 4 — Compliance sign-off

Compliance sign-off is recorded. For an EU or UK investment firm, note the gap this
leaves: RTS 6 Article 5(2) requires authorisation of the deployment by a person
designated by senior management. A recorded compliance sign-off is not automatically
that authorisation.

## 6. Report generation and retention

Output the `OnboardingAuditReport` and persist it whole:

- `status` / `is_onboarding_approved` — the verdict. Conjunctive: all four gates or
  none. `total_gates_passed` is a diagnostic, never a score, and there is no waiver
  path or override flag.
- `failed_gates` and each gate's `failed_criteria` — machine-readable reasons.
- `policy_applied` — **the thresholds actually used**. Without it the verdict is
  unfalsifiable: a config of zeros emits the identical `ONBOARDING_PASSED` string as
  the strict default.
- `policy_weakened` — any threshold set below the shipped default, so a deliberately
  relaxed gate is visible in the record rather than inferable only by someone who
  remembers what the defaults were.

The report is a point-in-time snapshot. It has no expiry, no re-audit tracking and no
portfolio view. A substantial update to the strategy is a new deployment decision, not
a continuation of this one.

## 7. What happens after a pass

An onboarding pass says "may begin", not "deploy at target size":

1. Obtain the deployment authorisation (RTS 6 Art. 5(2) where applicable).
2. Set the predefined deployment limits (RTS 6 Art. 8) — instruments, order
   price/value/count, strategy positions, venues.
3. Ramp capital in stages — `incremental-capital-deployment-for-new-strategies`.
4. Keep the strategy-level and portfolio-level kill switches independent of strategy
   logic — `kill-switch-and-drawdown-circuit-breakers`.
