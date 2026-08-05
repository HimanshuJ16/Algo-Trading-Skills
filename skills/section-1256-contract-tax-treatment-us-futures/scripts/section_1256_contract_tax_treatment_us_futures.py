import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Record:
    """Legacy Record container for backward compatibility."""
    id: str
    value: float

class Section1256ContractType(str, Enum):
    REGULATED_FUTURES = "REGULATED_FUTURES"               # CME/ICE e.g. ES, NQ, CL
    BROAD_INDEX_OPTIONS = "BROAD_INDEX_OPTIONS"           # SPX, NDX, RUT, VIX options
    FOREIGN_CURRENCY_CONTRACT = "FOREIGN_CURRENCY_CONTRACT" # Interbank / Regulated Forex
    DEALER_EQUITY_OPTION = "DEALER_EQUITY_OPTION"
    NON_QUALIFYING_SINGLE_STOCK = "NON_QUALIFYING_SINGLE_STOCK"

@dataclass
class Section1256Trade:
    trade_id: str
    symbol: str
    contract_type: Section1256ContractType
    realized_pnl_usd: float
    unrealized_mtm_pnl_usd: float = 0.0                    # Dec 31 Mark-to-Market PnL
    is_open_at_year_end: bool = False

@dataclass
class Form6781TaxSummary:
    total_realized_pnl_usd: float
    total_unrealized_mtm_pnl_usd: float
    net_section_1256_pnl_usd: float
    long_term_portion_60_pct_usd: float                    # 60% Long-Term
    short_term_portion_40_pct_usd: float                   # 40% Short-Term
    estimated_blended_tax_usd: float                       # Calculated at ~26.8% max rate vs 37% short-term rate
    tax_savings_vs_short_term_usd: float
    audit_notes: str

class Section1256ContractTaxTreatmentUsFuturesEngine:
    """
    Production-grade IRS Section 1256 contract tax engine processing futures and broad index options
    under the 60/40 statutory split, year-end Mark-to-Market rules, and IRS Form 6781 reporting.
    """
    # Statutory IRS 60/40 Split Ratios
    LONG_TERM_RATIO = 0.60
    SHORT_TERM_RATIO = 0.40

    # Max US Federal Tax Rates (2026)
    MAX_LONG_TERM_RATE = 0.20
    MAX_SHORT_TERM_RATE = 0.37
    BLENDED_MAX_RATE = (LONG_TERM_RATIO * MAX_LONG_TERM_RATE) + (SHORT_TERM_RATIO * MAX_SHORT_TERM_RATE) # 26.8%

    def __init__(self):
        self.records: List[Record] = []
        self.trades: List[Section1256Trade] = []

    def add_record(self, record: Record):
        """Legacy add_record method retained for 100% backward compatibility."""
        self.records.append(record)

    def process(self) -> float:
        """Legacy process method retained for 100% backward compatibility."""
        return sum(r.value for r in self.records)

    def add_trade(self, trade: Section1256Trade):
        """Adds a Section 1256 qualifying or non-qualifying trade."""
        if trade.contract_type == Section1256ContractType.NON_QUALIFYING_SINGLE_STOCK:
            logger.warning(f"Trade '{trade.trade_id}' ({trade.symbol}) is NOT Section 1256 eligible. Excluded from 60/40 treatment.")
            return
        self.trades.append(trade)

    def generate_form_6781_summary(self) -> Form6781TaxSummary:
        """
        Calculates IRS Section 1256 tax treatment under Form 6781:
        1. Net Realized + Year-End Mark-to-Market Unrealized PnL.
        2. Apply Statutory 60% Long-Term / 40% Short-Term split.
        3. Quantify tax savings vs standard 100% Short-Term capital gains rate.
        """
        total_realized = sum(t.realized_pnl_usd for t in self.trades)
        total_unrealized_mtm = sum(t.unrealized_mtm_pnl_usd for t in self.trades if t.is_open_at_year_end)
        net_pnl = total_realized + total_unrealized_mtm

        if net_pnl > 0:
            long_term_pnl = round(net_pnl * self.LONG_TERM_RATIO, 2)
            short_term_pnl = round(net_pnl * self.SHORT_TERM_RATIO, 2)
            estimated_tax = round((long_term_pnl * self.MAX_LONG_TERM_RATE) + (short_term_pnl * self.MAX_SHORT_TERM_RATE), 2)
            ordinary_short_term_tax = round(net_pnl * self.MAX_SHORT_TERM_RATE, 2)
            tax_savings = round(ordinary_short_term_tax - estimated_tax, 2)
        else:
            long_term_pnl = round(net_pnl * self.LONG_TERM_RATIO, 2)
            short_term_pnl = round(net_pnl * self.SHORT_TERM_RATIO, 2)
            estimated_tax = 0.0
            tax_savings = 0.0

        notes = (
            f"IRS FORM 6781 SUMMARY: Net Section 1256 PnL = ${net_pnl:,.2f} "
            f"(Realized ${total_realized:,.2f} + MTM Unrealized ${total_unrealized_mtm:,.2f}). "
            f"60% Long-Term = ${long_term_pnl:,.2f}, 40% Short-Term = ${short_term_pnl:,.2f}. "
            f"Tax Savings = ${tax_savings:,.2f} (Blended Max Rate = 26.8% vs 37.0%)."
        )

        logger.info(notes)

        return Form6781TaxSummary(
            total_realized_pnl_usd=round(total_realized, 2),
            total_unrealized_mtm_pnl_usd=round(total_unrealized_mtm, 2),
            net_section_1256_pnl_usd=round(net_pnl, 2),
            long_term_portion_60_pct_usd=long_term_pnl,
            short_term_portion_40_pct_usd=short_term_pnl,
            estimated_blended_tax_usd=estimated_tax,
            tax_savings_vs_short_term_usd=tax_savings,
            audit_notes=notes
        )
