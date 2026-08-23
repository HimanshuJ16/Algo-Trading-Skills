import datetime
import logging
import math
import re
from dataclasses import dataclass, field
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

NS_PER_SECOND = 1_000_000_000
NS_PER_MILLISECOND = 1_000_000
NS_PER_MICROSECOND = 1_000

# Nanosecond UTC epoch must fit a signed 64-bit integer, which is what every
# downstream store (Arrow/Parquet TIMESTAMP(NANOS), kdb+, ClickHouse DateTime64)
# uses. int64 nanoseconds saturate at 2262-04-11T23:47:16.854775807Z.
INT64_MIN = -(2 ** 63)
INT64_MAX = 2 ** 63 - 1

# Largest magnitude a float64 represents with integer exactness (2**53).
_FLOAT_EXACT_INT_LIMIT = 2 ** 53

# All Decimal arithmetic runs in this explicit context rather than the process
# default. decimal.getcontext() is caller-mutable global state: with prec=6 set
# elsewhere in the process, 1_700_000_000_123 ms silently normalized to
# 1700000000000000000 ns. 60 significant digits comfortably covers int64
# nanoseconds (19 digits) plus any sub-nanosecond tail the caller supplies.
_DECIMAL_CONTEXT = Context(prec=60, rounding=ROUND_HALF_EVEN)

# Precision tiers ordered coarse -> fine, used both for reporting and for
# comparing an observed tier against a required one.
PRECISION_TIER_ORDER: Dict[str, int] = {
    "SECONDS": 0,
    "MILLISECONDS": 1,
    "MICROSECONDS": 2,
    "NANOSECONDS": 3,
}

# Scale factor (nanoseconds per unit) for each numeric input format.
_NUMERIC_FORMAT_SCALES: Dict[str, int] = {
    "SECONDS": NS_PER_SECOND,
    "MILLISECONDS": NS_PER_MILLISECOND,
    "MICROSECONDS": NS_PER_MICROSECOND,
    "NANOSECONDS": 1,
}

_ISO_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:[.,](?P<frac>\d+))?"
    r"(?P<offset>Z|z|[+-]\d{2}:?\d{2})?$"
)


@dataclass
class VendorTickRecord:
    """
    One raw tick as delivered by a vendor.

    ``event_key`` is the identifier that lets two vendors' records be recognized
    as describing the SAME market event - an exchange sequence number, matching
    engine event id, or venue trade id. Cross-vendor timestamp skew is only
    meaningful between records that share an event key; without it, the gap
    between two consecutive ticks is just the gap between two different events.
    """

    tick_id: str
    vendor_id: str                      # e.g. 'BLOOMBERG', 'REFINITIV', 'DATABENTO', 'ICE'
    symbol: str
    price: float
    volume: float
    raw_timestamp: Union[str, float, int]
    precision_format: str               # 'SECONDS' | 'MILLISECONDS' | 'MICROSECONDS' | 'NANOSECONDS' | 'ISO8601'
    event_key: Optional[str] = None     # exchange sequence number / venue trade id


@dataclass
class NormalizedTickRecord:
    tick_id: str
    vendor_id: str
    symbol: str
    price: float
    volume: float
    raw_timestamp: Union[str, float, int]
    normalized_ns_utc: int              # 64-bit integer nanosecond UTC epoch
    iso_utc_str: str                    # full nanosecond fidelity, 9 fractional digits
    precision_tier: str                 # 'NANOSECONDS' | 'MICROSECONDS' | 'MILLISECONDS' | 'SECONDS'
    is_out_of_order: bool
    arrival_index: int = -1             # position in the input (arrival) sequence
    event_key: Optional[str] = None
    meets_precision_requirement: bool = True


@dataclass
class VendorSkewObservation:
    """
    Signed timestamp skew between two vendors for the SAME event.

    ``skew_ns`` = t(vendor_b) - t(vendor_a) with vendors ordered lexicographically,
    so the sign identifies which vendor is ahead. This is a skew observation, not
    a clock-offset measurement: it also contains the two vendors' capture-point
    and propagation differences.
    """

    symbol: str
    event_key: str
    vendor_a: str
    vendor_b: str
    skew_ns: int
    exceeds_threshold: bool


@dataclass
class TimestampReconciliationReport:
    total_ticks_processed: int
    normalized_ticks: List[NormalizedTickRecord]
    out_of_order_count: int
    precision_counts: Dict[str, int]
    vendor_drift_warnings: List[str]
    vendor_skew_observations: List[VendorSkewObservation] = field(default_factory=list)
    skew_pairs_evaluated: int = 0
    precision_violation_count: int = 0
    required_precision_tier: Optional[str] = None


class CrossVendorTimestampReconciler:
    """
    Market data reconciliation engine normalizing multi-vendor timestamps
    (s, ms, us, ns, ISO-8601) to 64-bit nanosecond UTC epoch integers, detecting
    out-of-order arrivals, auditing precision tiers, and measuring cross-vendor
    timestamp skew for matched events.

    All conversions use exact integer/Decimal arithmetic. Scaling through
    float64 - e.g. ``int(1_700_000_000_123 * 1e6)`` - is wrong at nanosecond
    resolution because float64 carries 53 significand bits: an epoch value near
    1.7e18 ns has a representable spacing of 256 ns, so the naive expression
    yields 1700000000123000064 rather than 1700000000123000000.

    SCOPE: this engine reconciles timestamp REPRESENTATION across vendors. It
    does not measure a host's clock offset against UTC (that is PTP/NTP
    telemetry) and cannot distinguish a vendor's clock error from a difference
    in where each vendor timestamps the event (matching engine vs capture NIC).
    """

    def __init__(
        self,
        max_allowed_vendor_drift_ms: float = 5.0,
        required_precision_tier: Optional[str] = None,
    ) -> None:
        """
        ``max_allowed_vendor_drift_ms``: skew threshold applied to matched
        cross-vendor event pairs.
        ``required_precision_tier``: optional minimum tier (e.g. 'MICROSECONDS');
        ticks coarser than this are flagged rather than silently accepted.
        """
        if not isinstance(max_allowed_vendor_drift_ms, (int, float)) or isinstance(
            max_allowed_vendor_drift_ms, bool
        ):
            raise TypeError("max_allowed_vendor_drift_ms must be a real number")
        if not math.isfinite(float(max_allowed_vendor_drift_ms)) or max_allowed_vendor_drift_ms < 0:
            raise ValueError(
                f"max_allowed_vendor_drift_ms must be finite and non-negative, "
                f"got {max_allowed_vendor_drift_ms!r}"
            )
        if required_precision_tier is not None:
            tier = str(required_precision_tier).upper()
            if tier not in PRECISION_TIER_ORDER:
                raise ValueError(
                    f"required_precision_tier must be one of "
                    f"{sorted(PRECISION_TIER_ORDER, key=PRECISION_TIER_ORDER.get)}, "
                    f"got {required_precision_tier!r}"
                )
            required_precision_tier = tier

        self.max_allowed_vendor_drift_ms = max_allowed_vendor_drift_ms
        self.required_precision_tier = required_precision_tier
        # Threshold held in integer nanoseconds so the comparison itself never
        # round-trips through float.
        self._max_drift_ns = int(_DECIMAL_CONTEXT.to_integral_value(
            _DECIMAL_CONTEXT.multiply(
                Decimal(str(max_allowed_vendor_drift_ms)), Decimal(NS_PER_MILLISECOND)
            )
        ))

    # ------------------------------------------------------------------
    # Timestamp normalization
    # ------------------------------------------------------------------

    def normalize_timestamp_to_ns(
        self,
        raw_ts: Union[str, float, int],
        precision_format: str
    ) -> Tuple[int, str, str]:
        """
        Parses a raw vendor timestamp and returns
        ``(nanosecond_epoch_int64, iso_utc_string, precision_tier)``.

        Numeric formats are scaled with exact Decimal arithmetic in a private
        60-digit context (ROUND_HALF_EVEN), so a caller's global
        ``decimal.getcontext()`` cannot alter the result. A float input
        is converted via ``Decimal(str(value))``, i.e. the shortest decimal that
        round-trips the float, which reconstructs the value the vendor meant
        (1700000000.1 -> 1700000000100000000 ns) instead of the binary
        approximation's tail. A float still cannot CARRY nanoseconds at epoch
        magnitude, so float input is logged as lossy.

        ISO-8601 is parsed with an explicit fractional-digit split rather than
        ``datetime``: ``datetime`` caps at microseconds and would silently drop
        the last three digits of a nanosecond timestamp.

        Raises ValueError on unsupported formats, unparseable values, values
        outside the signed 64-bit nanosecond range, and float inputs declared as
        NANOSECONDS (where the float has already destroyed the precision the
        format claims).
        """
        if not isinstance(precision_format, str) or not precision_format.strip():
            raise ValueError(
                f"precision_format must be a non-empty string, got {precision_format!r}"
            )
        fmt = precision_format.strip().upper()

        if fmt in _NUMERIC_FORMAT_SCALES:
            ns = self._numeric_to_ns(raw_ts, fmt)
            precision_tier = fmt
        elif fmt in ("ISO8601", "ISO8601_STRING"):
            ns, precision_tier = self._iso8601_to_ns(raw_ts)
        else:
            raise ValueError(f"Unsupported precision format: {precision_format}")

        if not INT64_MIN <= ns <= INT64_MAX:
            raise ValueError(
                f"Normalized timestamp {ns} is outside the signed 64-bit nanosecond "
                f"range [{INT64_MIN}, {INT64_MAX}] (int64 ns saturates in 2262)."
            )

        return ns, self.ns_to_iso_utc(ns), precision_tier

    def _numeric_to_ns(self, raw_ts: Union[str, float, int], fmt: str) -> int:
        scale = _NUMERIC_FORMAT_SCALES[fmt]

        if isinstance(raw_ts, bool):
            raise ValueError(f"raw_timestamp must be numeric or a numeric string, got {raw_ts!r}")

        if isinstance(raw_ts, float):
            if not math.isfinite(raw_ts):
                raise ValueError(f"raw_timestamp must be finite, got {raw_ts!r}")
            if fmt == "NANOSECONDS" and not (
                raw_ts.is_integer() and abs(raw_ts) <= _FLOAT_EXACT_INT_LIMIT
            ):
                # A float carrying an epoch nanosecond count has already lost
                # precision: 1.70000000012345678e18 -> 1700000000123456768.
                raise ValueError(
                    "NANOSECONDS timestamps must be supplied as int or str; a float "
                    f"cannot represent {raw_ts!r} exactly (float64 is exact only to "
                    f"{_FLOAT_EXACT_INT_LIMIT} and epoch nanoseconds far exceed it)."
                )
            logger.debug(
                "Float raw_timestamp %r declared as %s: float64 resolution at epoch "
                "magnitude is ~238ns, so sub-microsecond detail cannot be recovered.",
                raw_ts, fmt,
            )
            value = Decimal(str(raw_ts))
        elif isinstance(raw_ts, int):
            value = Decimal(raw_ts)
        elif isinstance(raw_ts, str):
            try:
                value = Decimal(raw_ts.strip())
            except InvalidOperation:
                raise ValueError(
                    f"raw_timestamp {raw_ts!r} is not a valid decimal number for format {fmt}"
                ) from None
            if not value.is_finite():
                raise ValueError(f"raw_timestamp must be finite, got {raw_ts!r}")
        else:
            raise ValueError(
                f"raw_timestamp must be int, float or str, got {type(raw_ts).__name__}"
            )

        scaled = _DECIMAL_CONTEXT.multiply(value, Decimal(scale))
        ns = int(_DECIMAL_CONTEXT.to_integral_value(scaled))
        if scaled != ns:
            logger.warning(
                "Sub-nanosecond component of raw_timestamp %r (%s) discarded: %s -> %d ns.",
                raw_ts, fmt, scaled, ns,
            )
        return ns

    def _iso8601_to_ns(self, raw_ts: Union[str, float, int]) -> Tuple[int, str]:
        if not isinstance(raw_ts, str):
            raise ValueError(
                f"ISO8601 raw_timestamp must be a string, got {type(raw_ts).__name__}"
            )
        match = _ISO_PATTERN.match(raw_ts.strip())
        if match is None:
            raise ValueError(
                f"Unparseable ISO-8601 timestamp {raw_ts!r}; expected "
                "'YYYY-MM-DDTHH:MM:SS[.fraction][Z|+HH:MM]'."
            )

        frac = match.group("frac") or ""
        if len(frac) > 9:
            # Keep 9 digits only if the surplus is zero padding; otherwise the
            # value carries sub-nanosecond detail this schema cannot hold.
            if frac[9:].strip("0"):
                raise ValueError(
                    f"ISO-8601 timestamp {raw_ts!r} carries sub-nanosecond precision "
                    f"({len(frac)} fractional digits); int64 nanoseconds cannot represent it."
                )
            frac = frac[:9]

        offset = match.group("offset")
        if offset is None:
            logger.warning(
                "ISO-8601 timestamp %r has no UTC offset; interpreting as UTC. "
                "If the vendor emits local exchange time this is a silent shift.",
                raw_ts,
            )
            offset_str = "+00:00"
        elif offset in ("Z", "z"):
            offset_str = "+00:00"
        else:
            offset_str = offset if ":" in offset else f"{offset[:3]}:{offset[3:]}"

        try:
            dt = datetime.datetime.fromisoformat(
                f"{match.group('date')}T{match.group('time')}{offset_str}"
            )
        except ValueError as exc:
            raise ValueError(f"Unparseable ISO-8601 timestamp {raw_ts!r}: {exc}") from None

        # Whole seconds via integer arithmetic; the fraction is added as an
        # integer so nanosecond digits survive (datetime itself stops at us).
        # timedelta // timedelta is exact integer division - no float step.
        epoch_seconds = (
            dt - datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
        ) // datetime.timedelta(seconds=1)
        fraction_ns = int(frac.ljust(9, "0")) if frac else 0
        ns = epoch_seconds * NS_PER_SECOND + fraction_ns

        digits = len(frac)
        if digits == 0:
            tier = "SECONDS"
        elif digits <= 3:
            tier = "MILLISECONDS"
        elif digits <= 6:
            tier = "MICROSECONDS"
        else:
            tier = "NANOSECONDS"
        return ns, tier

    @staticmethod
    def ns_to_iso_utc(ns: int) -> str:
        """
        Renders a nanosecond epoch as an ISO-8601 UTC string with all 9
        fractional digits, using integer division so the rendering never passes
        through float (which would perturb the microsecond digits) and never
        truncates to milliseconds (which would make the audit string a lossy
        copy of the value it documents).
        """
        seconds, remainder_ns = divmod(int(ns), NS_PER_SECOND)  # floors for negatives
        dt_utc = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
        return f"{dt_utc.strftime('%Y-%m-%dT%H:%M:%S')}.{remainder_ns:09d}Z"

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def reconcile_vendor_ticks(
        self, raw_ticks: List[VendorTickRecord]
    ) -> TimestampReconciliationReport:
        """
        Normalizes multi-vendor tick timestamps, flags out-of-order ARRIVALS,
        audits precision tiers, and measures cross-vendor skew on matched events.

        Out-of-order detection walks the input in ARRIVAL order and flags any
        tick whose timestamp precedes the highest timestamp already seen
        (delta_t < 0). Inspecting adjacent pairs of the *sorted* list instead
        under-counts: arrivals [t=5, t=1, t=2, t=3] contain three late ticks,
        but only one adjacent inversion survives sorting.

        The batch is treated as ONE merged stream: the running maximum spans all
        symbols and vendors, which is the property a sequencer or replay buffer
        needs. For per-symbol ordering (where a late MSFT tick after an AAPL tick
        is not a defect), group the batch by symbol and reconcile each group.

        Cross-vendor skew is computed ONLY between records from different
        vendors that share ``(symbol, event_key)`` - i.e. that claim to describe
        the same market event. Comparing consecutive ticks instead measures the
        interval between two DIFFERENT events and reports it as clock drift.
        When no record carries an event key, skew analysis is skipped and
        ``skew_pairs_evaluated`` is 0 rather than emitting unfounded warnings.

        Raises ValueError on duplicate ``tick_id`` values: tick_id is the record
        key for arrival ordering, and duplicates silently corrupt it.
        """
        if not isinstance(raw_ticks, (list, tuple)):
            raise TypeError(
                f"raw_ticks must be a list of VendorTickRecord, got {type(raw_ticks).__name__}"
            )

        normalized_list: List[NormalizedTickRecord] = []
        precision_counts: Dict[str, int] = {tier: 0 for tier in PRECISION_TIER_ORDER}
        seen_ids: Dict[str, int] = {}
        required_rank = (
            PRECISION_TIER_ORDER[self.required_precision_tier]
            if self.required_precision_tier
            else None
        )
        precision_violations = 0
        running_max_ns: Optional[int] = None
        ooo_count = 0

        for arrival_index, tick in enumerate(raw_ticks):
            if not isinstance(tick, VendorTickRecord):
                raise TypeError(
                    f"raw_ticks[{arrival_index}] must be VendorTickRecord, "
                    f"got {type(tick).__name__}"
                )
            for required in ("tick_id", "vendor_id", "symbol"):
                value = getattr(tick, required)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"raw_ticks[{arrival_index}].{required} must be a non-empty string"
                    )
            if tick.tick_id in seen_ids:
                raise ValueError(
                    f"Duplicate tick_id {tick.tick_id!r} at arrival positions "
                    f"{seen_ids[tick.tick_id]} and {arrival_index}; tick_id must be unique."
                )
            seen_ids[tick.tick_id] = arrival_index

            ns_ts, iso_str, tier = self.normalize_timestamp_to_ns(
                tick.raw_timestamp, tick.precision_format
            )
            precision_counts[tier] = precision_counts.get(tier, 0) + 1

            meets_requirement = True
            if required_rank is not None and PRECISION_TIER_ORDER[tier] < required_rank:
                meets_requirement = False
                precision_violations += 1
                logger.warning(
                    "PRECISION SHORTFALL [%s/%s]: tick %s carries %s resolution, "
                    "below the required %s.",
                    tick.symbol, tick.vendor_id, tick.tick_id, tier,
                    self.required_precision_tier,
                )

            # delta_t < 0 against the running maximum, evaluated in arrival order.
            is_out_of_order = running_max_ns is not None and ns_ts < running_max_ns
            if is_out_of_order:
                ooo_count += 1
                logger.warning(
                    "OUT_OF_ORDER [%s/%s]: tick %s arrived %d ns behind the latest "
                    "timestamp already seen.",
                    tick.symbol, tick.vendor_id, tick.tick_id, running_max_ns - ns_ts,
                )
            else:
                running_max_ns = ns_ts if running_max_ns is None else max(running_max_ns, ns_ts)

            normalized_list.append(NormalizedTickRecord(
                tick_id=tick.tick_id,
                vendor_id=tick.vendor_id,
                symbol=tick.symbol,
                price=tick.price,
                volume=tick.volume,
                raw_timestamp=tick.raw_timestamp,
                normalized_ns_utc=ns_ts,
                iso_utc_str=iso_str,
                precision_tier=tier,
                is_out_of_order=is_out_of_order,
                arrival_index=arrival_index,
                event_key=tick.event_key,
                meets_precision_requirement=meets_requirement,
            ))

        # Stable temporal ordering: ties broken by arrival index so the output
        # sequence is reproducible for identical input.
        sorted_ticks = sorted(
            normalized_list, key=lambda t: (t.normalized_ns_utc, t.arrival_index)
        )

        observations, warnings_list = self._measure_event_skew(normalized_list)

        return TimestampReconciliationReport(
            total_ticks_processed=len(raw_ticks),
            normalized_ticks=sorted_ticks,
            out_of_order_count=ooo_count,
            precision_counts=precision_counts,
            vendor_drift_warnings=warnings_list,
            vendor_skew_observations=observations,
            skew_pairs_evaluated=len(observations),
            precision_violation_count=precision_violations,
            required_precision_tier=self.required_precision_tier,
        )

    def _measure_event_skew(
        self, ticks: List[NormalizedTickRecord]
    ) -> Tuple[List[VendorSkewObservation], List[str]]:
        """
        Pairwise signed skew between vendors reporting the same
        ``(symbol, event_key)``. Multiple records from one vendor for the same
        event (e.g. an amended print) are reduced to the earliest timestamp so a
        vendor is never compared against itself.
        """
        grouped: Dict[Tuple[str, str], Dict[str, int]] = {}
        for tick in ticks:
            if tick.event_key is None or str(tick.event_key).strip() == "":
                continue
            group = grouped.setdefault((tick.symbol, str(tick.event_key)), {})
            existing = group.get(tick.vendor_id)
            if existing is None or tick.normalized_ns_utc < existing:
                group[tick.vendor_id] = tick.normalized_ns_utc

        if not grouped:
            logger.info(
                "Cross-vendor skew analysis skipped: no tick carried an event_key. "
                "The interval between consecutive ticks is not clock drift - supply "
                "an exchange sequence number or venue trade id to enable this check."
            )
            return [], []

        observations: List[VendorSkewObservation] = []
        warnings_list: List[str] = []
        for (symbol, event_key) in sorted(grouped):
            vendor_times = grouped[(symbol, event_key)]
            vendors = sorted(vendor_times)
            for i, vendor_a in enumerate(vendors):
                for vendor_b in vendors[i + 1:]:
                    skew_ns = vendor_times[vendor_b] - vendor_times[vendor_a]
                    exceeds = abs(skew_ns) > self._max_drift_ns
                    observations.append(VendorSkewObservation(
                        symbol=symbol,
                        event_key=event_key,
                        vendor_a=vendor_a,
                        vendor_b=vendor_b,
                        skew_ns=skew_ns,
                        exceeds_threshold=exceeds,
                    ))
                    if exceeds:
                        msg = (
                            f"CROSS-VENDOR SKEW [{symbol} event {event_key}]: "
                            f"{vendor_b} is {skew_ns / NS_PER_MILLISECOND:+.3f}ms relative to "
                            f"{vendor_a}, exceeding the {self.max_allowed_vendor_drift_ms:.2f}ms "
                            f"threshold."
                        )
                        warnings_list.append(msg)
                        logger.warning(msg)
        return observations, warnings_list
