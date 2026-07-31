import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class RealizedTrade:
    trade_id: str
    symbol: str
    sell_date: str
    sell_price: float
    cost_basis: float
    quantity: float
    potential_wash_sale_loss: float = 0.0 # Standard wash sale amount if applicable

@dataclass
class TaxLot:
    lot_id: str
    symbol: str
    buy_date: str
    buy_price: float
    quantity: float
    year_end_fmv_price: float              # Fair Market Value on Dec 31st

@dataclass
class MtmTaxReport:
    is_mtm_elected: bool
    total_realized_pl_usd: float
    unrealized_mtm_pl_usd: float
    wash_sale_disallowed_usd: float
    total_reportable_taxable_pl_usd: float
    reportable_loss_deduction_usd: float  # Amount allowed against ordinary income
    tax_form_mapping: str                 # 'Form 4797 Part II (Ordinary Income)' or 'Form 8949 / Schedule D (Capital)'
    status: str                           # 'MTM_ORDINARY_TAX_COMPUTED', 'CAPITAL_TAX_COMPUTED'
    audit_notes: str

class MarkToMarketTaxEngine:
    """
    US tax accounting engine evaluating IRS Section 475(f) Mark-to-Market (MTM) election for active traders,
    eliminating wash sale rules, marking open positions to year-end FMV, and reporting ordinary P&L on Form 4797.
    """
    def __init__(self, max_capital_loss_deduction_usd: float = 3000.0):
        self.max_capital_loss_deduction_usd = max_capital_loss_deduction_usd

    def calculate_tax_liability(
        self,
        is_mtm_elected: bool,
        realized_trades: List[RealizedTrade],
        open_tax_lots: List[TaxLot]
    ) -> MtmTaxReport:
        """
        Calculates reportable P&L, applies wash sale exemptions for MTM traders, and maps to IRS forms.
        """
        # 1. Realized P&L
        tot_realized_pl = sum((t.sell_price - t.cost_basis) * t.quantity for t in realized_trades)

        if is_mtm_elected:
            # SECTION 475(f) MTM ACCOUNTING RULES
            # A. Wash sale rules (IRC 1091) do NOT apply
            wash_sale_disallowed = 0.0

            # B. Mark open year-end positions to FMV
            unrealized_mtm_pl = sum(
                (lot.year_end_fmv_price - lot.buy_price) * lot.quantity
                for lot in open_tax_lots
            )

            # C. Total Net Ordinary Income/Loss (Form 4797 Part II)
            total_reportable_pl = round(tot_realized_pl + unrealized_mtm_pl, 2)
            
            # D. Loss Deduction: 100% of loss is deductible against ordinary income (No $3,000 cap!)
            loss_deduction = total_reportable_pl if total_reportable_pl < 0 else 0.0

            form_map = "Form 4797 Part II (Ordinary Income)"
            status = "MTM_ORDINARY_TAX_COMPUTED"
            notes = (
                f"SECTION 475(f) MTM ELECTED: Realized P&L = ${tot_realized_pl:,.2f}, "
                f"Year-End MTM P&L = ${unrealized_mtm_pl:,.2f}. Total Ordinary P&L = ${total_reportable_pl:,.2f}. "
                f"Wash Sales Exempt ($0.00). Reported on {form_map} with 100% loss deductibility."
            )
            logger.info(notes)

        else:
            # STANDARD CAPITAL ACCOUNTING RULES (SCHEDULE D)
            # A. Enforce Wash Sale Rules (IRC 1091)
            wash_sale_disallowed = sum(t.potential_wash_sale_loss for t in realized_trades if t.potential_wash_sale_loss > 0)
            
            # B. Open positions NOT marked to market
            unrealized_mtm_pl = 0.0

            # C. Net Capital Gain/Loss
            net_capital_pl = round(tot_realized_pl + wash_sale_disallowed, 2)
            total_reportable_pl = net_capital_pl

            # D. Capital Loss Deduction Cap ($3,000 max against ordinary income)
            if net_capital_pl < 0:
                loss_deduction = max(net_capital_pl, -self.max_capital_loss_deduction_usd)
            else:
                loss_deduction = 0.0

            form_map = "Form 8949 / Schedule D (Capital)"
            status = "CAPITAL_TAX_COMPUTED"
            notes = (
                f"STANDARD CAPITAL ACCOUNTING: Realized P&L = ${tot_realized_pl:,.2f}, "
                f"Wash Sale Disallowed = ${wash_sale_disallowed:,.2f}. Net Capital P&L = ${net_capital_pl:,.2f}. "
                f"Loss Deduction Capped at ${abs(loss_deduction):,.2f}/yr against ordinary income. Reported on {form_map}."
            )
            logger.info(notes)

        return MtmTaxReport(
            is_mtm_elected=is_mtm_elected,
            total_realized_pl_usd=round(tot_realized_pl, 2),
            unrealized_mtm_pl_usd=round(unrealized_mtm_pl, 2),
            wash_sale_disallowed_usd=round(wash_sale_disallowed, 2),
            total_reportable_taxable_pl_usd=total_reportable_pl,
            reportable_loss_deduction_usd=round(loss_deduction, 2),
            tax_form_mapping=form_map,
            status=status,
            audit_notes=notes
        )
