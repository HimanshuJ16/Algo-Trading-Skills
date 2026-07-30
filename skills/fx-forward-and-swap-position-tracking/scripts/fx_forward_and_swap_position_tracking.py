import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class FxContractPosition:
    contract_id: str
    currency_pair: str                  # e.g. 'EUR/USD'
    base_currency: str                  # e.g. 'EUR'
    quote_currency: str                 # e.g. 'USD'
    contract_type: str                  # 'FX_FORWARD' or 'FX_SWAP'
    position_side: str                  # 'BUY' (Long Base) or 'SELL' (Short Base)
    notional_base_currency: float       # e.g. 1,000,000 EUR
    agreed_forward_rate: float          # e.g. 1.1050
    days_to_maturity: int               # e.g. 90 days

@dataclass
class FxValuationDetail:
    contract_id: str
    currency_pair: str
    position_side: str
    notional_base: float
    agreed_forward_rate: float
    current_spot_rate: float
    fair_market_forward_rate: float
    swap_points: float                  # (F_fair - Spot) * 10,000
    unrealized_mtm_pnl_quote: float

@dataclass
class FxForwardPositionReport:
    total_open_contracts: int
    net_unrealized_mtm_pnl_usd: float
    net_exposure_by_base_currency: Dict[str, float] # e.g. {'EUR': +1000000.0}
    valuation_details: List[FxValuationDetail]
    audit_notes: str

class FxForwardSwapTrackingEngine:
    """
    Quantitative treasury engine for pricing FX outright forwards and FX swaps via Covered Interest Rate Parity (CIRP),
    calculating swap points, and tracking Mark-to-Market (MtM) PnL.
    """
    def __init__(self, day_count_basis: int = 360):
        self.day_count_basis = day_count_basis

    def calculate_cirp_forward_rate(
        self,
        spot_rate: float,
        base_interest_rate: float,      # r_base (e.g. 0.03 for EUR 3%)
        quote_interest_rate: float,     # r_quote (e.g. 0.05 for USD 5%)
        days_to_maturity: int
    ) -> float:
        """
        Calculates fair forward rate using Covered Interest Rate Parity (CIRP):
        F = Spot * (1 + r_quote * (T / 360)) / (1 + r_base * (T / 360))
        """
        if spot_rate <= 0 or days_to_maturity < 0:
            raise ValueError("Spot rate must be > 0 and days_to_maturity >= 0.")

        t_factor = days_to_maturity / float(self.day_count_basis)
        numerator = 1.0 + (quote_interest_rate * t_factor)
        denominator = 1.0 + (base_interest_rate * t_factor)

        fair_forward = spot_rate * (numerator / denominator)
        return round(fair_forward, 6)

    def audit_portfolio_positions(
        self,
        positions: List[FxContractPosition],
        market_rates: Dict[str, Dict[str, float]] # e.g. {'EUR/USD': {'spot': 1.1000, 'r_base': 0.03, 'r_quote': 0.05}}
    ) -> FxForwardPositionReport:
        """
        Audits portfolio of open FX Forwards & Swaps, revaluing Mark-to-Market PnL and net exposures.
        """
        if not positions:
            raise ValueError("Positions list cannot be empty.")

        details: List[FxValuationDetail] = []
        net_exposure: Dict[str, float] = {}
        total_mtm_usd = 0.0

        for pos in positions:
            m_data = market_rates.get(pos.currency_pair)
            if not m_data:
                raise ValueError(f"Missing market rate data for currency pair '{pos.currency_pair}'.")

            spot = m_data["spot"]
            r_b = m_data["r_base"]
            r_q = m_data["r_quote"]

            fair_forward = self.calculate_cirp_forward_rate(spot, r_b, r_q, pos.days_to_maturity)
            swap_pts = round((fair_forward - spot) * 10000.0, 2)

            side_clean = pos.position_side.upper()

            # Long FX Forward PnL = Notional * (F_market - F_contract)
            # Short FX Forward PnL = Notional * (F_contract - F_market)
            if side_clean == "BUY":
                rate_diff = fair_forward - pos.agreed_forward_rate
                signed_notional = pos.notional_base_currency
            else:
                rate_diff = pos.agreed_forward_rate - fair_forward
                signed_notional = -pos.notional_base_currency

            mtm_pnl = round(pos.notional_base_currency * rate_diff, 2)
            total_mtm_usd += mtm_pnl

            # Accumulate Net Base Currency Exposure
            curr_exp = net_exposure.get(pos.base_currency, 0.0)
            net_exposure[pos.base_currency] = round(curr_exp + signed_notional, 2)

            details.append(FxValuationDetail(
                contract_id=pos.contract_id,
                currency_pair=pos.currency_pair,
                position_side=side_clean,
                notional_base=pos.notional_base_currency,
                agreed_forward_rate=pos.agreed_forward_rate,
                current_spot_rate=spot,
                fair_market_forward_rate=fair_forward,
                swap_points=swap_pts,
                unrealized_mtm_pnl_quote=mtm_pnl
            ))

        notes = (
            f"FX FORWARD & SWAP AUDIT COMPLETE ({len(positions)} contracts): "
            f"Net Unrealized MtM PnL = ${total_mtm_usd:,.2f}. Net Exposures: {net_exposure}."
        )
        logger.info(notes)

        return FxForwardPositionReport(
            total_open_contracts=len(positions),
            net_unrealized_mtm_pnl_usd=round(total_mtm_usd, 2),
            net_exposure_by_base_currency=net_exposure,
            valuation_details=details,
            audit_notes=notes
        )
