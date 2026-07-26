import math
import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

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

class CrossMarginingCalculator:
    """
    Quantitative treasury engine for calculating cross-margining offsets across clearing houses
    (CME-OCC, CME-FICC), reducing initial margin, and quantifying capital efficiency.
    """
    def __init__(self, minimum_floor_pct: float = 0.20):
        self.minimum_floor_pct = minimum_floor_pct # 20% risk floor
        self.correlation_offsets: Dict[Tuple[str, str], float] = {}

    def register_correlation_offset(self, asset_class_1: str, asset_class_2: str, correlation: float):
        key1 = (asset_class_1.upper(), asset_class_2.upper())
        key2 = (asset_class_2.upper(), asset_class_1.upper())
        self.correlation_offsets[key1] = correlation
        self.correlation_offsets[key2] = correlation

    def compute_standalone_margin(self, components: List[AssetClassMarginComponent]) -> float:
        return sum(c.standalone_margin_usd for c in components)

    def calculate_cross_margin(self, components: List[AssetClassMarginComponent]) -> CrossMarginAuditReport:
        """
        Calculates cross-margined portfolio requirement: M_cross = sqrt(sum(M_i^2) + 2*sum(rho_ij * M_i * M_j))
        enforcing minimum clearing risk floor.
        """
        if not components:
            return CrossMarginAuditReport(0.0, 0.0, 0.0, 0.0, False)

        standalone_sum = self.compute_standalone_margin(components)
        if standalone_sum <= 0:
            return CrossMarginAuditReport(0.0, 0.0, 0.0, 0.0, False)

        # 1. Sum of squared margins
        variance_sum = sum(c.standalone_margin_usd ** 2 for c in components)

        # 2. Cross covariance terms
        covariance_sum = 0.0
        n = len(components)
        for i in range(n):
            for j in range(i + 1, n):
                a1 = components[i].asset_class_id.upper()
                a2 = components[j].asset_class_id.upper()
                rho = self.correlation_offsets.get((a1, a2), 0.0)
                
                cov = rho * components[i].standalone_margin_usd * components[j].standalone_margin_usd
                covariance_sum += cov

        raw_cross_variance = max(0.0, variance_sum + 2.0 * covariance_sum)
        raw_cross_margin = math.sqrt(raw_cross_variance)

        # 3. Apply Minimum Risk Floor
        risk_floor = standalone_sum * self.minimum_floor_pct
        is_floor_applied = raw_cross_margin < risk_floor
        final_cross_margin = max(raw_cross_margin, risk_floor)

        margin_savings = standalone_sum - final_cross_margin
        efficiency_gain = (margin_savings / standalone_sum) * 100.0

        logger.info(
            f"Cross-Margining Audit: Standalone=${standalone_sum:,.2f} -> Cross-Margined=${final_cross_margin:,.2f} "
            f"(Savings=${margin_savings:,.2f}, Efficiency=+{efficiency_gain:.1f}%)"
        )

        return CrossMarginAuditReport(
            total_standalone_margin_usd=round(standalone_sum, 2),
            total_cross_margined_requirement_usd=round(final_cross_margin, 2),
            margin_savings_usd=round(margin_savings, 2),
            capital_efficiency_gain_pct=round(efficiency_gain, 2),
            is_floor_applied=is_floor_applied
        )
