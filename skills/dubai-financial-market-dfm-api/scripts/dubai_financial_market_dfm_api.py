import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class DfmOrderRequest:
    cl_ord_id: str
    nin_investor_number: str            # 10-digit National Investor Number e.g. '1099887766'
    symbol: str                         # e.g. 'EMAAR', 'DEWA', 'DIB'
    side: str                           # 'BUY' or 'SELL'
    order_qty: int
    price_aed: float
    prior_settlement_price_aed: float   # Prior day closing settlement price
    currency: str = "AED"

@dataclass
class DfmFixMessagePayload:
    fix_version: str                    # 'FIX.4.4'
    msg_type: str                       # 'D' (New Order Single)
    cl_ord_id: str
    account_nin: str                    # Tag 1
    symbol: str                         # Tag 55
    side: int                           # Tag 54 (1=Buy, 2=Sell)
    order_qty: int                      # Tag 38
    price: float                        # Tag 44
    currency: str                       # Tag 15 ('AED')
    fix_raw_string: str

@dataclass
class DfmOrderExecutionReport:
    cl_ord_id: str
    symbol: str
    status: str                         # 'STATUS_OK', 'INVALID_NIN', 'INVALID_TICK_SIZE', 'CIRCUIT_BREAKER_BAND_BREACH'
    is_dispatched: bool
    fix_payload: Optional[DfmFixMessagePayload]
    rejection_reason: Optional[str]

class DubaiFinancialMarketApiEngine:
    """
    Quantitative venue integration engine for Dubai Financial Market (DFM) FIX 4.4 protocol,
    validating National Investor Numbers (NIN), AED tick size regimes, and 10% daily price band circuit breakers.
    """
    def __init__(self, broker_target_comp_id: str = "DFM_GW_01"):
        self.broker_target_comp_id = broker_target_comp_id

    def audit_dfm_tick_size(self, price_aed: float) -> Tuple[bool, float]:
        """
        Audits price against DFM 2026 tick size rules:
        - Price < 1.00 AED -> Tick = 0.001 AED
        - 1.00 <= Price < 10.00 AED -> Tick = 0.01 AED
        - 10.00 <= Price < 50.00 AED -> Tick = 0.02 AED
        - Price >= 50.00 AED -> Tick = 0.05 AED
        """
        if price_aed < 1.00:
            step = 0.001
        elif price_aed < 10.00:
            step = 0.01
        elif price_aed < 50.00:
            step = 0.02
        else:
            step = 0.05

        rem = round(price_aed % step, 6)
        is_valid = (rem == 0.0) or (abs(rem - step) < 1e-6)
        return is_valid, step

    def format_fix_44_payload(self, req: DfmOrderRequest) -> DfmFixMessagePayload:
        side_tag = 1 if req.side.upper() == "BUY" else 2
        raw = f"8=FIX.4.4|35=D|1={req.nin_investor_number}|11={req.cl_ord_id}|55={req.symbol}|54={side_tag}|38={req.order_qty}|44={req.price_aed}|15=AED|"

        return DfmFixMessagePayload(
            fix_version="FIX.4.4",
            msg_type="D",
            cl_ord_id=req.cl_ord_id,
            account_nin=req.nin_investor_number,
            symbol=req.symbol,
            side=side_tag,
            order_qty=req.order_qty,
            price=req.price_aed,
            currency="AED",
            fix_raw_string=raw
        )

    def process_dfm_order(self, req: DfmOrderRequest) -> DfmOrderExecutionReport:
        """
        Audits NIN, tick size, and circuit breaker limits prior to DFM FIX 4.4 order generation.
        """
        # 1. Audit 10-digit Investor NIN
        if not req.nin_investor_number or len(req.nin_investor_number) != 10 or not req.nin_investor_number.isdigit():
            msg = f"INVALID NIN: National Investor Number '{req.nin_investor_number}' must be a 10-digit numeric string."
            logger.error(msg)
            return DfmOrderExecutionReport(
                cl_ord_id=req.cl_ord_id, symbol=req.symbol, status="INVALID_NIN",
                is_dispatched=False, fix_payload=None, rejection_reason=msg
            )

        # 2. Audit AED Tick Size Regime
        is_tick_ok, required_step = self.audit_dfm_tick_size(req.price_aed)
        if not is_tick_ok:
            msg = f"INVALID TICK SIZE: Price {req.price_aed:.4f} AED violates DFM tick step {required_step} AED."
            logger.error(msg)
            return DfmOrderExecutionReport(
                cl_ord_id=req.cl_ord_id, symbol=req.symbol, status="INVALID_TICK_SIZE",
                is_dispatched=False, fix_payload=None, rejection_reason=msg
            )

        # 3. Audit Daily Price Band Circuit Breaker (10%)
        if req.prior_settlement_price_aed > 0:
            dev_pct = abs(req.price_aed - req.prior_settlement_price_aed) / req.prior_settlement_price_aed
            if dev_pct > 0.10:
                msg = f"CIRCUIT BREAKER BREACH: Price {req.price_aed:.2f} AED deviates {dev_pct*100:.2f}% (> 10%) from settlement {req.prior_settlement_price_aed:.2f} AED."
                logger.error(msg)
                return DfmOrderExecutionReport(
                    cl_ord_id=req.cl_ord_id, symbol=req.symbol, status="CIRCUIT_BREAKER_BAND_BREACH",
                    is_dispatched=False, fix_payload=None, rejection_reason=msg
                )

        payload = self.format_fix_44_payload(req)
        logger.info(f"DFM FIX 4.4 ORDER DISPATCHED [{req.cl_ord_id}]: {req.side} {req.order_qty} x {req.symbol} @ {req.price_aed:.2f} AED (NIN={req.nin_investor_number}).")

        return DfmOrderExecutionReport(
            cl_ord_id=req.cl_ord_id,
            symbol=req.symbol,
            status="STATUS_OK",
            is_dispatched=True,
            fix_payload=payload,
            rejection_reason=None
        )
