"""
multi-currency-pnl-and-fx-conversion: Production-grade multi-currency PnL engine,
point-in-time FX rate conversion, asset return vs FX translation PnL decomposition,
USD cross-rate triangulation, and currency-specific precision rounding.
"""
from dataclasses import dataclass
import datetime
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CURRENCY_DECIMALS: Dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "INR": 2,
    "JPY": 0,
    "AUD": 2,
    "CAD": 2,
    "BTC": 8,
    "ETH": 8,
}


@dataclass
class CurrencyAmount:
    amount: float
    currency: str
    timestamp: Optional[datetime.datetime] = None


@dataclass
class DecomposedPnL:
    native_currency: str
    base_currency: str
    native_price_pnl: float
    fx_translation_pnl: float
    total_base_pnl: float
    entry_fx_rate: float
    exit_fx_rate: float


class PointInTimeFXResolver:
    """
    Resolves point-in-time FX conversion rates using direct, inverse,
    or USD cross-rate triangulation.
    """

    def __init__(self, rate_provider_fn: Optional[Callable[[str, str, Optional[datetime.datetime]], float]] = None):
        self.rate_provider_fn = rate_provider_fn or self._default_rate_lookup

    def _default_rate_lookup(
        self, from_ccy: str, to_ccy: str, timestamp: Optional[datetime.datetime] = None
    ) -> float:
        if from_ccy == to_ccy:
            return 1.0
        # Reference fallback rates (USD base)
        usd_rates = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "INR": 83.50, "JPY": 155.0, "AUD": 1.50}
        if from_ccy in usd_rates and to_ccy in usd_rates:
            # Convert via USD bridge
            from_in_usd = 1.0 / usd_rates[from_ccy] if from_ccy != "USD" else 1.0
            to_per_usd = usd_rates[to_ccy]
            return from_in_usd * to_per_usd
        return 1.0

    def get_rate(self, from_ccy: str, to_ccy: str, timestamp: Optional[datetime.datetime] = None) -> float:
        if from_ccy.upper() == to_ccy.upper():
            return 1.0
        return self.rate_provider_fn(from_ccy.upper(), to_ccy.upper(), timestamp)


class MultiCurrencyPnLEngine:
    """
    Engine for multi-currency PnL aggregation, point-in-time FX conversion,
    and asset return vs FX translation gain/loss decomposition.
    """

    def __init__(self, fx_resolver: Optional[PointInTimeFXResolver] = None):
        self.fx_resolver = fx_resolver or PointInTimeFXResolver()

    def round_amount(self, amount: float, currency: str) -> float:
        decimals = CURRENCY_DECIMALS.get(currency.upper(), 2)
        return round(amount, decimals)

    def convert(
        self, amount: CurrencyAmount, target_currency: str, timestamp: Optional[datetime.datetime] = None
    ) -> CurrencyAmount:
        ts = amount.timestamp or timestamp
        if amount.currency.upper() == target_currency.upper():
            return amount

        rate = self.fx_resolver.get_rate(amount.currency, target_currency, ts)
        converted_raw = amount.amount * rate
        rounded = self.round_amount(converted_raw, target_currency)
        return CurrencyAmount(amount=rounded, currency=target_currency.upper(), timestamp=ts)

    def calculate_decomposed_pnl(
        self,
        entry_price: float,
        exit_price: float,
        quantity: float,
        native_currency: str,
        base_currency: str,
        entry_timestamp: Optional[datetime.datetime] = None,
        exit_timestamp: Optional[datetime.datetime] = None,
        override_entry_fx: Optional[float] = None,
        override_exit_fx: Optional[float] = None,
    ) -> DecomposedPnL:
        """
        Decomposes total base PnL into:
        1. Native Asset Price PnL (converted at entry FX rate)
        2. FX Translation PnL (gain/loss purely from FX spot movement)
        """
        native_ccy = native_currency.upper()
        base_ccy = base_currency.upper()

        entry_fx = (
            override_entry_fx
            if override_entry_fx is not None
            else self.fx_resolver.get_rate(native_ccy, base_ccy, entry_timestamp)
        )
        exit_fx = (
            override_exit_fx
            if override_exit_fx is not None
            else self.fx_resolver.get_rate(native_ccy, base_ccy, exit_timestamp)
        )

        native_pnl = (exit_price - entry_price) * quantity

        # Native Asset Price PnL in base currency (holding FX constant at entry_fx)
        native_price_pnl_base = native_pnl * entry_fx

        # FX Translation PnL (effect of (exit_fx - entry_fx) on position exit value)
        fx_translation_pnl_base = (exit_price * quantity) * (exit_fx - entry_fx)

        total_base_pnl = native_price_pnl_base + fx_translation_pnl_base

        return DecomposedPnL(
            native_currency=native_ccy,
            base_currency=base_ccy,
            native_price_pnl=self.round_amount(native_price_pnl_base, base_ccy),
            fx_translation_pnl=self.round_amount(fx_translation_pnl_base, base_ccy),
            total_base_pnl=self.round_amount(total_base_pnl, base_ccy),
            entry_fx_rate=entry_fx,
            exit_fx_rate=exit_fx,
        )

    def aggregate_in_base_currency(
        self,
        amounts: List[CurrencyAmount],
        base_currency: str,
        timestamp: Optional[datetime.datetime] = None,
    ) -> float:
        converted = [self.convert(a, base_currency, timestamp) for a in amounts]
        total = sum(c.amount for c in converted)
        return self.round_amount(total, base_currency)


# Backward compatibility functions
def convert(amount: CurrencyAmount, target_currency: str, rate_lookup_fn) -> CurrencyAmount:
    if amount.currency == target_currency:
        return amount
    ts = getattr(amount, "timestamp", None)
    rate = rate_lookup_fn(amount.currency, target_currency) if ts is None else rate_lookup_fn(amount.currency, target_currency, ts)
    return CurrencyAmount(amount.amount * rate, target_currency)


def aggregate_in_base_currency(amounts, base_currency, rate_lookup_fn):
    converted = [convert(a, base_currency, rate_lookup_fn) for a in amounts]
    return sum(c.amount for c in converted)
