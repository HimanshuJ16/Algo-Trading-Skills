import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class MasAlgoConfig:
    algo_id: str                        # Internal strategy ID
    mas_reg_number: str                 # Registered MAS Algorithm Identifier (e.g. 'SG_MAS_ALGO_9812')
    is_sandbox_tested: bool             # Mandatory pre-deployment sandbox sign-off
    is_kill_switch_ready: bool          # Automated kill switch capability
    max_order_notional_sgd: float = 1_000_000.0 # Max SGD value per order
    max_order_rate_per_sec: int = 50     # Max orders per second limit
    max_price_collar_pct: float = 10.0  # Max SGX price deviation collar (+/- 10%)

@dataclass
class SgxOrderRequest:
    algo_id: str
    symbol: str                         # SGX Ticker (e.g. 'D05' DBS, 'Z74' Singtel)
    side: str                           # 'BUY' or 'SELL'
    price_sgd: float
    quantity: int
    sgx_ref_price_sgd: float            # Current SGX reference/mid price
    current_order_rate_sec: int = 1     # Current order count in active 1-second window

@dataclass
class MasComplianceReport:
    algo_id: str
    mas_reg_number: str
    symbol: str
    order_notional_sgd: float
    price_deviation_pct: float
    is_compliant: bool
    status: str                         # 'MAS_ORDER_APPROVED', 'MAS_REJECT_GOVERNANCE_BREACH', 'MAS_REJECT_PRICE_COLLAR_BREACH', 'MAS_REJECT_NOTIONAL_EXCEEDED', 'MAS_REJECT_RATE_LIMIT_EXCEEDED'
    audit_notes: str

class MasSingaporeAlgoComplianceEngine:
    """
    Pre-trade risk control and regulatory compliance engine enforcing MAS Singapore Securities and Futures Act (SFA) guidelines,
    SGX price collars, algorithm registration IDs, and emergency kill switches.
    """
    def __init__(self):
        pass

    def validate_sgx_order_compliance(
        self,
        config: MasAlgoConfig,
        order: SgxOrderRequest
    ) -> MasComplianceReport:
        """
        Audits order against MAS SFA governance, SGX price collars, and pre-trade limits.
        """
        algo_id = config.algo_id
        notional_sgd = round(order.price_sgd * order.quantity, 2)

        # 1. MAS Governance & Sandbox Audit
        if not config.mas_reg_number or not config.mas_reg_number.startswith("SG_MAS_"):
            status = "MAS_REJECT_GOVERNANCE_BREACH"
            notes = f"MAS REJECT [{algo_id}]: Missing or invalid MAS Algorithm Registration Number ('{config.mas_reg_number}')."
            logger.error(notes)
            return MasComplianceReport(algo_id=algo_id, mas_reg_number=config.mas_reg_number, symbol=order.symbol,
                                         order_notional_sgd=notional_sgd, price_deviation_pct=0.0,
                                         is_compliant=False, status=status, audit_notes=notes)

        if not config.is_sandbox_tested or not config.is_kill_switch_ready:
            status = "MAS_REJECT_GOVERNANCE_BREACH"
            notes = (
                f"MAS REJECT [{algo_id}]: Algorithm fails MAS SFA governance check! "
                f"Sandbox Tested = {config.is_sandbox_tested}, Kill Switch Ready = {config.is_kill_switch_ready}."
            )
            logger.error(notes)
            return MasComplianceReport(algo_id=algo_id, mas_reg_number=config.mas_reg_number, symbol=order.symbol,
                                         order_notional_sgd=notional_sgd, price_deviation_pct=0.0,
                                         is_compliant=False, status=status, audit_notes=notes)

        # 2. SGX Price Collar Audit
        if order.sgx_ref_price_sgd <= 0.0:
            raise ValueError("SGX reference price must be strictly positive.")

        dev_pct = round(abs(order.price_sgd - order.sgx_ref_price_sgd) / order.sgx_ref_price_sgd * 100.0, 2)
        if dev_pct > config.max_price_collar_pct:
            status = "MAS_REJECT_PRICE_COLLAR_BREACH"
            notes = (
                f"MAS REJECT [{order.symbol}]: Order price ${order.price_sgd:.2f} SGD deviates {dev_pct:.2f}% "
                f"from SGX reference price ${order.sgx_ref_price_sgd:.2f} SGD (Collar limit = +/-{config.max_price_collar_pct}%)."
            )
            logger.warning(notes)
            return MasComplianceReport(algo_id=algo_id, mas_reg_number=config.mas_reg_number, symbol=order.symbol,
                                         order_notional_sgd=notional_sgd, price_deviation_pct=dev_pct,
                                         is_compliant=False, status=status, audit_notes=notes)

        # 3. Notional & Rate Limit Audit
        if notional_sgd > config.max_order_notional_sgd:
            status = "MAS_REJECT_NOTIONAL_EXCEEDED"
            notes = f"MAS REJECT [{order.symbol}]: Order notional ${notional_sgd:,.2f} SGD exceeds limit (${config.max_order_notional_sgd:,.2f} SGD)."
            logger.warning(notes)
            return MasComplianceReport(algo_id=algo_id, mas_reg_number=config.mas_reg_number, symbol=order.symbol,
                                         order_notional_sgd=notional_sgd, price_deviation_pct=dev_pct,
                                         is_compliant=False, status=status, audit_notes=notes)

        if order.current_order_rate_sec > config.max_order_rate_per_sec:
            status = "MAS_REJECT_RATE_LIMIT_EXCEEDED"
            notes = f"MAS REJECT [{algo_id}]: Order rate ({order.current_order_rate_sec} orders/sec) exceeds max limit ({config.max_order_rate_per_sec}/sec)."
            logger.warning(notes)
            return MasComplianceReport(algo_id=algo_id, mas_reg_number=config.mas_reg_number, symbol=order.symbol,
                                         order_notional_sgd=notional_sgd, price_deviation_pct=dev_pct,
                                         is_compliant=False, status=status, audit_notes=notes)

        # 4. MAS Order Approved!
        status = "MAS_ORDER_APPROVED"
        notes = (
            f"MAS ORDER APPROVED [{order.symbol} - SGX]: {order.side} {order.quantity:,} shares "
            f"@ ${order.price_sgd:.2f} SGD (${notional_sgd:,.2f} SGD notional). "
            f"Price deviation = {dev_pct:.2f}%. Reg Tag = {config.mas_reg_number}."
        )
        logger.info(notes)

        return MasComplianceReport(
            algo_id=algo_id,
            mas_reg_number=config.mas_reg_number,
            symbol=order.symbol,
            order_notional_sgd=notional_sgd,
            price_deviation_pct=dev_pct,
            is_compliant=True,
            status=status,
            audit_notes=notes
        )
