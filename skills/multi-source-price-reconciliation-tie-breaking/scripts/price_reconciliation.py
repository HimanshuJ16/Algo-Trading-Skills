"""Multi-source price reconciliation: outlier attribution, tolerance audit, deterministic tie-breaking.

This module takes several vendors' quotes for **the same instrument at the same
moment** and answers two separate questions:

1. Is there a price we can use?
2. Did anything independent actually corroborate it?

Conflating those two is what makes a multi-vendor pricing stack dangerous rather
than safe. A price chosen by a tie-breaker *because the vendors disagreed* is
usable but uncorroborated; a price averaged from three vendors that agreed to
within a tick is corroborated. The report carries both ``status`` and
``is_cross_verified`` so a caller can tell them apart.

Attribution needs at least three sources
----------------------------------------
Outlier rejection here is a distance test against the **median**. With two quotes
the median is exactly midway between them, so both are always equidistant: either
both pass or both fail, and no outlier can ever be attributed. With an odd number
of quotes the median *is* one of the quotes, so at least one always survives. With
an even number split into two clusters (100, 100, 105, 105) the median falls in the
gap and every quote can fail at once - a deadlock, not a detection. That case is
reported as ``RECONCILIATION_UNRESOLVED``; it is never silently downgraded to "no
outliers found", which is what makes an audit trail lie about what happened.

Why a fixed percentage bound and not MAD
-----------------------------------------
A median-absolute-deviation / modified-z-score filter adapts its threshold to the
observed dispersion, which is the wrong behaviour here. Vendor quotes for a liquid
instrument cluster tightly, so MAD collapses toward zero and an ordinary one-tick
disagreement becomes an enormous z-score; when more than half the quotes are
identical - routine for prices quoted on a discrete tick grid - MAD is exactly zero
and the score is undefined. A fixed economic bound (``max_deviation_pct``) degrades
gracefully in both cases and is calibratable per instrument from recorded data. This
module therefore does **not** implement MAD.

Clock discipline
----------------
``timestamp`` and ``as_of`` are **local receipt times on one clock**, never vendor-
or exchange-supplied event times. Staleness is a duration; measuring it across two
vendors' clocks measures their skew instead. See
``clock-skew-correction-for-tick-timestamps``.

What this module is NOT
-----------------------
* Not a failover controller. It reconciles the quotes it is handed; deciding which
  vendors to subscribe to when one dies is
  ``vendor-outage-fallback-data-source-hierarchy``.
* Not a normaliser. Every quote must already be on the same price basis (all last
  trade, or all mid) in the same currency - see ``multi-exchange-feed-normalization``.
* Not a risk control. It emits a status; it stops nothing on its own.

Regulatory context is in ``references/standards.md``. Nothing here is compliance advice.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "TieBreakerMethod",
    "ReconciliationStatus",
    "Resolution",
    "VendorPriceQuote",
    "ReconciliationConfig",
    "PriceReconciliationReport",
    "MultiSourcePriceReconcilerEngine",
    "COMPOSITE_WEIGHTED",
    "NO_WINNING_VENDOR",
    "MIN_SOURCES_FOR_ATTRIBUTION",
]

#: ``winning_vendor_id`` when every valid quote contributed to a composite price.
COMPOSITE_WEIGHTED = "COMPOSITE_WEIGHTED"
#: ``winning_vendor_id`` when no quote was usable at all.
NO_WINNING_VENDOR = "NONE"

#: Below this many quotes, a median distance test cannot attribute an outlier.
MIN_SOURCES_FOR_ATTRIBUTION = 3


class TieBreakerMethod(str, Enum):
    """How to resolve quotes that disagree by more than the agreement tolerance."""

    #: Lowest ``vendor_priority`` number wins. A *policy*, not a detection.
    PRIORITY = "PRIORITY"
    #: Highest ``timestamp`` wins.
    FRESHNESS = "FRESHNESS"
    #: Largest ``volume_depth`` wins.
    VOLUME_WEIGHTED = "VOLUME_WEIGHTED"
    #: Always composite, even when the vendors disagree - see
    #: :class:`ReconciliationConfig` for why that is usually a mistake.
    WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE"


class ReconciliationStatus(str, Enum):
    """Outcome of a reconciliation, independent of which vendor won."""

    #: Two or more independent quotes agreed within tolerance. The only
    #: cross-verified outcome.
    SUCCESS = "RECONCILIATION_SUCCESS"
    #: A price was produced from a single surviving source. Usable, uncorroborated.
    UNCORROBORATED = "RECONCILIATION_UNCORROBORATED"
    #: Sources disagreed beyond tolerance, or the median filter deadlocked. A price
    #: was selected by policy; nothing verified it.
    UNRESOLVED = "RECONCILIATION_UNRESOLVED"
    #: Every quote was stale. ``canonical_price`` is ``None`` - not a cached value.
    NO_USABLE_QUOTE = "RECONCILIATION_NO_USABLE_QUOTE"


class Resolution(str, Enum):
    """Which rule produced ``canonical_price``."""

    SINGLE_QUOTE = "SINGLE_QUOTE"
    WEIGHTED_AVERAGE = "WEIGHTED_AVERAGE"
    PRIORITY_RANK = "PRIORITY_RANK"
    FRESHNESS_TIMESTAMP = "FRESHNESS_TIMESTAMP"
    VOLUME_DEPTH = "VOLUME_DEPTH"
    NONE = "NONE"


@dataclass
class VendorPriceQuote:
    """One vendor's quote for one instrument, timestamped on the local receipt clock.

    Prices must be finite and strictly positive. Percentage-of-median arithmetic is
    undefined at or below zero, so genuinely negative marks - the April 2020 WTI
    settlement, a negative-carry calendar spread - are rejected here rather than
    silently mis-filtered. Reconcile such instruments on an absolute-difference
    basis instead.
    """

    vendor_id: str
    symbol: str
    price: float
    timestamp: float
    volume_depth: float = 100.0
    vendor_priority: int = 1            # 1 = highest priority; lower number wins
    reliability_weight: float = 1.0     # Relative weight in a composite price; > 0

    def __post_init__(self) -> None:
        if not isinstance(self.vendor_id, str) or not self.vendor_id.strip():
            raise ValueError("vendor_id must be a non-empty string.")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError(f"[{self.vendor_id}] symbol must be a non-empty string.")

        self.price = float(self.price)
        if not math.isfinite(self.price):
            raise ValueError(
                f"[{self.vendor_id}/{self.symbol}] price must be finite, got {self.price!r}. "
                "A non-finite quote compares False against every threshold and would pass "
                "the outlier filter unfiltered."
            )
        if self.price <= 0.0:
            raise ValueError(
                f"[{self.vendor_id}/{self.symbol}] price must be strictly positive, got "
                f"{self.price!r}. Percentage-of-median deviation is undefined at or below zero."
            )

        self.timestamp = float(self.timestamp)
        if not math.isfinite(self.timestamp):
            raise ValueError(f"[{self.vendor_id}/{self.symbol}] timestamp must be finite.")

        self.volume_depth = float(self.volume_depth)
        if not math.isfinite(self.volume_depth) or self.volume_depth < 0.0:
            raise ValueError(
                f"[{self.vendor_id}/{self.symbol}] volume_depth must be finite and >= 0."
            )

        if isinstance(self.vendor_priority, bool) or not isinstance(self.vendor_priority, int):
            raise ValueError(
                f"[{self.vendor_id}/{self.symbol}] vendor_priority must be an int rank."
            )

        self.reliability_weight = float(self.reliability_weight)
        if not math.isfinite(self.reliability_weight) or self.reliability_weight <= 0.0:
            raise ValueError(
                f"[{self.vendor_id}/{self.symbol}] reliability_weight must be finite and > 0, "
                f"got {self.reliability_weight!r}. Zero weights collapse the composite "
                "denominator; negative weights place the composite outside the quote range."
            )


@dataclass
class ReconciliationConfig:
    """Reconciliation thresholds.

    **None of these defaults is a standard.** No regulator, exchange or vendor
    publishes a cross-vendor divergence tolerance; they are starting points to be
    re-derived per instrument and per vendor set from recorded quote history. See
    ``references/standards.md``.

    ``min_absolute_tolerance`` is the one most often left wrong. Both percentage
    bounds are floored at this absolute price difference so that a legal one-tick
    disagreement is never treated as a divergence. The 0.05% default tolerance is
    *narrower than one $0.01 tick* for any US NMS stock quoted between $1.00 and
    $20.00, which makes every lawful penny of disagreement look like a vendor conflict.

    ``WEIGHTED_AVERAGE`` forces a composite even when the vendors disagree. That
    manufactures a price no vendor quoted and no venue ever showed; the result is
    still reported with ``is_cross_verified=False`` and status
    ``RECONCILIATION_UNRESOLVED``, but prefer an explicit tie-breaker unless a
    blended benchmark is genuinely what you want.
    """

    max_deviation_pct: float = 0.01          # Distance from median at which a quote is an outlier
    tolerance_pct: float = 0.0005            # Spread within which surviving quotes "agree"
    tie_breaker_method: str = "PRIORITY"     # See TieBreakerMethod
    min_absolute_tolerance: float = 0.0      # Tick floor, in price units, for both bounds
    max_quote_age_seconds: Optional[float] = None   # None disables staleness gating
    min_sources_for_outlier_filter: int = MIN_SOURCES_FOR_ATTRIBUTION
    price_precision: Optional[int] = None    # None = no rounding (crypto-safe default)

    def __post_init__(self) -> None:
        self.max_deviation_pct = float(self.max_deviation_pct)
        if not math.isfinite(self.max_deviation_pct) or self.max_deviation_pct <= 0.0:
            raise ValueError("max_deviation_pct must be finite and > 0.")

        self.tolerance_pct = float(self.tolerance_pct)
        if not math.isfinite(self.tolerance_pct) or self.tolerance_pct < 0.0:
            raise ValueError("tolerance_pct must be finite and >= 0.")

        if self.tolerance_pct > self.max_deviation_pct:
            raise ValueError(
                f"tolerance_pct ({self.tolerance_pct}) exceeds max_deviation_pct "
                f"({self.max_deviation_pct}): a quote inside the agreement band would also "
                "be rejected as an outlier."
            )

        try:
            self.tie_breaker_method = TieBreakerMethod(str(self.tie_breaker_method).upper())
        except ValueError:
            raise ValueError(
                f"Unknown tie_breaker_method {self.tie_breaker_method!r}. Valid: "
                f"{[m.value for m in TieBreakerMethod]}. A misspelt method must not silently "
                "fall back to input order - that is exactly the non-deterministic pricing "
                "this skill exists to prevent."
            ) from None

        self.min_absolute_tolerance = float(self.min_absolute_tolerance)
        if not math.isfinite(self.min_absolute_tolerance) or self.min_absolute_tolerance < 0.0:
            raise ValueError("min_absolute_tolerance must be finite and >= 0.")

        if self.max_quote_age_seconds is not None:
            self.max_quote_age_seconds = float(self.max_quote_age_seconds)
            if not math.isfinite(self.max_quote_age_seconds) or self.max_quote_age_seconds <= 0.0:
                raise ValueError("max_quote_age_seconds must be None, or finite and > 0.")

        if (isinstance(self.min_sources_for_outlier_filter, bool)
                or not isinstance(self.min_sources_for_outlier_filter, int)
                or self.min_sources_for_outlier_filter < MIN_SOURCES_FOR_ATTRIBUTION):
            raise ValueError(
                "min_sources_for_outlier_filter must be an int >= "
                f"{MIN_SOURCES_FOR_ATTRIBUTION}. With two quotes the median lies exactly "
                "midway between them, so both are equidistant and no outlier can be attributed."
            )

        if self.price_precision is not None:
            if (isinstance(self.price_precision, bool)
                    or not isinstance(self.price_precision, int)
                    or self.price_precision < 0):
                raise ValueError("price_precision must be None or a non-negative int.")


@dataclass
class PriceReconciliationReport:
    """Structured, auditable outcome of one reconciliation.

    ``canonical_price`` is ``None`` when no quote was usable. That is deliberate:
    substituting the last known value there is how a stale price reaches an order.
    """

    symbol: str
    canonical_price: Optional[float]
    total_quotes_received: int
    valid_quotes_count: int
    outlier_quotes_count: int
    winning_vendor_id: str
    tie_breaker_used: str
    reconciled_quotes: List[VendorPriceQuote]
    outlier_quotes: List[VendorPriceQuote]
    status: str
    audit_notes: str
    #: True only when >= 2 independent sources agreed within the effective tolerance.
    is_cross_verified: bool = False
    #: Quotes excluded by the staleness gate before any pricing arithmetic.
    stale_quotes: List[VendorPriceQuote] = field(default_factory=list)
    #: Vendors that actually contributed to ``canonical_price``, sorted.
    contributing_vendor_ids: Tuple[str, ...] = ()
    #: (max - min) / median over the surviving quotes. ``None`` when nothing was compared.
    observed_spread_pct: Optional[float] = None
    #: The agreement tolerance actually applied, after the absolute (tick) floor.
    effective_tolerance_pct: Optional[float] = None
    #: Median of the surviving quotes. ``None`` when nothing was priced.
    median_price: Optional[float] = None
    #: True when the median filter rejected every quote and could attribute nothing.
    filter_deadlocked: bool = False


class MultiSourcePriceReconcilerEngine:
    """Reconciles several vendors' quotes for one instrument into one canonical price.

    The engine holds configuration only and keeps no per-symbol state, so one instance
    is safe to share across threads provided ``config`` is not mutated after construction.
    """

    def __init__(self, config: Optional[ReconciliationConfig] = None) -> None:
        self.config = config or ReconciliationConfig()

    # ------------------------------------------------------------------- public

    def reconcile_prices(
        self,
        symbol: str,
        quotes: Sequence[VendorPriceQuote],
        as_of: Optional[float] = None,
    ) -> PriceReconciliationReport:
        """Filter outliers, audit agreement, and resolve a canonical price.

        Args:
            symbol: The instrument being reconciled. Every quote must carry it.
            quotes: One quote per vendor. Duplicate ``vendor_id`` values are rejected -
                the same vendor counted twice biases the median and the composite and
                reports a quorum of independent sources that does not exist.
            as_of: Local receipt-clock reading against which quote age is measured.
                Required when ``max_quote_age_seconds`` is configured, and deliberately
                not defaulted to the freshest quote in the batch: that default can never
                detect a batch in which every vendor has stopped updating.

        Returns:
            A :class:`PriceReconciliationReport`. Callers must branch on ``status`` and
            ``is_cross_verified``; ``canonical_price`` may be ``None``.

        Raises:
            ValueError: On an empty batch, a symbol mismatch, a duplicate vendor, or a
                missing ``as_of`` when staleness gating is enabled.
        """
        cfg = self.config
        quote_list = self._validate_batch(symbol, quotes)
        total_count = len(quote_list)

        usable, stale = self._partition_by_age(symbol, quote_list, as_of)

        if not usable:
            return self._no_usable_quote_report(symbol, total_count, stale, as_of)

        if len(usable) == 1:
            return self._single_source_report(symbol, total_count, usable[0], stale)

        valid, outliers, deadlocked = self._filter_outliers(symbol, usable)

        valid_prices = [q.price for q in valid]
        median_valid = statistics.median(valid_prices)
        spread_pct = (max(valid_prices) - min(valid_prices)) / median_valid
        tolerance = self._effective_bound(cfg.tolerance_pct, median_valid)

        agrees = (spread_pct <= tolerance) and not deadlocked

        if agrees or cfg.tie_breaker_method is TieBreakerMethod.WEIGHTED_AVERAGE:
            canonical = self._weighted_average(valid)
            winner = COMPOSITE_WEIGHTED
            resolution = Resolution.WEIGHTED_AVERAGE
            contributors = tuple(sorted(q.vendor_id for q in valid))
        else:
            winning_quote = self._tie_break(valid, cfg.tie_breaker_method)
            canonical = winning_quote.price
            winner = winning_quote.vendor_id
            resolution = _RESOLUTION_BY_METHOD[cfg.tie_breaker_method]
            contributors = (winning_quote.vendor_id,)

        cross_verified = agrees and len(valid) >= 2
        if cross_verified:
            status = ReconciliationStatus.SUCCESS
        elif len(valid) == 1:
            status = ReconciliationStatus.UNCORROBORATED
        else:
            status = ReconciliationStatus.UNRESOLVED

        notes = (
            f"{status.value} [{symbol}]: canonical={self._round(canonical)!r} via "
            f"{resolution.value} (winner '{winner}'); {total_count} received, "
            f"{len(stale)} stale, {len(outliers)} outliers, {len(valid)} valid; "
            f"spread={spread_pct * 100:.4f}% vs tolerance {tolerance * 100:.4f}%; "
            f"cross_verified={cross_verified}."
        )
        if deadlocked:
            notes += (
                " Median filter rejected every quote (clusters straddling the median): no "
                "outlier could be attributed, so all quotes were retained unverified."
            )
        if cross_verified:
            logger.info("%s", notes)
        else:
            logger.warning("%s", notes)

        return PriceReconciliationReport(
            symbol=symbol,
            canonical_price=self._round(canonical),
            total_quotes_received=total_count,
            valid_quotes_count=len(valid),
            outlier_quotes_count=len(outliers),
            winning_vendor_id=winner,
            tie_breaker_used=resolution.value,
            reconciled_quotes=valid,
            outlier_quotes=outliers,
            status=status.value,
            audit_notes=notes,
            is_cross_verified=cross_verified,
            stale_quotes=stale,
            contributing_vendor_ids=contributors,
            observed_spread_pct=spread_pct,
            effective_tolerance_pct=tolerance,
            median_price=median_valid,
            filter_deadlocked=deadlocked,
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _validate_batch(
        symbol: str, quotes: Sequence[VendorPriceQuote]
    ) -> List[VendorPriceQuote]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        quote_list = list(quotes)
        if not quote_list:
            raise ValueError("Quotes list cannot be empty.")

        mismatched = sorted({q.vendor_id for q in quote_list if q.symbol != symbol})
        if mismatched:
            raise ValueError(
                f"Quotes for a different instrument were submitted under symbol {symbol!r} "
                f"by vendor(s) {mismatched}. Reconciling across instruments produces a "
                "confident price for the wrong security."
            )

        seen: Set[str] = set()
        duplicates: Set[str] = set()
        for quote in quote_list:
            if quote.vendor_id in seen:
                duplicates.add(quote.vendor_id)
            seen.add(quote.vendor_id)
        if duplicates:
            raise ValueError(
                f"Duplicate vendor_id(s) {sorted(duplicates)} for {symbol!r}. One vendor "
                "counted twice biases the median and the composite, and reports a quorum "
                "of independent sources that does not exist."
            )
        return quote_list

    def _partition_by_age(
        self,
        symbol: str,
        quotes: List[VendorPriceQuote],
        as_of: Optional[float],
    ) -> Tuple[List[VendorPriceQuote], List[VendorPriceQuote]]:
        max_age = self.config.max_quote_age_seconds
        if max_age is None:
            if as_of is not None:
                logger.warning(
                    "as_of supplied for %s but max_quote_age_seconds is not configured: "
                    "stale quotes will be priced as if fresh.", symbol,
                )
            return list(quotes), []

        if as_of is None:
            raise ValueError(
                "as_of is required when max_quote_age_seconds is configured. Staleness must "
                "be measured against an explicit local clock reading; anchoring it to the "
                "freshest quote in the batch can never detect a batch in which every vendor "
                "has stopped updating."
            )
        as_of = float(as_of)
        if not math.isfinite(as_of):
            raise ValueError("as_of must be a finite local receipt-clock reading.")

        fresh: List[VendorPriceQuote] = []
        stale: List[VendorPriceQuote] = []
        for quote in quotes:
            age = as_of - quote.timestamp
            if age < 0.0:
                logger.warning(
                    "RECONCILIATION CLOCK [%s]: vendor '%s' quote is %.6fs in the future "
                    "relative to as_of; check receipt-clock discipline.",
                    symbol, quote.vendor_id, -age,
                )
            if age > max_age:
                stale.append(quote)
                logger.warning(
                    "RECONCILIATION STALE [%s]: vendor '%s' quote age %.3fs exceeds "
                    "max_quote_age_seconds %.3fs; excluded before pricing.",
                    symbol, quote.vendor_id, age, max_age,
                )
            else:
                fresh.append(quote)
        return fresh, stale

    def _filter_outliers(
        self, symbol: str, usable: List[VendorPriceQuote]
    ) -> Tuple[List[VendorPriceQuote], List[VendorPriceQuote], bool]:
        """Return ``(valid, outliers, deadlocked)``."""
        cfg = self.config
        if len(usable) < cfg.min_sources_for_outlier_filter:
            logger.info(
                "RECONCILIATION [%s]: %d source(s) is below the %d needed to attribute an "
                "outlier by median distance; filtering skipped.",
                symbol, len(usable), cfg.min_sources_for_outlier_filter,
            )
            return list(usable), [], False

        median_all = statistics.median(q.price for q in usable)
        bound = self._effective_bound(cfg.max_deviation_pct, median_all)

        valid: List[VendorPriceQuote] = []
        outliers: List[VendorPriceQuote] = []
        for quote in usable:
            deviation = abs(quote.price - median_all) / median_all
            if deviation > bound:
                outliers.append(quote)
                logger.warning(
                    "RECONCILIATION OUTLIER [%s]: vendor '%s' price %r deviated %.4f%% from "
                    "median %r (bound %.4f%%).",
                    symbol, quote.vendor_id, quote.price, deviation * 100,
                    median_all, bound * 100,
                )
            else:
                valid.append(quote)

        if not valid:
            logger.error(
                "RECONCILIATION DEADLOCK [%s]: every quote failed the median distance test. "
                "The quotes form clusters straddling the median, so no outlier is "
                "attributable. Retaining all quotes as unverified.", symbol,
            )
            return list(usable), [], True

        return valid, outliers, False

    def _effective_bound(self, pct: float, reference_price: float) -> float:
        """Floor a percentage bound at ``min_absolute_tolerance`` price units."""
        bound = max(pct, self.config.min_absolute_tolerance / reference_price)
        if bound >= 1.0:
            # A bound of 100% of the reference price can never be breached, so the
            # control it governs is off. Almost always a min_absolute_tolerance set in
            # the wrong price units for this instrument.
            logger.warning(
                "RECONCILIATION CONFIG: effective bound %.4f%% at reference price %r "
                "exceeds 100%%; this check can never fire. Check min_absolute_tolerance "
                "(%r) against the instrument's price scale.",
                bound * 100, reference_price, self.config.min_absolute_tolerance,
            )
        return bound

    @staticmethod
    def _weighted_average(valid: List[VendorPriceQuote]) -> float:
        # Sorted so that floating-point summation order - and therefore the last bits of
        # the result - does not depend on the caller's list order.
        ordered = sorted(valid, key=lambda q: q.vendor_id)
        total_weight = math.fsum(q.reliability_weight for q in ordered)
        weighted = math.fsum(q.price * q.reliability_weight for q in ordered)
        return weighted / total_weight

    @staticmethod
    def _tie_break(
        valid: List[VendorPriceQuote], method: TieBreakerMethod
    ) -> VendorPriceQuote:
        # Every key is a total order ending in vendor_id, so the winner never depends on
        # the caller's iteration order.
        if method is TieBreakerMethod.PRIORITY:
            return min(valid, key=lambda q: (q.vendor_priority, -q.timestamp, q.vendor_id))
        if method is TieBreakerMethod.FRESHNESS:
            return min(valid, key=lambda q: (-q.timestamp, q.vendor_priority, q.vendor_id))
        if method is TieBreakerMethod.VOLUME_WEIGHTED:
            return min(valid, key=lambda q: (-q.volume_depth, q.vendor_priority, q.vendor_id))
        raise ValueError(f"{method!r} is not a selection tie-breaker.")

    def _round(self, price: Optional[float]) -> Optional[float]:
        if price is None or self.config.price_precision is None:
            return price
        return round(price, self.config.price_precision)

    def _no_usable_quote_report(
        self,
        symbol: str,
        total_count: int,
        stale: List[VendorPriceQuote],
        as_of: Optional[float],
    ) -> PriceReconciliationReport:
        notes = (
            f"{ReconciliationStatus.NO_USABLE_QUOTE.value} [{symbol}]: all {total_count} "
            f"quote(s) exceeded max_quote_age_seconds {self.config.max_quote_age_seconds} "
            f"as of {as_of}. No canonical price is emitted; the last known value is "
            "deliberately not substituted."
        )
        logger.error("%s", notes)
        return PriceReconciliationReport(
            symbol=symbol,
            canonical_price=None,
            total_quotes_received=total_count,
            valid_quotes_count=0,
            outlier_quotes_count=0,
            winning_vendor_id=NO_WINNING_VENDOR,
            tie_breaker_used=Resolution.NONE.value,
            reconciled_quotes=[],
            outlier_quotes=[],
            status=ReconciliationStatus.NO_USABLE_QUOTE.value,
            audit_notes=notes,
            is_cross_verified=False,
            stale_quotes=stale,
        )

    def _single_source_report(
        self,
        symbol: str,
        total_count: int,
        quote: VendorPriceQuote,
        stale: List[VendorPriceQuote],
    ) -> PriceReconciliationReport:
        notes = (
            f"{ReconciliationStatus.UNCORROBORATED.value} [{symbol}]: single usable quote "
            f"from '{quote.vendor_id}' at {self._round(quote.price)!r} ({total_count} "
            f"received, {len(stale)} stale). Nothing corroborated it."
        )
        logger.warning("%s", notes)
        return PriceReconciliationReport(
            symbol=symbol,
            canonical_price=self._round(quote.price),
            total_quotes_received=total_count,
            valid_quotes_count=1,
            outlier_quotes_count=0,
            winning_vendor_id=quote.vendor_id,
            tie_breaker_used=Resolution.SINGLE_QUOTE.value,
            reconciled_quotes=[quote],
            outlier_quotes=[],
            status=ReconciliationStatus.UNCORROBORATED.value,
            audit_notes=notes,
            is_cross_verified=False,
            stale_quotes=stale,
            contributing_vendor_ids=(quote.vendor_id,),
            observed_spread_pct=0.0,
            median_price=quote.price,
        )


_RESOLUTION_BY_METHOD = {
    TieBreakerMethod.PRIORITY: Resolution.PRIORITY_RANK,
    TieBreakerMethod.FRESHNESS: Resolution.FRESHNESS_TIMESTAMP,
    TieBreakerMethod.VOLUME_WEIGHTED: Resolution.VOLUME_DEPTH,
    TieBreakerMethod.WEIGHTED_AVERAGE: Resolution.WEIGHTED_AVERAGE,
}
