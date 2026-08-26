"""
job-posting-data-as-a-growth-signal: role-weighted corporate hiring expansion score
from web-scraped job posting snapshots.

What this engine measures
-------------------------
A single-firm, single-period **fundamental expansion** score: how much a company's
active requisition count grew quarter-over-quarter, tilted toward strategic
(Engineering/R&D, Sales) role mix, and shrunk toward neutral when the postings look
stale enough to be ghost listings.

What it does NOT measure
------------------------
An expected return. ``EXPANSION_BULLISH`` is a statement about the *firm's* trajectory,
not a forecast that the stock outperforms. The accounting-based hiring-rate factor runs
the other way in the cross section: Belo, Lin and Bazdresch (JPE 2014) find high hiring
rates predict *lower* subsequent returns. Treat the score as a fundamental input to be
combined with valuation and risk factors, never as a standalone trade trigger.

Design notes that matter when reading the output
------------------------------------------------
* **The score saturates.** ``corporate_growth_score`` is a hard clamp to [-1, +1], so any
  firm with QoQ growth above roughly 75% lands on exactly +1.0 and becomes
  indistinguishable from every other fast grower. For cross-sectional ranking use
  ``raw_growth_score`` (unclamped) or ``qoq_postings_growth_pct``; ``|raw_growth_score| > 1``
  means the reported score is saturated.
* **The stale haircut shrinks toward neutral in both directions.** Multiplying by
  ``(1 - stale_haircut_factor)`` weakens a bearish reading exactly as much as a bullish
  one (-0.54 becomes -0.27). That is the intended semantics -- stale postings make the
  count less informative, they do not make the firm more bearish -- but it means a
  contracting firm with ghost listings can be pushed into ``STABLE_NEUTRAL``.
* **``avg_posting_duration_days`` is a blunt ghost detector.** An average hides the tail:
  90% fresh postings plus 10% two-year-old ghosts averages ~100 days and never trips a
  120-day threshold. Where the vendor exposes a stale *share*, prefer it and set the
  threshold accordingly.
* **Long duration is not always ghosting.** Chen and Li (Review of Accounting Studies,
  2023) find longer vacancy duration for *high-skill* roles is associated with *higher*
  future profitability, while fast fills signal strength for low-skill roles. Applying a
  flat haircut to an engineering-heavy firm penalises the case the evidence says is
  benign. Tune ``stale_haircut_factor`` per role mix rather than accepting the default
  blindly.
* **Small bases explode.** 2 -> 10 postings is +400% growth and would saturate bullish.
  Snapshots whose previous count is below ``min_previous_postings`` are classified
  ``INSUFFICIENT_DATA`` with a zero score instead of producing a signal.

The thresholds here (120 days, 0.5 haircut, 0.25 classification band, 10-posting floor)
are this module's configurable defaults. They are not an industry or regulatory standard
and no external source prescribes them -- calibrate them on your own vendor panel.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Union

logger = logging.getLogger(__name__)

#: Signal classifications emitted by JobPostingSignalEngine.calculate_growth_score.
EXPANSION_BULLISH = "EXPANSION_BULLISH"
CONTRACTION_BEARISH = "CONTRACTION_BEARISH"
STABLE_NEUTRAL = "STABLE_NEUTRAL"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

Number = Union[int, float]


class JobPostingSignalError(ValueError):
    """Raised when a snapshot or engine configuration cannot support a valid signal."""


def _require_finite(name: str, value: Number) -> float:
    """Reject non-numeric, NaN and infinite inputs before they reach the arithmetic.

    NaN is the dangerous case: an unchecked NaN growth rate clamps to -1.0 and would be
    reported as a confident CONTRACTION_BEARISH, and ``nan > threshold`` is False so a
    NaN duration would silently escape the ghost-listing penalty.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JobPostingSignalError(f"{name} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise JobPostingSignalError(f"{name} must be finite, got {value!r}")
    return numeric


def _require_non_negative(name: str, value: Number) -> float:
    numeric = _require_finite(name, value)
    if numeric < 0.0:
        raise JobPostingSignalError(f"{name} must be >= 0, got {numeric}")
    return numeric


def _require_share(name: str, value: Number) -> float:
    """Validate a 0.0-1.0 share.

    Rejects rather than clamps: a caller passing 50 for 50% is a bug, and clamping it to
    1.0 would silently return a confident wrong weight.
    """
    numeric = _require_finite(name, value)
    if not 0.0 <= numeric <= 1.0:
        raise JobPostingSignalError(
            f"{name} must be a share in [0.0, 1.0] (0.5 == 50%), got {numeric}")
    return numeric


@dataclass
class CompanyJobPostingSnapshot:
    """One company, one observation period, as delivered by the posting vendor.

    Both counts must come from the same vendor panel under the same point-in-time
    convention; counts spliced across vendors, or across a panel-coverage change,
    measure the vendor rather than the company.
    """

    ticker: str                         # e.g. 'NVDA'
    company_name: str                   # e.g. 'NVIDIA Corporation'
    current_active_postings_count: int  # Current active requisitions (e.g. 300)
    previous_active_postings_count: int # Previous quarter active requisitions (e.g. 150)
    engineering_postings_pct: float     # Share of postings in R&D / Engineering / AI (0.0 to 1.0)
    sales_postings_pct: float           # Share of postings in Sales / BD (0.0 to 1.0)
    avg_posting_duration_days: float    # Average duration postings have been open (e.g. 35.0 days)

    def validate(self) -> None:
        """Raise JobPostingSignalError if this snapshot cannot produce a valid signal."""
        if not isinstance(self.ticker, str) or not self.ticker.strip():
            raise JobPostingSignalError("ticker must be a non-empty string")
        _require_non_negative(
            "current_active_postings_count", self.current_active_postings_count)
        _require_non_negative(
            "previous_active_postings_count", self.previous_active_postings_count)
        eng = _require_share("engineering_postings_pct", self.engineering_postings_pct)
        sales = _require_share("sales_postings_pct", self.sales_postings_pct)
        if eng + sales > 1.0 + 1e-9:
            # Both are shares of the same posting count, so they cannot overlap.
            raise JobPostingSignalError(
                f"engineering_postings_pct + sales_postings_pct must be <= 1.0, "
                f"got {eng} + {sales} = {eng + sales}")
        _require_non_negative("avg_posting_duration_days", self.avg_posting_duration_days)


@dataclass
class JobPostingSignalReport:
    ticker: str
    company_name: str
    qoq_postings_growth_pct: float
    role_weighting_factor: float
    has_ghost_postings_penalty: bool
    corporate_growth_score: float       # Normalized -1.0 to +1.0 (saturates at the bounds)
    signal_classification: str          # See the module-level classification constants
    audit_notes: str
    raw_growth_score: float = 0.0       # Unclamped; |raw| > 1 means the score above is saturated


class JobPostingSignalEngine:
    """
    Quantitative alternative data signal engine analyzing web-scraped corporate job postings,
    measuring QoQ hiring velocity, engineering/R&D role mix, ghost job penalties, and corporate expansion scores.

    All four constructor parameters are calibration choices, not sourced standards. See
    the module docstring for what the resulting score does and does not claim.
    """

    def __init__(
        self,
        ghost_job_stale_days_threshold: float = 120.0,
        stale_haircut_factor: float = 0.5,
        min_previous_postings: int = 10,
        classification_threshold: float = 0.25,
    ) -> None:
        self.ghost_job_stale_days_threshold = _require_non_negative(
            "ghost_job_stale_days_threshold", ghost_job_stale_days_threshold)
        self.stale_haircut_factor = _require_share(
            "stale_haircut_factor", stale_haircut_factor)
        self.min_previous_postings = int(
            _require_non_negative("min_previous_postings", min_previous_postings))
        self.classification_threshold = _require_finite(
            "classification_threshold", classification_threshold)
        if not 0.0 < self.classification_threshold <= 1.0:
            # The score is clamped to [-1, 1], so a threshold above 1.0 is dead
            # configuration: nothing could ever classify as bullish or bearish.
            raise JobPostingSignalError(
                f"classification_threshold must be in (0.0, 1.0], got {self.classification_threshold}")

    def calculate_growth_score(
        self, snapshot: CompanyJobPostingSnapshot
    ) -> JobPostingSignalReport:
        """
        Calculates role-weighted QoQ job postings growth rate and applies ghost job stale listing penalties.

        Raises:
            JobPostingSignalError: if the snapshot is malformed -- negative counts, NaN
                or infinite values, shares outside [0, 1], role shares summing above
                100%, or a blank ticker.
        """
        snapshot.validate()

        curr = float(snapshot.current_active_postings_count)
        prev = float(snapshot.previous_active_postings_count)

        # 1. QoQ Growth Rate. Undefined against a zero base -- reported as 0.0 and gated
        #    in step 4 rather than divided against a fabricated denominator.
        growth_pct = round(((curr - prev) / prev) * 100.0, 2) if prev > 0.0 else 0.0

        # 2. Role Weighting Factor (Higher R&D/Sales -> Higher expansion score)
        eng_pct = float(snapshot.engineering_postings_pct)
        sales_pct = float(snapshot.sales_postings_pct)
        role_factor = round(1.0 + (eng_pct * 0.5) + (sales_pct * 0.3), 3)

        # 3. Ghost Job Stale Listing Penalty (avg duration strictly above the threshold).
        #    Shrinks the score toward neutral in both directions: stale postings make the
        #    count less informative, they do not make the firm more bearish.
        has_ghost_penalty = (
            snapshot.avg_posting_duration_days > self.ghost_job_stale_days_threshold)
        stale_haircut = (1.0 - self.stale_haircut_factor) if has_ghost_penalty else 1.0

        # 4. Small-base gate. A 2 -> 10 posting move is +400% and would saturate bullish.
        #    A zero previous base is always gated, whatever the floor is set to: growth
        #    against it is undefined, and reporting the 0.0 placeholder as a real 0%
        #    would call a 0 -> 300 expansion "flat".
        insufficient_base = prev <= 0.0 or prev < self.min_previous_postings

        # 5. Normalized Growth Score Calculation (-1.0 to +1.0)
        raw_score = 0.0 if insufficient_base else (growth_pct / 100.0) * role_factor * stale_haircut
        raw_score = round(raw_score, 4)
        norm_score = round(min(1.0, max(-1.0, raw_score)), 4)

        # Signal Classification
        if insufficient_base:
            sig = INSUFFICIENT_DATA
        elif norm_score >= self.classification_threshold:
            sig = EXPANSION_BULLISH
        elif norm_score <= -self.classification_threshold:
            sig = CONTRACTION_BEARISH
        else:
            sig = STABLE_NEUTRAL

        ghost_str = (
            f" ({self.stale_haircut_factor * 100:.0f}% Ghost Haircut Applied, shrinks toward neutral)"
            if has_ghost_penalty else "")
        if not insufficient_base:
            base_str = ""
        elif prev <= 0.0:
            base_str = " Previous base is zero: growth undefined, score suppressed."
        else:
            base_str = (f" Previous base {prev:,.0f} below the "
                        f"{self.min_previous_postings}-posting floor: score suppressed.")
        # Never print "+0.0%" for a zero base -- 0 -> 300 postings is undefined, not flat.
        growth_str = "undefined" if prev <= 0.0 else f"{growth_pct:+.1f}%"
        saturation_str = " [SATURATED at clamp bound]" if abs(raw_score) > 1.0 else ""
        notes = (
            f"JOB POSTING SIGNAL [{snapshot.ticker} - {snapshot.company_name}]: "
            f"QoQ Growth = {growth_str} ({curr:,.0f} vs {prev:,.0f} active listings). "
            f"Role Factor = {role_factor:.2f} (Eng: {eng_pct*100:.0f}%, Sales: {sales_pct*100:.0f}%). "
            f"Growth Score = {norm_score:+.2f}{saturation_str}{ghost_str} -> {sig}.{base_str}"
        )
        logger.info(notes)

        return JobPostingSignalReport(
            ticker=snapshot.ticker,
            company_name=snapshot.company_name,
            qoq_postings_growth_pct=growth_pct,
            role_weighting_factor=role_factor,
            has_ghost_postings_penalty=has_ghost_penalty,
            corporate_growth_score=norm_score,
            signal_classification=sig,
            audit_notes=notes,
            raw_growth_score=raw_score,
        )
