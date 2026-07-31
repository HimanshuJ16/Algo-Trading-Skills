import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class IrsPositionSpec:
    swap_id: str                        # e.g. 'IRS_SOFR_5Y_001'
    notional_usd: float                 # e.g. $10,000,000
    pay_receive_type: str               # 'PAY_FIXED' (Pay Fixed, Rec Float) or 'RECEIVE_FIXED' (Rec Fixed, Pay Float)
    fixed_rate_pct: float               # e.g. 4.25%
    tenor_years: float                  # e.g. 5.0 years
    floating_rate_index: str = "SOFR"   # 'SOFR', 'EURIBOR', 'SONIA'

@dataclass
class MultiAssetPortfolioRisk:
    bonds_dv01_usd: float               # Bond portfolio DV01 (e.g. -$5,000 / bps)
    equities_notional_usd: float        # Equity holdings notional
    irs_positions: List[IrsPositionSpec]

@dataclass
class InterestRateSwapExposureReport:
    total_irs_notional_usd: float
    total_irs_dv01_usd: float           # USD gain/loss per +1 bps parallel shift
    bonds_dv01_usd: float
    net_portfolio_dv01_usd: float
    pnl_impact_plus_10bps_usd: float    # Expected USD PnL for +10 bps rate rise
    required_hedge_irs_notional_usd: float # Required Pay-Fixed Notional to achieve DV01 Neutrality (0.0 Net DV01)
    status: str                         # 'IRS_RISK_AUDIT_SUCCESS'
    audit_notes: str

class InterestRateSwapExposureEngine:
    """
    Fixed income risk analytics engine for Interest Rate Swaps (IRS),
    calculating Swap DV01/PV01 sensitivities, Pay-Fixed vs Receive-Fixed exposures, and multi-asset portfolio yield curve hedging.
    """
    def __init__(self):
        pass

    def calculate_swap_dv01(self, spec: IrsPositionSpec) -> float:
        """
        Calculates Swap DV01 (Dollar Value of a 1 bps parallel yield curve shift).
        - Pay-Fixed Swap: Short duration / Gains when rates rise -> DV01 > 0
        - Receive-Fixed Swap: Long duration / Gains when rates fall -> DV01 < 0
        """
        type_clean = spec.pay_receive_type.strip().upper()
        if type_clean not in {"PAY_FIXED", "RECEIVE_FIXED"}:
            raise ValueError(f"Invalid swap type '{spec.pay_receive_type}'. Must be 'PAY_FIXED' or 'RECEIVE_FIXED'.")

        # Modified Duration approximation for par swap: D_mod approx Tenor / 2.0
        mod_duration = max(0.5, spec.tenor_years / 2.0)

        # Base DV01 = Notional * Modified Duration * 0.0001
        base_dv01 = spec.notional_usd * mod_duration * 0.0001

        # Directional Sign:
        # Pay-Fixed gains when rates rise -> +DV01
        # Receive-Fixed loses when rates rise -> -DV01
        return base_dv01 if type_clean == "PAY_FIXED" else -base_dv01

    def analyze_portfolio_irs_exposure(
        self,
        portfolio: MultiAssetPortfolioRisk
    ) -> InterestRateSwapExposureReport:
        """
        Aggregates DV01 across bonds and IRS swap positions, and computes required hedging notional for net DV01 neutrality.
        """
        total_irs_notional = sum(p.notional_usd for p in portfolio.irs_positions)
        total_irs_dv01 = sum(self.calculate_swap_dv01(p) for p in portfolio.irs_positions)

        bonds_dv01 = portfolio.bonds_dv01_usd
        net_portfolio_dv01 = bonds_dv01 + total_irs_dv01

        # PnL impact for +10 bps parallel rate rise
        pnl_plus_10bps = net_portfolio_dv01 * 10.0

        # Calculate Required 5Y Pay-Fixed IRS Notional to achieve Net DV01 Neutrality (0.0 Net DV01)
        # 5Y Pay-Fixed DV01 per $1 Notional = 2.5 * 0.0001 = 0.00025 USD / bps per $1
        benchmark_5y_dv01_per_dollar = 5.0 / 2.0 * 0.0001 # 0.00025
        hedge_shortfall_dv01 = -net_portfolio_dv01
        required_hedge_notional = round(hedge_shortfall_dv01 / benchmark_5y_dv01_per_dollar, 2)

        notes = (
            f"IRS RISK REPORT: Total IRS Notional = ${total_irs_notional:,.0f}, "
            f"IRS DV01 = ${total_irs_dv01:,.2f}/bps, Bonds DV01 = ${bonds_dv01:,.2f}/bps. "
            f"Net Portfolio DV01 = ${net_portfolio_dv01:,.2f}/bps. "
            f"Expected PnL for +10 bps Rate Rise = ${pnl_plus_10bps:,.2f} USD. "
            f"Required 5Y Pay-Fixed Hedge Notional = ${required_hedge_notional:,.2f} USD to achieve DV01 Neutrality."
        )
        logger.info(notes)

        return InterestRateSwapExposureReport(
            total_irs_notional_usd=total_irs_notional,
            total_irs_dv01_usd=round(total_irs_dv01, 2),
            bonds_dv01_usd=bonds_dv01,
            net_portfolio_dv01_usd=round(net_portfolio_dv01, 2),
            pnl_impact_plus_10bps_usd=round(pnl_plus_10bps, 2),
            required_hedge_irs_notional_usd=required_hedge_notional,
            status="IRS_RISK_AUDIT_SUCCESS",
            audit_notes=notes
        )
