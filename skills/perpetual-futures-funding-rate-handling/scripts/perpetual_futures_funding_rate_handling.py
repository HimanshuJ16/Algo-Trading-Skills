import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Configuration:
    enabled: bool = True
    threshold: float = 0.5

@dataclass
class PerpetualPosition:
    symbol: str
    position_qty: float                  # + for Long, - for Short
    side: str                            # 'LONG', 'SHORT'
    entry_price: float
    mark_price: float

@dataclass
class FundingRateUpdate:
    symbol: str
    funding_rate: float                  # e.g. 0.0001 = +0.01%
    next_funding_timestamp_utc: str
    funding_interval_hours: int = 8

@dataclass
class FundingPolicyConfig:
    max_adverse_funding_apr: float = 0.25  # Max acceptable 25% adverse APR drag
    auto_close_high_drag: bool = True

@dataclass
class FundingRateReport:
    symbol: str
    position_notional_usd: float
    funding_rate_pct: float
    funding_payment_usd: float          # Positive = Outflow (fee paid), Negative = Inflow (income)
    annualized_funding_apr: float
    is_adverse_drag_high: bool
    status: str                          # 'FUNDING_INFLOW_INCOME', 'FUNDING_OUTFLOW_OK', 'ADVERSE_FUNDING_DRAG_BREACH'
    audit_notes: str

class PerpetualFuturesFundingRateHandlingEngine:
    """
    Perpetual futures funding rate handling engine calculating periodic funding payments,
    annualized funding APR drag, and auditing adverse funding drag limits for crypto perpetual swaps.
    """
    def __init__(
        self,
        config: Optional[Configuration] = None,
        policy: Optional[FundingPolicyConfig] = None
    ):
        self.config = config or Configuration()
        self.policy = policy or FundingPolicyConfig()

    def execute(self, data: dict) -> dict:
        """
        Legacy execution method for backward compatibility.
        """
        if not self.config.enabled:
            return {"status": "disabled"}
        return {
            "status": "success",
            "processed": True,
            "value": data.get("value", 0) * self.config.threshold
        }

    def process_funding_update(
        self, pos: PerpetualPosition, update: FundingRateUpdate
    ) -> FundingRateReport:
        """
        Calculates funding payment, annualized APR drag, and audits adverse funding thresholds.
        """
        if not self.config.enabled:
            return FundingRateReport(
                symbol=pos.symbol,
                position_notional_usd=0.0,
                funding_rate_pct=0.0,
                funding_payment_usd=0.0,
                annualized_funding_apr=0.0,
                is_adverse_drag_high=False,
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled."
            )

        notional_val = abs(pos.position_qty) * pos.mark_price
        side_upper = pos.side.upper()
        rate = update.funding_rate

        # Long pays if F > 0, receives if F < 0. Short pays if F < 0, receives if F > 0.
        if side_upper == "LONG":
            payment_usd = notional_val * rate
        else:
            payment_usd = -notional_val * rate

        # Annualized APR = rate * (365 * 24 / interval_hours) * 100
        payments_per_year = (365.0 * 24.0) / float(max(1, update.funding_interval_hours))
        annualized_apr = rate * payments_per_year * 100.0

        # Adverse APR drag evaluation
        is_adverse = False
        adverse_apr = 0.0
        if payment_usd > 0:  # Payment is an outflow fee paid
            adverse_apr = abs(annualized_apr)
            if adverse_apr > (self.policy.max_adverse_funding_apr * 100.0):
                is_adverse = True

        if is_adverse:
            status = "ADVERSE_FUNDING_DRAG_BREACH"
        elif payment_usd > 0:
            status = "FUNDING_OUTFLOW_OK"
        else:
            status = "FUNDING_INFLOW_INCOME"

        notes = (
            f"PERPETUAL FUNDING AUDIT [{pos.symbol} {side_upper} - {status}]: Notional = ${notional_val:,.2f}, "
            f"Rate = {rate * 100.0:+.4f}%, Payment = ${payment_usd:+,.2f} ({'FEE' if payment_usd > 0 else 'INCOME'}), "
            f"Annualized APR = {annualized_apr:+.2f}%."
        )

        if is_adverse:
            logger.warning(f"ADVERSE FUNDING DRAG ALERT: {notes}")
        else:
            logger.info(notes)

        return FundingRateReport(
            symbol=pos.symbol,
            position_notional_usd=round(notional_val, 2),
            funding_rate_pct=round(rate * 100.0, 4),
            funding_payment_usd=round(payment_usd, 2),
            annualized_funding_apr=round(annualized_apr, 2),
            is_adverse_drag_high=is_adverse,
            status=status,
            audit_notes=notes
        )
