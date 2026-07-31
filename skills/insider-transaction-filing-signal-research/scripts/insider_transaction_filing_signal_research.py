import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Executive Role Weights: Higher info symmetry -> Higher weight
ROLE_WEIGHT_MAP = {
    "CEO": 1.0,
    "CFO": 1.0,
    "DIRECTOR": 0.6,
    "TEN_PCT_OWNER": 0.3
}

@dataclass
class Form4TransactionRecord:
    filing_id: str
    insider_name: str
    insider_role: str                   # 'CEO', 'CFO', 'DIRECTOR', 'TEN_PCT_OWNER'
    transaction_code: str               # 'P' (Open Market Purchase), 'S' (Open Market Sale)
    shares: int
    price: float
    is_rule_10b5_1: bool                # Form 4 Rule 10b5-1 Plan Checkbox
    is_open_market: bool = True         # True for open market, False for option grants/gifts
    filing_timestamp_nanos: int = 0

@dataclass
class InsiderFilingSignalReport:
    symbol: str
    total_filings_analyzed: int
    opportunistic_buys_count: int
    opportunistic_sales_count: int
    routine_10b5_1_filtered_count: int
    total_opportunistic_buy_notional_usd: float
    total_opportunistic_sell_notional_usd: float
    weighted_net_sentiment_score: float # Normalized -1.0 to +1.0
    signal_classification: str          # 'STRONG_BULLISH_OPPORTUNISTIC_BUY', 'BEARISH_OPPORTUNISTIC_SELL', 'NEUTRAL_ROUTINE'
    audit_notes: str

class InsiderFilingSignalEngine:
    """
    Quantitative factor research engine for SEC Form 4 insider transaction filings,
    isolating opportunistic non-10b5-1 C-suite open-market purchases from routine scheduled sales.
    """
    def __init__(self):
        pass

    def get_role_weight(self, role: str) -> float:
        return ROLE_WEIGHT_MAP.get(role.strip().upper(), 0.5)

    def analyze_form4_filings(
        self,
        symbol: str,
        filings: List[Form4TransactionRecord]
    ) -> InsiderFilingSignalReport:
        """
        Filters routine 10b5-1 trades, applies executive role weights, and calculates net insider sentiment score.
        """
        if not filings:
            notes = f"INSIDER SIGNAL [{symbol}]: No Form 4 filings provided. Neutral signal."
            logger.info(notes)
            return InsiderFilingSignalReport(
                symbol=symbol, total_filings_analyzed=0, opportunistic_buys_count=0,
                opportunistic_sales_count=0, routine_10b5_1_filtered_count=0,
                total_opportunistic_buy_notional_usd=0.0, total_opportunistic_sell_notional_usd=0.0,
                weighted_net_sentiment_score=0.0, signal_classification="NEUTRAL_ROUTINE", audit_notes=notes
            )

        opp_buys_count = 0
        opp_sells_count = 0
        routine_filtered_count = 0

        tot_buy_notional = 0.0
        tot_sell_notional = 0.0

        weighted_buy_score = 0.0
        weighted_sell_score = 0.0

        for f in filings:
            # 1. Filter Routine 10b5-1 Trades and Non-Open-Market Transactions
            if f.is_rule_10b5_1 or not f.is_open_market:
                routine_filtered_count += 1
                continue

            role_w = self.get_role_weight(f.insider_role)
            notional = float(f.shares) * f.price
            t_code = f.transaction_code.strip().upper()

            if t_code == "P": # Open Market Purchase
                opp_buys_count += 1
                tot_buy_notional += notional
                weighted_buy_score += (notional * role_w)
            elif t_code == "S": # Open Market Sale
                opp_sells_count += 1
                tot_sell_notional += notional
                weighted_sell_score += (notional * role_w)

        tot_weighted_score = weighted_buy_score + weighted_sell_score
        if tot_weighted_score > 0:
            net_sentiment = round((weighted_buy_score - weighted_sell_score) / tot_weighted_score, 4)
        else:
            net_sentiment = 0.0

        # Signal Classification
        if net_sentiment >= 0.3 and opp_buys_count > 0:
            sig = "STRONG_BULLISH_OPPORTUNISTIC_BUY"
        elif net_sentiment <= -0.3 and opp_sells_count > 0:
            sig = "BEARISH_OPPORTUNISTIC_SELL"
        else:
            sig = "NEUTRAL_ROUTINE"

        notes = (
            f"INSIDER FILING REPORT [{symbol}]: Total Filings = {len(filings)} "
            f"({routine_filtered_count} routine 10b5-1 filtered). "
            f"Opportunistic Buys = {opp_buys_count} (${tot_buy_notional:,.0f}), "
            f"Opportunistic Sales = {opp_sells_count} (${tot_sell_notional:,.0f}). "
            f"Weighted Net Sentiment = {net_sentiment:+.2f} ({sig})."
        )
        logger.info(notes)

        return InsiderFilingSignalReport(
            symbol=symbol,
            total_filings_analyzed=len(filings),
            opportunistic_buys_count=opp_buys_count,
            opportunistic_sales_count=opp_sells_count,
            routine_10b5_1_filtered_count=routine_filtered_count,
            total_opportunistic_buy_notional_usd=round(tot_buy_notional, 2),
            total_opportunistic_sell_notional_usd=round(tot_sell_notional, 2),
            weighted_net_sentiment_score=net_sentiment,
            signal_classification=sig,
            audit_notes=notes
        )
