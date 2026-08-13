"""american-vs-european-style-option-exercise-handling:
Evaluates the optimal early-exercise boundary for American options.

European options are out of scope — they can only be exercised at expiration and
have no early-exercise decision to make.
"""
import dataclasses
import logging
import math
from typing import Tuple

logger = logging.getLogger(__name__)

VALID_OPTION_TYPES = frozenset({"CALL", "PUT"})


@dataclasses.dataclass(frozen=True)
class OptionState:
    """Immutable snapshot of an American option's market state for exercise evaluation."""

    option_type: str            # 'CALL' or 'PUT' (case-insensitive, normalized to uppercase)
    spot_price: float           # Current price of underlying
    strike_price: float         # Strike price of the option
    market_price: float         # Current market price of the option (continuation value)

    is_ex_dividend_tomorrow: bool = False
    dividend_amount: float = 0.0

    def __post_init__(self) -> None:
        normalized = self.option_type.strip().upper()
        object.__setattr__(self, "option_type", normalized)

        if normalized not in VALID_OPTION_TYPES:
            raise ValueError(
                f"Invalid option_type '{self.option_type}'. Must be one of {VALID_OPTION_TYPES}."
            )

        for field_name in ("spot_price", "strike_price", "market_price", "dividend_amount"):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or math.isnan(value) or math.isinf(value):
                raise ValueError(f"{field_name} must be a finite number, got {value!r}.")
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative, got {value}.")

    @property
    def intrinsic_value(self) -> float:
        if self.option_type == "CALL":
            return max(0.0, self.spot_price - self.strike_price)
        return max(0.0, self.strike_price - self.spot_price)

    @property
    def time_value(self) -> float:
        return self.market_price - self.intrinsic_value


class EarlyExerciseEvaluator:
    """
    Evaluates whether an American option should be exercised early.

    The core tradeoff: early exercise captures intrinsic value immediately but
    destroys the option's remaining time value (optionality premium). The
    decision is optimal only when the value forfeited by holding is less than
    the value captured by exercising now.

    Key rules:

    - **Non-dividend calls**: It is a mathematical error to exercise early when
      ``market_price >= intrinsic_value`` — selling the call captures both
      intrinsic and time value. However, if the call is trading *below* parity
      (``market_price < intrinsic``, e.g. due to illiquidity), early exercise
      captures more value than selling.
    - **Dividend calls**: Exercise only if the dividend strictly exceeds the
      remaining time value being sacrificed.
    - **Deep ITM puts**: Exercise if intrinsic value strictly exceeds the
      market price (below-parity condition).
    """

    def evaluate(self, state: OptionState) -> Tuple[bool, str]:
        """Returns ``(should_exercise: bool, reason: str)``."""
        intrinsic = state.intrinsic_value
        time_value = state.time_value

        # 1. OTM / ATM — no intrinsic value to capture
        if intrinsic <= 0:
            return False, "Option is Out-of-the-Money (OTM) or At-the-Money (ATM). Never exercise."

        # 2. CALL OPTION RULES
        if state.option_type == "CALL":
            has_dividend = state.is_ex_dividend_tomorrow and state.dividend_amount > 0

            if not has_dividend:
                if state.market_price < intrinsic:
                    return True, (
                        f"Call trading below parity: Market ({state.market_price:.2f}) < "
                        f"Intrinsic ({intrinsic:.2f}). Early exercise captures more value than selling."
                    )
                return False, (
                    "Never early exercise a Call option without an imminent dividend. "
                    "Sell it instead to capture time value."
                )

            if state.market_price < intrinsic:
                return True, (
                    f"Call trading below parity: Market ({state.market_price:.2f}) < "
                    f"Intrinsic ({intrinsic:.2f}). Early exercise optimal (dividend capture secondary)."
                )

            if state.dividend_amount > time_value:
                return True, (
                    f"Imminent dividend ({state.dividend_amount}) exceeds remaining time value "
                    f"({time_value:.2f}). Early exercise optimal."
                )
            return False, (
                "Imminent dividend does not exceed remaining time value. Do not exercise."
            )

        # 3. PUT OPTION RULES
        if intrinsic > state.market_price:
            return True, (
                f"Deep ITM Put: Intrinsic value ({intrinsic:.2f}) > "
                f"Market value ({state.market_price:.2f}). Early exercise optimal."
            )
        return False, (
            "Put continuation value (Market Price) >= Intrinsic value. Do not exercise."
        )
