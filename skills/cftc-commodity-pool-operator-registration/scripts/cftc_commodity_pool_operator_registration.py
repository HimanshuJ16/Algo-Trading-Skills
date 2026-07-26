import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class PortfolioState:
    liquidation_value: float
    current_commodity_initial_margin: float
    current_commodity_notional: float

@dataclass
class ProposedTrade:
    is_commodity_interest: bool
    required_initial_margin: float
    notional_value: float

class CftcCpoComplianceEngine:
    """
    Evaluates trades against the CFTC Rule 4.13(a)(3) de minimis exemption.
    A fund is exempt from CPO registration if it passes AT LEAST ONE of two tests:
    1. Margin Test: Aggregate Initial Margin <= 5% of Liquidation Value
    2. Notional Test: Aggregate Net Notional <= 100% of Liquidation Value
    """
    def __init__(self):
        self.margin_threshold = 0.05
        self.notional_threshold = 1.00

    def check_trade_compliance(self, portfolio: PortfolioState, trade: ProposedTrade) -> bool:
        """
        Returns True if the trade is allowed under the de minimis exemption.
        Returns False if the trade would cause the fund to violate the exemption.
        """
        if portfolio.liquidation_value <= 0:
            logger.error("Liquidation value is zero or negative. Cannot establish exemption thresholds.")
            return False

        if not trade.is_commodity_interest:
            return True

        new_total_margin = portfolio.current_commodity_initial_margin + trade.required_initial_margin
        new_total_notional = portfolio.current_commodity_notional + abs(trade.notional_value)

        margin_ratio = new_total_margin / portfolio.liquidation_value
        notional_ratio = new_total_notional / portfolio.liquidation_value

        passes_margin_test = margin_ratio <= self.margin_threshold
        passes_notional_test = notional_ratio <= self.notional_threshold

        if passes_margin_test or passes_notional_test:
            logger.info(
                f"Trade Approved. Margin Ratio: {margin_ratio:.2%}, Notional Ratio: {notional_ratio:.2%}. "
                f"(Requires Margin <= 5% OR Notional <= 100%)"
            )
            return True
        else:
            logger.critical(
                f"Trade REJECTED. CFTC CPO Exemption Violation. "
                f"Margin Ratio: {margin_ratio:.2%} (Limit 5%), Notional Ratio: {notional_ratio:.2%} (Limit 100%)."
            )
            return False
