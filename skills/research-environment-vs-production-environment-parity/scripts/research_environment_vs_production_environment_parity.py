"""research-environment-vs-production-environment-parity: promotion gate comparing a
RESEARCH environment against a PRODUCTION environment before a model, alpha signal or
feature pipeline is allowed to trade real capital.

Five vectors are audited: CPython release, installed package versions, declared
floating-point precision, feature-definition hashes, and shadow-execution signal
diffing (research output vs production output on identical inputs).

This module is a **gate**, not a report generator
------------------------------------------------
The expensive failure mode is a false PASS -- certifying parity for an environment that
will trade differently from the one the strategy was validated in. Every input is
therefore validated up front, and ambiguous or absent evidence raises instead of being
audited as "equal". Three fail-open holes this validation exists to close:

1. An audit over empty ``package_versions`` / ``feature_definitions`` compared nothing,
   found zero discrepancies and reported ``PARITY_VERIFIED``. Absent evidence is not
   parity, so both maps are required to be non-empty.
2. A NaN production signal passed silently. ``abs(1.5 - nan) > tol`` is ``False``, so
   the most common numeric production failure -- a model emitting NaN -- was certified
   as parity-verified. Non-finite values are now their own CRITICAL discrepancy,
   checked before any tolerance comparison. (``math.isclose(inf, inf)`` is ``True``, so
   the infinite case has to be intercepted first as well.)
3. Both arguments share a type, so swapping them produced a plausible report about the
   wrong direction. ``env_type`` is now a checked role, not a label.

Signal comparison
-----------------
Comparison is :func:`math.isclose` with an explicit ``abs_tol`` (PEP 485 semantics:
``abs(a-b) <= max(rel_tol * max(abs(a), abs(b)), abs_tol)``). The scale is symmetric in
the two values rather than anchored on the research value, and ``abs_tol`` states
outright what counts as float round-off instead of hiding it in a magic denominator
floor. A sign flip needs no separate check: opposite signs at any material magnitude
produce a relative difference of at least 1.0, three orders of magnitude above the
default tolerance. That property holds only while ``signal_abs_tol`` stays small --
raise it far enough and a direction flip near zero starts passing.

Floating-point precision, honestly
----------------------------------
A float32/float64 mismatch is not automatically a tolerance breach, and this module
does not claim it is. Well-conditioned recursions barely diverge: an EMA over 50,000
bars computed both ways differs by roughly 3e-7 relative, comfortably inside the
default 0.1% tolerance. Ill-conditioned ones detonate: the textbook one-pass variance
``E[x^2] - E[x]^2`` over 500 samples with mean 45,000 returns 0.977 in float64 and
**-2176.0** in float32, a negative variance. Precision drift is therefore audited as a
declared-configuration vector in its own right, because whether it moves a number
depends on the conditioning of a computation this module cannot see.

Package drift is not cosmetic, which is why it can block
--------------------------------------------------------
NumPy 2.0 adopted NEP 50 promotion. Per the official migration guide, "``np.float32(3)
+ 3.`` now returns a float32 when it previously returned a float64", and "for floating
point values, this can lead to lower precision results when working with scalars". A
major-version bump can therefore silently change the working precision of a feature
computation whose source code did not change at all. Version drift in a numerically
relevant package -- and any major-version bump in any package -- is CRITICAL by
default. Drift in a package that cannot plausibly move a number (a linter, a test
runner) is a WARNING and does not block.

Limitations (documented, deliberate)
------------------------------------
- **Declared snapshots only.** Every value is supplied by the caller. Nothing here
  imports a package, inspects an interpreter or runs a model, so the audit is exactly
  as trustworthy as the collection step that built the snapshot. A snapshot
  hand-written to match passes cleanly. Collect from the live target.
- **A matching feature hash proves the recorded strings match, not that two
  implementations agree.** If research hashes Python source and production hashes a
  C++ translation of it, the vector is permanent noise; if both sides hash the same
  stale manifest, they match while the deployed code differs.
- **No market-data parity.** Input data equality is out of scope; see the
  ``data-pipeline-schema-contract-testing`` and
  ``point-in-time-database-for-ml-training-data`` skills.
- **No latency or timing parity.** Signal *values* are compared, never when they
  arrived; see ``model-inference-latency-budget-for-live-trading``.
- **Package versions are compared as strings, not resolved per PEP 440.** ``1.24.3``
  and ``1.24.3+cu118`` are drift, deliberately: they are different builds.
- **WARNING findings never block.** Only CRITICAL findings clear
  ``is_parity_achieved``. Read ``warning_discrepancies`` before treating a
  ``PARITY_VERIFIED`` verdict as "nothing to look at".
"""
import logging
import math
import numbers
import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

ENV_RESEARCH = "RESEARCH"
ENV_PRODUCTION = "PRODUCTION"

#: The only two roles this gate audits. ``env_type`` is a checked role rather than a
#: free-text label: both audit arguments share a type, so without this an argument swap
#: would type-check and produce a plausible report about the wrong direction.
VALID_ENV_TYPES: FrozenSet[str] = frozenset({ENV_RESEARCH, ENV_PRODUCTION})

COMPONENT_PYTHON = "PYTHON_VERSION"
COMPONENT_PACKAGE = "PACKAGE"
COMPONENT_PRECISION = "FLOAT_PRECISION"
COMPONENT_FEATURE = "FEATURE"
COMPONENT_SIGNAL = "SIGNAL_OUTPUT"

SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_WARNING = "WARNING"

STATUS_VERIFIED = "PARITY_VERIFIED"
STATUS_BREACHED = "PARITY_BREACHED"

#: Recorded when a package is installed in one environment only.
NOT_INSTALLED = "NOT_INSTALLED"
#: Recorded when a feature is defined in one environment only.
MISSING = "MISSING"

#: Packages whose version drift is treated as CRITICAL because a version change there
#: can move a computed number. An engineering default and a starting point, not an
#: inventory and not a standard -- override it to match the stack you actually run.
DEFAULT_NUMERICALLY_CRITICAL_PACKAGES: FrozenSet[str] = frozenset({
    "numpy", "pandas", "scipy", "scikit-learn", "statsmodels", "numba", "numexpr",
    "bottleneck", "pyarrow", "polars", "ta-lib", "torch", "tensorflow", "xgboost",
    "lightgbm",
})

#: Maximum relative difference between a research and a production signal before the
#: sample is a breach. A house rule, not a published standard -- no regulator or
#: standards body specifies a signal-skew tolerance.
DEFAULT_MAX_SIGNAL_REL_DIFF = 0.001

#: Absolute difference at or below which two signals are treated as equal regardless of
#: relative size. Sits far above float64 round-off for O(1) values and far below any
#: tradeable magnitude, so it absorbs arithmetic noise around zero without absorbing a
#: real disagreement. Raising it materially will start hiding sign flips near zero.
DEFAULT_SIGNAL_ABS_TOL = 1e-12

#: Signal breaches are individually recorded up to this many; past it the counts stay
#: exact but the list stops growing. A day of shadow-diffed ticks is millions of
#: samples, and a gate must not exhaust memory building a report nobody reads.
DEFAULT_MAX_REPORTED_SIGNAL_BREACHES = 50

#: Requires major.minor.patch. ``"3.11"`` is rejected: two environments both declaring
#: ``"3.11"`` compare equal while running 3.11.2 and 3.11.8. Suffixes are allowed
#: (``"3.13.0rc1"``).
_PYTHON_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")

#: PEP 503 name normalization, so ``scikit_learn`` and ``scikit-learn`` are one package
#: rather than two phantom one-sided installs.
_PACKAGE_NAME_SEPARATORS_RE = re.compile(r"[-_.]+")

_LEADING_INTEGER_RE = re.compile(r"^(\d+)")

#: Unambiguous spellings of the IEEE 754 binary interchange formats. Anything outside
#: this map is compared as its own literal token, so an exotic or vendor-specific
#: precision is still auditable by exact string.
_PRECISION_ALIASES: Dict[str, str] = {
    "float16": "float16", "fp16": "float16", "half": "float16", "binary16": "float16",
    "float32": "float32", "fp32": "float32", "single": "float32", "binary32": "float32",
    "float64": "float64", "fp64": "float64", "double": "float64", "binary64": "float64",
    "decimal": "decimal",
}

#: Declarations that name no specific format. These raise rather than being compared:
#: bare ``float`` is binary64 in Python and binary32 in C, so two environments meaning
#: opposite things would compare equal and pass.
_AMBIGUOUS_PRECISIONS: FrozenSet[str] = frozenset({
    "float", "real", "number", "numeric", "auto", "mixed", "default", "native",
})


def _require_non_blank(value: object, field_name: str, env_type: str) -> str:
    """Return ``value`` stripped, raising unless it is a non-blank string.

    A parity gate must never read absent evidence as a match: two environments that
    both failed to resolve a field would otherwise compare ``"" == ""`` and pass.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{env_type or '<unnamed>'}: '{field_name}' is missing or blank. A parity "
            f"gate must not treat absent evidence as a match -- two environments that "
            f"both failed to resolve this field would compare equal and certify parity "
            f"on no evidence. Resolve the real value, or fail the collection step."
        )
    return value.strip()


def normalize_package_name(name: str) -> str:
    """Normalize a distribution name per PEP 503 (separators collapsed, lower-cased)."""
    return _PACKAGE_NAME_SEPARATORS_RE.sub("-", name.strip()).lower()


def normalize_float_precision(value: str, env_type: str = "") -> str:
    """Canonicalize a declared floating-point precision.

    ``double``, ``fp64`` and ``binary64`` all denote IEEE 754 binary64 and must not
    audit as drift against ``float64``. Values outside :data:`_PRECISION_ALIASES` are
    returned lower-cased and compared literally, so a platform-specific precision stays
    auditable; genuinely ambiguous tokens raise.

    Raises:
        ValueError: If the value is blank or names no specific format.
    """
    token = _require_non_blank(value, "float_precision", env_type).lower()
    if token in _AMBIGUOUS_PRECISIONS:
        raise ValueError(
            f"{env_type or '<unnamed>'}: float_precision {value!r} does not name a "
            f"specific format. Bare 'float' is binary64 in Python and binary32 in C, "
            f"so two environments meaning opposite things would compare equal and "
            f"pass. Declare one of {sorted(set(_PRECISION_ALIASES.values()))}, or an "
            f"explicit platform-specific token."
        )
    return _PRECISION_ALIASES.get(token, token)


def _normalize_str_map(
    raw: object,
    field_name: str,
    env_type: str,
    key_normalizer=None,
) -> Dict[str, str]:
    """Validate and normalize a non-empty ``str -> str`` mapping.

    Raises:
        TypeError: If ``raw`` is not a dict.
        ValueError: If it is empty, holds a blank key or value, or two keys collide
            under normalization with conflicting values.
    """
    if not isinstance(raw, dict):
        raise TypeError(
            f"{env_type or '<unnamed>'}: '{field_name}' must be a dict of "
            f"name -> value, got {type(raw).__name__}."
        )
    if not raw:
        raise ValueError(
            f"{env_type or '<unnamed>'}: '{field_name}' is empty. An audit over an "
            f"empty map compares nothing, finds zero discrepancies and certifies "
            f"parity on no evidence at all. Populate it from the live environment, or "
            f"fail the collection step."
        )

    normalized: Dict[str, str] = {}
    for key, value in raw.items():
        clean_key = _require_non_blank(key, f"{field_name} key", env_type)
        if key_normalizer is not None:
            clean_key = key_normalizer(clean_key)
        clean_value = _require_non_blank(value, f"{field_name}[{clean_key}]", env_type)
        if clean_key in normalized and normalized[clean_key] != clean_value:
            raise ValueError(
                f"{env_type or '<unnamed>'}: '{field_name}' declares conflicting "
                f"values {normalized[clean_key]!r} and {clean_value!r} for "
                f"{clean_key!r} after normalization. The audit cannot pick one."
            )
        normalized[clean_key] = clean_value
    return normalized


@dataclass
class EnvironmentSnapshot:
    """Declared state of one environment.

    All fields are validated and normalized in place on construction: ``env_type`` is
    upper-cased and checked against :data:`VALID_ENV_TYPES`, package names are
    normalized per PEP 503, and ``float_precision`` is canonicalized.

    Args:
        env_type: ``'RESEARCH'`` or ``'PRODUCTION'``. A checked role, not a label.
        python_version: Full ``major.minor.patch`` release, e.g. ``'3.11.8'``.
        package_versions: Distribution name -> version string. Must be non-empty.
        float_precision: Working precision, e.g. ``'float64'``.
        feature_definitions: Feature name -> code hash or formula. Must be non-empty.

    Raises:
        TypeError: If either mapping is not a dict.
        ValueError: If any field is blank, ``env_type`` is unrecognized,
            ``python_version`` is not a full release, either mapping is empty or holds
            blank entries, or ``float_precision`` is ambiguous.
    """

    env_type: str
    python_version: str
    package_versions: Dict[str, str]
    float_precision: str
    feature_definitions: Dict[str, str]

    def __post_init__(self) -> None:
        env = _require_non_blank(self.env_type, "env_type", "").upper()
        if env not in VALID_ENV_TYPES:
            raise ValueError(
                f"Unknown env_type {self.env_type!r}. Expected one of "
                f"{sorted(VALID_ENV_TYPES)}. The role is checked rather than trusted "
                f"as a label: both audit arguments share this type, so an unchecked "
                f"role would let a swapped call report on the wrong direction."
            )
        self.env_type = env

        version = _require_non_blank(self.python_version, "python_version", env)
        if not _PYTHON_VERSION_RE.match(version):
            raise ValueError(
                f"{env}: python_version {self.python_version!r} is not a full release. "
                f"Expected 'major.minor.patch' (e.g. '3.11.8'); '3.11' on both sides "
                f"compares equal across 3.11.2 and 3.11.8."
            )
        self.python_version = version

        self.package_versions = _normalize_str_map(
            self.package_versions, "package_versions", env, normalize_package_name)
        self.float_precision = normalize_float_precision(self.float_precision, env)
        self.feature_definitions = _normalize_str_map(
            self.feature_definitions, "feature_definitions", env)

    @property
    def python_minor_release(self) -> Tuple[int, int]:
        """``(major, minor)`` of the declared CPython release."""
        match = _PYTHON_VERSION_RE.match(self.python_version)
        if match is None:  # pragma: no cover - guaranteed by __post_init__
            raise ValueError(f"Unparseable python_version {self.python_version!r}.")
        return int(match.group(1)), int(match.group(2))


@dataclass
class ParityDiscrepancy:
    """One difference found between the two environments."""

    component: str      # COMPONENT_PYTHON / _PACKAGE / _PRECISION / _FEATURE / _SIGNAL
    item_name: str
    research_val: str
    production_val: str
    severity: str       # SEVERITY_CRITICAL or SEVERITY_WARNING
    details: str = ""   # why this severity, and what to do about it


@dataclass
class EnvironmentParityReport:
    """Outcome of one audit.

    ``total_discrepancies`` and ``critical_discrepancies`` count every discrepancy
    *detected*. ``discrepancies`` may hold fewer entries: signal breaches stop being
    recorded individually past the configured cap. The counts, not ``len()``, are
    authoritative, and the gate is ``is_parity_achieved``.
    """

    is_parity_achieved: bool
    discrepancies: List[ParityDiscrepancy]
    total_discrepancies: int
    critical_discrepancies: int
    status: str
    audit_notes: str
    warning_discrepancies: int = 0
    signal_samples_compared: int = 0
    signal_breach_count: int = 0
    #: True when shadow diffing actually ran. A static-only audit can still return
    #: PARITY_VERIFIED; that verdict says nothing about the numbers the model emits.
    signal_diffing_performed: bool = False
    discrepancies_truncated: bool = False

    @property
    def critical_component_names(self) -> List[str]:
        """Components carrying at least one recorded CRITICAL discrepancy."""
        seen: List[str] = []
        for d in self.discrepancies:
            if d.severity == SEVERITY_CRITICAL and d.component not in seen:
                seen.append(d.component)
        return seen


class ResearchEnvironmentVsProductionEnvironmentParityEngine:
    """Audits a RESEARCH snapshot against a PRODUCTION snapshot.

    Args:
        max_signal_rel_diff: Relative tolerance for shadow-diffed signal samples. Must
            satisfy ``0 < x < 1``; zero would flag ordinary float64 round-off as a
            breach.
        signal_abs_tol: Absolute tolerance below which a difference is treated as
            arithmetic noise. Must be finite and non-negative.
        numerically_critical_packages: Distribution names whose version drift blocks.
            Defaults to :data:`DEFAULT_NUMERICALLY_CRITICAL_PACKAGES`. Names are
            normalized per PEP 503. Pass an empty iterable only if you deliberately
            intend every non-major package drift to be advisory.
        max_reported_signal_breaches: Cap on individually recorded signal breaches.
            Counts stay exact above the cap.

    Raises:
        TypeError: If a tolerance is not a real number, or
            ``numerically_critical_packages`` is a bare string -- which would otherwise
            register one single-character package name per letter.
        ValueError: If a tolerance is outside its documented range.
    """

    def __init__(
        self,
        max_signal_rel_diff: float = DEFAULT_MAX_SIGNAL_REL_DIFF,
        signal_abs_tol: float = DEFAULT_SIGNAL_ABS_TOL,
        numerically_critical_packages: Optional[Iterable[str]] = None,
        max_reported_signal_breaches: int = DEFAULT_MAX_REPORTED_SIGNAL_BREACHES,
    ) -> None:
        if isinstance(max_signal_rel_diff, bool) or not isinstance(
                max_signal_rel_diff, numbers.Real):
            raise TypeError("max_signal_rel_diff must be a real number.")
        if not math.isfinite(float(max_signal_rel_diff)) or not (
                0.0 < float(max_signal_rel_diff) < 1.0):
            raise ValueError(
                f"max_signal_rel_diff must satisfy 0 < x < 1, got "
                f"{max_signal_rel_diff!r}. Zero would report every float64 rounding "
                f"difference as a parity breach; 1.0 or more would pass a sign flip."
            )
        if isinstance(signal_abs_tol, bool) or not isinstance(
                signal_abs_tol, numbers.Real):
            raise TypeError("signal_abs_tol must be a real number.")
        if not math.isfinite(float(signal_abs_tol)) or float(signal_abs_tol) < 0.0:
            raise ValueError(
                f"signal_abs_tol must be finite and non-negative, got "
                f"{signal_abs_tol!r}."
            )
        if isinstance(numerically_critical_packages, str):
            raise TypeError(
                "numerically_critical_packages must be an iterable of names, not a "
                "single string -- a str would register one package per character."
            )
        if isinstance(max_reported_signal_breaches, bool) or not isinstance(
                max_reported_signal_breaches, int):
            raise TypeError("max_reported_signal_breaches must be an int.")
        if max_reported_signal_breaches < 1:
            raise ValueError(
                "max_reported_signal_breaches must be at least 1, otherwise a breached "
                "audit would carry no example of what breached."
            )

        self.max_signal_rel_diff = float(max_signal_rel_diff)
        self.signal_abs_tol = float(signal_abs_tol)
        self.max_reported_signal_breaches = max_reported_signal_breaches
        source = (DEFAULT_NUMERICALLY_CRITICAL_PACKAGES
                  if numerically_critical_packages is None
                  else numerically_critical_packages)
        self.numerically_critical_packages: FrozenSet[str] = frozenset(
            normalize_package_name(str(name)) for name in source if str(name).strip())

    def audit_environment_parity(
        self,
        research_env: EnvironmentSnapshot,
        prod_env: EnvironmentSnapshot,
        test_signals: Optional[Sequence[Sequence[float]]] = None,
    ) -> EnvironmentParityReport:
        """Audit the five parity vectors and return a structured verdict.

        Args:
            research_env: Snapshot with ``env_type == 'RESEARCH'``.
            prod_env: Snapshot with ``env_type == 'PRODUCTION'``.
            test_signals: Optional ``(research_signal, production_signal)`` pairs
                produced on identical inputs. ``None`` means shadow diffing was not run
                and is recorded as such; an empty sequence raises, because "I ran it and
                compared nothing" is the fail-open case this gate exists to catch.

        Returns:
            An :class:`EnvironmentParityReport`. Branch on ``is_parity_achieved``.

        Raises:
            TypeError: If either snapshot is not an :class:`EnvironmentSnapshot`, or a
                signal sample is not a pair of real numbers.
            ValueError: If the snapshots are passed in the wrong roles, or
                ``test_signals`` is an empty sequence.
        """
        if not isinstance(research_env, EnvironmentSnapshot) or not isinstance(
                prod_env, EnvironmentSnapshot):
            raise TypeError(
                "audit_environment_parity expects two EnvironmentSnapshot instances "
                "(research first, production second)."
            )
        if research_env.env_type != ENV_RESEARCH or prod_env.env_type != ENV_PRODUCTION:
            raise ValueError(
                f"Arguments are in the wrong roles: got env_type "
                f"{research_env.env_type!r} then {prod_env.env_type!r}, expected "
                f"{ENV_RESEARCH!r} then {ENV_PRODUCTION!r}. Both arguments share a "
                f"type, so a swapped call would otherwise produce a plausible report "
                f"describing the drift backwards."
            )

        discrepancies: List[ParityDiscrepancy] = []
        discrepancies.extend(self._audit_python_version(research_env, prod_env))
        discrepancies.extend(self._audit_packages(research_env, prod_env))
        discrepancies.extend(self._audit_precision(research_env, prod_env))
        discrepancies.extend(self._audit_features(research_env, prod_env))

        static_total = len(discrepancies)
        static_critical = sum(
            1 for d in discrepancies if d.severity == SEVERITY_CRITICAL)

        recorded_breaches: List[ParityDiscrepancy] = []
        samples_compared = 0
        signal_breach_count = 0
        if test_signals is not None:
            samples_compared, signal_breach_count, recorded_breaches = (
                self._audit_signals(test_signals))
            discrepancies.extend(recorded_breaches)

        total_count = static_total + signal_breach_count
        critical_count = static_critical + signal_breach_count
        warning_count = total_count - critical_count
        truncated = len(recorded_breaches) < signal_breach_count

        is_parity = critical_count == 0
        status = STATUS_VERIFIED if is_parity else STATUS_BREACHED
        if test_signals is None:
            signal_note = ("signal diffing NOT performed (this verdict covers declared "
                           "configuration only)")
        else:
            signal_note = (f"{signal_breach_count} of {samples_compared} signal samples "
                           f"breached")
        notes = (
            f"ENVIRONMENT PARITY [{status}]: Total Discrepancies = {total_count}, "
            f"Critical = {critical_count}, Warning = {warning_count}; {signal_note}."
        )
        if truncated:
            notes += (f" Signal breach list truncated to the first "
                      f"{self.max_reported_signal_breaches}; counts above are exact.")

        if is_parity:
            logger.info(notes)
        else:
            logger.warning(notes)

        return EnvironmentParityReport(
            is_parity_achieved=is_parity,
            discrepancies=discrepancies,
            total_discrepancies=total_count,
            critical_discrepancies=critical_count,
            status=status,
            audit_notes=notes,
            warning_discrepancies=warning_count,
            signal_samples_compared=samples_compared,
            signal_breach_count=signal_breach_count,
            signal_diffing_performed=test_signals is not None,
            discrepancies_truncated=truncated,
        )

    # -- individual vectors ---------------------------------------------------

    def _audit_python_version(
        self,
        research_env: EnvironmentSnapshot,
        prod_env: EnvironmentSnapshot,
    ) -> List[ParityDiscrepancy]:
        """Compare CPython releases.

        A differing ``major.minor`` is CRITICAL rather than advisory. CPython's
        version-specific ABI tags (``cp310``, ``cp311``) are what compiled extensions
        are built against -- ``abi3`` exists precisely because the versioned tags do not
        carry across minor releases -- so the same pinned package version installed
        under two different minor releases is a different compiled binary. A differing
        patch release is a WARNING: it ships stdlib and security fixes, but CPython does
        not vary IEEE 754 arithmetic across patch releases.
        """
        if research_env.python_version == prod_env.python_version:
            return []
        minor_drift = research_env.python_minor_release != prod_env.python_minor_release
        return [ParityDiscrepancy(
            component=COMPONENT_PYTHON,
            item_name="python_version",
            research_val=research_env.python_version,
            production_val=prod_env.python_version,
            severity=SEVERITY_CRITICAL if minor_drift else SEVERITY_WARNING,
            details=(
                "CPython minor release differs; compiled extensions are built against "
                "version-specific ABI tags, so identically pinned packages are "
                "different binaries here. Rebuild both environments on one release."
                if minor_drift else
                "CPython patch release differs. Stdlib and security fixes differ; IEEE "
                "754 arithmetic does not. Align it, but this does not block."
            ),
        )]

    def _audit_packages(
        self,
        research_env: EnvironmentSnapshot,
        prod_env: EnvironmentSnapshot,
    ) -> List[ParityDiscrepancy]:
        """Compare installed distribution versions, sorted for deterministic output."""
        found: List[ParityDiscrepancy] = []
        all_pkgs = set(research_env.package_versions) | set(prod_env.package_versions)
        for pkg in sorted(all_pkgs):
            r_ver = research_env.package_versions.get(pkg, NOT_INSTALLED)
            p_ver = prod_env.package_versions.get(pkg, NOT_INSTALLED)
            if r_ver == p_ver:
                continue
            severity, reason = self._classify_package_drift(pkg, r_ver, p_ver)
            found.append(ParityDiscrepancy(
                component=COMPONENT_PACKAGE,
                item_name=pkg,
                research_val=r_ver,
                production_val=p_ver,
                severity=severity,
                details=reason,
            ))
        return found

    def _classify_package_drift(
        self,
        package: str,
        research_version: str,
        production_version: str,
    ) -> Tuple[str, str]:
        """Decide whether one package's drift blocks the promotion.

        Blocking: installed on one side only; a major-version bump, which is a declared
        break; and any drift in a package flagged as numerically relevant. Everything
        else is advisory.
        """
        if NOT_INSTALLED in (research_version, production_version):
            return SEVERITY_CRITICAL, (
                "Package present in one environment only. An import that resolves in "
                "research and fails -- or resolves to a different implementation -- in "
                "production is not a difference to triage after promotion."
            )
        r_major = _LEADING_INTEGER_RE.match(research_version)
        p_major = _LEADING_INTEGER_RE.match(production_version)
        if r_major is None or p_major is None:
            return SEVERITY_CRITICAL, (
                "At least one version string carries no leading release number, so no "
                "equivalence can be established between the two builds."
            )
        if r_major.group(1) != p_major.group(1):
            return SEVERITY_CRITICAL, (
                "Major-version drift. A major release is a declared break: NumPy 2.0's "
                "NEP 50 promotion change alone alters the dtype -- and therefore the "
                "precision -- of arithmetic whose source code did not change."
            )
        if package in self.numerically_critical_packages:
            return SEVERITY_CRITICAL, (
                "Version drift in a numerically relevant package: a minor or patch "
                "release here can move a computed feature value."
            )
        return SEVERITY_WARNING, (
            "Version drift in a package not flagged as numerically relevant. Align it "
            "for reproducibility; it does not block on its own. Add the name to "
            "numerically_critical_packages if it can move a number in your stack."
        )

    def _audit_precision(
        self,
        research_env: EnvironmentSnapshot,
        prod_env: EnvironmentSnapshot,
    ) -> List[ParityDiscrepancy]:
        """Compare declared working precision (canonicalized, so double == float64)."""
        if research_env.float_precision == prod_env.float_precision:
            return []
        return [ParityDiscrepancy(
            component=COMPONENT_PRECISION,
            item_name="float_precision",
            research_val=research_env.float_precision,
            production_val=prod_env.float_precision,
            severity=SEVERITY_CRITICAL,
            details=(
                "Declared working precision differs. Whether that moves a number "
                "depends on the conditioning of each computation: a well-conditioned "
                "EMA barely drifts, while a one-pass variance on a large-mean price "
                "series can return a negative variance in float32. The gate cannot see "
                "which of the two your features are, so it blocks."
            ),
        )]

    def _audit_features(
        self,
        research_env: EnvironmentSnapshot,
        prod_env: EnvironmentSnapshot,
    ) -> List[ParityDiscrepancy]:
        """Compare feature definitions, sorted for deterministic output."""
        found: List[ParityDiscrepancy] = []
        all_feats = set(research_env.feature_definitions) | set(
            prod_env.feature_definitions)
        for feat in sorted(all_feats):
            r_def = research_env.feature_definitions.get(feat, MISSING)
            p_def = prod_env.feature_definitions.get(feat, MISSING)
            if r_def == p_def:
                continue
            one_sided = MISSING in (r_def, p_def)
            found.append(ParityDiscrepancy(
                component=COMPONENT_FEATURE,
                item_name=feat,
                research_val=r_def,
                production_val=p_def,
                severity=SEVERITY_CRITICAL,
                details=(
                    "Feature defined in one environment only. A feature the live path "
                    "cannot compute reaches the model as a default, not as the value it "
                    "was trained on."
                    if one_sided else
                    "Feature definitions differ. Two implementations of the same "
                    "indicator are two indicators; establish which one the backtest "
                    "results were earned with before changing either."
                ),
            ))
        return found

    def _audit_signals(
        self,
        test_signals: Sequence[Sequence[float]],
    ) -> Tuple[int, int, List[ParityDiscrepancy]]:
        """Shadow-diff research against production signal outputs.

        Returns:
            ``(samples_compared, breach_count, recorded_breaches)``. The breach count is
            exact; the recorded list stops at ``max_reported_signal_breaches``.

        Raises:
            TypeError: If ``test_signals`` is not a sized non-string sequence, or a
                sample is not a pair of real numbers.
            ValueError: If the sequence is empty.
        """
        sample_count = _sized_sequence_len(
            test_signals,
            "test_signals must be a sized sequence of (research_signal, "
            "production_signal) pairs, or None if shadow diffing was not run.",
        )
        if sample_count == 0:
            raise ValueError(
                "test_signals is empty. An empty sample is not evidence of signal "
                "parity -- it would certify the strongest vector in this audit on zero "
                "comparisons. Pass None to record that shadow diffing was not run."
            )

        recorded: List[ParityDiscrepancy] = []
        breach_count = 0
        for idx, sample in enumerate(test_signals):
            r_sig, p_sig = _coerce_signal_pair(sample, idx)

            # Non-finite values are intercepted before any tolerance comparison.
            # abs(x - nan) > tol is False, so a NaN would otherwise pass silently; and
            # math.isclose(inf, inf) is True, so two infinities would report parity.
            if not math.isfinite(r_sig) or not math.isfinite(p_sig):
                breach_count += 1
                if len(recorded) < self.max_reported_signal_breaches:
                    recorded.append(ParityDiscrepancy(
                        component=COMPONENT_SIGNAL,
                        item_name=f"signal_sample_{idx}",
                        research_val=repr(r_sig),
                        production_val=repr(p_sig),
                        severity=SEVERITY_CRITICAL,
                        details=(
                            "Non-finite signal value. This is a computation failure, "
                            "not a tolerance question: every comparison against NaN is "
                            "False, so an unguarded tolerance check passes the sample."
                        ),
                    ))
                continue

            if math.isclose(r_sig, p_sig, rel_tol=self.max_signal_rel_diff,
                            abs_tol=self.signal_abs_tol):
                continue

            breach_count += 1
            if len(recorded) < self.max_reported_signal_breaches:
                scale = max(abs(r_sig), abs(p_sig))
                rel = abs(r_sig - p_sig) / scale if scale > 0.0 else float("inf")
                recorded.append(ParityDiscrepancy(
                    component=COMPONENT_SIGNAL,
                    item_name=f"signal_sample_{idx}",
                    research_val=repr(r_sig),
                    production_val=repr(p_sig),
                    severity=SEVERITY_CRITICAL,
                    details=(
                        f"Relative difference {rel:.6g} exceeds tolerance "
                        f"{self.max_signal_rel_diff:.6g} (absolute floor "
                        f"{self.signal_abs_tol:.3g})."
                    ),
                ))
        return sample_count, breach_count, recorded


def _sized_sequence_len(value: object, message: str) -> int:
    """Return ``len(value)``, rejecting strings, mappings and unsized iterables.

    Deliberately duck-typed rather than an ``isinstance`` check against
    ``collections.abc.Sequence``: a NumPy array or a pandas Series is the natural
    container for shadow-diff output and registers as neither.
    """
    if isinstance(value, (str, bytes, bytearray, dict, set, frozenset)):
        raise TypeError(message)
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(message) from exc


def _coerce_signal_pair(sample: object, idx: int) -> Tuple[float, float]:
    """Validate one shadow-diff sample and return it as a ``(float, float)`` pair.

    Raises:
        TypeError: If the sample is not a pair of real, non-boolean numbers.
    """
    length = _sized_sequence_len(
        sample,
        f"test_signals[{idx}] must be a (research_signal, production_signal) pair, got "
        f"{type(sample).__name__}.",
    )
    if length != 2:
        raise TypeError(
            f"test_signals[{idx}] must hold exactly 2 values, got {length}."
        )
    coerced: List[float] = []
    for position, value in enumerate(sample):  # type: ignore[call-overload]
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            side = "research" if position == 0 else "production"
            raise TypeError(
                f"test_signals[{idx}] {side} value {value!r} is not a real number. A "
                f"non-numeric placeholder for a signal the model failed to produce must "
                f"fail the audit, not be compared."
            )
        coerced.append(float(value))
    return coerced[0], coerced[1]
