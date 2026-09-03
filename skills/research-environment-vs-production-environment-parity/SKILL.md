---
name: research-environment-vs-production-environment-parity
description: >-
  Use when promoting a model or feature pipeline out of notebooks into live execution
  and the question is whether production computes the same number, auditing runtime,
  package and data-path parity vectors. Deployment gating is
  environment-parity-dev-staging-production.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: quant-research-alt-data
  tags: environment-parity, research-vs-production, mlops, signal-drift, feature-parity, precision-mismatch, promotion-gate, shadow-execution
  brokers_frameworks: "Parity Audit Framework; Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill at the moment a strategy stops being research and starts being capital
at risk: the promotion of a model, alpha signal or feature pipeline from a notebook or
offline backtester into the live execution path. It answers one question — *will the
production path compute the same numbers the backtest was validated on?*

The five vectors it audits are the ones whose drift is silent rather than loud. Nothing
crashes when production resolves `numpy` 2.1 against research's 1.26, when the live
feature service still runs last quarter's MACD, or when the production model emits NaN
for an instrument that was never in the research universe. The backtest keeps looking
excellent; only the P&L disagrees.

It sits below `environment-parity-dev-staging-production`, which gates a *deployment*
across DEV/STAGING/PROD on lockfile hashes, schema heads and broker endpoint mode. This
skill is about the *numbers*: same interpreter, same numerics libraries, same precision,
same feature definitions, same signal values on identical inputs.

## When NOT to Use

- **As evidence about a running host.** Every value audited is *declared* by the caller
  in an `EnvironmentSnapshot`. Nothing imports a package, inspects an interpreter or
  runs a model. A snapshot hand-written to match the other side passes cleanly. Collect
  the values from the live target — `importlib.metadata.version()`,
  `platform.python_version()`, the feature registry — never from a config file that
  describes it.
- **As a data-parity check.** Whether research and production see the same *input* bars
  is a different problem, and this module does not touch it. Use
  `data-pipeline-schema-contract-testing` and
  `point-in-time-database-for-ml-training-data`.
- **As a latency or timing check.** Signal *values* are compared, never when they
  arrived. A production path that computes the right number 400 ms too late passes this
  audit. See `model-inference-latency-budget-for-live-trading` and
  `strategy-latency-budget-decomposition`.
- **As ongoing live monitoring.** This is a point-in-time gate run before promotion and
  after any material change, not a running divergence tracker. For the post-promotion
  question — is realized performance drifting from backtested performance — use
  `backtest-vs-live-performance-divergence-tracking` and `model-staleness-detection`.
- **As proof that two feature implementations agree.** A matching hash proves the two
  recorded *strings* match. If research hashes Python source and production hashes a
  C++ translation, the vector is permanent noise; if both sides hash the same stale
  manifest, they match while the deployed code differs. The hash is only as good as
  what you hash. `feature-store-for-live-and-backtest-parity` removes the problem
  instead of auditing it, by making both paths call one implementation.
- **As a substitute for a separate testing environment.** EU firms in scope of RTS 6
  Article 7 must test in an environment *separated* from production. Auditing parity
  between two environments does not discharge the obligation to have two.

## Prerequisites

- An `EnvironmentSnapshot` per side, with `env_type` exactly `RESEARCH` and
  `PRODUCTION`. The role is checked, not trusted as a label — both arguments share a
  type, so a swapped call would otherwise report the drift backwards.
- `python_version` as a full `major.minor.patch` release (`platform.python_version()`
  returns it in the right form). `3.11` is rejected.
- `package_versions` and `feature_definitions` both **non-empty**. An empty map is
  rejected rather than audited: it would compare nothing and certify parity on no
  evidence. Package names are normalized per PEP 503, so `scikit_learn` and
  `scikit-learn` are one package.
- `float_precision` naming a specific format. `float64`, `double`, `fp64` and `binary64`
  all canonicalize to `float64`; bare `float` is rejected as ambiguous, because it means
  binary64 in Python and binary32 in C.
- For step 5, paired `(research_signal, production_signal)` outputs from both models on
  **identical** inputs. Pass `None` if shadow diffing was not run; an empty list raises.

## Workflow

1. **Collect both snapshots from the live targets, and let collection fail loudly.**
   - **Decision point — a value you could not resolve is not an empty string.** Every
     blank field, and every empty map, raises `ValueError` rather than being audited.
     This is the single most important behaviour in the module: under naive equality,
     two environments that both failed to enumerate their packages compare `{} == {}`,
     find zero discrepancies and report `PARITY_VERIFIED` on no evidence at all. If a
     collection step fails, fail the promotion step.

2. **Audit the CPython release.**
   A differing `major.minor` blocks; a differing patch is a non-blocking warning.
   - **Decision point — do not wave through a minor-release difference because the
     package pins match.** Compiled extensions are built against version-specific ABI
     tags (`cp310`, `cp311`); `abi3` exists precisely because those tags do not carry
     across minor releases. `numpy==1.26.4` under 3.10 and under 3.11 is the same
     version string and a different compiled binary.

3. **Audit installed package versions.**
   Blocking when a package is installed on one side only, when the major version
   differs, or when a *numerically relevant* package drifts at all. Advisory otherwise.
   - **Decision point — decide which drift is numerically relevant for your stack, once,
     in configuration.** The default `numerically_critical_packages` set covers the
     usual numerics and ML stack; a linter or test runner is left advisory. Do not
     resolve an alert by deleting a package from the set — resolve it by aligning the
     environments, or by recording why that package cannot move a number.

4. **Audit declared floating-point precision.**
   - **Decision point — a precision mismatch blocks even when the signals agree.**
     Whether it moves a number depends on conditioning, which this gate cannot see. A
     well-conditioned EMA over 50,000 bars diverges by ~3e-7 relative between float32
     and float64 — inside tolerance. The one-pass variance `E[x²] − E[x]²` on a series
     with mean 45,000 returns 0.977 in float64 and **−2176.0** in float32. Passing
     signal diffing on today's sample is not evidence about tomorrow's.

5. **Audit feature definitions, then shadow-diff the signals.**
   Feature drift and one-sided definitions block. Signal samples are compared with
   `math.isclose` against a relative tolerance (default 0.1%) and an absolute floor.
   - **Decision point — a non-finite value is a computation failure, not a tolerance
     question.** NaN and infinity are intercepted *before* the tolerance comparison and
     recorded as CRITICAL. Never fold them into a numeric comparison: every comparison
     against NaN is `False`, so an unguarded tolerance check silently passes a
     production model that emits NaN.
   - **Decision point — sample the inputs that break things, not the easy ones.** Feed
     the illiquid names, the halted sessions, the first bars after a corporate action,
     and instruments with no history. Parity on 10,000 mid-session large-cap bars is
     the cheapest possible sample and the least informative.

6. **Gate on the verdict, and read what it does not cover.**
   - **Decision point — `PARITY_VERIFIED` from a static-only audit is not signal
     parity.** With `test_signals=None` the report sets `signal_diffing_performed` to
     False and says so in `audit_notes`. Branch on `is_parity_achieved`, then check
     that the vector you care about was actually exercised.
   - **Decision point — a verified verdict can still carry warnings.**
     `warning_discrepancies` does not block. Read it before closing the ticket.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Auditing missing evidence as parity.** The failure mode that makes a parity gate
  worse than no gate: a collection script returns empty package and feature maps, each
  compares equal to the equally-empty other side, and the promotion records
  `PARITY_VERIFIED`. Absent evidence must raise. Never default a parity field.
- **Letting NaN through the tolerance check.** `abs(research − production) > tol` is
  `False` when either value is NaN, so the single most common numeric production failure
  passes as parity. Two infinities are worse: `math.isclose(inf, inf)` is `True`. Test
  for finiteness first, always.
- **Filing dependency drift as a warning.** A version mismatch in `numpy`, `pandas` or
  `scipy` is the mechanism, not the symptom. NumPy 2.0's NEP 50 promotion change alone
  means `np.float32(3) + 3.` now returns float32 where it previously returned float64 —
  the working precision of a feature changes while its source code does not.
- **Anchoring the relative difference on the research value alone.** `diff /
  max(abs(research), 1e-5)` turns into an absolute check below 1e-5 and hides a sign
  flip near zero: +1e-9 against −1e-9 scores 2e-4, inside a 0.1% tolerance, while a long
  has become a short. Scale symmetrically and state the absolute floor explicitly.
- **Reading a matching feature hash as matching behaviour.** It proves two strings
  match. Hash the deployed artifact, not a manifest that is updated by hand.
- **Treating `float64` and `double` as drift.** They are the same IEEE 754 binary64
  format. A gate that fires on spelling gets muted, and a muted gate catches nothing.
- **Pinning only the minor Python version.** `3.11` on both sides compares equal while
  one host runs 3.11.2 and the other 3.11.8.
- **Building an unbounded discrepancy list.** A day of shadow-diffed ticks is millions
  of samples; a gate that materializes one report object per breach dies before it
  reports anything. Cap the recorded examples, keep the counts exact.
- **Passing an empty signal list and reading the pass as signal parity.** An empty
  sample certifies the strongest vector in the audit on zero comparisons. It raises for
  that reason; `None` is how you record that shadow diffing was not run.
- **Swapping the two arguments.** Both are `EnvironmentSnapshot`, so the swap
  type-checks and produces a plausible report about the wrong direction. The roles are
  required to match the parameter order so the mistake raises instead.

## Verification

- Construct matching `RESEARCH` and `PRODUCTION` snapshots; verify
  `status == "PARITY_VERIFIED"`, `is_parity_achieved` is `True`, and — because no
  signals were passed — that `signal_diffing_performed` is `False`.
- Fail-open regression checks, each of which must **raise** rather than pass: an empty
  `package_versions`, an empty `feature_definitions`, a blank `python_version`,
  `python_version="3.11"`, `float_precision="float"`, `env_type="NOTEBOOK"`, a swapped
  argument order, and `test_signals=[]`.
- Change one feature hash; verify `PARITY_BREACHED` with exactly 1 critical discrepancy
  on component `FEATURE`. Remove a feature entirely; verify the sentinel `MISSING`
  appears on the side that lacks it.
- Signal regression checks: `(1.5, nan)`, `(nan, 1.5)`, `(nan, nan)` and `(inf, inf)`
  must each breach; `(1e-9, -1e-9)` must breach; `(1e-18, -1e-18)` and `(0.0, 0.0)` must
  pass; `(1.0, 1.05)` and `(1.05, 1.0)` must produce the same verdict.
- Set `numpy` to `1.24.3` on one side; verify it blocks. Set an unlisted package to a
  new patch release; verify it produces a warning that does *not* block, and that
  `warning_discrepancies` is non-zero while `critical_discrepancies` is zero.
- Feed 200 breaching samples with `max_reported_signal_breaches=5`; verify
  `critical_discrepancies == 200`, `len(discrepancies) == 5`, and
  `discrepancies_truncated` is `True`.
- Run `python -m unittest discover -s skills/research-environment-vs-production-environment-parity/scripts` and
  confirm a 100% pass rate.

## Related Skills

- `environment-parity-dev-staging-production`
- `feature-store-for-live-and-backtest-parity`
- `dependency-pinning-and-reproducible-builds`
- `reproducible-ml-training-pipelines`
- `data-pipeline-schema-contract-testing`
- `backtest-vs-live-performance-divergence-tracking`
- `paper-to-live-promotion-checklist`
- `strategy-research-to-production-pipeline-governance`
