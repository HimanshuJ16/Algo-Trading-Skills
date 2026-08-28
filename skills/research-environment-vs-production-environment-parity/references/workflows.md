# Workflows — research-environment-vs-production-environment-parity

The deep procedure behind `SKILL.md`. Run it before a promotion, and again after any
material change to either side.

## 0. Decide what "production" means before you collect anything

RTS 6 Article 7(1) defines a production environment as one "where algorithmic trading
systems effectively operate", comprising "software and hardware used by traders, order
routing to trading venues, market data, dependent databases, risk control systems, data
capture, analysis systems and post-trade processing systems". That is broader than the
process that runs the model. This module audits the numerical slice of it: interpreter,
libraries, precision, feature definitions, signal outputs. Name explicitly which host,
container image or service the `PRODUCTION` snapshot describes, and record it alongside
the report — a snapshot taken from the wrong replica is worse than no snapshot.

## 1. Collect both snapshots from the live targets

```python
import importlib.metadata as md
import platform

snapshot = EnvironmentSnapshot(
    env_type="PRODUCTION",
    python_version=platform.python_version(),          # '3.11.8', never '3.11'
    package_versions={p: md.version(p) for p in TRACKED_DISTRIBUTIONS},
    float_precision="float64",                          # what the pipeline actually uses
    feature_definitions=load_feature_hashes(),          # from the deployed artifact
)
```

Rules that make the collection trustworthy:

- **Never substitute a default for a value you could not resolve.** Blank fields and
  empty maps raise. Let the exception fail the promotion step; do not catch it and pass
  `"unknown"`, which reintroduces the fail-open through a side door.
- **Enumerate the same distribution list on both sides.** Collecting only what each side
  happens to have installed turns a genuine one-sided install into a silent omission.
  Drive both from one `TRACKED_DISTRIBUTIONS` list, then let the audit report
  `NOT_INSTALLED`.
- **Hash the deployed artifact, not a manifest.** `feature_definitions` values should be
  digests of the code or serialized transform actually loaded at runtime. A hand-updated
  registry produces two matching strings and two different implementations.
- **Declare the precision the pipeline uses, not the precision the language defaults
  to.** A NumPy pipeline that casts to `float32` for memory is a `float32` environment
  regardless of what Python's `float` is.

## 2. Interpreter vector

| Difference | Severity | Why |
|---|---|---|
| Identical | — | — |
| Patch only (`3.11.2` vs `3.11.8`) | WARNING | Stdlib and security fixes differ. IEEE 754 arithmetic does not vary across CPython patch releases. Align it, but it does not block. |
| Minor or major (`3.10.x` vs `3.11.x`) | CRITICAL | Compiled extensions are built against version-specific ABI tags. `numpy==1.26.4` under two different minor releases is one version string and two binaries. |

Resolution is always to rebuild one side onto the other's release — never to relax the
check.

## 3. Package vector

Classification, in order:

1. Present on one side only → CRITICAL. An import that resolves in research and resolves
   differently (or not at all) in production is not a post-promotion triage item.
2. Either version string has no leading release number (a git SHA, `unknown`) → CRITICAL.
   No equivalence can be established between two opaque builds.
3. Major version differs → CRITICAL, for any package. A major release is a declared
   break. NumPy 2.0's NEP 50 change is the canonical example: `np.float32(3) + 3.` now
   returns float32 where it previously returned float64, silently changing the working
   precision of arithmetic whose source code did not change.
4. Package is in `numerically_critical_packages` → CRITICAL at any version difference.
5. Otherwise → WARNING.

**Decision point — triaging a package alert.** Establish which of three situations you
are in before touching anything:

- *Production is ahead because a dependency was intentionally bumped.* The research
  environment is stale. Re-run the research validation on the new pin before promoting;
  do not promote against a backtest earned on the old one.
- *Research is ahead because a notebook environment drifted.* Production is correct.
  Rebuild the research environment from the lockfile and re-run the validation.
- *Nobody knows which is intended.* Neither environment is under change control. Fix that
  first — see `dependency-pinning-and-reproducible-builds`.

Never resolve an alert by removing the package from `numerically_critical_packages`
unless you can state why that package cannot move a number in your stack, and record
that reasoning.

## 4. Precision vector

A declared mismatch blocks. The canonicalization step means `float64`, `double`, `fp64`
and `binary64` are one value, so the gate fires on substance rather than spelling; a
value outside the alias table is compared literally, and an ambiguous token (`float`,
`mixed`, `auto`) raises.

**Why this blocks even when signal diffing passes.** Precision drift is conditional, not
uniform:

- Well-conditioned: an EMA over 50,000 bars differs by roughly 3e-7 relative between
  float32 and float64. Inside a 0.1% tolerance by four orders of magnitude.
- Ill-conditioned: the one-pass variance `E[x²] − E[x]²` over 500 samples with mean
  45,000 returns 0.977 in float64 and **−2176.0** in float32 — a negative variance,
  which will propagate as a NaN through any volatility term that takes its square root.

The gate cannot see which of the two shapes your features are, and today's sample does
not tell you about tomorrow's inputs. Block, then either align the precision or document
per feature why the conditioning makes it safe.

## 5. Feature vector

Both a differing definition and a one-sided definition are CRITICAL; the one-sided case
records the `MISSING` sentinel on the side that lacks it.

**Decision point — a one-sided feature is usually worse than a mismatched one.** A
feature the live path cannot compute does not raise: it reaches the model as an imputed
default, a zero, or a forward-filled stale value, and the model produces a confident
prediction from it. Confirm what the serving path actually does with an absent column
before concluding the impact is small.

**Decision point — what the hash proves.** It proves the recorded strings match. If
research hashes Python source and production hashes a C++ port, the vector will never
match and will be muted, which is worse than not having it. In that situation the right
move is not a looser check — it is `feature-store-for-live-and-backtest-parity`, which
removes the divergence by making both paths call one implementation.

## 6. Signal vector (shadow diffing)

Run both models on **identical** inputs and pair the outputs positionally.

Order of checks per sample, which matters:

1. **Type check.** A non-real or boolean value raises. A `None` or `"n/a"` placeholder
   for a signal the model failed to produce must fail the audit, not be compared.
2. **Finiteness check.** NaN or infinity on either side is a CRITICAL breach, recorded
   before any tolerance arithmetic. `abs(x - nan) > tol` is `False`, and
   `math.isclose(inf, inf)` is `True`; folding non-finite values into the numeric
   comparison passes them both.
3. **Tolerance check.** `math.isclose(r, p, rel_tol=max_signal_rel_diff,
   abs_tol=signal_abs_tol)` — PEP 485 semantics, `abs(a-b) <= max(rel_tol *
   max(abs(a), abs(b)), abs_tol)`. The scale is symmetric in the two values, so the
   verdict does not depend on argument order.

Sign flips need no separate rule: opposite signs at equal magnitude give a relative
difference of exactly 2.0, and `max_signal_rel_diff` is constrained below 1.0, so no
admissible configuration passes a direction flip at material magnitude. That property
depends on `signal_abs_tol` staying small — it is the reason the default is 1e-12 rather
than something comfortable.

**Decision point — choosing the sample.** Parity on 10,000 mid-session large-cap bars is
the cheapest possible evidence. Include:

- Illiquid instruments where the feature windows are sparse.
- Sessions with halts, auctions and early closes.
- The first bars after a corporate action.
- Newly listed instruments with insufficient history — the case that produces NaN.
- Any instrument the research universe excluded but production will route.

**Decision point — reporting volume.** Breaches are recorded up to
`max_reported_signal_breaches` (default 50) while `signal_breach_count` and
`critical_discrepancies` stay exact; `discrepancies_truncated` says whether the list is
partial. Never widen the cap to "see everything" on a million-sample run — read the
counts, then re-run on the narrow slice you want examples from.

## 7. Gate and record

- Branch on `is_parity_achieved`, never on a count or a score.
- Check `signal_diffing_performed`. A static-only audit can return `PARITY_VERIFIED`;
  that verdict says nothing about the numbers the model emits.
- Read `warning_discrepancies` before closing the ticket. Warnings do not block, and that
  is exactly why they get ignored into a real incident.
- Retain the report against the promotion. For an EU firm in scope of RTS 6 it is part of
  the change-control record ESMA's ¶31 expects to be timestamped and approved; for
  everyone else it is what makes the next incident reconstructable.
- Re-run the whole audit after any material change to either side — a dependency bump, a
  retrained model, a feature redefinition — not only at first promotion.
