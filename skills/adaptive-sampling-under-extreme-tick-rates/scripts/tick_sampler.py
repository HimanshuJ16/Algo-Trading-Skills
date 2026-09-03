"""Adaptive systematic tick sampling with volume and VWAP preservation."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import logging
import math
import threading
import time
from numbers import Real
from typing import Optional


logger = logging.getLogger(__name__)


class SamplingMode(Enum):
    PASSTHROUGH = "PASSTHROUGH"
    SYSTEMATIC_SAMPLING = "SYSTEMATIC_SAMPLING"


@dataclass(frozen=True, slots=True)
class SampledTick:
    """One emitted aggregate representing ``aggregated_tick_count`` raw trades.

    ``price`` is the volume-weighted average price of the trades represented,
    not a traded price, whenever ``aggregated_tick_count > 1``. ``sequence_id``
    and ``timestamp`` identify the last raw trade in the block (``-1`` for a
    synthetic flush). ``mode`` and ``sampling_factor`` describe the policy in
    force at emission time, so only ``aggregated_tick_count`` is authoritative
    for how many raw trades the record carries.
    """

    symbol: str
    sequence_id: int
    timestamp: float
    price: float
    volume: float
    sampling_factor: int
    mode: SamplingMode
    aggregated_tick_count: int = 1
    is_flush: bool = False


class AdaptiveTickSamplerEngine:
    """Thread-safe per-symbol systematic sampler for trade tick streams.

    The engine preserves aggregate traded volume and price-volume notional, but
    sampled output is not a replacement for every tick, quote, or order-book
    update. Consumers must not use it for microstructure or compliance logic.
    """

    def __init__(
        self,
        target_max_rate_per_sec: int = 5000,
        *,
        enforce_monotonic_sequence: bool = True,
        clock: Callable[[], float] = time.time,
    ):
        if (
            isinstance(target_max_rate_per_sec, bool)
            or not isinstance(target_max_rate_per_sec, int)
            or target_max_rate_per_sec < 1
        ):
            raise ValueError("target_max_rate_per_sec must be a positive integer")
        if not isinstance(enforce_monotonic_sequence, bool):
            raise TypeError("enforce_monotonic_sequence must be a bool")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self.target_max_rate_per_sec = target_max_rate_per_sec
        self.enforce_monotonic_sequence = enforce_monotonic_sequence
        self.clock = clock
        self._lock = threading.RLock()

        self.current_window_sec: dict[str, int] = {}
        self.current_window_count: dict[str, int] = {}
        self.previous_window_count: dict[str, int] = {}
        self.tick_counters: dict[str, int] = {}
        self.accumulated_vol: dict[str, float] = {}
        self.accumulated_notional: dict[str, float] = {}
        self.last_sequence_id: dict[str, int] = {}
        self.last_timestamp: dict[str, float] = {}

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        if not isinstance(symbol, str):
            raise TypeError("symbol must be a string")
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol must not be empty")
        return normalized_symbol

    @staticmethod
    def _finite_real(value: object, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError(f"{field_name} must be a real number")
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError(f"{field_name} must be finite")
        return numeric_value

    def _validate_tick(
        self,
        symbol: str,
        sequence_id: int,
        price: float,
        volume: float,
        timestamp: Optional[float],
    ) -> tuple[str, int, float, float, float]:
        normalized_symbol = self._normalize_symbol(symbol)
        if isinstance(sequence_id, bool) or not isinstance(sequence_id, int):
            raise TypeError("sequence_id must be an integer")

        normalized_price = self._finite_real(price, "price")
        if normalized_price <= 0.0:
            raise ValueError("price must be greater than zero")

        normalized_volume = self._finite_real(volume, "volume")
        if normalized_volume <= 0.0:
            raise ValueError("volume must be greater than zero")

        event_timestamp = self.clock() if timestamp is None else timestamp
        normalized_timestamp = self._finite_real(event_timestamp, "timestamp")
        notional = normalized_price * normalized_volume
        if not math.isfinite(notional):
            raise ValueError("price multiplied by volume must be finite")

        previous_sequence_id = self.last_sequence_id.get(normalized_symbol)
        if (
            self.enforce_monotonic_sequence
            and previous_sequence_id is not None
            and sequence_id <= previous_sequence_id
        ):
            raise ValueError(
                f"sequence_id must increase for {normalized_symbol}: "
                f"{sequence_id} <= {previous_sequence_id}"
            )

        previous_timestamp = self.last_timestamp.get(normalized_symbol)
        if previous_timestamp is not None and normalized_timestamp < previous_timestamp:
            raise ValueError(
                f"timestamp moved backwards for {normalized_symbol}: "
                f"{normalized_timestamp} < {previous_timestamp}"
            )

        return (
            normalized_symbol,
            sequence_id,
            normalized_price,
            normalized_volume,
            normalized_timestamp,
        )

    def _get_rolling_rate(self, symbol: str, now: float) -> float:
        current_second = math.floor(now)
        if symbol not in self.current_window_sec:
            self.current_window_sec[symbol] = current_second
            self.current_window_count[symbol] = 0
            self.previous_window_count[symbol] = 0

        if current_second > self.current_window_sec[symbol]:
            elapsed_seconds = current_second - self.current_window_sec[symbol]
            self.previous_window_count[symbol] = (
                self.current_window_count[symbol] if elapsed_seconds == 1 else 0
            )
            self.current_window_count[symbol] = 0
            self.current_window_sec[symbol] = current_second

        self.current_window_count[symbol] += 1
        fraction_of_second = now - current_second
        return (
            self.previous_window_count[symbol] * (1.0 - fraction_of_second)
            + self.current_window_count[symbol]
        )

    def _initialize_symbol(self, symbol: str) -> None:
        if symbol not in self.tick_counters:
            self.tick_counters[symbol] = 0
            self.accumulated_vol[symbol] = 0.0
            self.accumulated_notional[symbol] = 0.0

    def ingest_tick(
        self,
        symbol: str,
        sequence_id: int,
        price: float,
        volume: float,
        timestamp: Optional[float] = None,
    ) -> Optional[SampledTick]:
        """Ingest one trade tick and emit an aggregate when its policy allows."""
        with self._lock:
            (
                normalized_symbol,
                normalized_sequence_id,
                normalized_price,
                normalized_volume,
                normalized_timestamp,
            ) = self._validate_tick(symbol, sequence_id, price, volume, timestamp)

            # Checked before any state mutation - including the rolling-rate
            # window - so a rejected tick leaves the engine unchanged and can be
            # quarantined upstream without a state rollback.
            next_volume = (
                self.accumulated_vol.get(normalized_symbol, 0.0) + normalized_volume
            )
            next_notional = self.accumulated_notional.get(normalized_symbol, 0.0) + (
                normalized_price * normalized_volume
            )
            if not math.isfinite(next_volume) or not math.isfinite(next_notional):
                raise OverflowError("aggregate volume or notional exceeded finite range")

            current_rate = self._get_rolling_rate(
                normalized_symbol, normalized_timestamp
            )
            if current_rate > self.target_max_rate_per_sec:
                sampling_factor = max(
                    2,
                    math.ceil(current_rate / self.target_max_rate_per_sec),
                )
                mode = SamplingMode.SYSTEMATIC_SAMPLING
            else:
                sampling_factor = 1
                mode = SamplingMode.PASSTHROUGH

            self._initialize_symbol(normalized_symbol)
            self.tick_counters[normalized_symbol] += 1
            self.accumulated_vol[normalized_symbol] = next_volume
            self.accumulated_notional[normalized_symbol] = next_notional
            self.last_sequence_id[normalized_symbol] = normalized_sequence_id
            self.last_timestamp[normalized_symbol] = normalized_timestamp

            if (
                self.tick_counters[normalized_symbol] < sampling_factor
                and mode == SamplingMode.SYSTEMATIC_SAMPLING
            ):
                return None

            aggregated_tick_count = self.tick_counters[normalized_symbol]
            emitted_volume = self.accumulated_vol[normalized_symbol]
            emitted_vwap = (
                self.accumulated_notional[normalized_symbol] / emitted_volume
            )
            self.tick_counters[normalized_symbol] = 0
            self.accumulated_vol[normalized_symbol] = 0.0
            self.accumulated_notional[normalized_symbol] = 0.0

            if mode == SamplingMode.SYSTEMATIC_SAMPLING:
                logger.info(
                    "Adaptive sampler [%s]: rate=%.0f/s target=%d/s factor=1:%d "
                    "vwap=%.6f volume=%.6f",
                    normalized_symbol,
                    current_rate,
                    self.target_max_rate_per_sec,
                    sampling_factor,
                    emitted_vwap,
                    emitted_volume,
                )

            return SampledTick(
                symbol=normalized_symbol,
                sequence_id=normalized_sequence_id,
                timestamp=normalized_timestamp,
                price=emitted_vwap,
                volume=emitted_volume,
                sampling_factor=sampling_factor,
                mode=mode,
                aggregated_tick_count=aggregated_tick_count,
            )

    def flush(
        self,
        symbol: str,
        timestamp: Optional[float] = None,
    ) -> Optional[SampledTick]:
        """Flush residual aggregate state without changing event-time order."""
        with self._lock:
            normalized_symbol = self._normalize_symbol(symbol)
            if normalized_symbol not in self.accumulated_vol:
                return None
            if self.accumulated_vol[normalized_symbol] <= 0.0:
                return None

            flush_timestamp = (
                self.last_timestamp[normalized_symbol]
                if timestamp is None
                else self._finite_real(timestamp, "timestamp")
            )
            if flush_timestamp < self.last_timestamp[normalized_symbol]:
                raise ValueError("flush timestamp cannot precede the last tick")

            emitted_volume = self.accumulated_vol[normalized_symbol]
            emitted_vwap = (
                self.accumulated_notional[normalized_symbol] / emitted_volume
            )
            aggregated_tick_count = self.tick_counters[normalized_symbol]
            self.tick_counters[normalized_symbol] = 0
            self.accumulated_vol[normalized_symbol] = 0.0
            self.accumulated_notional[normalized_symbol] = 0.0

            return SampledTick(
                symbol=normalized_symbol,
                sequence_id=-1,
                timestamp=flush_timestamp,
                price=emitted_vwap,
                volume=emitted_volume,
                sampling_factor=1,
                mode=SamplingMode.PASSTHROUGH,
                aggregated_tick_count=aggregated_tick_count,
                is_flush=True,
            )

    def flush_all(self, timestamp: Optional[float] = None) -> list[SampledTick]:
        """Flush all residual symbol aggregates in deterministic symbol order."""
        with self._lock:
            flush_timestamp = (
                None if timestamp is None else self._finite_real(timestamp, "timestamp")
            )
            symbols_with_residuals = [
                symbol
                for symbol in sorted(self.accumulated_vol)
                if self.accumulated_vol[symbol] > 0.0
            ]
            if flush_timestamp is not None:
                for symbol in symbols_with_residuals:
                    if flush_timestamp < self.last_timestamp[symbol]:
                        raise ValueError(
                            f"flush timestamp cannot precede the last tick for {symbol}"
                        )

            outputs = []
            for symbol in symbols_with_residuals:
                flushed_tick = self.flush(symbol, timestamp=flush_timestamp)
                if flushed_tick is not None:
                    outputs.append(flushed_tick)
            return outputs

    def reset_symbol(
        self,
        symbol: str,
        *,
        flush: bool = True,
        timestamp: Optional[float] = None,
    ) -> Optional[SampledTick]:
        """Flush and remove all state for a symbol, or discard only after review."""
        with self._lock:
            normalized_symbol = self._normalize_symbol(symbol)
            flushed_tick = (
                self.flush(normalized_symbol, timestamp=timestamp) if flush else None
            )
            for state in (
                self.current_window_sec,
                self.current_window_count,
                self.previous_window_count,
                self.tick_counters,
                self.accumulated_vol,
                self.accumulated_notional,
                self.last_sequence_id,
                self.last_timestamp,
            ):
                state.pop(normalized_symbol, None)
            return flushed_tick

    @property
    def tracked_symbol_count(self) -> int:
        with self._lock:
            return len(self.tick_counters)