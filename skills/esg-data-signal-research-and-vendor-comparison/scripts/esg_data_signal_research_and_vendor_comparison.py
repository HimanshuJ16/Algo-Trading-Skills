"""
ESG Data Signal Research and Vendor Comparison Engine.

Normalizes ESG assessments from three vendors onto a common [0.0, 1.0] scale,
computes a multi-vendor consensus score and the dispersion between vendors, and
emits ESG factor-overlay research signals plus a rule-based exclusion flag.

Vendor scale provenance
-----------------------
Every normalization constant below is anchored to the vendor's own published
methodology. None of them are tuned or fitted.

* MSCI ESG Ratings -- seven-band letter scale AAA..CCC derived from a 0-10
  final Industry-Adjusted Score. MSCI's Exhibit 2 splits 0-10 into seven equal
  bands (width 10/7 = 1.4286): CCC 0.0-1.429, B 1.429-2.857, BB 2.857-4.286,
  BBB 4.286-5.714, A 5.714-7.143, AA 7.143-8.571, AAA 8.571-10.0. A letter
  therefore identifies an interval, not a point; the unbiased point estimate for
  the underlying score is the band MID-POINT, which is what MSCI_RATING_MAP
  holds. Mapping AAA to 1.0 and CCC to 0.0 (band end-points) overstates both
  tails by ~7 percentage points and mechanically inflates dispersion against
  vendors reported on a continuous scale.
  MSCI ESG Ratings Methodology, March 2026, section 2.1 "Rating & scores".
  https://www.msci.com/documents/1296102/34424357/MSCI+ESG+Ratings+Methodology.pdf

* Morningstar Sustainalytics ESG Risk Rating -- unmanaged-risk score where 0 is
  best. The five published risk categories are Negligible 0-9.99, Low
  10-19.99, Medium 20-29.99, High 30-39.99 and Severe "40 and higher": the top
  band is open-ended, so 100 is a nominal bound that is not observed in
  practice. Rescaling by 100 would map a Severe-risk issuer scoring 45 to 0.55
  -- above the mid-point of the normalized scale -- and no realistic issuer
  could ever reach the laggard band. The rescale is therefore bounded at the
  Severe threshold; scores at or beyond it normalize to 0.0.
  Sustainalytics, "The ESG Risk Rating: Frequently Asked Questions - For
  Companies", August 2020.
  https://connect.sustainalytics.com/hubfs/SFS/Sustainalytics%20ESG%20Risk%20Rating%20-%20FAQs%20for%20Corporations.pdf

* LSEG ESG Scores (formerly Refinitiv / ASSET4) -- reported 0-100, so the
  rescale is a straight division by 100.
  LSEG ESG Scores Methodology, July 2026.
  https://www.lseg.com/content/dam/data-analytics/en_us/documents/methodology/lseg-esg-scores-methodology-final.pdf

Known limitation: reference-frame incompatibility
-------------------------------------------------
The three vendors do not measure the same quantity against the same benchmark.
MSCI ratings are explicitly industry-relative ("intended to be interpreted
relative to a company's industry peers"). Sustainalytics states the opposite --
"These risk categories are absolute, i.e. comparable across sectors."
LSEG/Refinitiv benchmarks environmental and social categories against the TRBC
industry group and governance against the country of incorporation. Putting
them on a common [0, 1] axis makes them arithmetically averageable, NOT
conceptually comparable; Berg, Koelbel & Rigobon (2022) attribute 38% of ESG
rating divergence to exactly this "scope" difference. For research that must be
comparable across vendors, normalize cross-sectionally (percentile rank within
the traded universe on a common date) instead of rescaling nominal vendor
scales. This engine scores one issuer at a time and cannot do that for you.

  Berg, F., Koelbel, J. F., & Rigobon, R. (2022). "Aggregate Confusion: The
  Divergence of ESG Ratings." Review of Finance 26(6), 1315-1344.
  Reported pairwise correlations between six raters range from 0.38 to 0.71.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import math
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --- Emitted signals ---------------------------------------------------------
SIGNAL_BULLISH = "BULLISH_ESG_LEADER"
SIGNAL_BEARISH = "BEARISH_ESG_LAGGARD"
SIGNAL_EXCLUDED = "EXCLUDED_SECTOR"
SIGNAL_HIGH_DISAGREEMENT = "NEUTRAL_HIGH_DISAGREEMENT"
SIGNAL_INSUFFICIENT_COVERAGE = "INSUFFICIENT_VENDOR_COVERAGE"
SIGNAL_NEUTRAL = "NEUTRAL"

# --- Vendor scale constants (see module docstring for provenance) ------------
# Ordered worst -> best. MSCI publishes exactly these seven bands.
MSCI_RATING_ORDER: Tuple[str, ...] = ("CCC", "B", "BB", "BBB", "A", "AA", "AAA")

# Band mid-points on [0, 1]: CCC -> 1/14, B -> 3/14, ... AAA -> 13/14.
MSCI_RATING_MAP: Dict[str, float] = {
    rating: round((index + 0.5) / len(MSCI_RATING_ORDER), 4)
    for index, rating in enumerate(MSCI_RATING_ORDER)
}

# Sustainalytics "Severe Risk" begins at 40 and is open-ended above it.
SUSTAINALYTICS_SEVERE_THRESHOLD = 40.0
# Nominal reported bounds, used only for range validation of raw vendor input.
SUSTAINALYTICS_SCALE_MAX = 100.0
REFINITIV_SCALE_MAX = 100.0

# Published Sustainalytics risk categories, as (inclusive lower bound, label).
SUSTAINALYTICS_RISK_CATEGORIES: Tuple[Tuple[float, str], ...] = (
    (40.0, "SEVERE"),
    (30.0, "HIGH"),
    (20.0, "MEDIUM"),
    (10.0, "LOW"),
    (0.0, "NEGLIGIBLE"),
)

# Rounding applied to every reported score. Four places are required because the
# MSCI band mid-points (e.g. 13/14 = 0.9286) are not representable at three.
_SCORE_PRECISION = 4

__all__ = [
    "EsgDataSignalEngine",
    "EsgSignalAuditReport",
    "RawVendorEsgData",
    "MSCI_RATING_MAP",
    "MSCI_RATING_ORDER",
    "SUSTAINALYTICS_RISK_CATEGORIES",
    "SUSTAINALYTICS_SEVERE_THRESHOLD",
    "SIGNAL_BEARISH",
    "SIGNAL_BULLISH",
    "SIGNAL_EXCLUDED",
    "SIGNAL_HIGH_DISAGREEMENT",
    "SIGNAL_INSUFFICIENT_COVERAGE",
    "SIGNAL_NEUTRAL",
]


def _coerce_score(value: object, vendor: str, lower: float, upper: float) -> float:
    """Validate one raw vendor score and return it as a float.

    Rejects booleans, non-numerics, NaN/infinity, and values outside the
    vendor's published reporting range. Out-of-range and non-finite values are
    raised rather than clipped: silently clamping them turns a corrupt vendor
    feed into a confident-looking score.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"{vendor} score must be a real number, got {type(value).__name__}: {value!r}"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(
            f"{vendor} score must be finite; got {value!r}. A missing score must be "
            f"passed as None, never as NaN."
        )
    if not lower <= numeric <= upper:
        raise ValueError(
            f"{vendor} score {numeric} is outside the vendor's reported range "
            f"[{lower}, {upper}]. Fix the feed rather than clipping the value."
        )
    return numeric


@dataclass
class RawVendorEsgData:
    """One issuer's raw, vendor-native ESG assessments.

    All three vendor fields are optional: ``None`` means "this vendor does not
    cover this issuer". A malformed value is *not* the same thing and raises.

    Attributes:
        ticker: Security identifier. Must be a non-empty string.
        msci_rating: MSCI ESG letter rating, 'AAA' (best) to 'CCC' (worst).
            Case and surrounding whitespace are tolerated; any other token
            raises ``ValueError``.
        sustainalytics_risk_score: Sustainalytics ESG Risk Rating, 0.0 (best)
            to 100.0 (worst).
        refinitiv_esg_score: LSEG/Refinitiv ESG score, 0.0 (worst) to 100.0
            (best).
        has_controversial_weapons: Controversial-weapons involvement. Retained
            for backwards compatibility; equivalent to adding
            'CONTROVERSIAL_WEAPONS' to ``exclusion_reasons``.
        exclusion_reasons: Any further rule-based exclusion screens the issuer
            has already failed (e.g. 'TOBACCO', 'THERMAL_COAL_1PCT',
            'UNGC_VIOLATION'). These are inputs, not outputs: this engine does
            not evaluate revenue-threshold screens.
        as_of: Timezone-aware timestamp identifying the *vintage* of this vendor
            snapshot. Required for backtests, because ESG vendors restate
            history (see module docstring). Naive datetimes are rejected.
    """

    ticker: str
    msci_rating: Optional[str] = None
    sustainalytics_risk_score: Optional[float] = None
    refinitiv_esg_score: Optional[float] = None
    has_controversial_weapons: bool = False
    exclusion_reasons: Sequence[str] = field(default_factory=tuple)
    as_of: Optional[datetime] = None


@dataclass
class EsgSignalAuditReport:
    """Structured, auditable output of a single-issuer ESG assessment.

    ``consensus_esg_score`` and ``vendor_disagreement_dispersion`` are ``None``
    whenever they are undefined -- no vendor coverage, or a single vendor -- so
    that a downstream factor ranker can never mistake "not measured" for
    "measured as zero", which would sort an uncovered issuer as the worst ESG
    name in the universe.
    """

    ticker: str
    normalized_msci_score: Optional[float]
    normalized_sustainalytics_score: Optional[float]
    normalized_refinitiv_score: Optional[float]
    consensus_esg_score: Optional[float]
    vendor_disagreement_dispersion: Optional[float]
    has_high_vendor_disagreement: bool
    signal: str
    audit_notes: str
    vendor_count: int = 0
    exclusion_reasons: Tuple[str, ...] = ()
    as_of: Optional[datetime] = None


class EsgDataSignalEngine:
    """Cross-vendor ESG normalization, consensus, dispersion and signal engine.

    The engine is deterministic and stateless: every call to
    :meth:`analyze_esg_signal` depends only on its argument and the constructor
    configuration.

    Args:
        disagreement_threshold: Dispersion above which the vendors are treated
            as materially disagreeing. Illustrative default, not a calibrated
            constant.
        leader_threshold: Consensus at or above which a leader signal may fire.
        laggard_threshold: Consensus at or below which a laggard signal may fire.
        min_vendors_for_conviction: Minimum number of covering vendors before
            any directional signal is emitted. The default of 2 exists because
            a single vendor produces a dispersion of zero by construction --
            reporting that as "low vendor disagreement" asserts corroboration
            that was never observed.
        sustainalytics_severe_threshold: Risk score treated as the bottom of the
            normalized scale. Defaults to Sustainalytics' published Severe-band
            floor; set to 100.0 to reproduce a naive full-scale rescale.
        msci_rating_map: Override for the letter -> [0, 1] mapping, e.g. to
            supply cross-sectional percentiles instead of band mid-points.
    """

    def __init__(
        self,
        disagreement_threshold: float = 0.25,
        leader_threshold: float = 0.75,
        laggard_threshold: float = 0.30,
        min_vendors_for_conviction: int = 2,
        sustainalytics_severe_threshold: float = SUSTAINALYTICS_SEVERE_THRESHOLD,
        msci_rating_map: Optional[Mapping[str, float]] = None,
    ) -> None:
        if not 0.0 < disagreement_threshold <= 1.0:
            raise ValueError(
                f"disagreement_threshold must be in (0.0, 1.0], got {disagreement_threshold}"
            )
        if not 0.0 <= laggard_threshold < leader_threshold <= 1.0:
            raise ValueError(
                "thresholds must satisfy 0.0 <= laggard_threshold < leader_threshold "
                f"<= 1.0, got laggard={laggard_threshold}, leader={leader_threshold}"
            )
        if min_vendors_for_conviction < 1:
            raise ValueError(
                f"min_vendors_for_conviction must be >= 1, got {min_vendors_for_conviction}"
            )
        if not 0.0 < sustainalytics_severe_threshold <= SUSTAINALYTICS_SCALE_MAX:
            raise ValueError(
                "sustainalytics_severe_threshold must be in (0.0, "
                f"{SUSTAINALYTICS_SCALE_MAX}], got {sustainalytics_severe_threshold}"
            )

        self.disagreement_threshold = disagreement_threshold
        self.leader_threshold = leader_threshold
        self.laggard_threshold = laggard_threshold
        self.min_vendors_for_conviction = min_vendors_for_conviction
        self.sustainalytics_severe_threshold = sustainalytics_severe_threshold
        self._msci_rating_map: Dict[str, float] = dict(
            msci_rating_map if msci_rating_map is not None else MSCI_RATING_MAP
        )

    # --- Vendor normalization ------------------------------------------------

    def normalize_msci(self, rating: Optional[str]) -> Optional[float]:
        """Map an MSCI ESG letter rating onto [0.0, 1.0] via its band mid-point.

        Returns ``None`` only for ``None`` or an empty/whitespace string, both
        of which mean "MSCI does not cover this issuer". Any other unrecognised
        token raises ``ValueError``: returning ``None`` for 'A+' or 'AAAA'
        would silently drop a live vendor opinion out of the consensus and
        report it as missing coverage.
        """
        if rating is None:
            return None
        if not isinstance(rating, str):
            raise TypeError(
                f"msci_rating must be a string or None, got {type(rating).__name__}"
            )
        token = rating.strip().upper()
        if not token:
            return None
        try:
            return self._msci_rating_map[token]
        except KeyError:
            raise ValueError(
                f"Unrecognised MSCI ESG letter rating {rating!r}. Expected one of "
                f"{sorted(self._msci_rating_map)}, or None for no coverage."
            ) from None

    def normalize_sustainalytics(self, risk_score: Optional[float]) -> Optional[float]:
        """Invert a Sustainalytics ESG Risk Rating onto [0.0, 1.0].

        The rescale is bounded at the Severe-band floor (default 40.0), not at
        the nominal 100. Scores at or beyond that floor normalize to 0.0, which
        is correct: the Severe band is open-ended, so all of it is the bottom of
        the observable scale.
        """
        if risk_score is None:
            return None
        numeric = _coerce_score(
            risk_score, "Sustainalytics ESG Risk Rating", 0.0, SUSTAINALYTICS_SCALE_MAX
        )
        bounded = min(numeric, self.sustainalytics_severe_threshold)
        return round(1.0 - (bounded / self.sustainalytics_severe_threshold), _SCORE_PRECISION)

    def normalize_refinitiv(self, esg_score: Optional[float]) -> Optional[float]:
        """Rescale an LSEG/Refinitiv 0-100 ESG score onto [0.0, 1.0]."""
        if esg_score is None:
            return None
        numeric = _coerce_score(
            esg_score, "LSEG/Refinitiv ESG score", 0.0, REFINITIV_SCALE_MAX
        )
        return round(numeric / REFINITIV_SCALE_MAX, _SCORE_PRECISION)

    @staticmethod
    def sustainalytics_risk_category(risk_score: float) -> str:
        """Return the published Sustainalytics risk category for a raw score."""
        numeric = _coerce_score(
            risk_score, "Sustainalytics ESG Risk Rating", 0.0, SUSTAINALYTICS_SCALE_MAX
        )
        for lower_bound, label in SUSTAINALYTICS_RISK_CATEGORIES:
            if numeric >= lower_bound:
                return label
        raise AssertionError("unreachable: categories cover [0, 100]")

    # --- Analysis ------------------------------------------------------------

    def analyze_esg_signal(self, data: RawVendorEsgData) -> EsgSignalAuditReport:
        """Normalize, aggregate and classify one issuer's vendor ESG data.

        Signal precedence, first match wins:

        1. ``EXCLUDED_SECTOR`` -- a rule-based exclusion screen was already
           failed. This overrides any consensus score.
        2. ``INSUFFICIENT_VENDOR_COVERAGE`` -- fewer covering vendors than
           ``min_vendors_for_conviction``.
        3. ``NEUTRAL_HIGH_DISAGREEMENT`` -- dispersion above threshold. Applied
           symmetrically: vendors that cannot agree do not support a laggard
           call any more than they support a leader call.
        4. ``BULLISH_ESG_LEADER`` -- consensus at or above ``leader_threshold``.
        5. ``BEARISH_ESG_LAGGARD`` -- consensus at or below ``laggard_threshold``.
        6. ``NEUTRAL``.

        Raises:
            TypeError: ``data`` is not a ``RawVendorEsgData``, or a field has
                the wrong type.
            ValueError: ``ticker`` is empty, ``as_of`` is timezone-naive, or a
                vendor value is non-finite, out of range, or an unrecognised
                MSCI rating token.
        """
        if not isinstance(data, RawVendorEsgData):
            raise TypeError(
                f"data must be a RawVendorEsgData, got {type(data).__name__}"
            )
        if not isinstance(data.ticker, str) or not data.ticker.strip():
            raise ValueError("ticker must be a non-empty string")
        as_of = self._validate_as_of(data.as_of)
        exclusions = self._collect_exclusions(data)

        normalized_msci = self.normalize_msci(data.msci_rating)
        normalized_sustainalytics = self.normalize_sustainalytics(
            data.sustainalytics_risk_score
        )
        normalized_refinitiv = self.normalize_refinitiv(data.refinitiv_esg_score)

        covering: List[float] = [
            score
            for score in (
                normalized_msci,
                normalized_sustainalytics,
                normalized_refinitiv,
            )
            if score is not None
        ]
        vendor_count = len(covering)

        consensus: Optional[float] = None
        dispersion: Optional[float] = None
        if vendor_count:
            # Deviations are taken about the unrounded mean; rounding the mean
            # first would bias the variance upward.
            raw_mean = sum(covering) / vendor_count
            consensus = round(raw_mean, _SCORE_PRECISION)
            if vendor_count > 1:
                variance = sum((s - raw_mean) ** 2 for s in covering) / vendor_count
                dispersion = round(math.sqrt(variance), _SCORE_PRECISION)

        has_high_disagreement = (
            dispersion is not None and dispersion > self.disagreement_threshold
        )

        signal, notes = self._classify(
            ticker=data.ticker,
            exclusions=exclusions,
            vendor_count=vendor_count,
            consensus=consensus,
            dispersion=dispersion,
            has_high_disagreement=has_high_disagreement,
        )

        return EsgSignalAuditReport(
            ticker=data.ticker,
            normalized_msci_score=normalized_msci,
            normalized_sustainalytics_score=normalized_sustainalytics,
            normalized_refinitiv_score=normalized_refinitiv,
            consensus_esg_score=consensus,
            vendor_disagreement_dispersion=dispersion,
            has_high_vendor_disagreement=has_high_disagreement,
            signal=signal,
            audit_notes=notes,
            vendor_count=vendor_count,
            exclusion_reasons=exclusions,
            as_of=as_of,
        )

    # --- Internals -----------------------------------------------------------

    @staticmethod
    def _validate_as_of(as_of: Optional[datetime]) -> Optional[datetime]:
        if as_of is None:
            return None
        if not isinstance(as_of, datetime):
            raise TypeError(
                f"as_of must be a datetime or None, got {type(as_of).__name__}"
            )
        if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
            raise ValueError(
                "as_of must be timezone-aware. ESG vendors restate historical "
                "scores, so the snapshot vintage must be unambiguous."
            )
        return as_of

    @staticmethod
    def _collect_exclusions(data: RawVendorEsgData) -> Tuple[str, ...]:
        """Merge the legacy boolean and the generic reason list, order-preserving."""
        if not isinstance(data.has_controversial_weapons, bool):
            raise TypeError(
                "has_controversial_weapons must be a bool, got "
                f"{type(data.has_controversial_weapons).__name__}"
            )
        if isinstance(data.exclusion_reasons, str):
            raise TypeError(
                "exclusion_reasons must be a sequence of strings, not a single string"
            )
        collected: List[str] = []
        if data.has_controversial_weapons:
            collected.append("CONTROVERSIAL_WEAPONS")
        for reason in data.exclusion_reasons:
            if not isinstance(reason, str):
                raise TypeError(
                    f"exclusion_reasons entries must be strings, got {type(reason).__name__}"
                )
            token = reason.strip().upper()
            if token and token not in collected:
                collected.append(token)
        return tuple(collected)

    def _classify(
        self,
        ticker: str,
        exclusions: Tuple[str, ...],
        vendor_count: int,
        consensus: Optional[float],
        dispersion: Optional[float],
        has_high_disagreement: bool,
    ) -> Tuple[str, str]:
        if exclusions:
            notes = (
                f"EXCLUDED [{ticker}]: failed rule-based exclusion screen(s) "
                f"{', '.join(exclusions)}. Consensus score is reported for audit only "
                f"and must not re-admit the issuer."
            )
            logger.warning(notes)
            return SIGNAL_EXCLUDED, notes

        if vendor_count < self.min_vendors_for_conviction:
            if vendor_count == 0:
                notes = (
                    f"NO ESG DATA [{ticker}]: no vendor covers this issuer. Consensus "
                    f"and dispersion are undefined, not zero."
                )
            else:
                reason = (
                    "dispersion is undefined with a single opinion, so vendor "
                    "agreement cannot be evidenced"
                    if vendor_count == 1
                    else "the configured coverage floor was not met"
                )
                notes = (
                    f"INSUFFICIENT VENDOR COVERAGE [{ticker}]: {vendor_count} of "
                    f"{self.min_vendors_for_conviction} required vendors -- {reason}."
                )
            logger.warning(notes)
            return SIGNAL_INSUFFICIENT_COVERAGE, notes

        # vendor_count >= 1 here, so consensus is defined. dispersion is still
        # None when the caller lowered min_vendors_for_conviction to 1.
        if consensus is None:  # pragma: no cover - guarded by the branch above
            raise AssertionError("consensus must be defined once coverage is met")
        dispersion_text = "undefined (single vendor)" if dispersion is None else f"{dispersion:.4f}"

        if has_high_disagreement:
            notes = (
                f"HIGH VENDOR DISAGREEMENT [{ticker}]: dispersion {dispersion_text} "
                f"exceeds threshold {self.disagreement_threshold} across {vendor_count} "
                f"vendors (consensus {consensus:.4f}). Rating noise dominates; no "
                f"directional signal."
            )
            logger.warning(notes)
            return SIGNAL_HIGH_DISAGREEMENT, notes

        if consensus >= self.leader_threshold:
            notes = (
                f"BULLISH ESG LEADER [{ticker}]: consensus {consensus:.4f} across "
                f"{vendor_count} vendor(s) with dispersion {dispersion_text}."
            )
            logger.info(notes)
            return SIGNAL_BULLISH, notes

        if consensus <= self.laggard_threshold:
            notes = (
                f"BEARISH ESG LAGGARD [{ticker}]: consensus {consensus:.4f} across "
                f"{vendor_count} vendor(s) with dispersion {dispersion_text}."
            )
            logger.warning(notes)
            return SIGNAL_BEARISH, notes

        notes = (
            f"NEUTRAL ESG [{ticker}]: consensus {consensus:.4f} in the middle band "
            f"across {vendor_count} vendor(s)."
        )
        logger.info(notes)
        return SIGNAL_NEUTRAL, notes
