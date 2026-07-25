"""
counterparty-credit-risk-for-otc-derivatives: PFE, CVA, ISDA CSA collateral threshold,
and counterparty credit limit manager.
"""
from dataclasses import dataclass
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OTCTrade:
    trade_id: str
    counterparty_id: str
    mark_to_market_usd: float
    volatility_usd: float
    maturity_years: float


@dataclass
class CounterpartyProfile:
    counterparty_id: str
    rating: str
    probability_of_default: float  # e.g. 0.02 = 2%
    recovery_rate: float            # e.g. 0.40 = 40%
    credit_limit_usd: float
    csa_collateral_threshold_usd: float


@dataclass
class CounterpartyExposureReport:
    counterparty_id: str
    total_mtm_usd: float
    potential_future_exposure_pfe95_usd: float
    credit_valuation_adjustment_cva_usd: float
    credit_limit_usd: float
    credit_limit_utilization_pct: float
    collateral_margin_call_usd: float
    is_limit_breached: bool
    status_message: str


class CounterpartyCreditRiskManager:
    """
    Evaluates Potential Future Exposure (PFE), Credit Valuation Adjustment (CVA),
    and ISDA CSA collateral thresholds for bilateral OTC derivative portfolios.
    """

    def __init__(self):
        self._profiles: Dict[str, CounterpartyProfile] = {}

    def register_counterparty(self, profile: CounterpartyProfile) -> None:
        self._profiles[profile.counterparty_id] = profile

    def evaluate_counterparty_exposure(
        self,
        counterparty_id: str,
        trades: List[OTCTrade],
    ) -> CounterpartyExposureReport:
        profile = self._profiles.get(counterparty_id)
        if not profile:
            raise ValueError(f"Unregistered OTC counterparty '{counterparty_id}'.")

        # Bilateral netting: aggregate MTM and combined volatility
        tot_mtm = sum(t.mark_to_market_usd for t in trades)
        max_maturity = max((t.maturity_years for t in trades), default=1.0)
        tot_vol = math.sqrt(sum((t.volatility_usd ** 2) for t in trades)) if trades else 0.0

        # PFE at 95% confidence = MTM + 1.645 * Vol * sqrt(T)
        pfe_addon = 1.645 * tot_vol * math.sqrt(max_maturity)
        pfe_95 = max(0.0, tot_mtm + pfe_addon)

        # CVA = (1 - Recovery) * PFE * PD
        lgd = 1.0 - profile.recovery_rate
        cva = lgd * pfe_95 * profile.probability_of_default

        utilization_pct = (pfe_95 / max(1.0, profile.credit_limit_usd)) * 100.0
        is_breached = pfe_95 > profile.credit_limit_usd

        # Margin Call if PFE > CSA Threshold
        margin_call = max(0.0, pfe_95 - profile.csa_collateral_threshold_usd)

        if is_breached:
            msg = (
                f"OTC CREDIT LIMIT BREACH for '{counterparty_id}': PFE95 (${pfe_95:,.2f}) > "
                f"Limit (${profile.credit_limit_usd:,.2f}). Margin Call: ${margin_call:,.2f}."
            )
            logger.error(msg)
        elif margin_call > 0:
            msg = (
                f"ISDA CSA MARGIN CALL for '{counterparty_id}': PFE95 (${pfe_95:,.2f}) > "
                f"Threshold (${profile.csa_collateral_threshold_usd:,.2f}). Collateral Required: ${margin_call:,.2f}."
            )
            logger.warning(msg)
        else:
            msg = f"OTC Exposure OK for '{counterparty_id}': PFE95=${pfe_95:,.2f} ({utilization_pct:.1f}% limit)."

        return CounterpartyExposureReport(
            counterparty_id=counterparty_id,
            total_mtm_usd=round(tot_mtm, 2),
            potential_future_exposure_pfe95_usd=round(pfe_95, 2),
            credit_valuation_adjustment_cva_usd=round(cva, 2),
            credit_limit_usd=round(profile.credit_limit_usd, 2),
            credit_limit_utilization_pct=round(utilization_pct, 2),
            collateral_margin_call_usd=round(margin_call, 2),
            is_limit_breached=is_breached,
            status_message=msg,
        )
