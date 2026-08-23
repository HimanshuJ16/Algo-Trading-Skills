"""FX quoting-convention normalization: base/terms ordering, quote inversion, pip sizing.

Scope note: ISO 4217 supplies the three-letter currency codes used here, but it
defines **no** base/terms ordering for currency pairs - its scope is limited to
"the structure for a three-letter alphabetic code and an equivalent three-digit
numeric code for the representation of currencies". The ordering applied by this
module is a de-facto interbank convention, not a standard. See
``references/standards.md`` for the evidence behind each ranking.

Because the ordering is conventional rather than normative, this module refuses
to invert a pair it cannot rank: an unrecognised currency yields an
``UNCLASSIFIED`` report and the vendor's quote is passed through untouched.
Inverting a pair you cannot rank is destructive (XAU/USD at 2000.10 becomes
USD/XAU at 0.0005); passing it through flagged is not.
"""

import logging
import math
import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# De-facto interbank base/terms ranking for the major currencies (index 0 ranks
# highest, i.e. is the base currency of any pair it appears in). NOT an ISO 4217
# artifact - ISO 4217 defines codes only. Sourced in references/standards.md.
# Deliberately limited to the majors: a currency absent from this list is
# reported UNCLASSIFIED rather than assumed to rank last. Extend it via the
# `priority_list` constructor argument for your own traded universe.
DEFAULT_CURRENCY_PRIORITY: Tuple[str, ...] = (
    "EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY",
)

# Terms currencies conventionally quoted to two decimal places, for which one
# pip is 0.01 rather than 0.0001. JPY is the established case; add your own if
# you trade a terms currency with the same convention.
DEFAULT_TWO_DECIMAL_TERMS: FrozenSet[str] = frozenset({"JPY"})

PIP_SIZE_STANDARD = 0.0001
PIP_SIZE_TWO_DECIMAL = 0.01

CLASSIFICATION_STANDARD = "STANDARD"
CLASSIFICATION_INVERTED = "INVERTED"
CLASSIFICATION_UNCLASSIFIED = "UNCLASSIFIED"

_CURRENCY_CODE_RE = re.compile(r"^[A-Z]{3}$")
# Separators seen across vendor symbologies: EUR/USD, EUR_USD, EUR-USD,
# EUR.USD, EUR:USD, "EUR USD", or bare EURUSD.
_SEPARATOR_RE = re.compile(r"[/\\_\-.: \t]")


def _require_positive_price(value: float, label: str) -> float:
    """Validate a quoted FX price: real, finite, strictly positive.

    NaN is the dangerous case. It propagates silently through subtraction and
    division, so an unvalidated NaN bid produces a NaN spread that compares
    False against every downstream risk threshold. Non-positive prices are
    equally invalid: an FX rate is a ratio of two positive amounts, and a zero
    or negative price makes the inversion 1/price meaningless or sign-flipping.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a real number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite, got {value!r}")
    if value <= 0.0:
        raise ValueError(f"{label} must be strictly positive, got {value!r}")
    return float(value)


@dataclass
class RawFxQuote:
    """A quote exactly as received from a vendor, before normalization."""

    raw_symbol: str                    # e.g. 'EUR/USD', 'USD/EUR', 'USDJPY'
    bid_price: float
    ask_price: float
    vendor_id: str


@dataclass
class NormalizedFxQuoteReport:
    """Normalization outcome for one quote.

    ``pip_size`` and ``spread_pips`` are ``None`` when the pair is
    ``UNCLASSIFIED``: the pip convention follows from knowing the terms
    currency's quoting convention, and this module will not invent one.
    ``spread_price`` is always populated - it is just ask minus bid in units of
    the terms currency, which needs no convention.

    Prices are reported at full float precision. Rounding a normalized price
    before publishing it is lossy and would leave ``spread_pips`` inconsistent
    with ``(normalized_ask - normalized_bid) / pip_size``.
    """

    raw_symbol: str
    normalized_symbol: str             # market-standard Base/Terms symbol
    is_inverted: bool
    normalized_bid: float
    normalized_ask: float
    pip_size: Optional[float]
    spread_pips: Optional[float]
    base_currency: str
    terms_currency: str
    # Added so a consumer can tell "verified standard order" apart from "could
    # not be ranked, left as the vendor sent it". Defaulted to keep positional
    # construction working.
    classification: str = CLASSIFICATION_STANDARD
    spread_price: float = 0.0
    is_crossed: bool = False


class CurrencyPairQuotingNormalizer:
    """Normalizes vendor FX quotes to the conventional base/terms ordering.

    The ranking is conventional, not normative, so the classifier is explicit
    about its own coverage:

    * both currencies ranked, already in order  -> ``STANDARD``, passed through
    * both currencies ranked, out of order      -> ``INVERTED``, flipped and
      cross-inverted
    * either currency unranked                  -> ``UNCLASSIFIED``, passed
      through untouched with no pip arithmetic, and a warning logged

    The third branch is the safety property that matters. Treating an unranked
    currency as "lowest priority" would invert XAU/USD (an ISO 4217 metal code,
    quoted by the LBMA as US dollars per troy ounce) into USD/XAU at 0.0005,
    and BTC/USD into USD/BTC at 0.0000167 - silent, catastrophic corruption of
    a price feed. Leaving an unrankable pair alone is recoverable; inverting it
    wrongly is not.
    """

    def __init__(
        self,
        priority_list: Optional[Sequence[str]] = None,
        two_decimal_terms_currencies: Optional[Sequence[str]] = None,
        pip_size_overrides: Optional[Dict[str, float]] = None,
    ):
        """
        Args:
            priority_list: Base/terms ranking, highest-ranked first. Defaults to
                the eight majors in `DEFAULT_CURRENCY_PRIORITY`. Every entry must
                be a distinct three-letter alphabetic code; a duplicate would
                make the ranking ambiguous and is rejected.
            two_decimal_terms_currencies: Terms currencies whose pip is 0.01.
                Defaults to `{"JPY"}`.
            pip_size_overrides: Explicit pip size per normalized pair symbol
                (e.g. ``{"XAU/USD": 0.01}``). Consulted before the terms-currency
                rule, so a non-FX pair can carry its own convention instead of
                inheriting the 0.0001 default.
        """
        self.priority_list: List[str] = self._validate_priority_list(
            priority_list if priority_list is not None else DEFAULT_CURRENCY_PRIORITY
        )
        self.two_decimal_terms_currencies: FrozenSet[str] = (
            DEFAULT_TWO_DECIMAL_TERMS
            if two_decimal_terms_currencies is None
            else frozenset(self._validate_code(c, "two_decimal_terms_currencies")
                           for c in two_decimal_terms_currencies)
        )
        self.pip_size_overrides: Dict[str, float] = self._validate_overrides(
            pip_size_overrides or {}
        )

    # ------------------------------------------------------------------ #
    # Configuration validation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_code(code: str, label: str) -> str:
        if not isinstance(code, str):
            raise TypeError(f"{label} entries must be strings, got {code!r}")
        upper = code.strip().upper()
        if not _CURRENCY_CODE_RE.match(upper):
            raise ValueError(
                f"{label} entry {code!r} is not a three-letter alphabetic currency code"
            )
        return upper

    def _validate_priority_list(self, codes: Sequence[str]) -> List[str]:
        validated = [self._validate_code(c, "priority_list") for c in codes]
        if not validated:
            raise ValueError("priority_list must contain at least one currency code")
        duplicates = {c for c in validated if validated.count(c) > 1}
        if duplicates:
            # list.index() silently returns the first hit, so a duplicate would
            # define a rank that no reader of the list could predict.
            raise ValueError(
                f"priority_list contains duplicate codes {sorted(duplicates)}; "
                "each currency must appear exactly once"
            )
        return validated

    def _validate_overrides(self, overrides: Dict[str, float]) -> Dict[str, float]:
        if not isinstance(overrides, dict):
            raise TypeError("pip_size_overrides must be a dict of symbol -> pip size")
        validated: Dict[str, float] = {}
        for symbol, pip in overrides.items():
            if not isinstance(symbol, str) or "/" not in symbol:
                raise ValueError(
                    f"pip_size_overrides key {symbol!r} must be a 'BASE/TERMS' symbol"
                )
            if isinstance(pip, bool) or not isinstance(pip, (int, float)):
                raise TypeError(f"pip_size_overrides[{symbol!r}] must be a number")
            if not math.isfinite(pip) or pip <= 0.0:
                raise ValueError(
                    f"pip_size_overrides[{symbol!r}] must be finite and positive, got {pip!r}"
                )
            validated[symbol.strip().upper()] = float(pip)
        return validated

    # ------------------------------------------------------------------ #
    # Parsing and ranking
    # ------------------------------------------------------------------ #
    def _get_currency_priority(self, ccy: str) -> Optional[int]:
        """Rank of `ccy`, or None if this normalizer cannot rank it.

        None means "unknown", not "lowest". Collapsing the two is the bug that
        inverts gold and crypto quotes.
        """
        try:
            return self.priority_list.index(ccy.upper())
        except ValueError:
            return None

    def parse_symbol(self, raw_symbol: str) -> Tuple[str, str]:
        """Split a vendor symbol into (currency_1, currency_2).

        Accepts the common separators and the bare six-character form. Both
        halves must be three-letter alphabetic codes: a four-or-more-letter
        leg (``BTC/USDT``, ``USDT/EUR``) is rejected rather than mis-split,
        and a six-character string that is not two currency codes
        (``123456``) is rejected rather than parsed into nonsense.
        """
        if not isinstance(raw_symbol, str):
            raise TypeError(f"raw_symbol must be a string, got {type(raw_symbol).__name__}")
        cleaned = _SEPARATOR_RE.sub("", raw_symbol).upper()
        if len(cleaned) != 6:
            raise ValueError(
                f"Unable to parse two 3-letter currency codes from {raw_symbol!r}: "
                f"got {len(cleaned)} characters after removing separators. Symbols with "
                "non-3-letter legs (e.g. crypto tickers like BTC/USDT) are out of scope."
            )
        c1, c2 = cleaned[:3], cleaned[3:]
        for code in (c1, c2):
            if not _CURRENCY_CODE_RE.match(code):
                raise ValueError(
                    f"{code!r} parsed from {raw_symbol!r} is not a three-letter "
                    "alphabetic currency code"
                )
        if c1 == c2:
            raise ValueError(
                f"{raw_symbol!r} names the same currency on both legs ({c1}); "
                "this is not a tradeable pair"
            )
        return c1, c2

    # ------------------------------------------------------------------ #
    # Normalization
    # ------------------------------------------------------------------ #
    def _resolve_pip_size(self, symbol: str, terms_ccy: str) -> float:
        if symbol in self.pip_size_overrides:
            return self.pip_size_overrides[symbol]
        if terms_ccy in self.two_decimal_terms_currencies:
            return PIP_SIZE_TWO_DECIMAL
        return PIP_SIZE_STANDARD

    def normalize_quote(self, quote: RawFxQuote) -> NormalizedFxQuoteReport:
        """Rank the pair, invert it if the vendor sent it backwards, and size the spread.

        Raises:
            TypeError: `raw_symbol` is not a string, or a price is not a number.
            ValueError: the symbol is unparseable or names one currency twice,
                or a price is non-finite or non-positive.
        """
        if not isinstance(quote, RawFxQuote):
            raise TypeError(f"quote must be a RawFxQuote, got {type(quote).__name__}")

        c1, c2 = self.parse_symbol(quote.raw_symbol)
        # Both prices are validated before any branch: the original code checked
        # only the inversion path, so a NaN or negative price on an
        # already-standard pair flowed straight into the report.
        bid = _require_positive_price(quote.bid_price, f"{quote.raw_symbol} bid_price")
        ask = _require_positive_price(quote.ask_price, f"{quote.raw_symbol} ask_price")

        p1 = self._get_currency_priority(c1)
        p2 = self._get_currency_priority(c2)

        if p1 is None or p2 is None:
            unranked = [c for c, p in ((c1, p1), (c2, p2)) if p is None]
            logger.warning(
                "UNCLASSIFIED FX PAIR [%s] from vendor %s: %s not in the configured "
                "priority list. Passing the quote through unchanged and omitting pip "
                "arithmetic; add the currency to `priority_list` to have it ranked.",
                quote.raw_symbol, quote.vendor_id, ", ".join(unranked),
            )
            classification = CLASSIFICATION_UNCLASSIFIED
            base_ccy, terms_ccy = c1, c2
            norm_bid, norm_ask = bid, ask
        elif p1 < p2:
            classification = CLASSIFICATION_STANDARD
            base_ccy, terms_ccy = c1, c2
            norm_bid, norm_ask = bid, ask
        else:
            classification = CLASSIFICATION_INVERTED
            base_ccy, terms_ccy = c2, c1
            # Cross-inversion: the best price at which the market buys the base
            # is the reciprocal of the best price at which it sells the terms.
            # Using 1/bid for the new bid would fabricate a narrower or negative
            # spread.
            norm_bid = 1.0 / ask
            norm_ask = 1.0 / bid
            logger.warning(
                "INVERTED FX QUOTE [%s] from vendor %s: flipped to %s/%s. "
                "Raw (%.6f/%.6f) -> normalized (%.6f/%.6f).",
                quote.raw_symbol, quote.vendor_id, base_ccy, terms_ccy,
                bid, ask, norm_bid, norm_ask,
            )

        normalized_symbol = f"{base_ccy}/{terms_ccy}"
        spread_price = norm_ask - norm_bid
        is_crossed = norm_bid > norm_ask
        if is_crossed:
            # Inversion preserves crossing, so this always reflects the vendor's
            # own data rather than an artefact of normalization. Momentary
            # crossed/locked books are real, so flag rather than reject.
            logger.warning(
                "CROSSED QUOTE [%s] from vendor %s: normalized bid %.6f exceeds ask %.6f.",
                normalized_symbol, quote.vendor_id, norm_bid, norm_ask,
            )

        if classification == CLASSIFICATION_UNCLASSIFIED:
            pip_size: Optional[float] = self.pip_size_overrides.get(normalized_symbol)
            spread_pips: Optional[float] = (
                round(spread_price / pip_size, 2) if pip_size is not None else None
            )
        else:
            pip_size = self._resolve_pip_size(normalized_symbol, terms_ccy)
            spread_pips = round(spread_price / pip_size, 2)

        return NormalizedFxQuoteReport(
            raw_symbol=quote.raw_symbol,
            normalized_symbol=normalized_symbol,
            is_inverted=(classification == CLASSIFICATION_INVERTED),
            normalized_bid=norm_bid,
            normalized_ask=norm_ask,
            pip_size=pip_size,
            spread_pips=spread_pips,
            base_currency=base_ccy,
            terms_currency=terms_ccy,
            classification=classification,
            spread_price=spread_price,
            is_crossed=is_crossed,
        )
