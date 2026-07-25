import logging
from dataclasses import dataclass
from typing import Dict, List

logger = logging.getLogger(__name__)

@dataclass
class Position:
    symbol: str
    delta_usd: float       # Positive for Long, Negative for Short
    base_margin_usd: float # The absolute margin required if traded in isolation

@dataclass
class MarginReport:
    isolated_margin_usd: float
    cross_margin_usd: float
    capital_efficiency_ratio: float
    total_offset_usd: float

class CrossMarginOptimizer:
    """
    Calculates portfolio margin requirements by recognizing offsetting
    directional risks based on a correlation matrix.
    """
    def __init__(self, correlation_matrix: Dict[str, Dict[str, float]], correlation_haircut: float = 0.80):
        """
        correlation_matrix: Dict of dicts e.g. {'BTC': {'ETH': 0.90}}
        correlation_haircut: Brokers never give 100% credit for correlation. 0.80 means 
                             a 0.90 correlation is treated as 0.72 for margin offset.
        """
        self.correlation_matrix = correlation_matrix
        self.correlation_haircut = correlation_haircut

    def _get_correlation(self, sym1: str, sym2: str) -> float:
        if sym1 == sym2:
            return 1.0
        try:
            return self.correlation_matrix[sym1][sym2]
        except KeyError:
            try:
                return self.correlation_matrix[sym2][sym1]
            except KeyError:
                return 0.0

    def calculate_margin(self, positions: List[Position]) -> MarginReport:
        isolated_margin = sum(p.base_margin_usd for p in positions)
        
        longs = [p for p in positions if p.delta_usd > 0]
        shorts = [p for p in positions if p.delta_usd < 0]
        
        total_offset = 0.0
        
        # O(N*M) pairwise offset calculation for Longs vs Shorts
        # In a real engine, this is solved via linear programming. 
        # This is a greedy heuristic for demonstration of the concept.
        
        # Track remaining margin available for offset
        remaining_margin = {p.symbol: p.base_margin_usd for p in positions}
        
        for l_pos in longs:
            for s_pos in shorts:
                if remaining_margin[l_pos.symbol] <= 0 or remaining_margin[s_pos.symbol] <= 0:
                    continue
                    
                corr = self._get_correlation(l_pos.symbol, s_pos.symbol)
                
                # Only offset if they are positively correlated (Long A hedges Short B)
                if corr > 0:
                    effective_corr = corr * self.correlation_haircut
                    
                    # The maximum offset is constrained by the smaller of the two margin requirements
                    max_possible_offset = min(remaining_margin[l_pos.symbol], remaining_margin[s_pos.symbol])
                    
                    # The actual offset credited by the broker
                    actual_offset = max_possible_offset * effective_corr
                    
                    total_offset += actual_offset
                    
                    # Deduct the used margin from the remaining pool
                    remaining_margin[l_pos.symbol] -= max_possible_offset
                    remaining_margin[s_pos.symbol] -= max_possible_offset
                    
        cross_margin = isolated_margin - total_offset
        
        # Capital Efficiency Ratio (CER)
        cer = isolated_margin / cross_margin if cross_margin > 0 else float('inf')
        
        logger.info(f"Margin Calc: Isolated=${isolated_margin:,.2f} | Cross=${cross_margin:,.2f} | CER={cer:.2f}x")
        
        return MarginReport(
            isolated_margin_usd=isolated_margin,
            cross_margin_usd=cross_margin,
            capital_efficiency_ratio=cer,
            total_offset_usd=total_offset
        )
