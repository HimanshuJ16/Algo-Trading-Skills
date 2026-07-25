"""
american-vs-european-style-option-exercise-handling:
Evaluates the optimal early-exercise boundary for American options.
"""
import dataclasses
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class OptionState:
    option_type: str        # 'CALL' or 'PUT'
    spot_price: float       # Current price of underlying
    strike_price: float     # Strike price of the option
    market_price: float     # Current market price of the option (Continuation Value)
    
    # Dividend Info
    is_ex_dividend_tomorrow: bool = False
    dividend_amount: float = 0.0


class EarlyExerciseEvaluator:
    """
    Evaluates whether an American option should be exercised early.
    European options are out of scope as they fundamentally cannot be exercised early.
    """

    def _get_intrinsic_value(self, state: OptionState) -> float:
        if state.option_type.upper() == 'CALL':
            return max(0.0, state.spot_price - state.strike_price)
        elif state.option_type.upper() == 'PUT':
            return max(0.0, state.strike_price - state.spot_price)
        else:
            raise ValueError(f"Unknown option type: {state.option_type}")

    def evaluate(self, state: OptionState) -> Tuple[bool, str]:
        """
        Returns (should_exercise: bool, reason: str).
        """
        intrinsic_value = self._get_intrinsic_value(state)
        time_value = state.market_price - intrinsic_value
        
        # 1. Out of the Money (OTM) checks
        if intrinsic_value <= 0:
            return False, "Option is Out-of-the-Money (OTM) or At-the-Money (ATM). Never exercise."

        # 2. CALL OPTION RULES
        if state.option_type.upper() == 'CALL':
            # The Golden Rule: Never early exercise a non-dividend paying call.
            if not state.is_ex_dividend_tomorrow or state.dividend_amount <= 0:
                return False, "Never early exercise a Call option without an imminent dividend. Sell it instead to capture time value."
                
            # If there IS a dividend tomorrow, compare the dividend to the remaining time value.
            # If the dividend exceeds the time value we are sacrificing, we should exercise.
            if state.dividend_amount > time_value:
                return True, f"Imminent dividend ({state.dividend_amount}) exceeds remaining time value ({time_value:.2f}). Early exercise optimal."
            else:
                return False, "Imminent dividend does not exceed remaining time value. Do not exercise."

        # 3. PUT OPTION RULES
        elif state.option_type.upper() == 'PUT':
            # For puts, if intrinsic value is strictly greater than the market price, 
            # there is a hard arbitrage / early exercise is strictly optimal.
            # (In reality, market makers usually price puts so market_price >= intrinsic,
            # but deep ITM illiquid puts frequently cross this boundary).
            if intrinsic_value > state.market_price:
                return True, f"Deep ITM Put: Intrinsic value ({intrinsic_value:.2f}) > Market value ({state.market_price:.2f}). Early exercise optimal."
            else:
                return False, "Put continuation value (Market Price) > Intrinsic value. Do not exercise."

        return False, "Unhandled edge case."
