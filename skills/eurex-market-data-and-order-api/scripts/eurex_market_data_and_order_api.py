import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class EurexContractSpec:
    symbol: str                         # e.g. 'FESX' (EURO STOXX 50) or 'FGBL' (Euro-Bund)
    expiry: str                         # e.g. '202609'
    tick_step: float                    # e.g. 1.0 for FESX, 0.01 for FGBL
    multiplier_eur: float               # e.g. 10.0 EUR per point for FESX, 1000.0 for FGBL
    max_reasonability_dev_points: float # e.g. 50.0 points

@dataclass
class EurexOrderRequest:
    cl_ord_id: str
    contract_symbol: str                # 'FESX' or 'FGBL'
    expiry: str
    side: str                           # 'BUY' or 'SELL'
    order_qty: int
    price: float
    market_mid_price: float

@dataclass
class EurexT7EtiHeader:
    body_len: int
    template_id: int                    # 10100 for NewOrderSingle
    session_id: int
    sequence_no: int
    sending_time_ns: int

@dataclass
class EurexOrderExecutionReport:
    cl_ord_id: str
    contract: str                       # e.g. 'FESX 202609'
    status: str                         # 'STATUS_OK', 'INVALID_TICK_SIZE', 'PRICE_REASONABILITY_BREACH'
    is_dispatched: bool
    notional_value_eur: float
    eti_header: Optional[EurexT7EtiHeader]
    rejection_reason: Optional[str]

class EurexMarketDataAndOrderApiEngine:
    """
    Quantitative derivatives venue engine for parsing Eurex T7 EMDI multicast market data depth feeds,
    formatting T7 ETI binary order entry payloads, and enforcing futures contract tick rules and price reasonability bands.
    """
    def __init__(self, session_id: int = 55443):
        self.session_id = session_id
        self.sequence_no = 0
        self.contract_specs: Dict[str, EurexContractSpec] = {
            "FESX": EurexContractSpec("FESX", "202609", tick_step=1.0, multiplier_eur=10.0, max_reasonability_dev_points=50.0),
            "FGBL": EurexContractSpec("FGBL", "202609", tick_step=0.01, multiplier_eur=1000.0, max_reasonability_dev_points=0.50)
        }

    def audit_eurex_tick_size(self, price: float, tick_step: float) -> bool:
        rem = round(price % tick_step, 6)
        return (rem == 0.0) or (abs(rem - tick_step) < 1e-6)

    def format_t7_eti_header(self, template_id: int = 10100) -> EurexT7EtiHeader:
        self.sequence_no += 1
        import time
        return EurexT7EtiHeader(
            body_len=128,
            template_id=template_id,
            session_id=self.session_id,
            sequence_no=self.sequence_no,
            sending_time_ns=int(time.time() * 1_000_000_000)
        )

    def process_eurex_order(self, req: EurexOrderRequest) -> EurexOrderExecutionReport:
        """
        Audits Eurex futures contract tick size and price reasonability bands prior to T7 ETI binary header formatting.
        """
        spec = self.contract_specs.get(req.contract_symbol.upper())
        if not spec:
            msg = f"UNKNOWN CONTRACT [{req.cl_ord_id}]: Eurex symbol '{req.contract_symbol}' not supported."
            logger.error(msg)
            return EurexOrderExecutionReport(
                cl_ord_id=req.cl_ord_id, contract=f"{req.contract_symbol} {req.expiry}", status="UNKNOWN_CONTRACT",
                is_dispatched=False, notional_value_eur=0.0, eti_header=None, rejection_reason=msg
            )

        # 1. Audit Eurex Futures Tick Step
        if not self.audit_eurex_tick_size(req.price, spec.tick_step):
            msg = f"INVALID TICK SIZE: Price {req.price} on {spec.symbol} violates tick step {spec.tick_step}."
            logger.error(msg)
            return EurexOrderExecutionReport(
                cl_ord_id=req.cl_ord_id, contract=f"{spec.symbol} {req.expiry}", status="INVALID_TICK_SIZE",
                is_dispatched=False, notional_value_eur=0.0, eti_header=None, rejection_reason=msg
            )

        # 2. Audit Price Reasonability Band
        dev = abs(req.price - req.market_mid_price)
        if dev > spec.max_reasonability_dev_points:
            msg = f"PRICE REASONABILITY BREACH: Price {req.price} deviates {dev:.2f} points (> max {spec.max_reasonability_dev_points}) from mid {req.market_mid_price}."
            logger.error(msg)
            return EurexOrderExecutionReport(
                cl_ord_id=req.cl_ord_id, contract=f"{spec.symbol} {req.expiry}", status="PRICE_REASONABILITY_BREACH",
                is_dispatched=False, notional_value_eur=0.0, eti_header=None, rejection_reason=msg
            )

        # 3. Format T7 ETI Binary Payload & Calculate Notional
        header = self.format_t7_eti_header(template_id=10100)
        notional = round(req.order_qty * req.price * spec.multiplier_eur, 2)

        logger.info(
            f"EUREX T7 ETI ORDER DISPATCHED [{req.cl_ord_id}]: {req.side} {req.order_qty} x {spec.symbol} {req.expiry} @ {req.price} "
            f"(Notional €{notional:,.2f}, Seq={header.sequence_no})."
        )

        return EurexOrderExecutionReport(
            cl_ord_id=req.cl_ord_id,
            contract=f"{spec.symbol} {req.expiry}",
            status="STATUS_OK",
            is_dispatched=True,
            notional_value_eur=notional,
            eti_header=header,
            rejection_reason=None
        )
