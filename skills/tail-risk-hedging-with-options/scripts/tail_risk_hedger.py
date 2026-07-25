from dataclasses import dataclass
from typing import List, Dict

@dataclass
class HedgingResult:
    hedged: bool
    options_bought: int
    cost: float

class TailRiskHedger:
    def __init__(self, budget_pct: float = 0.05):
        self.budget_pct = budget_pct

    def hedge(self, portfolio_value: float, option_price: float) -> HedgingResult:
        if option_price <= 0 or portfolio_value <= 0:
            return HedgingResult(False, 0, 0.0)
        max_spend = portfolio_value * self.budget_pct
        options_bought = int(max_spend // option_price)
        cost = options_bought * option_price
        return HedgingResult(options_bought > 0, options_bought, cost)
