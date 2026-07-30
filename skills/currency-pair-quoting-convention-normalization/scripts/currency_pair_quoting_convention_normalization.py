import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ISO 4217 Priority Order (Index 0 is highest priority base currency)
DEFAULT_CURRENCY_PRIORITY = [
    "EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"
]

@dataclass
class RawFxQuote:
    raw_symbol: str                    # e.g. 'EUR/USD', 'USD/EUR', 'USD/JPY', 'JPY/USD'
    bid_price: float
    ask_price: float
    vendor_id: str

@dataclass
class NormalizedFxQuoteReport:
    raw_symbol: str
    normalized_symbol: str             # Market-standard Base/Terms symbol
    is_inverted: bool
    normalized_bid: float
    normalized_ask: float
    pip_size: float
    spread_pips: float
    base_currency: str
    terms_currency: str

class CurrencyPairQuotingNormalizer:
    """
    FX market data normalization engine for enforcing ISO 4217 currency priority hierarchy,
    converting inverted quotes (USD/EUR -> EUR/USD), and recalibrating bid-ask spreads.
    """
    def __init__(self, priority_list: List[str] = None):
        self.priority_list = [c.upper() for c in (priority_list or DEFAULT_CURRENCY_PRIORITY)]

    def _get_currency_priority(self, ccy: str) -> int:
        ccy_upper = ccy.upper()
        if ccy_upper in self.priority_list:
            return self.priority_list.index(ccy_upper)
        return 999  # Fallback low priority for exotic currencies

    def parse_symbol(self, raw_symbol: str) -> Tuple[str, str]:
        cleaned = raw_symbol.replace("/", "").replace("_", "").replace("-", "").upper()
        if len(cleaned) == 6:
            return cleaned[:3], cleaned[3:]
        raise ValueError(f"Unable to parse 3-letter currency codes from {raw_symbol}")

    def normalize_quote(self, quote: RawFxQuote) -> NormalizedFxQuoteReport:
        """
        Audits currency pair priority and normalizes quote to market standard Base/Terms.
        """
        c1, c2 = self.parse_symbol(quote.raw_symbol)
        p1 = self._get_currency_priority(c1)
        p2 = self._get_currency_priority(c2)

        # Standard market convention: Base currency has HIGHER priority (lower index in list)
        if p1 <= p2:
            is_inverted = False
            base_ccy, terms_ccy = c1, c2
            norm_bid = quote.bid_price
            norm_ask = quote.ask_price
        else:
            is_inverted = True
            base_ccy, terms_ccy = c2, c1
            # Inversion transformation:
            # Standard Bid = 1 / Inverted Ask
            # Standard Ask = 1 / Inverted Bid
            if quote.ask_price <= 0 or quote.bid_price <= 0:
                raise ValueError("Invalid non-positive quote prices for inversion.")
            norm_bid = 1.0 / quote.ask_price
            norm_ask = 1.0 / quote.bid_price

            logger.warning(
                f"INVERTED FX QUOTE DETECTED [{quote.raw_symbol}]: Flipped to {base_ccy}/{terms_ccy}. "
                f"Raw ({quote.bid_price:.4f}/{quote.ask_price:.4f}) -> Normalized ({norm_bid:.5f}/{norm_ask:.5f})"
            )

        # Determine Pip Size: 0.01 for JPY terms, 0.0001 for standard terms
        pip_size = 0.01 if terms_ccy == "JPY" else 0.0001
        spread_pips = round((norm_ask - norm_bid) / pip_size, 2)

        return NormalizedFxQuoteReport(
            raw_symbol=quote.raw_symbol,
            normalized_symbol=f"{base_ccy}/{terms_ccy}",
            is_inverted=is_inverted,
            normalized_bid=round(norm_bid, 5),
            normalized_ask=round(norm_ask, 5),
            pip_size=pip_size,
            spread_pips=spread_pips,
            base_currency=base_ccy,
            terms_currency=terms_ccy
        )
