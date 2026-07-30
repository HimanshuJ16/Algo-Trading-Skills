import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class DarkTradingVolumeMetrics:
    isin: str
    symbol: str
    rolling_12m_total_eu_market_volume_eur: float
    rolling_12m_venue_dark_volume_eur: float
    rolling_12m_eu_wide_dark_volume_eur: float
    venue_id: str = "CBOE_DARK_EU"
    lis_threshold_eur: float = 100_000.0   # Large-in-scale minimum threshold

@dataclass
class SorOrderRouteRequest:
    order_id: str
    isin: str
    symbol: str
    order_val_eur: float
    intended_waiver_type: str              # 'RPW' (Reference Price Waiver), 'NTW', 'LIS'

@dataclass
class EsmaDvcAuditReport:
    order_id: str
    isin: str
    symbol: str
    venue_dark_share_pct: float
    eu_wide_dark_share_pct: float
    dvc_suspension_status: str             # 'ACTIVE_NO_SUSPENSION', 'SUSPENDED_4PCT_VENUE', 'SUSPENDED_8PCT_EU_WIDE'
    is_dark_routing_allowed: bool
    final_routed_venue_type: str           # 'DARK_RPW', 'DARK_LIS_EXEMPT', 'LIT_VENUE'
    audit_notes: str

class EsmaDoubleVolumeCapEngine:
    """
    Quantitative European regulatory compliance engine for monitoring ESMA Double Volume Cap (DVC) dark pool limits
    (4% venue / 8% EU-wide), managing dark trading suspensions, and rerouting orders to Lit or Large-In-Scale (LIS) waivers.
    """
    def __init__(self, single_venue_cap_pct: float = 4.0, eu_wide_cap_pct: float = 8.0):
        self.single_venue_cap_pct = single_venue_cap_pct
        self.eu_wide_cap_pct = eu_wide_cap_pct

    def audit_dvc_and_route_order(
        self,
        req: SorOrderRouteRequest,
        metrics: DarkTradingVolumeMetrics
    ) -> EsmaDvcAuditReport:
        """
        Audits ESMA 4% venue and 8% EU-wide DVC dark volume limits, enforcing Smart Order Router (SOR) rerouting.
        """
        if metrics.rolling_12m_total_eu_market_volume_eur <= 0:
            raise ValueError("Total EU market volume must be > 0.")

        venue_share = round((metrics.rolling_12m_venue_dark_volume_eur / metrics.rolling_12m_total_eu_market_volume_eur) * 100.0, 2)
        eu_share = round((metrics.rolling_12m_eu_wide_dark_volume_eur / metrics.rolling_12m_total_eu_market_volume_eur) * 100.0, 2)

        # Audit DVC Suspension Status
        suspension_status = "ACTIVE_NO_SUSPENSION"
        if eu_share >= self.eu_wide_cap_pct:
            suspension_status = "SUSPENDED_8PCT_EU_WIDE"
        elif venue_share >= self.single_venue_cap_pct:
            suspension_status = "SUSPENDED_4PCT_VENUE"

        is_suspended = (suspension_status != "ACTIVE_NO_SUSPENSION")

        # SOR Decision Logic
        is_dark_allowed = True
        routed_type = "DARK_RPW"
        notes = ""

        # Case 1: LIS Waiver Order (Exempt from DVC!)
        if req.intended_waiver_type.upper() == "LIS" or req.order_val_eur >= metrics.lis_threshold_eur:
            is_dark_allowed = True
            routed_type = "DARK_LIS_EXEMPT"
            notes = f"LIS EXEMPTION [{req.symbol}]: Order value €{req.order_val_eur:,.2f} >= LIS threshold €{metrics.lis_threshold_eur:,.2f}. Dark routing permitted."
            logger.info(notes)

        # Case 2: Standard RPW/NTW Dark Order during DVC Suspension
        elif is_suspended:
            is_dark_allowed = False
            routed_type = "LIT_VENUE"
            notes = (
                f"ESMA DVC SUSPENSION [{req.symbol}]: Stock suspended under {suspension_status} "
                f"(Venue: {venue_share}%, EU: {eu_share}%). Dark waiver blocked. Rerouted to LIT_VENUE."
            )
            logger.warning(notes)

        # Case 3: Active Dark Order
        else:
            is_dark_allowed = True
            routed_type = "DARK_RPW"
            notes = f"DARK ROUTE APPROVED [{req.symbol}]: DVC active (Venue: {venue_share}%, EU: {eu_share}%). Routed to DARK_RPW."

        return EsmaDvcAuditReport(
            order_id=req.order_id,
            isin=req.isin,
            symbol=req.symbol,
            venue_dark_share_pct=venue_share,
            eu_wide_dark_share_pct=eu_share,
            dvc_suspension_status=suspension_status,
            is_dark_routing_allowed=is_dark_allowed,
            final_routed_venue_type=routed_type,
            audit_notes=notes
        )
