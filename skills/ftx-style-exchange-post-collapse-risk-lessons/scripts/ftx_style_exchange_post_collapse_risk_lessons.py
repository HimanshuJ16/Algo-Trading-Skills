import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ExchangeSolvencyMetrics:
    venue_id: str
    exchange_name: str
    proof_of_reserves_ratio: float       # On-chain assets / client liabilities (e.g. 1.05 = 105%)
    native_token_collateral_pct: float  # % of total margin in native token (e.g. FTT)
    uses_off_exchange_settlement: bool  # True if using Copper ClearLoop / Fireblocks OES
    nav_exposure_pct: float             # % of total fund NAV deployed at venue
    has_independent_audit_report: bool

@dataclass
class ExchangePostCollapseRiskReport:
    venue_id: str
    exchange_name: str
    risk_score_0_to_100: float
    is_por_valid: bool
    is_native_token_safe: bool
    is_nav_exposure_safe: bool
    is_derisking_triggered: bool
    recommended_capital_withdrawal_pct: float
    status: str                         # 'VENUE_RISK_ACCEPTABLE', 'EXCHANGE_DERISK_TRIGGERED'
    audit_notes: str

class ExchangePostCollapseRiskEngine:
    """
    Crypto counterparty risk engine implementing post-FTX collapse risk frameworks,
    auditing Proof of Reserves (PoR), native token collateral concentration, off-exchange settlement (OES),
    and automated venue de-risking.
    """
    def __init__(
        self,
        min_por_coverage_ratio: float = 1.00,       # 100% PoR coverage
        max_native_token_ratio: float = 0.05,       # Max 5% native token collateral
        max_single_venue_nav_pct: float = 0.20       # Max 20% NAV per exchange
    ):
        self.min_por_coverage_ratio = min_por_coverage_ratio
        self.max_native_token_ratio = max_native_token_ratio
        self.max_single_venue_nav_pct = max_single_venue_nav_pct

    def audit_exchange_counterparty_risk(self, metrics: ExchangeSolvencyMetrics) -> ExchangePostCollapseRiskReport:
        """
        Audits crypto exchange counterparty solvency and governance risk post-FTX standards.
        """
        risk_penalties = 0.0
        violations: List[str] = []

        # 1. Proof of Reserves Coverage Audit
        is_por_valid = (metrics.proof_of_reserves_ratio >= self.min_por_coverage_ratio)
        if not is_por_valid:
            shortfall = round((self.min_por_coverage_ratio - metrics.proof_of_reserves_ratio) * 100.0, 1)
            risk_penalties += 40.0
            violations.append(f"PoR coverage shortfall {metrics.proof_of_reserves_ratio*100:.1f}% < 100.0%")

        # 2. Native Token Collateral Exposure Audit
        is_native_token_safe = (metrics.native_token_collateral_pct <= self.max_native_token_ratio)
        if not is_native_token_safe:
            risk_penalties += 30.0
            violations.append(f"Native token collateral {metrics.native_token_collateral_pct*100:.1f}% exceeds max {self.max_native_token_ratio*100:.1f}%")

        # 3. Off-Exchange Settlement (OES) Custody Benefit
        if not metrics.uses_off_exchange_settlement:
            risk_penalties += 15.0

        # 4. NAV Concentration Audit
        is_nav_safe = (metrics.nav_exposure_pct <= self.max_single_venue_nav_pct)
        if not is_nav_safe:
            risk_penalties += 15.0
            violations.append(f"NAV exposure {metrics.nav_exposure_pct*100:.1f}% exceeds max cap {self.max_single_venue_nav_pct*100:.1f}%")

        # 5. Independent Audit Report Benefit
        if not metrics.has_independent_audit_report:
            risk_penalties += 10.0

        risk_score = round(min(100.0, risk_penalties), 1)

        is_derisk_triggered = (risk_score >= 40.0) or (not is_por_valid) or (metrics.native_token_collateral_pct > 0.10)

        withdrawal_pct = 0.0
        if is_derisk_triggered:
            status = "EXCHANGE_DERISK_TRIGGERED"
            # Recommend withdrawing excess NAV down to 0% or 5%
            withdrawal_pct = 100.0 if not is_por_valid else round(min(100.0, ((metrics.nav_exposure_pct - 0.05) / metrics.nav_exposure_pct) * 100.0), 1)
            notes = (
                f"POST-FTX COUNTERPARTY RISK ALERT [{metrics.exchange_name}]: Risk Score = {risk_score:.1f}/100. "
                f"Violations: {'; '.join(violations)}. AUTOMATED DE-RISKING TRIGGERED! Recommend withdrawing {withdrawal_pct:.1f}% of funds to cold custody."
            )
            logger.critical(notes)
        else:
            status = "VENUE_RISK_ACCEPTABLE"
            notes = (
                f"EXCHANGE RISK ACCEPTABLE [{metrics.exchange_name}]: Risk Score = {risk_score:.1f}/100. "
                f"PoR coverage = {metrics.proof_of_reserves_ratio*100:.1f}%, Native Token = {metrics.native_token_collateral_pct*100:.1f}%. Venue cleared for trading."
            )
            logger.info(notes)

        return ExchangePostCollapseRiskReport(
            venue_id=metrics.venue_id,
            exchange_name=metrics.exchange_name,
            risk_score_0_to_100=risk_score,
            is_por_valid=is_por_valid,
            is_native_token_safe=is_native_token_safe,
            is_nav_exposure_safe=is_nav_safe,
            is_derisking_triggered=is_derisk_triggered,
            recommended_capital_withdrawal_pct=withdrawal_pct,
            status=status,
            audit_notes=notes
        )
