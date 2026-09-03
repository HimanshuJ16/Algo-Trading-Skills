# Standards — research-environment-vs-production-environment-parity

## What is a standard here, and what is a house rule

No regulator or standards body publishes a signal-skew tolerance, a required set of
parity vectors, or a mandatory package-pinning granularity. **The 0.1% tolerance in this
skill is an engineering default, not a standard**, and earlier versions of this document
were wrong to write it as a `MUST`. The regulatory material below is real, is scoped to
a named jurisdiction, and is about *testing and change-control discipline* — it does not
prescribe this calculation.

## Enforced rules (the module raises rather than auditing)

| Rule | Rationale |
|---|---|
| `env_type` ∈ {`RESEARCH`, `PRODUCTION`}, in that argument order | Both arguments share a type; an unchecked role lets a swapped call produce a plausible report describing the drift backwards. |
| Every scalar field non-blank | `"" == ""` would make a no-evidence audit report parity. |
| `package_versions` and `feature_definitions` non-empty | `{} == {}` compares nothing, finds zero discrepancies and certifies `PARITY_VERIFIED`. This is the fail-open hole the validation exists to close. |
| `python_version` matches `major.minor.patch` | `"3.11"` on both sides compares equal across 3.11.2 and 3.11.8. |
| `float_precision` names a specific format | Bare `float` is binary64 in Python and binary32 in C; two environments meaning opposite things would compare equal and pass. |
| `test_signals=[]` raises; `None` records "not run" | An empty sample would certify the strongest vector in the audit on zero comparisons. |
| Non-finite signal values are CRITICAL, checked before any tolerance test | Every comparison against NaN is `False`, and `math.isclose(inf, inf)` is `True`. |
| Signal samples must be pairs of real, non-boolean numbers | A `None` or `"n/a"` placeholder for a signal the model failed to produce must fail the audit, not be compared. |
| `numerically_critical_packages` is an iterable, not a bare string | A `str` satisfies `Iterable[str]`, so an unguarded string would register one single-character package name per letter. |

## Engineering defaults (calibrate before use)

| Parameter | Default | What it does |
|---|---|---|
| `max_signal_rel_diff` | `0.001` (0.1%) | Relative tolerance, applied symmetrically via `math.isclose`: `abs(a-b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)` (PEP 485). Constrained to `0 < x < 1`. |
| `signal_abs_tol` | `1e-12` | Absolute floor below which a difference is arithmetic noise. Far above float64 round-off for O(1) values, far below any tradeable magnitude. Raising it materially starts hiding sign flips near zero. |
| `numerically_critical_packages` | numpy, pandas, scipy, scikit-learn, statsmodels, numba, numexpr, bottleneck, pyarrow, polars, ta-lib, torch, tensorflow, xgboost, lightgbm | Version drift in these blocks. A starting point for a quant stack, not an inventory. |
| `max_reported_signal_breaches` | `50` | Caps the recorded examples. Counts stay exact above the cap. |
| Python patch drift | WARNING | Ships stdlib and security fixes; CPython does not vary IEEE 754 arithmetic across patch releases. |
| Python minor drift | CRITICAL | Different ABI, therefore different compiled extension binaries at identical version pins. |

## Verified technical facts

| Fact | Source |
|---|---|
| NumPy 2.0 adopted NEP 50 promotion: "`np.float32(3) + 3.` now returns a float32 when it previously returned a float64", and "for floating point values, this can lead to lower precision results when working with scalars". A major-version bump can therefore change the working precision of a computation whose source did not change. | [NumPy 2.0 migration guide](https://numpy.org/doc/stable/numpy_2_0_migration_guide.html) |
| CPython extension wheels carry version-specific ABI tags (`cp310`, `cp311`), and the stable ABI tag `abi3` exists precisely to mark forward compatibility across minor releases — so a non-`abi3` wheel for one minor release is a different build from the one for another. | [PyPA — Platform compatibility tags](https://packaging.python.org/en/latest/specifications/platform-compatibility-tags/) |
| Distribution names are compared after PEP 503 normalization (`[-_.]+` collapsed to `-`, lower-cased), so `scikit_learn` and `scikit-learn` are one package rather than two phantom one-sided installs. | [PyPA — Simple repository API (PEP 503 normalization)](https://packaging.python.org/en/latest/specifications/name-normalization/) |
| `math.isclose` implements `abs(a-b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)`, returns `False` for any NaN operand, and returns `True` for two identical infinities. | [PEP 485](https://peps.python.org/pep-0485/) / [`math.isclose`](https://docs.python.org/3/library/math.html#math.isclose) |
| IEEE 754 binary32 has a 24-bit significand (machine epsilon ≈ 1.19e-7); binary64 has 53 bits (≈ 2.22e-16). Python's `float` is binary64 (`sys.float_info.mant_dig == 53`). | Verified locally against `numpy.finfo` and `sys.float_info`; format definitions per IEEE 754. |
| A precision mismatch is not automatically a tolerance breach. Reproduced locally: an EMA over 50,000 bars differs by ≈3.4e-7 relative between float32 and float64, while the one-pass variance `E[x²] − E[x]²` over 500 samples with mean 45,000 returns 0.977 in float64 and **−2176.0** in float32. Conditioning, not precision alone, decides. | Reproduced in this repository's Python environment (NumPy 2.3.4). Restate the measurement on your own stack rather than quoting these figures as constants. |

## Regulatory context — two jurisdictions, and about discipline rather than this metric

**Neither regime below mandates numerical parity between a research and a production
environment.** They mandate that testing happen, that it happen somewhere separate, and
that material changes trigger retesting. This module produces evidence for those
obligations; it does not discharge any of them. Nothing here is legal advice — confirm
your own perimeter.

### EU — investment firms engaged in algorithmic trading under MiFID II

Commission Delegated Regulation (EU) 2017/589 of 19 July 2016 ("RTS 6"), Section I,
*Testing and deployment of trading algorithms systems and strategies*: Article 5
(general methodology), Article 6 (conformance testing), **Article 7 (testing
environments)**, Article 8 (controlled deployment), Article 10 (stress testing).

Article 7(1), quoted verbatim from EUR-Lex ([CELEX 32017R0589](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32017R0589)):

> "An investment firm shall ensure that testing of compliance with the criteria laid down
> in Article 5(4)(a), (b) and (d) is undertaken in an environment that is separated from
> its production environment and that is used specifically for the testing and
> development of algorithmic trading systems and trading algorithms."

The second subparagraph of Article 7(1) defines the term this skill is named after:

> "a production environment shall mean an environment where algorithmic trading systems
> effectively operate, and comprise software and hardware used by traders, order routing
> to trading venues, market data, dependent databases, risk control systems, data
> capture, analysis systems and post-trade processing systems."

Note the direction of the requirement: environments must be **separated**, not identical.
Auditing parity between them is a control a firm chooses; having two of them is not.

**ESMA, *Supervisory Briefing on Algorithmic Trading in the EU*, 26 February 2026,
ESMA74-1505669079-10311** ([esma.europa.eu](https://www.esma.europa.eu/sites/default/files/2026-02/ESMA74-1505669079-10311_Supervisory_Briefing_on_Algorithmic_Trading_in_the_EU.pdf))
— quoted verbatim from the published PDF:

- ¶30: "Testing of an algorithm, algorithmic trading system or algorithmic trading
  strategy is required following each 'material change' or 'substantial update' thereof.
  In this regard, firms should manage the risk that a series of minor or small changes
  due to recalibrations could accumulate over time, when uncontrolled or unchecked, into
  a material change in the model output without it being tested."
- ¶31: "A material change or substantial update is any modification that may alter the
  behaviour, risk profile, or compliance posture of an algorithm, algorithmic trading
  system or algorithmic trading strategy. Investment firms are required to timestamp,
  approve, and record all material changes." Its non-exhaustive table of change types
  warranting retesting includes **External Dependencies** — "Replacing third-party
  providers or data feeds, changes to the trading systems, or changes in access
  arrangements" — and **Adaptive Capabilities** — "Retraining or modifying machine
  learning components".

**Why this bears on the package and feature vectors.** A change to the resolved
dependency set and a retrained model both appear in ESMA's own table of change types
warranting retesting, so for an in-scope firm a `PACKAGE` or `FEATURE` discrepancy is a
signal to consider retesting, not merely a build-hygiene nit. The audit report is
correspondingly worth retaining: it timestamps which research environment a production
promotion was gated against.

### US — FINRA member firms

**FINRA Regulatory Notice 15-09**, *Guidance on Effective Supervision and Control
Practices for Firms Engaging in Algorithmic Trading Strategies*, 26 March 2015
([finra.org](https://www.finra.org/rules-guidance/notices/15-09)). This is **guidance,
not a rule** — it describes effective practices firms should consider. Relevant items,
quoted verbatim:

- Under *Software Testing and System Validation*: "Conducting any significant testing in
  a development environment that is segregated from production."
- Under *Software Testing and System Validation*: "Conducting testing to confirm that
  core code components operate as intended and do not produce unintended consequences";
  "Establishing a quality assurance process such that testing is performed independently
  of code development".
- Under *Software/Code Development and Implementation*: "Implementing a development and
  change management process that tracks the development of new trading code or material
  changes to existing code"; "Archiving code versions in a retrievable manner for a
  period of time that is reasonable in view of the firm's size and the complexity of its
  algorithmic trading program."

As with RTS 6, the notice asks for segregation and change control. It does not specify
that a test environment mirror production, and no tolerance is named anywhere in it.

### Do not cite here: US banking model-risk guidance

SR 11-7 / OCC Bulletin 2011-12 ("Supervisory Guidance on Model Risk Management") is
frequently reached for in this context. Two reasons not to: it applies to banking
organizations supervised by the Federal Reserve and OCC, not to trading firms generally;
and **OCC Bulletin 2011-12 was rescinded on 17 April 2026** by OCC Bulletin 2026-13,
*Model Risk Management: Revised Guidance*
([occ.gov](https://www.occ.gov/news-issuances/bulletins/2026/bulletin-2026-13.html)).
Check currentness before citing it anywhere in this repository.
