import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class ForexTradeRecord:
    trade_id: str
    pair: str                          # e.g. 'EUR/USD', 'USD/JPY'
    instrument_type: str               # 'SPOT_FOREX', 'CURRENCY_FUTURES', 'FORWARDS'
    realized_pnl_usd: float
    trade_date: str

@dataclass
class ForexTaxReport:
    total_realized_pnl_usd: float
    sec988_tax_liability_usd: float
    sec988_effective_tax_rate_pct: float
    sec1256_tax_liability_usd: float
    sec1256_effective_tax_rate_pct: float
    potential_tax_savings_usd: float
    recommended_election: str         # 'ELECT_SECTION_1256' or 'REMAIN_SECTION_988'
    rationale: str

class ForexTaxTreatmentEngine:
    """
    Quantitative tax engine for evaluating US IRC Section 988 (Ordinary Income/Loss) vs
    Section 1256 (60/40 Capital Gains Opt-Out Election) tax treatment for forex trading.
    """
    def __init__(
        self,
        ordinary_income_rate: float = 0.37,    # Default top federal ordinary income rate (37%)
        ltcg_rate: float = 0.20,                # Top long-term capital gains rate (20%)
        stcg_rate: float = 0.37                 # Short-term capital gains rate (37%)
    ):
        self.ordinary_income_rate = ordinary_income_rate
        self.ltcg_rate = ltcg_rate
        self.stcg_rate = stcg_rate
        
        # Section 1256 60/40 blended rate calculation
        self.sec1256_blended_rate = round(0.60 * self.ltcg_rate + 0.40 * self.stcg_rate, 4)

    def evaluate_forex_tax(self, trades: List[ForexTradeRecord]) -> ForexTaxReport:
        """
        Calculates tax liabilities under Section 988 vs Section 1256 Opt-Out.
        """
        total_pnl = sum(t.realized_pnl_usd for t in trades)

        if total_pnl >= 0:
            # Net Profit Case
            sec988_tax = total_pnl * self.ordinary_income_rate
            sec1256_tax = total_pnl * self.sec1256_blended_rate
            
            sec988_eff_rate = self.ordinary_income_rate * 100.0
            sec1256_eff_rate = self.sec1256_blended_rate * 100.0
            
            tax_savings = sec988_tax - sec1256_tax
            
            if tax_savings > 0:
                rec = "ELECT_SECTION_1256"
                rationale = (
                    f"Net profit of ${total_pnl:,.2f}. Section 1256 60/40 treatment lowers effective tax rate "
                    f"from {sec988_eff_rate:.1f}% to {sec1256_eff_rate:.1f}%, saving ${tax_savings:,.2f} in taxes."
                )
            else:
                rec = "REMAIN_SECTION_988"
                rationale = "Ordinary tax rate is lower or equal to Section 1256 blended rate."
        else:
            # Net Loss Case
            abs_loss = abs(total_pnl)
            sec988_tax = - (abs_loss * self.ordinary_income_rate) # Tax deduction benefit
            
            # Under Sec 1256, net capital loss is capped at $3,000 against ordinary income per year
            capped_loss_deduction = min(3000.0, abs_loss)
            sec1256_tax = - (capped_loss_deduction * self.ordinary_income_rate)
            
            sec988_eff_rate = self.ordinary_income_rate * 100.0
            sec1256_eff_rate = (capped_loss_deduction / abs_loss) * self.ordinary_income_rate * 100.0
            
            tax_savings = abs(sec988_tax) - abs(sec1256_tax) # Extra tax benefit under 988
            rec = "REMAIN_SECTION_988"
            rationale = (
                f"Net loss of -${abs_loss:,.2f}. Section 988 provides uncapped ordinary loss deduction "
                f"saving an extra ${tax_savings:,.2f} over Section 1256's $3,000 capital loss cap."
            )

        logger.info(f"FOREX TAX AUDIT: Net PnL=${total_pnl:,.2f} -> Recommended: {rec}")

        return ForexTaxReport(
            total_realized_pnl_usd=round(total_pnl, 2),
            sec988_tax_liability_usd=round(sec988_tax, 2),
            sec988_effective_tax_rate_pct=round(sec988_eff_rate, 2),
            sec1256_tax_liability_usd=round(sec1256_tax, 2),
            sec1256_effective_tax_rate_pct=round(sec1256_eff_rate, 2),
            potential_tax_savings_usd=round(tax_savings, 2),
            recommended_election=rec,
            rationale=rationale
        )
