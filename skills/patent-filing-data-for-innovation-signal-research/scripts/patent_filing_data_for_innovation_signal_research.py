"""
Patent filing innovation signal engine — point-in-time cross-sectional factor.

Builds an Innovation Quality Score (IQS) from corporate patent filings and turns
it into a cross-sectionally standardised equity factor. Everything in this module
exists to keep two specific biases out of the result: **look-ahead on the
publication date** and **truncation of forward-citation counts**.

Point-in-time discipline
------------------------
A patent filing is *not* public on its filing date. Three dates matter and
conflating them is look-ahead bias:

* ``filing_date`` — when the application was filed. Confidential on that day:
  35 U.S.C. 122(a) keeps applications "in confidence".
* ``pre_grant_publication_date`` — the 18-month A-publication. Under
  35 U.S.C. 122(b)(1)(A) an application is published "promptly after the
  expiration of a period of 18 months from the earliest filing date for which a
  benefit is sought." MPEP 1120 states the projected publication date is the
  later of 18 months from the earliest claimed filing date or roughly 14 weeks
  from the filing-receipt mailing date, and that publications issue weekly on
  Thursdays. Article 93(1)(a) EPC sets the same 18-month rule at the EPO,
  running from the filing *or priority* date.
* ``grant_date`` — publication of the granted patent.

An application is therefore tradable information from the **earlier of
pre-grant publication and grant**, which is what
:attr:`PatentFilingRecord.public_availability_date` returns. Two carve-outs make
this a genuine ``min()`` rather than "filing + 18 months":

* A **non-publication request** under 35 U.S.C. 122(b)(2)(B)(i) is available
  where the invention "has not been and will not be the subject of an
  application filed in another country ... that requires publication". Those
  applications are never A-published and become public only at grant.
* An application under a **secrecy order** (35 U.S.C. 181) is not published at
  all while the order stands.

So ``pre_grant_publication_date`` may legitimately be ``None``. Supply the dates
you actually observed; do not synthesise them by adding 18 months to the filing
date.

Citation truncation
-------------------
``forward_citations`` is a *cumulative* count that keeps growing for decades.
Hall, Jaffe & Trajtenberg (2001) quantify it: if the lag distribution were
stable, "patents granted in year 2,000 will receive just half of their citations
by 2,010, 75% by 2,020, and even by 2,050 they will still be receiving some."

Two consequences, both handled here:

1. **Look-ahead.** Scoring a 2016 cross-section with a citation count pulled from
   today's database imports a decade of future information. Every record must
   carry ``citations_observed_asof`` — the date the count was read — and the
   engine refuses counts observed after the ``as_of`` cutoff.
2. **Truncation.** Even a correctly-dated count is biased against recent
   patents, which have had less time to be cited. HJT's remedy is the
   "fixed-effects approach ... scaling citation counts by dividing them by the
   average citation count for a group of patents to which the patent of interest
   belongs." Hirshleifer, Hsu & Li (2013) implement exactly that, scaling by the
   "average number of citations received in year t by all patents of the same
   subcategory granted in year t-j", which "helps control for citation propensity
   attributed to differences in technology fields, grant year, and citing year."
   :func:`PatentInnovationEngine` builds those cohorts from
   ``(technology_class, availability_year)`` and divides.

Scale, not size
---------------
The published return predictor is an *efficiency* ratio, not a count.
Hirshleifer, Hsu & Li (2013) find "innovative efficiency (IE), patents or
citations scaled by research and development expenditures, is a strong positive
predictor of future returns after controlling for firm characteristics and
risk." A raw patent count is largely a firm-size proxy, and a *sum* of citations
over patents is mechanically collinear with that count. This engine therefore

* divides the velocity term by a caller-supplied ``innovation_input`` (R&D
  spend, in the caller's own units) when one is available, and flags the report
  when it is not, and
* uses the **mean** cohort-adjusted citation ratio per patent as the quality
  term, which is scale-free by construction and not a second copy of velocity.

Composite construction
----------------------
The two terms are standardised *before* they are weighted. Weighting a raw count
against a sum of logs, as a naive ``w_v * V + w_c * C`` does, does not give the
two terms the influence the weights advertise — whichever term happens to carry
the larger numeric scale dominates. Each component is z-scored across the
universe first, so ``velocity_weight`` and ``citation_weight`` mean what they say.

Sources
-------
* 35 U.S.C. 122 — confidential status; 18-month publication; non-publication
  request: <https://www.uspto.gov/web/offices/pac/mpep/s1120.html>
* MPEP 1120, "Eighteen-Month Publication of Patent Applications".
* Article 93 EPC, "Publication of the European patent application".
* Hall, B. H., Jaffe, A. B. & Trajtenberg, M. (2001). "The NBER Patent Citation
  Data File: Lessons, Insights and Methodological Tools." NBER WP 8498.
  <https://www.nber.org/system/files/working_papers/w8498/w8498.pdf>
* Hirshleifer, D., Hsu, P.-H. & Li, D. (2013). "Innovative efficiency and stock
  returns." *Journal of Financial Economics* 107(3), 632-654.
* USPTO Patents Pendency dashboard (live application-to-grant pendency):
  <https://www.uspto.gov/dashboard/patents/pendency.html>
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "PatentDataError",
    "PatentFilingRecord",
    "PatentSignalConfig",
    "AssetInnovationScore",
    "PatentInnovationReport",
    "PatentFilingDataForInnovationSignalResearchEngine",
    "PatentInnovationEngine",
]

# Engineering default, not a sourced constant: universes smaller than this give
# cross-sectional z-scores too coarse to rank on. Surfaced as a report flag
# rather than an exception so research code can still inspect small samples.
RECOMMENDED_MIN_UNIVERSE = 30


class PatentDataError(ValueError):
    """Raised when a supplied patent record cannot be scored as given."""


def _require_finite_non_negative(value: object, field_name: str, patent_id: str) -> float:
    """Coerce to a finite, non-negative float or raise.

    Guards a real silent-corruption path: ``max(0, float('nan'))`` evaluates to
    ``0`` in Python, so a NaN citation count read from a vendor join would be
    silently scored as "zero citations" rather than as missing data.
    """
    if value is None:
        raise PatentDataError(f"{field_name} is None for patent {patent_id!r}; supply a number")
    if isinstance(value, bool):
        raise PatentDataError(f"{field_name} is a bool for patent {patent_id!r}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise PatentDataError(
            f"{field_name}={value!r} for patent {patent_id!r} is not numeric"
        ) from exc
    if not math.isfinite(numeric):
        raise PatentDataError(
            f"{field_name}={value!r} for patent {patent_id!r} is not finite "
            "(NaN/Inf indicates a failed upstream join, not a zero count)"
        )
    if numeric < 0:
        raise PatentDataError(
            f"{field_name}={numeric} for patent {patent_id!r} is negative"
        )
    return numeric


@dataclass(frozen=True)
class PatentFilingRecord:
    """One patent, carrying every date needed to establish when it became public.

    Args:
        asset_id: Issuer identifier the patent is attributed to. Normalised to
            upper case; attribution to a listed issuer is the caller's problem
            (assignee disambiguation is not solved here).
        patent_id: Publication/grant number. Must be unique — duplicates are
            deduplicated and counted, because assignee-disambiguation joins
            routinely emit one row per assignee per patent.
        filing_date: Application filing date. Confidential when filed
            (35 U.S.C. 122(a)); never used as an availability date.
        grant_date: Grant/issue date, or ``None`` if still pending.
        pre_grant_publication_date: The 18-month A-publication date, or ``None``
            where no pre-grant publication exists (non-publication request under
            35 U.S.C. 122(b)(2)(B)(i), or a secrecy order under 35 U.S.C. 181).
        forward_citations: Cumulative forward citations **as observed on**
            ``citations_observed_asof``.
        citations_observed_asof: The date ``forward_citations`` was read from the
            source database. Required whenever ``forward_citations > 0``: a count
            without an observation date cannot be checked for look-ahead.
        claim_count: Independent + dependent claim count. Carried and reported
            as a diagnostic; deliberately **not** scored (see module docs).
        technology_class: Technology classification (CPC/IPC/USPC subcategory).
            Forms the citation cohort together with the availability year.
    """

    asset_id: str
    patent_id: str
    filing_date: date
    grant_date: Optional[date] = None
    pre_grant_publication_date: Optional[date] = None
    forward_citations: int = 0
    citations_observed_asof: Optional[date] = None
    claim_count: Optional[int] = None
    technology_class: str = "UNCLASSIFIED"

    @property
    def public_availability_date(self) -> Optional[date]:
        """Earliest date the filing's existence was public, or ``None``.

        The earlier of pre-grant publication and grant. ``None`` means the
        application was still confidential as of every date on record — a
        pending, unpublished application carries no tradable information.
        """
        candidates = [d for d in (self.pre_grant_publication_date, self.grant_date) if d is not None]
        return min(candidates) if candidates else None


@dataclass(frozen=True)
class PatentSignalConfig:
    """Weights and gates. Every default here is an engineering default.

    Only ``lookback_years`` has a published antecedent: Hirshleifer, Hsu & Li
    (2013) aggregate "patent citations received in year t by patents granted in
    years t-1 to t-5", a 5-year window they tie to evidence that "technology
    cycles measured by the duration of the benefits of R&D spending are
    approximately 5 years in most industries (Lev and Sougiannis, 1996)."
    """

    velocity_weight: float = 0.50
    citation_weight: float = 0.50
    lookback_years: int = 5
    winsorize_z: float = 3.0
    # Minimum patents in a (technology_class, year) cohort before its mean is
    # trusted as a truncation denominator. A cohort of one is just the patent
    # itself and would adjust every such patent to exactly 1.0.
    min_cohort_size: int = 5
    # Compress the cohort-adjusted ratio with ln(1+x) so a single mega-cited
    # patent cannot carry an asset's quality score. Engineering choice layered
    # on top of the sourced cohort adjustment, not part of HJT.
    log_compress_citations: bool = True
    min_universe_size: int = 2
    # Citation counts read on different dates cover different exposure windows,
    # so a wide spread makes the cross-section incomparable even though every
    # count individually passes the as_of check. Engineering default.
    max_citation_observation_span_days: int = 31

    def __post_init__(self) -> None:
        for name in ("velocity_weight", "citation_weight"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be a finite number, got {value!r}")
            if value < 0:
                raise ValueError(f"{name} must be non-negative, got {value}")
        if self.velocity_weight + self.citation_weight <= 0:
            raise ValueError("velocity_weight + citation_weight must be > 0")
        if self.lookback_years <= 0:
            raise ValueError(f"lookback_years must be positive, got {self.lookback_years}")
        if self.winsorize_z <= 0:
            raise ValueError(f"winsorize_z must be positive, got {self.winsorize_z}")
        if self.min_cohort_size < 1:
            raise ValueError(f"min_cohort_size must be >= 1, got {self.min_cohort_size}")
        if self.max_citation_observation_span_days < 0:
            raise ValueError(
                "max_citation_observation_span_days must be non-negative, got "
                f"{self.max_citation_observation_span_days}"
            )
        if self.min_universe_size < 2:
            raise ValueError(
                f"min_universe_size must be >= 2 (a cross-section of one has no "
                f"dispersion to standardise against), got {self.min_universe_size}"
            )


@dataclass(frozen=True)
class AssetInnovationScore:
    """Per-asset diagnostics behind one IQS. Every term is inspectable."""

    asset_id: str
    patents_in_window: int
    velocity: float
    velocity_scaled: bool
    citation_quality: float
    cohort_adjusted_patents: int
    mean_claim_count: Optional[float]
    velocity_z: float
    citation_z: float
    iqs: float
    z_score: float


@dataclass
class PatentInnovationReport:
    """Result of one cross-sectional evaluation.

    The exclusion counters reconcile exactly:
    ``records_supplied == patents_scored + duplicate_patent_ids_dropped +
    not_yet_public_excluded + outside_lookback_window_excluded``.
    A mismatch means records were lost upstream.
    """

    as_of: Optional[date]
    total_assets: int
    asset_scores: Dict[str, float]
    z_scores: Dict[str, float]
    top_innovator: str
    status: str  # SIGNALS_GENERATED | EMPTY_UNIVERSE | INSUFFICIENT_UNIVERSE | NO_DISPERSION
    audit_notes: str
    detail: Dict[str, AssetInnovationScore] = field(default_factory=dict)
    records_supplied: int = 0
    patents_scored: int = 0
    duplicate_patent_ids_dropped: int = 0
    not_yet_public_excluded: int = 0
    outside_lookback_window_excluded: int = 0
    cohorts_below_min_size: int = 0
    citation_observation_span_days: int = 0
    assets_missing_innovation_input: List[str] = field(default_factory=list)
    universe_below_recommended_size: bool = False
    winsorisation_can_bind: bool = False
    warnings: List[str] = field(default_factory=list)

    def reconciles(self) -> bool:
        """True when every supplied record is accounted for exactly once."""
        return self.records_supplied == (
            self.patents_scored
            + self.duplicate_patent_ids_dropped
            + self.not_yet_public_excluded
            + self.outside_lookback_window_excluded
        )


class PatentFilingDataForInnovationSignalResearchEngine:
    """Cross-sectional innovation factor from point-in-time patent filings.

    Usage is deliberately as-of driven: pass the whole filing history and roll
    ``as_of`` forward. Records not yet public at ``as_of`` are counted in
    ``not_yet_public_excluded``, never silently dropped.
    """

    def __init__(self, config: Optional[PatentSignalConfig] = None) -> None:
        self.config = config or PatentSignalConfig()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def compute_patent_innovation_signals(
        self,
        records: Sequence[PatentFilingRecord],
        as_of: date,
        innovation_inputs: Optional[Mapping[str, float]] = None,
    ) -> PatentInnovationReport:
        """Score the universe as it was knowable on ``as_of``.

        Args:
            records: Patent filings across the whole universe. Filings that were
                not yet public on ``as_of`` may be included; they are excluded
                and counted rather than rejected.
            as_of: The evaluation instant. Required — there is no safe default,
                because omitting the cutoff is precisely the look-ahead this
                engine exists to prevent.
            innovation_inputs: Optional ``{asset_id: R&D spend}`` in the caller's
                own units, used to turn the velocity count into the R&D-scaled
                efficiency ratio that Hirshleifer, Hsu & Li (2013) document.
                Assets without an entry fall back to an unscaled count and are
                listed in ``assets_missing_innovation_input`` — mixing scaled and
                unscaled velocities in one cross-section is not comparable, so
                treat a non-empty list as a data gap, not a warning to ignore.

        Returns:
            A :class:`PatentInnovationReport`. ``z_scores`` is the factor.

        Raises:
            TypeError: if ``as_of`` is not a ``date``.
            PatentDataError: if a record is internally inconsistent — a citation
                count observed after ``as_of``, a non-finite or negative count,
                a grant date before the filing date, or a blank identifier.
        """
        if not isinstance(as_of, date):
            raise TypeError(f"as_of must be a datetime.date, got {type(as_of).__name__}")

        records = list(records or [])
        if not records:
            return PatentInnovationReport(
                as_of=as_of,
                total_assets=0,
                asset_scores={},
                z_scores={},
                top_innovator="NONE",
                status="EMPTY_UNIVERSE",
                audit_notes="No patent records provided.",
            )

        window_start = self._window_start(as_of)
        eligible, counters = self._select_eligible(records, as_of, window_start)

        report = PatentInnovationReport(
            as_of=as_of,
            total_assets=0,
            asset_scores={},
            z_scores={},
            top_innovator="NONE",
            status="EMPTY_UNIVERSE",
            audit_notes="",
            records_supplied=len(records),
            duplicate_patent_ids_dropped=counters["duplicates"],
            not_yet_public_excluded=counters["not_public"],
            outside_lookback_window_excluded=counters["outside_window"],
            patents_scored=len(eligible),
        )

        if not eligible:
            report.audit_notes = (
                f"No patent was public and inside the {self.config.lookback_years}-year "
                f"window ending {as_of.isoformat()}. Supplied={len(records)}, "
                f"not-yet-public={counters['not_public']}, "
                f"outside-window={counters['outside_window']}, "
                f"duplicates={counters['duplicates']}."
            )
            logger.info(report.audit_notes)
            return report

        cohort_means, cohorts_below_min = self._build_citation_cohorts(eligible)
        report.cohorts_below_min_size = cohorts_below_min

        observation_dates = [
            record.citations_observed_asof
            for record, _ in eligible
            if record.citations_observed_asof is not None
        ]
        if observation_dates:
            report.citation_observation_span_days = (
                max(observation_dates) - min(observation_dates)
            ).days

        per_asset = self._aggregate_by_asset(eligible, cohort_means, innovation_inputs)
        report.total_assets = len(per_asset)

        if len(per_asset) < self.config.min_universe_size:
            report.status = "INSUFFICIENT_UNIVERSE"
            counts = ", ".join(
                f"{a}={int(v['count'])} patent(s)" for a, v in sorted(per_asset.items())
            )
            report.audit_notes = (
                f"Universe of {len(per_asset)} asset(s) is below min_universe_size="
                f"{self.config.min_universe_size}; a cross-sectional z-score is undefined "
                f"with no peers to standardise against. No factor produced. Eligible: {counts}."
            )
            logger.warning(report.audit_notes)
            return report

        self._finalise(report, per_asset, innovation_inputs, as_of)
        return report

    # ------------------------------------------------------------------ #
    # Stage 1 — point-in-time eligibility
    # ------------------------------------------------------------------ #

    def _window_start(self, as_of: date) -> date:
        """Start of the rolling lookback window (exclusive lower bound is not used;
        the window is inclusive on both ends)."""
        try:
            return as_of.replace(year=as_of.year - self.config.lookback_years)
        except ValueError:
            # 29 February with a non-leap target year.
            return as_of.replace(year=as_of.year - self.config.lookback_years, day=28)

    def _select_eligible(
        self,
        records: Sequence[PatentFilingRecord],
        as_of: date,
        window_start: date,
    ) -> Tuple[List[Tuple[PatentFilingRecord, date]], Dict[str, int]]:
        """Validate, deduplicate, and keep only records public within the window."""
        counters = {"duplicates": 0, "not_public": 0, "outside_window": 0}
        seen_patent_ids: Dict[str, None] = {}
        eligible: List[Tuple[PatentFilingRecord, date]] = []

        for record in records:
            self._validate_record(record, as_of)

            key = record.patent_id.strip().upper()
            if key in seen_patent_ids:
                # One patent, several assignee rows: counting it twice inflates
                # velocity for exactly the largest, most-joined issuers.
                counters["duplicates"] += 1
                continue
            seen_patent_ids[key] = None

            available = record.public_availability_date
            if available is None or available > as_of:
                counters["not_public"] += 1
                continue
            if available < window_start:
                counters["outside_window"] += 1
                continue

            eligible.append((record, available))

        # Deterministic downstream ordering regardless of caller input order.
        eligible.sort(key=lambda pair: (pair[0].asset_id.strip().upper(), pair[0].patent_id))
        return eligible, counters

    def _validate_record(self, record: PatentFilingRecord, as_of: date) -> None:
        """Reject records that cannot be scored without guessing."""
        if not isinstance(record, PatentFilingRecord):
            raise PatentDataError(f"expected PatentFilingRecord, got {type(record).__name__}")
        if not record.asset_id or not record.asset_id.strip():
            raise PatentDataError(f"blank asset_id on patent {record.patent_id!r}")
        if not record.patent_id or not record.patent_id.strip():
            raise PatentDataError(f"blank patent_id for asset {record.asset_id!r}")
        if not isinstance(record.filing_date, date):
            raise PatentDataError(
                f"filing_date must be a datetime.date on patent {record.patent_id!r}"
            )

        for name in ("grant_date", "pre_grant_publication_date"):
            value = getattr(record, name)
            if value is None:
                continue
            if not isinstance(value, date):
                raise PatentDataError(
                    f"{name} must be a datetime.date or None on patent {record.patent_id!r}"
                )
            if value < record.filing_date:
                raise PatentDataError(
                    f"{name}={value.isoformat()} precedes filing_date="
                    f"{record.filing_date.isoformat()} on patent {record.patent_id!r}"
                )

        citations = _require_finite_non_negative(
            record.forward_citations, "forward_citations", record.patent_id
        )
        if citations > 0:
            if record.citations_observed_asof is None:
                raise PatentDataError(
                    f"patent {record.patent_id!r} carries {citations:g} forward citations with no "
                    "citations_observed_asof; an undated cumulative count cannot be checked for "
                    "look-ahead (see Hall, Jaffe & Trajtenberg 2001 on citation truncation)"
                )
            if not isinstance(record.citations_observed_asof, date):
                raise PatentDataError(
                    f"citations_observed_asof must be a datetime.date on patent {record.patent_id!r}"
                )
            if record.citations_observed_asof > as_of:
                raise PatentDataError(
                    f"patent {record.patent_id!r} has citations observed "
                    f"{record.citations_observed_asof.isoformat()}, after as_of="
                    f"{as_of.isoformat()}: that count contains citations that had not yet been "
                    "made. Re-read the citation count as of the evaluation date."
                )

        if record.claim_count is not None:
            _require_finite_non_negative(record.claim_count, "claim_count", record.patent_id)

    # ------------------------------------------------------------------ #
    # Stage 2 — citation truncation adjustment
    # ------------------------------------------------------------------ #

    def _build_citation_cohorts(
        self, eligible: Sequence[Tuple[PatentFilingRecord, date]]
    ) -> Tuple[Dict[Tuple[str, int], float], int]:
        """Mean citations per ``(technology_class, availability_year)`` cohort.

        This is Hall, Jaffe & Trajtenberg's fixed-effects adjustment: dividing a
        patent's citations by its cohort mean removes the part of the count that
        is explained by "differences in technology fields, grant year, and citing
        year" (Hirshleifer, Hsu & Li 2013) rather than by the patent's impact.

        Returns the usable cohort means and the count of cohorts that were too
        small to trust.
        """
        buckets: Dict[Tuple[str, int], List[float]] = {}
        for record, available in eligible:
            key = (record.technology_class.strip().upper() or "UNCLASSIFIED", available.year)
            buckets.setdefault(key, []).append(float(record.forward_citations))

        means: Dict[Tuple[str, int], float] = {}
        below_min = 0
        for key, values in buckets.items():
            if len(values) < self.config.min_cohort_size:
                below_min += 1
                continue
            mean = sum(values) / len(values)
            # A cohort in which nothing was cited yet carries no information;
            # dividing by it would be a zero-division, so leave it unadjusted.
            if mean > 0.0:
                means[key] = mean
        return means, below_min

    # ------------------------------------------------------------------ #
    # Stage 3 — per-asset aggregation
    # ------------------------------------------------------------------ #

    def _aggregate_by_asset(
        self,
        eligible: Sequence[Tuple[PatentFilingRecord, date]],
        cohort_means: Mapping[Tuple[str, int], float],
        innovation_inputs: Optional[Mapping[str, float]],
    ) -> Dict[str, Dict[str, object]]:
        """Collapse patents to one velocity and one quality term per asset."""
        grouped: Dict[str, List[Tuple[PatentFilingRecord, date]]] = {}
        for record, available in eligible:
            grouped.setdefault(record.asset_id.strip().upper(), []).append((record, available))

        normalised_inputs: Dict[str, float] = {}
        if innovation_inputs:
            for asset, value in innovation_inputs.items():
                numeric = _require_finite_non_negative(value, "innovation_input", str(asset))
                if numeric > 0.0:
                    normalised_inputs[str(asset).strip().upper()] = numeric

        per_asset: Dict[str, Dict[str, object]] = {}
        for asset, patents in sorted(grouped.items()):
            count = len(patents)

            scale = normalised_inputs.get(asset)
            velocity = (count / scale) if scale else float(count)

            ratios: List[float] = []
            adjusted = 0
            for record, available in patents:
                key = (record.technology_class.strip().upper() or "UNCLASSIFIED", available.year)
                mean = cohort_means.get(key)
                if mean:
                    ratios.append(float(record.forward_citations) / mean)
                    adjusted += 1
                else:
                    # No trustworthy cohort: fall back to the raw count. It stays
                    # truncation-biased, which is why cohorts_below_min_size is
                    # reported rather than hidden.
                    ratios.append(float(record.forward_citations))

            if self.config.log_compress_citations:
                ratios = [math.log1p(r) for r in ratios]
            quality = sum(ratios) / count  # mean, not sum: scale-free by construction

            claims = [float(r.claim_count) for r, _ in patents if r.claim_count is not None]
            per_asset[asset] = {
                "count": count,
                "velocity": velocity,
                "velocity_scaled": scale is not None,
                "quality": quality,
                "cohort_adjusted": adjusted,
                "mean_claim_count": (sum(claims) / len(claims)) if claims else None,
            }
        return per_asset

    # ------------------------------------------------------------------ #
    # Stage 4 — standardise, weight, winsorise
    # ------------------------------------------------------------------ #

    @staticmethod
    def _zscores(values: Mapping[str, float]) -> Tuple[Dict[str, float], float]:
        """Population z-scores. Returns the scores and the dispersion used.

        A degenerate component (every asset identical) yields all-zero scores
        rather than a fabricated ranking; the caller checks the returned sigma.
        """
        keys = list(values)
        n = len(keys)
        mean = sum(values[k] for k in keys) / n
        variance = sum((values[k] - mean) ** 2 for k in keys) / n
        sigma = math.sqrt(variance)
        if sigma <= 0.0:
            return {k: 0.0 for k in keys}, 0.0
        return {k: (values[k] - mean) / sigma for k in keys}, sigma

    def _finalise(
        self,
        report: PatentInnovationReport,
        per_asset: Dict[str, Dict[str, object]],
        innovation_inputs: Optional[Mapping[str, float]],
        as_of: date,
    ) -> None:
        cfg = self.config

        velocity_z, velocity_sigma = self._zscores(
            {a: float(v["velocity"]) for a, v in per_asset.items()}
        )
        quality_z, quality_sigma = self._zscores(
            {a: float(v["quality"]) for a, v in per_asset.items()}
        )

        weight_sum = cfg.velocity_weight + cfg.citation_weight
        composite = {
            asset: (cfg.velocity_weight * velocity_z[asset] + cfg.citation_weight * quality_z[asset])
            / weight_sum
            for asset in per_asset
        }

        final_z, composite_sigma = self._zscores(composite)
        limit = cfg.winsorize_z
        z_scores = {a: round(max(-limit, min(limit, z)), 6) for a, z in final_z.items()}

        report.asset_scores = {a: round(composite[a], 6) for a in per_asset}
        report.z_scores = z_scores
        report.detail = {
            asset: AssetInnovationScore(
                asset_id=asset,
                patents_in_window=int(values["count"]),
                velocity=round(float(values["velocity"]), 6),
                velocity_scaled=bool(values["velocity_scaled"]),
                citation_quality=round(float(values["quality"]), 6),
                cohort_adjusted_patents=int(values["cohort_adjusted"]),
                mean_claim_count=(
                    round(float(values["mean_claim_count"]), 4)
                    if values["mean_claim_count"] is not None
                    else None
                ),
                velocity_z=round(velocity_z[asset], 6),
                citation_z=round(quality_z[asset], 6),
                iqs=round(composite[asset], 6),
                z_score=z_scores[asset],
            )
            for asset, values in per_asset.items()
        }

        n = len(per_asset)
        report.universe_below_recommended_size = n < RECOMMENDED_MIN_UNIVERSE
        # Population sigma bounds |z| at sqrt(N - 1), so a winsorisation limit
        # above that is inert -- at the +/-3.0 default it does nothing until
        # N >= 10, and the z-scores are then bounded by universe size rather
        # than by anything about innovation.
        max_attainable_z = math.sqrt(n - 1)
        report.winsorisation_can_bind = max_attainable_z >= limit

        missing = [a for a in sorted(per_asset) if not per_asset[a]["velocity_scaled"]]
        report.assets_missing_innovation_input = missing

        if missing and innovation_inputs:
            report.warnings.append(
                f"{len(missing)} asset(s) had no innovation_input and used an unscaled patent "
                "count while others used an R&D-scaled ratio; the cross-section is not comparable."
            )
        elif not innovation_inputs:
            report.warnings.append(
                "No innovation_input supplied: velocity is a raw patent count, so the factor "
                "carries a firm-size component. Hirshleifer, Hsu & Li (2013) document the return "
                "predictability for R&D-scaled innovative efficiency, not for raw counts."
            )
        if report.universe_below_recommended_size:
            report.warnings.append(
                f"Universe of {n} is below the recommended {RECOMMENDED_MIN_UNIVERSE} for "
                "cross-sectional standardisation."
            )
        if not report.winsorisation_can_bind:
            report.warnings.append(
                f"With population sigma and N={n}, |z| cannot exceed {max_attainable_z:.2f}, "
                f"so winsorisation at +/-{limit} is inert."
            )
        span = report.citation_observation_span_days
        if span > cfg.max_citation_observation_span_days:
            report.warnings.append(
                f"Citation counts were read across a {span}-day span (limit "
                f"{cfg.max_citation_observation_span_days}); counts measured over different "
                "exposure windows are not cross-comparable. Re-read the whole universe on one date."
            )
        if report.cohorts_below_min_size:
            report.warnings.append(
                f"{report.cohorts_below_min_size} citation cohort(s) below min_cohort_size="
                f"{cfg.min_cohort_size} used unadjusted counts and remain truncation-biased."
            )

        if composite_sigma <= 0.0:
            report.status = "NO_DISPERSION"
            report.top_innovator = "NONE"
            report.audit_notes = (
                f"All {n} assets scored identically as of {as_of.isoformat()}; the cross-section "
                "has no dispersion and every z-score is 0.0. There is nothing to rank."
            )
            logger.warning(report.audit_notes)
            return

        # Deterministic tie-break: highest score, then lowest asset_id. Without
        # the second key this follows caller input order.
        report.top_innovator = min(per_asset, key=lambda a: (-composite[a], a))
        report.status = "SIGNALS_GENERATED"
        report.audit_notes = (
            f"PATENT INNOVATION SIGNALS GENERATED as of {as_of.isoformat()}: assets={n}, "
            f"patents scored={report.patents_scored}/{report.records_supplied} "
            f"(not-yet-public={report.not_yet_public_excluded}, "
            f"outside-window={report.outside_lookback_window_excluded}, "
            f"duplicates={report.duplicate_patent_ids_dropped}), "
            f"top innovator='{report.top_innovator}' "
            f"(IQS={report.asset_scores[report.top_innovator]:+.4f}, "
            f"Z={report.z_scores[report.top_innovator]:+.2f})."
        )
        logger.info(report.audit_notes)
        if velocity_sigma <= 0.0:
            logger.info("Velocity component is degenerate; the factor is citation-quality only.")
        if quality_sigma <= 0.0:
            logger.info("Citation component is degenerate; the factor is velocity only.")


#: Shorter alias. The long name is retained because SKILL.md and downstream
#: research code reference it.
PatentInnovationEngine = PatentFilingDataForInnovationSignalResearchEngine
