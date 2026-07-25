"""
algorithmic-trading-firm-licensing-thresholds: 
Evaluates firm activity against SEC, MiFID II, and SEBI licensing thresholds.
"""
import dataclasses
import logging
from typing import Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class FirmTradingActivity:
    jurisdiction: str            # 'US', 'EU', 'IN'
    is_exchange_member: bool     # Is the firm a registered member of the national exchange?
    has_customers: bool          # Does the firm trade on behalf of clients?
    off_exchange_volume_usd: float # Volume traded in dark pools / ATS
    peak_orders_per_second: int  # Highest OPS observed over a rolling 1-second window
    

@dataclasses.dataclass
class LicensingComplianceReport:
    requires_registration: bool
    jurisdiction: str
    reason: Optional[str] = None


class LicensingThresholdEvaluator:
    """
    Evaluates trading activity against jurisdictional regulatory thresholds.
    """
    
    # SEBI (India) Retail Threshold. Exceeding this requires formal Algo Registration.
    SEBI_RETAIL_OPS_LIMIT = 10 
    
    # MiFID II (EU) HFT Designation Threshold (simplified benchmark for alerts)
    # Actually based on msgs/sec, but for this audit engine we use a high OPS threshold.
    MIFID_II_HFT_OPS_LIMIT = 50 

    def evaluate(self, activity: FirmTradingActivity) -> LicensingComplianceReport:
        jurisdiction = activity.jurisdiction.upper()
        
        # 1. Any firm with customers generally requires a broker-dealer/investment firm license globally.
        if activity.has_customers:
            return LicensingComplianceReport(
                requires_registration=True,
                jurisdiction=jurisdiction,
                reason="Firm trades on behalf of customers. Full broker-dealer/investment license required."
            )

        if jurisdiction == 'US':
            return self._evaluate_us(activity)
        elif jurisdiction == 'EU':
            return self._evaluate_eu(activity)
        elif jurisdiction == 'IN':
            return self._evaluate_in(activity)
        else:
            logger.warning(f"Unrecognized jurisdiction: {jurisdiction}. Defaulting to safe (registration required).")
            return LicensingComplianceReport(True, jurisdiction, "Unrecognized jurisdiction requires manual legal review.")

    def _evaluate_us(self, activity: FirmTradingActivity) -> LicensingComplianceReport:
        """
        US SEC Rule 15b9-1 Amendments (2023). 
        Proprietary traders must register with FINRA if they trade off-exchange.
        """
        if not activity.is_exchange_member:
            return LicensingComplianceReport(True, 'US', "Not an exchange member. Broker-dealer registration required.")
            
        if activity.off_exchange_volume_usd > 0:
            return LicensingComplianceReport(
                True, 'US', 
                "Off-exchange volume detected. Exceeds narrow SEC Rule 15b9-1 exemptions. FINRA registration required."
            )
            
        return LicensingComplianceReport(False, 'US', "Operates entirely on-exchange as a member. Exempt from FINRA under 15b9-1.")

    def _evaluate_eu(self, activity: FirmTradingActivity) -> LicensingComplianceReport:
        """
        MiFID II. High Frequency Trading designation.
        """
        if activity.peak_orders_per_second >= self.MIFID_II_HFT_OPS_LIMIT:
            return LicensingComplianceReport(
                True, 'EU', 
                f"Peak OPS ({activity.peak_orders_per_second}) exceeds HFT designation threshold. MiFID II Investment Firm license required."
            )
            
        return LicensingComplianceReport(False, 'EU', "Operating below HFT thresholds under ancillary activity exemption.")

    def _evaluate_in(self, activity: FirmTradingActivity) -> LicensingComplianceReport:
        """
        SEBI (India). Algorithmic trading OPS limits for non-registered entities.
        """
        if activity.peak_orders_per_second >= self.SEBI_RETAIL_OPS_LIMIT:
            return LicensingComplianceReport(
                True, 'IN', 
                f"Peak OPS ({activity.peak_orders_per_second}) exceeds retail limit ({self.SEBI_RETAIL_OPS_LIMIT}). SEBI Algo Registration required."
            )
            
        return LicensingComplianceReport(False, 'IN', "Operating within retail automated trading limits.")
