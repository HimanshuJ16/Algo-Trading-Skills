import struct
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class XetraOrderRequest:
    cl_ord_id: str
    isin: str                           # e.g. 'DE0007100000' (Mercedes-Benz)
    side: str                           # 'BUY' or 'SELL'
    order_qty: int
    price_eur: float
    account_type: str                   # 'P' (Proprietary), 'A' (Agent), 'M' (Market Maker)
    mifid_short_code: int               # MiFID II DEA ID (e.g. 99201)

@dataclass
class T7EtiBinaryHeader:
    body_len: int
    template_id: int                    # e.g. 10100 for NewOrderSingle
    session_id: int
    sequence_no: int
    sending_time_ns: int

@dataclass
class XetraOrderExecutionReport:
    cl_ord_id: str
    isin: str
    status: str                         # 'STATUS_OK', 'INVALID_TICK_SIZE', 'INVALID_MIFID_TAG'
    is_dispatched: bool
    eti_header: Optional[T7EtiBinaryHeader]
    rejection_reason: Optional[str]

class DeutscheBorseXetraApiEngine:
    """
    Quantitative venue integration engine for Deutsche Börse Xetra T7 Enhanced Trading Interface (ETI),
    validating MiFID II RTS 28 order parameters, T7 tick size regimes, and binary message formatting.
    """
    def __init__(self, session_id: int = 12345):
        self.session_id = session_id
        self.sequence_no = 0

    def audit_xetra_tick_size(self, price_eur: float) -> Tuple[bool, float]:
        """
        Audits price against Xetra price-band tick size rules:
        - Price < €10.00 -> Tick = €0.001
        - €10.00 <= Price < €50.00 -> Tick = €0.005
        - Price >= €50.00 -> Tick = €0.01
        """
        if price_eur < 10.0:
            step = 0.001
        elif price_eur < 50.0:
            step = 0.005
        else:
            step = 0.01

        # Check modulo remainder within floating point tolerance
        rem = round(price_eur % step, 6)
        is_valid = (rem == 0.0) or (abs(rem - step) < 1e-6)
        return is_valid, step

    def format_t7_eti_header(self, template_id: int = 10100, body_len: int = 128) -> T7EtiBinaryHeader:
        self.sequence_no += 1
        import time
        now_ns = int(time.time() * 1_000_000_000)

        return T7EtiBinaryHeader(
            body_len=body_len,
            template_id=template_id,
            session_id=self.session_id,
            sequence_no=self.sequence_no,
            sending_time_ns=now_ns
        )

    def process_xetra_order(self, req: XetraOrderRequest) -> XetraOrderExecutionReport:
        """
        Audits MiFID II tags and Xetra tick size compliance prior to T7 ETI binary header formatting.
        """
        # 1. Audit MiFID II Account Tag
        if req.account_type.upper() not in ["P", "A", "M"]:
            msg = f"INVALID MiFID TAG: account_type '{req.account_type}' not in ['P', 'A', 'M']."
            logger.error(msg)
            return XetraOrderExecutionReport(
                cl_ord_id=req.cl_ord_id, isin=req.isin, status="INVALID_MIFID_TAG",
                is_dispatched=False, eti_header=None, rejection_reason=msg
            )

        # 2. Audit Xetra Tick Size Regime
        is_tick_ok, required_step = self.audit_xetra_tick_size(req.price_eur)
        if not is_tick_ok:
            msg = f"INVALID TICK SIZE: Price €{req.price_eur:.4f} violates Xetra tick step €{required_step}."
            logger.error(msg)
            return XetraOrderExecutionReport(
                cl_ord_id=req.cl_ord_id, isin=req.isin, status="INVALID_TICK_SIZE",
                is_dispatched=False, eti_header=None, rejection_reason=msg
            )

        # 3. Format T7 ETI Header & Dispatch
        header = self.format_t7_eti_header(template_id=10100)
        logger.info(
            f"T7 ETI ORDER DISPATCHED [{req.cl_ord_id}]: {req.side} {req.order_qty} x {req.isin} @ €{req.price_eur:.2f} "
            f"(Account={req.account_type}, ShortCode={req.mifid_short_code}, Seq={header.sequence_no})"
        )

        return XetraOrderExecutionReport(
            cl_ord_id=req.cl_ord_id,
            isin=req.isin,
            status="STATUS_OK",
            is_dispatched=True,
            eti_header=header,
            rejection_reason=None
        )
