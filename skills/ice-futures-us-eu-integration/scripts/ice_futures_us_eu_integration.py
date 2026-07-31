import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ICE Month Code Mapping: F(Jan), G(Feb), H(Mar), J(Apr), K(May), M(Jun), N(Jul), Q(Aug), U(Sep), V(Oct), X(Nov), Z(Dec)
ICE_MONTH_CODES = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"
}

@dataclass
class IceContractSpec:
    root_symbol: str                    # e.g. 'B', 'T', 'SB', 'DX'
    name: str                           # e.g. 'Brent Crude'
    exchange_mic: str                   # 'IFEU' (Europe) or 'IFUS' (US)
    multiplier: float                   # e.g. 1000 for Brent, 112000 for Sugar 11
    tick_size: float                    # e.g. 0.01 for Brent, 0.0001 for Sugar 11
    currency: str                       # 'USD', 'EUR', 'GBP'
    max_ncr_ticks: int = 100            # ICE No-Cancellation Range max tick deviation

@dataclass
class IceOrderPayload:
    root_symbol: str
    month_code: str                     # e.g. 'Z'
    year: int                           # e.g. 2026
    side: str                           # 'BUY' or 'SELL'
    price: float
    quantity: int
    bbo_price: float                    # Current best bid/ask for NCR audit

@dataclass
class IceFuturesOrderReport:
    formatted_ice_symbol: str           # e.g. 'BZ26'
    fix_tag_55_symbol: str
    fix_tag_207_mic: str                # 'IFEU' or 'IFUS'
    fix_tag_200_maturity: str           # '202612'
    notional_value_usd: float
    tick_value_usd: float
    is_price_tick_valid: bool
    is_ncr_reasonability_valid: bool
    status: str                         # 'ORDER_ROUTED_APPROVED', 'INVALID_TICK_SIZE', 'NCR_LIMIT_EXCEEDED'
    audit_notes: str

class IceFuturesIntegrationEngine:
    """
    Quantitative market connectivity engine for Intercontinental Exchange (ICE Futures US & Europe),
    enforcing FIX Tag 207 MIC routing, contract month symbol formatting, and NCR reasonability price limits.
    """
    def __init__(self):
        # ICE Contract Catalog
        self.catalog: Dict[str, IceContractSpec] = {
            "B": IceContractSpec("B", "Brent Crude Futures", "IFEU", multiplier=1000.0, tick_size=0.01, currency="USD", max_ncr_ticks=100),
            "T": IceContractSpec("T", "Dutch TTF Natural Gas", "IFEU", multiplier=744.0, tick_size=0.005, currency="EUR", max_ncr_ticks=100),
            "SB": IceContractSpec("SB", "Sugar No. 11 Futures", "IFUS", multiplier=112000.0, tick_size=0.0001, currency="USD", max_ncr_ticks=100),
            "DX": IceContractSpec("DX", "US Dollar Index Futures", "IFUS", multiplier=1000.0, tick_size=0.005, currency="USD", max_ncr_ticks=50),
        }

    def format_ice_symbol(self, root: str, month_code: str, year: int) -> Tuple[str, str]:
        """
        Formats ICE contract string (e.g., 'BZ26') and FIX Tag 200 Maturity string ('202612').
        """
        year_str = str(year)
        yy = year_str[-2:]

        # Map month_code to month number
        month_num = None
        for m_num, m_code in ICE_MONTH_CODES.items():
            if m_code.upper() == month_code.upper():
                month_num = m_num
                break
        if month_num is None:
            raise ValueError(f"Invalid ICE Month Code '{month_code}'.")

        maturity_yyyymm = f"{year}{month_num:02d}"
        ice_symbol = f"{root.upper()}{month_code.upper()}{yy}"
        return ice_symbol, maturity_yyyymm

    def process_and_route_order(self, payload: IceOrderPayload) -> IceFuturesOrderReport:
        """
        Validates and formats order payload for ICE FIX gateway routing.
        """
        root = payload.root_symbol.upper()
        if root not in self.catalog:
            raise ValueError(f"Unsupported ICE Contract root '{root}'. Supported: {list(self.catalog.keys())}")

        spec = self.catalog[root]
        ice_symbol, maturity_yyyymm = self.format_ice_symbol(root, payload.month_code, payload.year)

        # Valuation
        notional_val = round(payload.price * spec.multiplier * payload.quantity, 2)
        tick_val = round(spec.tick_size * spec.multiplier, 4)

        # Audit price tick alignment
        ticks_count = round(payload.price / spec.tick_size, 6)
        is_tick_valid = abs(ticks_count - round(ticks_count)) < 1e-5

        # Audit ICE No-Cancellation Range (NCR) reasonability limits
        price_diff = abs(payload.price - payload.bbo_price)
        diff_ticks = int(round(price_diff / spec.tick_size))
        is_ncr_valid = (diff_ticks <= spec.max_ncr_ticks)

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = f"ICE ORDER REJECTED [{ice_symbol}]: Price ${payload.price} violates contract tick size ${spec.tick_size}."
            logger.warning(notes)
        elif not is_ncr_valid:
            status = "NCR_LIMIT_EXCEEDED"
            notes = (
                f"ICE ORDER REJECTED [{ice_symbol}]: Order price deviation ({diff_ticks} ticks from BBO ${payload.bbo_price}) "
                f"exceeds ICE NCR limit ({spec.max_ncr_ticks} ticks)."
            )
            logger.warning(notes)
        else:
            status = "ORDER_ROUTED_APPROVED"
            notes = (
                f"ICE ORDER ROUTED [{ice_symbol} - {spec.exchange_mic}]: {payload.side} {payload.quantity} @ ${payload.price} "
                f"(Notional = ${notional_val:,.2f}, FIX Tag 207 = {spec.exchange_mic}, Tag 200 = {maturity_yyyymm})."
            )
            logger.info(notes)

        return IceFuturesOrderReport(
            formatted_ice_symbol=ice_symbol,
            fix_tag_55_symbol=ice_symbol,
            fix_tag_207_mic=spec.exchange_mic,
            fix_tag_200_maturity=maturity_yyyymm,
            notional_value_usd=notional_val,
            tick_value_usd=tick_val,
            is_price_tick_valid=is_tick_valid,
            is_ncr_reasonability_valid=is_ncr_valid,
            status=status,
            audit_notes=notes
        )
