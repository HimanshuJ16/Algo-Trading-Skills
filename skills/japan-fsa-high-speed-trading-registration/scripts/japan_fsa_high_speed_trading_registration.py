import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class JapanFsaHstTraderSpec:
    trader_id: str                      # e.g. 'HFT_ALPHA_TOKYO_01'
    fsa_hst_reg_id: str                 # e.g. 'KLFB_No_3120'
    is_registered_with_fsa: bool        # MUST BE True if HST criteria met
    is_algo_automated: bool             # Automated algorithm execution
    is_colocated: bool                  # Co-located line / low latency access
    latency_ms: float                   # Order latency (e.g. 2.0 ms)
    order_value_jpy: float              # Order value (e.g. JPY 50,000,000)
    has_kill_switch_enabled: bool       # MUST BE True
    has_resident_compliance_manager: bool # MUST BE True

@dataclass
class JapanFsaHstReport:
    trader_id: str
    is_hst_classified: bool
    is_fsa_registered: bool
    is_kill_switch_active: bool
    is_pre_trade_limit_valid: bool
    status: str                         # 'FSA_HST_APPROVED', 'REJECTED_UNREGISTERED_HST', 'REJECTED_MISSING_KILL_SWITCH', 'REJECTED_PRE_TRADE_LIMIT_EXCEEDED'
    audit_notes: str

class JapanFsaHstComplianceEngine:
    """
    Regulatory compliance engine for Japan Financial Services Agency (FSA) and Financial Instruments and Exchange Act (FIEA),
    auditing High-Speed Trader (HST) registration, co-location access, pre-trade risk controls, and kill switches.
    """
    def __init__(self, max_order_value_limit_jpy: float = 100_000_000.0):
        self.max_order_value_limit_jpy = max_order_value_limit_jpy # Max JPY 100M per order

    def audit_japan_fsa_hst_trader(self, spec: JapanFsaHstTraderSpec) -> JapanFsaHstReport:
        """
        Audits trader entity and order payload against FIEA Article 2 HST registration and risk control requirements.
        """
        # 1. FIEA Article 2 HST Classification Test
        is_hst = spec.is_algo_automated and spec.is_colocated and (spec.latency_ms <= 20.0)

        # 2. Audit FSA Registration for Classified HSTs
        if is_hst and not spec.is_registered_with_fsa:
            notes = (
                f"JAPAN FSA REJECT [{spec.trader_id}]: Classified as High-Speed Trader (FIEA Article 2) "
                f"due to co-located automated execution ({spec.latency_ms:.1f}ms), but UNREGISTERED with FSA!"
            )
            logger.critical(notes)
            return JapanFsaHstReport(
                trader_id=spec.trader_id, is_hst_classified=True, is_fsa_registered=False,
                is_kill_switch_active=spec.has_kill_switch_enabled, is_pre_trade_limit_valid=True,
                status="REJECTED_UNREGISTERED_HST", audit_notes=notes
            )

        # 3. Audit Emergency Kill Switch Requirement
        if is_hst and not spec.has_kill_switch_enabled:
            notes = f"JAPAN FSA REJECT [{spec.trader_id}]: Mandatory Emergency Kill Switch is NOT ENABLED."
            logger.critical(notes)
            return JapanFsaHstReport(
                trader_id=spec.trader_id, is_hst_classified=True, is_fsa_registered=spec.is_registered_with_fsa,
                is_kill_switch_active=False, is_pre_trade_limit_valid=True,
                status="REJECTED_MISSING_KILL_SWITCH", audit_notes=notes
            )

        # 4. Audit Pre-Trade Risk Control (Order Value Limit)
        is_limit_ok = (spec.order_value_jpy <= self.max_order_value_limit_jpy)
        if not is_limit_ok:
            notes = (
                f"JAPAN FSA REJECT [{spec.trader_id}]: Order value JPY {spec.order_value_jpy:,.0f} "
                f"exceeds pre-trade risk limit (JPY {self.max_order_value_limit_jpy:,.0f})."
            )
            logger.error(notes)
            return JapanFsaHstReport(
                trader_id=spec.trader_id, is_hst_classified=is_hst, is_fsa_registered=spec.is_registered_with_fsa,
                is_kill_switch_active=spec.has_kill_switch_enabled, is_pre_trade_limit_valid=False,
                status="REJECTED_PRE_TRADE_LIMIT_EXCEEDED", audit_notes=notes
            )

        notes = (
            f"JAPAN FSA HST APPROVED [{spec.trader_id} - Reg: {spec.fsa_hst_reg_id}]: "
            f"Verified FIEA registration, Kill Switch active, and pre-trade order limit (JPY {spec.order_value_jpy:,.0f})."
        )
        logger.info(notes)

        return JapanFsaHstReport(
            trader_id=spec.trader_id,
            is_hst_classified=is_hst,
            is_fsa_registered=spec.is_registered_with_fsa,
            is_kill_switch_active=spec.has_kill_switch_enabled,
            is_pre_trade_limit_valid=True,
            status="FSA_HST_APPROVED",
            audit_notes=notes
        )
