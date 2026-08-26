"""
multi-exchange-feed-normalization:
Maps heterogeneous venue trade-tick payloads onto one canonical ``UnifiedTick``
with a single symbol namespace, a single side convention, and a single timestamp
timescale.

Design rule: never fabricate a field
------------------------------------
Every parse failure raises ``NormalizationError``. Nothing in this module
substitutes a plausible-looking default for a value it could not read. That rule
exists because the three defaults a normalizer is tempted to reach for are each
a silent, expensive corruption:

  * ``price = 0.0`` for a missing price. A zero print reaching a strategy trips
    stop-losses, poisons VWAP and produces infinite returns, and no downstream
    consumer can distinguish it from a real quote.
  * ``side = BUY`` for a missing aggressor flag. Order-flow imbalance is a signed
    sum; a fabricated side biases it in a fixed direction rather than adding
    noise. ``UNKNOWN`` is emitted instead, and consumers must handle it.
  * ``exchange_timestamp = time.time()`` for an unparseable timestamp. This is
    the worst of the three: it makes ``receipt - exchange`` latency collapse to
    roughly zero, so a stale or corrupt feed reads as a perfectly healthy one and
    every staleness and clock-skew monitor downstream goes blind.

Side convention
---------------
``UnifiedTick.side`` is always the **aggressor (taker)** side. Venues do not
agree on this, and the disagreement is invisible if you map the venue field
straight through:

  * Binance ``m`` is documented "Is the buyer the market maker?", so ``m=true``
    means a resting buyer was hit and the aggressor was the **seller**.
  * Coinbase documents ``side`` on both the Exchange ``matches`` channel and the
    Advanced Trade ``market_trades`` channel as the **maker** order side ("If
    the side is ``sell`` this indicates the maker was a sell order").
    ``parse_coinbase`` therefore **inverts** it.

Mapping Coinbase's field through unchanged while deriving Binance's from ``m``
yields a normalized ``side`` whose sign is flipped for one of the two venues, so
any cross-venue order-flow imbalance built on it is wrong in a way that
single-venue unit tests cannot show. ``test_feed_normalizer.py`` pins the
cross-venue case directly.

Timestamps
----------
Epoch units are detected from magnitude against disjoint bands, or declared
explicitly per venue via ``TimestampUnit``. The bands for seconds, milliseconds,
microseconds and nanoseconds are three orders of magnitude apart, so for any
instant between 2001-09-09 and 2100-01-01 exactly one band matches and detection
is unambiguous. A value matching no band raises rather than being rescaled on a
guess.

Explicit units matter because magnitude alone is not a safe signal in general:
Binance serves the same ``trade``/``aggTrade`` streams in microseconds when the
connection carries ``timeUnit=MICROSECOND``, and the naive "divide by 1000 if
large" rule turns those into timestamps in the year ~55839 without error.

Naive (timezone-less) timestamps carry no recoverable instant, so the assumed
zone is an explicit constructor argument, ``naive_timestamp_tz``. This is not
cosmetic for this skill's venue set: ``pykiteconnect`` builds ``last_trade_time``
with ``datetime.fromtimestamp(epoch)``, which yields a naive datetime in the
*recording host's* local zone.

Scope
-----
Trade prints only. This module does not sequence, deduplicate, gap-detect or
order the stream -- see ``sequence-number-gap-detection-for-feeds`` and
``multi-source-price-reconciliation-tie-breaking``.
"""
from dataclasses import dataclass, field
import datetime
from enum import Enum
import logging
import math
import re
import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "NormalizationError",
    "NormalizedSide",
    "TimestampUnit",
    "UnifiedTick",
    "TickNormalizerRegistry",
]

#: Plausible epoch window, in seconds, used both to identify a timestamp's unit
#: and to reject values that are not timestamps at all. Lower bound 2001-09-09
#: (1e9 s); upper bound 2100-01-01. The per-unit bands derived from this window
#: are separated by three orders of magnitude and therefore never overlap.
_EPOCH_MIN_S = 1_000_000_000.0
_EPOCH_MAX_S = 4_102_444_800.0

#: Ticks per second for each supported epoch unit.
_UNIT_TICKS_PER_SECOND: Dict[str, float] = {
    "s": 1.0,
    "ms": 1e3,
    "us": 1e6,
    "ns": 1e9,
}

#: ``datetime.fromisoformat`` accepts at most 6 fractional-second digits before
#: Python 3.11. Venue feeds do emit 9 (nanoseconds), so the fraction is truncated
#: rather than allowed to raise.
_ISO_FRACTION_RE = re.compile(r"\.(\d+)")


class NormalizationError(ValueError):
    """
    Raised when a tick payload cannot be normalized.

    Subclasses ``ValueError`` so existing ``except ValueError`` handlers keep
    working. Every failure path in this module raises this type specifically, so
    a feed handler can catch one class at the venue boundary and route the
    offending payload to a dead-letter queue, instead of guessing which of
    ``KeyError`` / ``TypeError`` / ``ValueError`` a given venue parser might leak.
    """


class NormalizedSide(str, Enum):
    """Aggressor (taker) side of a trade. ``UNKNOWN`` is a real, expected value."""

    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


class TimestampUnit(str, Enum):
    """Epoch unit of a venue's numeric timestamps."""

    AUTO = "auto"
    SECONDS = "s"
    MILLISECONDS = "ms"
    MICROSECONDS = "us"
    NANOSECONDS = "ns"


@dataclass
class UnifiedTick:
    """
    One canonical trade print.

    Attributes:
        symbol: Canonical cross-venue instrument symbol.
        venue: Lower-cased venue identifier the tick was parsed from.
        price: Trade price in the instrument's quote currency. Always finite.
        quantity: Trade size in instrument units. Always finite and strictly
            positive -- a zero-size trade print is a data error at every venue
            this module parses, and admitting one divides by zero in VWAP.
        side: Aggressor (taker) side, never the maker side. See module docstring.
        exchange_timestamp: Venue-stamped trade time, epoch seconds (UTC).
        receipt_timestamp: Local wall-clock time the payload was received, epoch
            seconds. Defaults to construction time, which is only correct when
            the tick is built inline on the socket-read path. Anything that
            queues raw payloads before parsing must capture arrival time at read
            and pass it into ``normalize()``; otherwise this measures dequeue
            time and every latency number derived from it silently includes the
            queue.

    Non-negotiable invariants (finite fields, positive quantity, in-range
    timestamps) are enforced here rather than in the parsers, so a custom parser
    installed via ``register_parser`` cannot emit a malformed tick. Price *sign*
    policy is a registry-level decision and lives there.
    """

    symbol: str
    venue: str
    price: float
    quantity: float
    side: NormalizedSide
    exchange_timestamp: float
    receipt_timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.symbol:
            raise NormalizationError("UnifiedTick.symbol must be a non-empty string.")
        for name in ("price", "quantity", "exchange_timestamp", "receipt_timestamp"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise NormalizationError(f"UnifiedTick.{name} must be numeric, got {value!r}.")
            value = float(value)
            if not math.isfinite(value):
                raise NormalizationError(f"UnifiedTick.{name} must be finite, got {value!r}.")
            setattr(self, name, value)
        if self.quantity <= 0.0:
            raise NormalizationError(
                f"UnifiedTick.quantity must be strictly positive, got {self.quantity!r}. "
                "A zero-size trade print is a data error, not a trade."
            )
        for name in ("exchange_timestamp", "receipt_timestamp"):
            value = getattr(self, name)
            if not _EPOCH_MIN_S <= value <= _EPOCH_MAX_S:
                raise NormalizationError(
                    f"UnifiedTick.{name}={value!r} is outside the plausible epoch-seconds range "
                    f"[{_EPOCH_MIN_S}, {_EPOCH_MAX_S}]. This usually means a unit mismatch "
                    "(ms/us/ns passed as seconds)."
                )
        if not isinstance(self.side, NormalizedSide):
            raise NormalizationError(f"UnifiedTick.side must be a NormalizedSide, got {self.side!r}.")


#: A venue parser: ``(payload, receipt_timestamp) -> UnifiedTick``.
ParserFn = Callable[[Mapping[str, Any], Optional[float]], UnifiedTick]


class TickNormalizerRegistry:
    """
    Parses heterogeneous venue tick payloads into ``UnifiedTick`` objects.

    Args:
        strict_symbols: When ``True`` (default), a symbol with no registered
            mapping raises instead of being passed through. Pass-through is what
            turns ``BTCUSDT`` from Binance and ``BTC-USD`` from Coinbase into two
            unrelated instruments in a consolidated book -- the same tradable
            silently double-counted, with per-venue prices never compared. That
            failure stays invisible until a position report disagrees with the
            broker, so an unmapped symbol is treated as a configuration error.
            Set ``False`` only for exploratory work; the fallback is logged.
        naive_timestamp_tz: Timezone assumed for timestamps carrying no offset
            (naive ``datetime`` objects and offset-less ISO strings). Defaults to
            UTC. Pass ``None`` to mean "host local", which is the correct choice
            for ``pykiteconnect``, whose ``last_trade_time`` is built by
            ``datetime.fromtimestamp(epoch)`` and is therefore naive in the
            recording host's local zone -- ``None`` inverts that exactly.
        allow_non_positive_price: When ``False`` (default), a price of zero or
            below is rejected. Set ``True`` only for instruments where a
            non-positive price is economically real -- notably energy futures
            after CME enabled negative pricing on Globex in April 2020, when the
            NYMEX WTI May-2020 contract settled at -$37.63/bbl. It is not real
            for any of the crypto or cash-equity venues parsed here, where a
            non-positive price means a corrupt payload.
    """

    def __init__(
        self,
        strict_symbols: bool = True,
        naive_timestamp_tz: Optional[datetime.tzinfo] = datetime.timezone.utc,
        allow_non_positive_price: bool = False,
    ) -> None:
        self.symbol_mappings: Dict[Tuple[str, str], str] = {}  # (venue, raw_symbol) -> canonical
        self.strict_symbols = strict_symbols
        self.naive_timestamp_tz = naive_timestamp_tz
        self.allow_non_positive_price = allow_non_positive_price
        self._parsers: Dict[str, ParserFn] = {}
        self._timestamp_units: Dict[str, TimestampUnit] = {}
        self.register_parser("binance", self.parse_binance)
        self.register_parser("coinbase", self.parse_coinbase)
        self.register_parser("zerodha", self.parse_zerodha)

    # ---------------------------------------------------------------- parsers

    def register_parser(
        self,
        venue: str,
        parser: ParserFn,
        timestamp_unit: TimestampUnit = TimestampUnit.AUTO,
    ) -> None:
        """
        Install (or replace) the parser for ``venue``.

        Venues beyond the three built in are added here rather than by editing a
        dispatch chain, so adding a venue does not require forking this module.
        ``timestamp_unit`` declares the venue's numeric epoch unit; leave it
        ``AUTO`` unless the venue can emit timestamps outside the plausible epoch
        window, or you want a mis-configured feed to fail loudly on the first
        tick rather than at the first out-of-band value.
        """
        if not callable(parser):
            raise NormalizationError(f"Parser for venue '{venue}' is not callable.")
        key = venue.lower()
        if key in self._parsers:
            logger.warning("Replacing existing parser for venue '%s'.", key)
        self._parsers[key] = parser
        self._timestamp_units[key] = TimestampUnit(timestamp_unit)

    def supported_venues(self) -> Tuple[str, ...]:
        """Venues with a registered parser, sorted."""
        return tuple(sorted(self._parsers))

    # ---------------------------------------------------------------- symbols

    def register_symbol_mapping(self, venue: str, raw_symbol: str, canonical_symbol: str) -> None:
        """Map one venue-specific ticker onto a canonical cross-venue symbol."""
        if not raw_symbol or not canonical_symbol:
            raise NormalizationError(
                f"Symbol mapping for venue '{venue}' requires non-empty raw and canonical symbols."
            )
        self.symbol_mappings[(venue.lower(), raw_symbol.upper())] = canonical_symbol.upper()

    def get_canonical_symbol(self, venue: str, raw_symbol: str) -> str:
        """
        Resolve a venue ticker to its canonical symbol.

        Raises:
            NormalizationError: if the symbol is empty, or is unmapped while
                ``strict_symbols`` is set.
        """
        if not raw_symbol:
            raise NormalizationError(f"Venue '{venue}' payload carries no instrument symbol.")
        key = (venue.lower(), raw_symbol.upper())
        mapped = self.symbol_mappings.get(key)
        if mapped is not None:
            return mapped
        if self.strict_symbols:
            raise NormalizationError(
                f"No canonical symbol registered for venue '{venue}' symbol '{raw_symbol}'. "
                "Register it with register_symbol_mapping(), or construct the registry with "
                "strict_symbols=False to pass unmapped tickers through (which risks the same "
                "instrument appearing under two symbols in a consolidated view)."
            )
        logger.warning(
            "Unmapped symbol '%s' on venue '%s' passed through verbatim; it will not "
            "consolidate with the same instrument from another venue.",
            raw_symbol,
            venue,
        )
        return raw_symbol.upper()

    # ------------------------------------------------------------- primitives

    @staticmethod
    def _require(payload: Mapping[str, Any], *names: str) -> Tuple[str, Any]:
        """Return ``(field_name, value)`` for the first present, non-``None`` field."""
        for name in names:
            if payload.get(name) is not None:
                return name, payload[name]
        raise NormalizationError(
            f"Payload is missing all of the expected fields {names!r}; "
            f"keys present: {sorted(payload)!r}."
        )

    @staticmethod
    def _to_float(value: Any, field_name: str) -> float:
        """Parse a venue numeric (string or number) into a finite float."""
        if isinstance(value, bool):
            raise NormalizationError(f"Field '{field_name}' is a bool ({value!r}), not a number.")
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise NormalizationError(f"Field '{field_name}' is not numeric: {value!r}.") from exc
        if not math.isfinite(parsed):
            # float("nan") and float("inf") both succeed, and a NaN price then
            # propagates through every downstream comparison silently as False.
            raise NormalizationError(f"Field '{field_name}' is not finite: {value!r}.")
        return parsed

    def _parse_price(self, payload: Mapping[str, Any], *names: str) -> float:
        name, raw = self._require(payload, *names)
        price = self._to_float(raw, name)
        if price <= 0.0 and not self.allow_non_positive_price:
            raise NormalizationError(
                f"Field '{name}' price {price!r} is not strictly positive. Set "
                "allow_non_positive_price=True only for instruments where that is real "
                "(e.g. energy futures since CME enabled negative pricing in April 2020)."
            )
        return price

    def _parse_quantity(self, payload: Mapping[str, Any], *names: str) -> float:
        name, raw = self._require(payload, *names)
        return self._to_float(raw, name)

    @staticmethod
    def _epoch_from_number(value: float, unit: TimestampUnit, field_name: str) -> float:
        """
        Convert a numeric epoch in ``unit`` to epoch seconds.

        With ``AUTO`` the unit is inferred from magnitude. The candidate bands sit
        three decades apart, so at most one matches and the inference cannot be
        ambiguous for any instant inside the plausible epoch window. A value
        matching none of them is rejected rather than rescaled on a guess.
        """
        if unit is not TimestampUnit.AUTO:
            seconds = value / _UNIT_TICKS_PER_SECOND[unit.value]
            if not _EPOCH_MIN_S <= seconds <= _EPOCH_MAX_S:
                raise NormalizationError(
                    f"Field '{field_name}' value {value!r} declared as '{unit.value}' resolves to "
                    f"{seconds!r} epoch seconds, outside the plausible range."
                )
            return seconds
        for ticks in _UNIT_TICKS_PER_SECOND.values():
            seconds = value / ticks
            if _EPOCH_MIN_S <= seconds <= _EPOCH_MAX_S:
                return seconds
        raise NormalizationError(
            f"Field '{field_name}' value {value!r} falls in no supported epoch band (seconds, "
            "milliseconds, microseconds or nanoseconds) of the plausible window "
            "[2001-09-09, 2100-01-01]. Declare the unit explicitly via register_parser() if the "
            "venue really emits this scale."
        )

    def _datetime_to_epoch(self, value: datetime.datetime) -> float:
        """Convert a datetime to epoch seconds, resolving naive values explicitly."""
        if value.tzinfo is not None:
            return value.timestamp()
        if self.naive_timestamp_tz is None:
            # "Host local" -- the exact inverse of datetime.fromtimestamp(epoch),
            # which is how pykiteconnect builds last_trade_time.
            return value.timestamp()
        return value.replace(tzinfo=self.naive_timestamp_tz).timestamp()

    def _coerce_timestamp(
        self,
        ts_val: Any,
        field_name: str = "timestamp",
        unit: TimestampUnit = TimestampUnit.AUTO,
    ) -> float:
        """
        Coerce a venue timestamp to epoch seconds (UTC).

        Accepts ``datetime`` objects, numeric epochs, numeric-looking strings
        (including float-seconds such as ``"1700000000.123"``) and ISO-8601
        strings with a ``Z`` or numeric offset. Raises on anything else; it never
        substitutes the current time, because a fabricated exchange timestamp
        makes a stale feed indistinguishable from a healthy one.
        """
        if ts_val is None:
            raise NormalizationError(
                f"Field '{field_name}' is missing; refusing to fabricate a timestamp."
            )
        if isinstance(ts_val, bool):
            raise NormalizationError(f"Field '{field_name}' is a bool ({ts_val!r}), not a timestamp.")
        if isinstance(ts_val, datetime.datetime):
            return self._datetime_to_epoch(ts_val)
        if isinstance(ts_val, (int, float)):
            numeric = float(ts_val)
            if not math.isfinite(numeric):
                raise NormalizationError(f"Field '{field_name}' is not finite: {ts_val!r}.")
            return self._epoch_from_number(numeric, unit, field_name)
        if isinstance(ts_val, str):
            text = ts_val.strip()
            if not text:
                raise NormalizationError(f"Field '{field_name}' is an empty string.")
            try:
                # Covers "1700000000", "1700000000.123" and "1.7e12" alike.
                # str.isdigit() is not a usable guard here: it rejects
                # float-seconds strings and accepts superscripts such as
                # "²" that float() then rejects.
                numeric = float(text)
            except ValueError:
                return self._iso_to_epoch(text, field_name)
            if not math.isfinite(numeric):
                # float("nan") and float("inf") parse successfully from strings.
                raise NormalizationError(f"Field '{field_name}' is not finite: {ts_val!r}.")
            return self._epoch_from_number(numeric, unit, field_name)
        raise NormalizationError(
            f"Field '{field_name}' has unsupported timestamp type "
            f"{type(ts_val).__name__}: {ts_val!r}."
        )

    def _iso_to_epoch(self, text: str, field_name: str) -> float:
        """Parse an ISO-8601 timestamp, tolerating ``Z`` and sub-microsecond fractions."""
        candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        match = _ISO_FRACTION_RE.search(candidate)
        if match and len(match.group(1)) > 6:
            # e.g. nanosecond prints from ITCH-derived feeds; fromisoformat
            # rejects more than 6 fractional digits before Python 3.11.
            candidate = candidate[: match.start(1) + 6] + candidate[match.end(1) :]
        try:
            parsed = datetime.datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise NormalizationError(
                f"Field '{field_name}' is neither numeric nor ISO-8601: {text!r}."
            ) from exc
        return self._datetime_to_epoch(parsed)

    def _build(
        self,
        symbol: str,
        venue: str,
        price: float,
        quantity: float,
        side: NormalizedSide,
        exchange_timestamp: float,
        receipt_timestamp: Optional[float],
    ) -> UnifiedTick:
        return UnifiedTick(
            symbol=symbol,
            venue=venue,
            price=price,
            quantity=quantity,
            side=side,
            exchange_timestamp=exchange_timestamp,
            receipt_timestamp=time.time() if receipt_timestamp is None else receipt_timestamp,
        )

    # --------------------------------------------------------- venue parsers

    def parse_binance(
        self,
        payload: Mapping[str, Any],
        receipt_timestamp: Optional[float] = None,
    ) -> UnifiedTick:
        """
        Parse a Binance spot ``trade`` / ``aggTrade`` WebSocket payload.

        Fields per the Binance spot WebSocket streams spec: ``s`` symbol, ``p``
        price, ``q`` quantity, ``T`` trade time, ``E`` event time, ``m`` "Is the
        buyer the market maker?".

        ``T`` (trade time) is preferred over ``E`` (event time, i.e. when the
        server emitted the message); falling back to ``E`` is logged, because the
        two differ by the venue's internal publish delay and quietly mixing them
        contaminates any latency measurement built on the result.

        ``m`` is inverted to the aggressor side: the buyer being the maker means
        a resting bid was hit, so the aggressor was the seller. A payload with no
        ``m`` yields ``UNKNOWN`` rather than a default side.
        """
        _, raw_symbol = self._require(payload, "s")
        price = self._parse_price(payload, "p")
        quantity = self._parse_quantity(payload, "q")

        if payload.get("T") is not None:
            ts_field = "T"
        else:
            ts_field = "E"
            logger.warning("Binance payload has no trade time 'T'; falling back to event time 'E'.")
        _, ts_raw = self._require(payload, ts_field)
        unit = self._timestamp_units.get("binance", TimestampUnit.AUTO)
        exchange_timestamp = self._coerce_timestamp(ts_raw, ts_field, unit)

        is_buyer_maker = payload.get("m")
        if is_buyer_maker is None:
            logger.warning("Binance payload has no 'm' flag; aggressor side is UNKNOWN.")
            side = NormalizedSide.UNKNOWN
        elif not isinstance(is_buyer_maker, bool):
            raise NormalizationError(f"Binance field 'm' must be a bool, got {is_buyer_maker!r}.")
        else:
            side = NormalizedSide.SELL if is_buyer_maker else NormalizedSide.BUY

        return self._build(
            symbol=self.get_canonical_symbol("binance", str(raw_symbol)),
            venue="binance",
            price=price,
            quantity=quantity,
            side=side,
            exchange_timestamp=exchange_timestamp,
            receipt_timestamp=receipt_timestamp,
        )

    def parse_coinbase(
        self,
        payload: Mapping[str, Any],
        receipt_timestamp: Optional[float] = None,
    ) -> UnifiedTick:
        """
        Parse a Coinbase Exchange ``matches`` or Advanced Trade ``market_trades``
        payload.

        Coinbase documents ``side`` as the **maker** order side on both channels,
        so it is inverted here to produce the aggressor side that
        ``UnifiedTick.side`` promises. If your Coinbase data arrives via an
        aggregator that has already flipped it to taker convention, do not feed
        it here unflipped -- the two inversions cancel and the side is wrong
        again.
        """
        _, raw_symbol = self._require(payload, "product_id", "symbol")
        price = self._parse_price(payload, "price")
        quantity = self._parse_quantity(payload, "size", "last_size")
        _, ts_raw = self._require(payload, "time")
        unit = self._timestamp_units.get("coinbase", TimestampUnit.AUTO)
        exchange_timestamp = self._coerce_timestamp(ts_raw, "time", unit)

        maker_side = payload.get("side")
        if maker_side is None:
            logger.warning("Coinbase payload has no 'side' field; aggressor side is UNKNOWN.")
            side = NormalizedSide.UNKNOWN
        else:
            maker = str(maker_side).strip().upper()
            if maker == "BUY":
                side = NormalizedSide.SELL  # maker bought => taker sold
            elif maker == "SELL":
                side = NormalizedSide.BUY  # maker sold => taker bought
            else:
                logger.warning(
                    "Coinbase 'side' value %r is not BUY/SELL; aggressor side is UNKNOWN.",
                    maker_side,
                )
                side = NormalizedSide.UNKNOWN

        return self._build(
            symbol=self.get_canonical_symbol("coinbase", str(raw_symbol)),
            venue="coinbase",
            price=price,
            quantity=quantity,
            side=side,
            exchange_timestamp=exchange_timestamp,
            receipt_timestamp=receipt_timestamp,
        )

    def parse_zerodha(
        self,
        payload: Mapping[str, Any],
        receipt_timestamp: Optional[float] = None,
    ) -> UnifiedTick:
        """
        Parse a Zerodha Kite tick.

        ``pykiteconnect``'s WebSocket ticker emits ``last_traded_quantity`` and
        ``volume_traded``; the REST quote endpoint calls the same two fields
        ``last_quantity`` and ``volume``. Both spellings of the *trade size* are
        accepted. Neither spelling of *volume* is: those are cumulative session
        volume, and using a running day total as a per-tick trade size inflates
        every volume-weighted statistic downstream by orders of magnitude.

        ``last_trade_time`` arrives as a naive ``datetime`` built by
        ``datetime.fromtimestamp(epoch)``, i.e. in the recording host's local
        zone. Construct the registry with ``naive_timestamp_tz=None`` when
        consuming ``pykiteconnect`` objects directly, so the conversion inverts
        that exactly.

        Kite ticks carry no aggressor flag, so ``side`` is always ``UNKNOWN``. Do
        not infer it from tick direction: an uptick identifies the trade's price
        relative to the previous print, not which counterparty crossed.
        """
        _, raw_symbol = self._require(payload, "tradingsymbol", "instrument_token")
        price = self._parse_price(payload, "last_price")
        quantity = self._parse_quantity(payload, "last_traded_quantity", "last_quantity")
        _, ts_raw = self._require(payload, "last_trade_time", "exchange_timestamp")
        unit = self._timestamp_units.get("zerodha", TimestampUnit.AUTO)
        exchange_timestamp = self._coerce_timestamp(ts_raw, "last_trade_time", unit)

        return self._build(
            symbol=self.get_canonical_symbol("zerodha", str(raw_symbol)),
            venue="zerodha",
            price=price,
            quantity=quantity,
            side=NormalizedSide.UNKNOWN,
            exchange_timestamp=exchange_timestamp,
            receipt_timestamp=receipt_timestamp,
        )

    # --------------------------------------------------------------- dispatch

    def normalize(
        self,
        venue: str,
        raw_payload: Mapping[str, Any],
        receipt_timestamp: Optional[float] = None,
    ) -> UnifiedTick:
        """
        Normalize one raw payload from ``venue``.

        Args:
            venue: Venue key; matched case-insensitively.
            raw_payload: The venue's decoded message.
            receipt_timestamp: Local epoch seconds captured when the payload was
                *read from the socket*. Pass it whenever payloads are queued
                before parsing; omitting it stamps parse time instead, which
                folds queue delay into every latency measurement taken from the
                resulting tick.

        Raises:
            NormalizationError: on an unregistered venue, a non-mapping payload,
                or any unreadable field.
        """
        if not isinstance(raw_payload, Mapping):
            raise NormalizationError(
                f"Payload for venue '{venue}' must be a mapping, got {type(raw_payload).__name__}."
            )
        parser = self._parsers.get(venue.lower())
        if parser is None:
            raise NormalizationError(
                f"Unsupported exchange venue '{venue}'. Registered venues: "
                f"{', '.join(self.supported_venues()) or '(none)'}. Add one with register_parser()."
            )
        return parser(raw_payload, receipt_timestamp)
