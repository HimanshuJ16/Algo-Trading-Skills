import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class AppreciatedPosition:
    symbol: str
    quantity: float
    cost_basis_per_share: float
    fair_market_value_per_share: float

@dataclass
class OffsettingTransaction:
    transaction_type: str              # 'SHORT_SALE', 'EQUITY_SWAP', 'FORWARD_CONTRACT', 'ITM_PUT'
    entry_date: date
    close_date: Optional[date]
    tax_year_end_date: date
    re_hedged_during_60_days: bool = False

@dataclass
class ConstructiveSaleResult:
    status: str                        # 'NOT_APPLICABLE', 'SAFE_HARBOR_QUALIFIED', 'CONSTRUCTIVE_SALE_TRIGGERED'
    unrealized_gain: float
    realized_taxable_gain: float
    constructive_sale_date: Optional[date]
    reason: str

class ConstructiveSaleRuleEngine:
    """
    US Tax Compliance engine for IRS Section 1259 Constructive Sale Detection
    and Section 1259(c)(3) 30-day / 60-day Safe Harbor Audit.
    """
    def evaluate_transaction(
        self,
        position: AppreciatedPosition,
        offsetting: Optional[OffsettingTransaction]
    ) -> ConstructiveSaleResult:
        """
        Evaluates an appreciated position and offsetting transaction for Section 1259 compliance.
        """
        total_cost_basis = position.quantity * position.cost_basis_per_share
        total_fmv = position.quantity * position.fair_market_value_per_share
        unrealized_gain = total_fmv - total_cost_basis

        # Rule 1: Section 1259 only applies to APPRECIATED financial positions (FMV > Cost Basis)
        if unrealized_gain <= 0:
            return ConstructiveSaleResult(
                status="NOT_APPLICABLE",
                unrealized_gain=round(unrealized_gain, 2),
                realized_taxable_gain=0.0,
                constructive_sale_date=None,
                reason="Position is at a loss or break-even. Section 1259 applies only to appreciated positions."
            )

        if offsetting is None:
            return ConstructiveSaleResult(
                status="NOT_APPLICABLE",
                unrealized_gain=round(unrealized_gain, 2),
                realized_taxable_gain=0.0,
                constructive_sale_date=None,
                reason="No offsetting position established."
            )

        # Rule 2: Audit Safe Harbor Exception (Section 1259(c)(3))
        # Condition A: Closed on or before Jan 30 following tax year-end (30 days post-year-end)
        max_allowed_close_date = offsetting.tax_year_end_date + timedelta(days=30)
        
        if offsetting.close_date is None or offsetting.close_date > max_allowed_close_date:
            logger.critical(f"Constructive Sale Triggered for {position.symbol}: Offsetting position not closed by {max_allowed_close_date}.")
            return ConstructiveSaleResult(
                status="CONSTRUCTIVE_SALE_TRIGGERED",
                unrealized_gain=round(unrealized_gain, 2),
                realized_taxable_gain=round(unrealized_gain, 2),
                constructive_sale_date=offsetting.entry_date,
                reason=f"Offsetting position not closed on or before 30 days after tax year-end ({max_allowed_close_date})."
            )

        # Condition B: Held 100% unhedged for at least 60 days following the closing date
        if offsetting.re_hedged_during_60_days:
            logger.critical(f"Constructive Sale Triggered for {position.symbol}: Re-hedged during 60-day safe harbor window.")
            return ConstructiveSaleResult(
                status="CONSTRUCTIVE_SALE_TRIGGERED",
                unrealized_gain=round(unrealized_gain, 2),
                realized_taxable_gain=round(unrealized_gain, 2),
                constructive_sale_date=offsetting.entry_date,
                reason="Position risk was reduced/hedged during the mandatory 60-day unhedged safe harbor window."
            )

        # Safe Harbor Qualified! No immediate constructive sale
        logger.info(f"Safe Harbor Qualified for {position.symbol}. Immediate tax gain avoided.")
        return ConstructiveSaleResult(
            status="SAFE_HARBOR_QUALIFIED",
            unrealized_gain=round(unrealized_gain, 2),
            realized_taxable_gain=0.0,
            constructive_sale_date=None,
            reason="Satisfies Section 1259(c)(3) safe harbor: Closed within 30 days post-tax-year and held unhedged 60+ days."
        )
