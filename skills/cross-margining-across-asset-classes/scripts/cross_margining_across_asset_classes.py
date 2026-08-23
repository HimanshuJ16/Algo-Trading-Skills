"""
cross-margining-across-asset-classes: pre-trade *estimator* for the initial-margin
offset a multi-asset portfolio may obtain under a cross-margining arrangement
(e.g. CME-OCC, CME-FICC/GSD), plus the resulting capital-efficiency figures.

What this module is
-------------------
An ISDA SIMM-style correlation aggregation of *already-known standalone* margin
numbers:

    M_cross = sqrt( sum_i M_i^2 + 2 * sum_{i<j} rho_ij * M_i * M_j )

This is the same aggregation shape ISDA SIMM uses to combine risk-class margins
(``sqrt(sum_r IM_r^2 + sum_{r != s} psi_rs IM_r IM_s)``), applied here across
asset classes / clearing houses.

What this module is NOT
-----------------------
It is NOT the clearing house's margin calculation and will not reproduce it.
CME SPAN 2 computes margin from historical-VaR plus stress scenario revaluation
(thousands of scenarios); OCC STANS uses full-portfolio Monte Carlo at a 99%
Expected Shortfall measure. Neither aggregates standalone margins by a pairwise
correlation. Treat the output as a treasury planning / capital-budgeting
estimate and reconcile it against the CCP's or clearing broker's published
number before committing collateral.

Fail-closed design
------------------
An asset-class pair with no explicitly registered offset receives
``default_correlation`` (1.0 by default), i.e. **no offset at all**, so the
estimate degrades toward the standalone sum rather than silently inventing a
diversification benefit for a pair that has no cross-margin agreement behind
it. Lower it only deliberately.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CrossMarginInputError(ValueError):
    """Raised when margin components or correlation offsets are malformed:
    non-finite or negative margins, duplicate asset-class identifiers, or a
    correlation outside [-1, 1]. Bad input is rejected rather than silently
    coerced into an understated margin estimate."""


class InconsistentCorrelationError(ValueError):
    """Raised when the supplied pairwise offsets imply a negative portfolio
    variance, i.e. they do not form a positive semi-definite correlation
    structure for these margin weights.

    This matters: with three or more asset classes, individually plausible
    pairwise offsets (say rho = -0.9 between all three pairs) can be jointly
    impossible. Clamping that negative quantity to zero would report a
    near-zero margin requirement no clearing house would ever grant, so the
    estimator refuses instead."""


@dataclass
class AssetClassMarginComponent:
    asset_class_id: str                # e.g. 'EQUITY_FUTURES', 'INDEX_OPTIONS', 'TREASURY_FUTURES'
    clearing_house: str                # e.g. 'CME', 'OCC', 'FICC'
    standalone_margin_usd: float


@dataclass
class CrossMarginAuditReport:
    total_standalone_margin_usd: float
    total_cross_margined_requirement_usd: float
    margin_savings_usd: float
    capital_efficiency_gain_pct: float
    is_floor_applied: bool
    # Audit trail: which offsets actually drove the saving, which pairs got no
    # offset because no arrangement was registered, and the floor in force.
    minimum_floor_pct: float = 0.0
    applied_offsets: List[Tuple[str, str, float]] = field(default_factory=list)
    unregistered_pairs: List[Tuple[str, str]] = field(default_factory=list)


class CrossMarginingCalculator:
    """
    Treasury-side estimator for cross-margining offsets across clearing houses
    (CME-OCC, CME-FICC), and the capital efficiency they imply.

    Parameters
    ----------
    minimum_floor_pct:
        Fraction of the standalone margin sum below which the estimate will not
        be reported. This is an **internal prudential parameter, not a clearing
        house or regulatory rule** -- no CCP publishes a
        percentage-of-standalone floor. Real floors are structured differently
        (e.g. FINRA Rule 4210(g)'s risk-based minimum of $0.375 per unit of
        deliverable per short option contract, or SPAN's short option minimum).
        Set it to 0.0 to disable, and calibrate it to your own model-risk
        appetite rather than treating 0.20 as authoritative.
    default_correlation:
        Correlation applied to any asset-class pair with no registered offset.
        Defaults to 1.0 (no offset -- fail closed), because an unregistered
        pair is normally a pair with no cross-margin arrangement covering it.
    """

    def __init__(
        self,
        minimum_floor_pct: float = 0.20,
        default_correlation: float = 1.0,
    ) -> None:
        if (
            not isinstance(minimum_floor_pct, (int, float))
            or isinstance(minimum_floor_pct, bool)
            or not math.isfinite(minimum_floor_pct)
            or not (0.0 <= minimum_floor_pct <= 1.0)
        ):
            raise CrossMarginInputError(
                f"minimum_floor_pct must be a finite value in [0, 1], got {minimum_floor_pct!r}"
            )
        self._validate_correlation(default_correlation, "default_correlation")
        self.minimum_floor_pct = float(minimum_floor_pct)
        self.default_correlation = float(default_correlation)
        self.correlation_offsets: Dict[Tuple[str, str], float] = {}
        self.offset_programs: Dict[Tuple[str, str], str] = {}

    @staticmethod
    def _validate_correlation(correlation: float, label: str) -> None:
        if not isinstance(correlation, (int, float)) or isinstance(correlation, bool):
            raise CrossMarginInputError(f"{label} must be a number, got {correlation!r}")
        if not math.isfinite(correlation) or not (-1.0 <= correlation <= 1.0):
            raise CrossMarginInputError(
                f"{label} must be a finite value in [-1, 1], got {correlation!r}"
            )

    def register_correlation_offset(
        self,
        asset_class_1: str,
        asset_class_2: str,
        correlation: float,
        program: Optional[str] = None,
    ) -> None:
        """Register a symmetric offset for one asset-class pair.

        ``program`` records the arrangement the offset is granted under (e.g.
        ``"CME-OCC"``, ``"CME-FICC/GSD"``) so an auditor can see that each
        offset traces to a real agreement rather than to a modelling
        assumption.
        """
        a1 = asset_class_1.strip().upper()
        a2 = asset_class_2.strip().upper()
        if not a1 or not a2:
            raise CrossMarginInputError("asset class identifiers must be non-empty")
        if a1 == a2:
            raise CrossMarginInputError(
                f"cannot register a cross-asset offset of an asset class against itself ({a1!r})"
            )
        self._validate_correlation(correlation, "correlation")

        for key in ((a1, a2), (a2, a1)):
            self.correlation_offsets[key] = float(correlation)
            if program:
                self.offset_programs[key] = program

    def _validate_components(self, components: List[AssetClassMarginComponent]) -> None:
        seen = set()
        for c in components:
            asset_id = c.asset_class_id.strip().upper()
            if not asset_id:
                raise CrossMarginInputError("asset_class_id must be non-empty")
            if asset_id in seen:
                # Two rows sharing an id would be looked up as a self-pair, take
                # the default correlation, and quietly misprice the portfolio.
                raise CrossMarginInputError(
                    f"duplicate asset_class_id {asset_id!r}: aggregate the standalone "
                    "margin for an asset class into a single component"
                )
            seen.add(asset_id)

            margin = c.standalone_margin_usd
            if not isinstance(margin, (int, float)) or isinstance(margin, bool):
                raise CrossMarginInputError(
                    f"{asset_id}: standalone_margin_usd must be a number, got {margin!r}"
                )
            if not math.isfinite(margin):
                raise CrossMarginInputError(
                    f"{asset_id}: standalone_margin_usd must be finite, got {margin!r}"
                )
            if margin < 0:
                raise CrossMarginInputError(
                    f"{asset_id}: standalone_margin_usd must be non-negative, got {margin!r}"
                )

    def compute_standalone_margin(
        self, components: List[AssetClassMarginComponent]
    ) -> float:
        return sum(c.standalone_margin_usd for c in components)

    def calculate_cross_margin(
        self, components: List[AssetClassMarginComponent]
    ) -> CrossMarginAuditReport:
        """
        Estimate the cross-margined portfolio requirement:

            M_cross = sqrt(sum(M_i^2) + 2 * sum_{i<j} rho_ij * M_i * M_j)

        floored at ``minimum_floor_pct`` of the standalone sum.

        Raises
        ------
        CrossMarginInputError
            On malformed components (non-finite, negative, or duplicated).
        InconsistentCorrelationError
            When the offsets imply a negative portfolio variance.
        """
        if not components:
            logger.warning(
                "Cross-margin requested with no margin components; returning zero report."
            )
            return CrossMarginAuditReport(0.0, 0.0, 0.0, 0.0, False, self.minimum_floor_pct)

        self._validate_components(components)

        standalone_sum = self.compute_standalone_margin(components)
        if standalone_sum <= 0:
            return CrossMarginAuditReport(0.0, 0.0, 0.0, 0.0, False, self.minimum_floor_pct)

        # 1. Sum of squared margins
        variance_sum = sum(c.standalone_margin_usd ** 2 for c in components)

        # 2. Cross covariance terms
        covariance_sum = 0.0
        applied_offsets: List[Tuple[str, str, float]] = []
        unregistered_pairs: List[Tuple[str, str]] = []
        n = len(components)
        for i in range(n):
            for j in range(i + 1, n):
                a1 = components[i].asset_class_id.strip().upper()
                a2 = components[j].asset_class_id.strip().upper()
                key = (a1, a2)
                if key in self.correlation_offsets:
                    rho = self.correlation_offsets[key]
                    applied_offsets.append((a1, a2, rho))
                else:
                    # Fail closed: no registered arrangement means no offset.
                    rho = self.default_correlation
                    unregistered_pairs.append(key)
                    logger.warning(
                        "No registered cross-margin offset for (%s, %s); applying "
                        "default_correlation=%s (no offset credit).", a1, a2, rho
                    )

                covariance_sum += (
                    rho
                    * components[i].standalone_margin_usd
                    * components[j].standalone_margin_usd
                )

        raw_cross_variance = variance_sum + 2.0 * covariance_sum

        # A materially negative quadratic form means the offsets are jointly
        # impossible. Clamping it to zero would manufacture a margin saving.
        tolerance = 1e-9 * variance_sum
        if raw_cross_variance < -tolerance:
            raise InconsistentCorrelationError(
                "supplied correlation offsets imply a negative portfolio variance "
                f"({raw_cross_variance:.6g}); the pairwise offsets are not jointly "
                "consistent (non positive semi-definite) for these margin weights"
            )
        raw_cross_margin = math.sqrt(max(0.0, raw_cross_variance))

        # 3. Apply the internal prudential floor (not a clearing house rule).
        risk_floor = standalone_sum * self.minimum_floor_pct
        is_floor_applied = raw_cross_margin < risk_floor
        final_cross_margin = max(raw_cross_margin, risk_floor)

        margin_savings = standalone_sum - final_cross_margin
        efficiency_gain = (margin_savings / standalone_sum) * 100.0

        logger.info(
            "Cross-Margining Audit (ESTIMATE, reconcile with CCP): Standalone=$%s -> "
            "Cross-Margined=$%s (Savings=$%s, Efficiency=+%.1f%%, floor_applied=%s, "
            "unregistered_pairs=%d)",
            f"{standalone_sum:,.2f}",
            f"{final_cross_margin:,.2f}",
            f"{margin_savings:,.2f}",
            efficiency_gain,
            is_floor_applied,
            len(unregistered_pairs),
        )

        return CrossMarginAuditReport(
            total_standalone_margin_usd=round(standalone_sum, 2),
            total_cross_margined_requirement_usd=round(final_cross_margin, 2),
            margin_savings_usd=round(margin_savings, 2),
            capital_efficiency_gain_pct=round(efficiency_gain, 2),
            is_floor_applied=is_floor_applied,
            minimum_floor_pct=self.minimum_floor_pct,
            applied_offsets=applied_offsets,
            unregistered_pairs=unregistered_pairs,
        )
