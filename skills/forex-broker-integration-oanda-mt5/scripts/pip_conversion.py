"""
forex-broker-integration-oanda-mt5: Production-grade Forex pip/pipette conversion engine,
overnight swap/rollover calculator (with Wednesday 3x triple-swap rule), lot-to-units manager,
and MT5 terminal health state monitor.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

JPY_PAIRS_PIP_DECIMALS = 2
DEFAULT_PIP_DECIMALS = 4
METALS_PIP_DECIMALS = 1  # XAU/USD Gold: 0.1 per pip

LOT_SIZES: Dict[str, float] = {
    "standard": 100_000.0,
    "mini": 10_000.0,
    "micro": 1_000.0,
    "nano": 100.0,
}


@dataclass
class SwapCalculationResult:
    pair: str
    side: str
    lots: float
    hold_days: int
    daily_swap_rate: float
    total_swap_cost: float
    is_triple_swap_applied: bool


class ForexPipEngine:
    """
    Handles Forex pip/pipette conversions across JPY pairs, standard 4-decimal pairs,
    and precious metals, plus account base currency pip value calculations.
    """

    @staticmethod
    def pip_size(pair: str) -> float:
        clean_pair = pair.upper().replace("/", "").replace("_", "")
        if "JPY" in clean_pair:
            decimals = JPY_PAIRS_PIP_DECIMALS
        elif "XAU" in clean_pair or "GOLD" in clean_pair:
            decimals = METALS_PIP_DECIMALS
        else:
            decimals = DEFAULT_PIP_DECIMALS
        return 10.0 ** (-decimals)

    @staticmethod
    def pipette_size(pair: str) -> float:
        """Pipette is 1/10th of a Pip (fractional pip)."""
        return ForexPipEngine.pip_size(pair) / 10.0

    @staticmethod
    def price_diff_to_pips(pair: str, price_diff: float) -> float:
        return price_diff / ForexPipEngine.pip_size(pair)

    @staticmethod
    def pips_to_price_diff(pair: str, pips: float) -> float:
        return pips * ForexPipEngine.pip_size(pair)

    @staticmethod
    def calculate_pip_value(
        pair: str,
        units: float,
        account_currency: str = "USD",
        quote_to_account_fx_rate: float = 1.0,
    ) -> float:
        """
        Calculates the monetary value of 1 pip in the account's base currency.
        """
        pip = ForexPipEngine.pip_size(pair)
        # Pip value in quote currency = units * pip_size
        pip_val_quote = units * pip
        # Convert to account currency
        pip_val_account = pip_val_quote * quote_to_account_fx_rate
        return round(pip_val_account, 4)


class SwapRolloverCalculator:
    """
    Calculates overnight financing swap charges/credits including the Wednesday 3x triple-swap rule.
    """

    def __init__(self, swap_rates_by_pair: Optional[Dict[str, Dict[str, float]]] = None):
        # Default swap rates per lot per day in account currency
        self.swap_rates = swap_rates_by_pair or {
            "EURUSD": {"long": -5.20, "short": +1.50},
            "USDJPY": {"long": +8.40, "short": -14.10},
            "GBPUSD": {"long": -4.80, "short": +0.90},
        }

    def calculate_swap(
        self,
        pair: str,
        side: str,  # "LONG" or "SHORT"
        lots: float,
        hold_days: int = 1,
        includes_wednesday: bool = False,
    ) -> SwapCalculationResult:
        clean_pair = pair.upper().replace("/", "").replace("_", "")
        side_clean = side.lower()

        pair_rates = self.swap_rates.get(clean_pair, {"long": -3.0, "short": -3.0})
        daily_rate = pair_rates.get(side_clean, -3.0)

        # Wednesday overnight incurs 3 days of swap (Wednesday + Saturday + Sunday)
        effective_days = hold_days + (2 if includes_wednesday else 0)
        total_swap = lots * daily_rate * effective_days

        return SwapCalculationResult(
            pair=clean_pair,
            side=side.upper(),
            lots=lots,
            hold_days=hold_days,
            daily_swap_rate=daily_rate,
            total_swap_cost=round(total_swap, 2),
            is_triple_swap_applied=includes_wednesday,
        )


class MT5BridgeMonitor:
    """
    Independent health check monitor verifying underlying MT5 terminal IPC connection state.
    """

    def __init__(self, terminal_connected_check_fn=None):
        self.check_fn = terminal_connected_check_fn or (lambda: True)

    def is_terminal_connected(self) -> Tuple[bool, str]:
        try:
            connected = self.check_fn()
            if not connected:
                return False, "MT5 Terminal has lost connection to broker server."
            return True, "MT5 Terminal IPC connection healthy."
        except Exception as e:
            return False, f"MT5 Terminal connection check exception: {str(e)}"


# Backward compatibility functions
def pip_size(pair: str) -> float:
    return ForexPipEngine.pip_size(pair)


def price_diff_to_pips(pair: str, price_diff: float) -> float:
    return ForexPipEngine.price_diff_to_pips(pair, price_diff)


def lots_to_units(lots: float, lot_type: str = "standard") -> float:
    if lot_type not in LOT_SIZES:
        raise ValueError(f"Unknown lot_type '{lot_type}', expected one of {list(LOT_SIZES)}")
    return lots * LOT_SIZES[lot_type]


def units_to_lots(units: float, lot_type: str = "standard") -> float:
    if lot_type not in LOT_SIZES:
        raise ValueError(f"Unknown lot_type '{lot_type}', expected one of {list(LOT_SIZES)}")
    return units / LOT_SIZES[lot_type]
