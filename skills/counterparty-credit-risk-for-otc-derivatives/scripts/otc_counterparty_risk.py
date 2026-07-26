import logging
from dataclasses import dataclass
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

@dataclass
class OtcContract:
    contract_id: str
    asset_class: str                   # 'EQUITY', 'FX', 'RATES', 'COMMODITY', 'CRYPTO'
    notional_usd: float
    mtm_value_usd: float
    sa_ccr_add_on_factor: float        # e.g. 0.06 (6% for equity swaps), 0.04 (FX)

@dataclass
class CsaTerms:
    netting_set_id: str
    threshold_usd: float               # Uncollateralized threshold (e.g. $100,000)
    minimum_transfer_amount: float     # MTA (e.g. $50,000)
    posted_collateral_usd: float
    counterparty_pd: float            # Annual Probability of Default (e.g. 0.02 = 2%)
    recovery_rate: float               # e.g. 0.40 (40%)

@dataclass
class OtcRiskReport:
    netting_set_id: str
    gross_mtm_usd: float
    net_mtm_usd: float
    netted_current_exposure_usd: float
    potential_future_exposure_usd: float
    exposure_at_default_usd: float
    cva_usd: float
    is_margin_call_triggered: bool
    margin_call_amount_usd: float
    is_credit_limit_breached: bool

class OtcCounterpartyRiskEngine:
    """
    Evaluates Counterparty Credit Risk (CCR) for bilateral OTC derivatives,
    calculating Netted Current Exposure, SA-CCR PFE, CVA, and CSA Margin Calls.
    """
    def __init__(self, max_ead_limit_usd: float = 1_000_000.0):
        self.max_ead_limit_usd = max_ead_limit_usd

    def calculate_current_exposure(self, contracts: List[OtcContract], csa: CsaTerms) -> float:
        """
        Calculates Netted Current Exposure: CE = max(0, sum(MTM) - Collateral - Threshold)
        """
        net_mtm = sum(c.mtm_value_usd for c in contracts)
        uncollateralized = net_mtm - csa.posted_collateral_usd - csa.threshold_usd
        return max(0.0, float(uncollateralized))

    def calculate_pfe(self, contracts: List[OtcContract]) -> float:
        """
        Calculates SA-CCR Potential Future Exposure (PFE) Add-on across contracts.
        PFE = sum(Notional * AddOnFactor)
        """
        pfe = sum(c.notional_usd * c.sa_ccr_add_on_factor for c in contracts)
        return float(pfe)

    def calculate_cva(self, ead: float, pd: float, recovery_rate: float) -> float:
        """
        Calculates Credit Valuation Adjustment (CVA): CVA = (1 - R) * EAD * PD
        """
        loss_given_default = 1.0 - recovery_rate
        return float(loss_given_default * ead * pd)

    def analyze_netting_set(
        self,
        contracts: List[OtcContract],
        csa: CsaTerms
    ) -> OtcRiskReport:
        """
        Audits an entire ISDA netting set and returns an OtcRiskReport.
        """
        gross_mtm = sum(abs(c.mtm_value_usd) for c in contracts)
        net_mtm = sum(c.mtm_value_usd for c in contracts)

        ce_net = self.calculate_current_exposure(contracts, csa)
        pfe = self.calculate_pfe(contracts)
        ead = ce_net + pfe

        cva = self.calculate_cva(ead, csa.counterparty_pd, csa.recovery_rate)

        # CSA Margin Call Audit: Uncollateralized exposure > Threshold + MTA
        uncollateralized_raw = net_mtm - csa.posted_collateral_usd
        margin_call_amount = max(0.0, uncollateralized_raw - csa.threshold_usd)
        
        is_margin_call = margin_call_amount >= csa.minimum_transfer_amount
        is_limit_breached = ead > self.max_ead_limit_usd

        if is_limit_breached:
            logger.error(
                f"OTC Credit Limit Breached for {csa.netting_set_id}: EAD ${ead:,.2f} > Limit ${self.max_ead_limit_usd:,.2f}"
            )

        return OtcRiskReport(
            netting_set_id=csa.netting_set_id,
            gross_mtm_usd=round(gross_mtm, 2),
            net_mtm_usd=round(net_mtm, 2),
            netted_current_exposure_usd=round(ce_net, 2),
            potential_future_exposure_usd=round(pfe, 2),
            exposure_at_default_usd=round(ead, 2),
            cva_usd=round(cva, 2),
            is_margin_call_triggered=is_margin_call,
            margin_call_amount_usd=round(margin_call_amount if is_margin_call else 0.0, 2),
            is_credit_limit_breached=is_limit_breached
        )
