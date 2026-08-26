"""Iceberg order routing: native exchange support vs broker-simulated vs client-side synthetic.

This module *plans* an iceberg execution. It never sends anything to a broker. Every
returned object describes a payload or a slice schedule that a separate order-dispatch
layer is expected to submit, acknowledge, and reconcile.

Three levels of iceberg support are distinguished, because "the broker API accepts a
display-size field" is not the same claim as "the exchange matching engine holds the
hidden quantity":

* ``NATIVE_EXCHANGE``  - the matching engine holds the reserve and replenishes the peak.
* ``BROKER_SIMULATED`` - the broker's own servers re-post each slice. No client round
  trip, but the reserve is not resting at the exchange.
* ``UNSUPPORTED``      - the client must slice, and every refill costs a network round trip.

Sourced behaviour this module encodes (verified against primary documentation):

* Replenishment loses time priority on every venue checked, native included.
  - CME Globex: "the Display Quantity order's priority is refreshed to be the lowest of
    the remaining orders at the price level (order is placed at the end of the queue)."
  - Nasdaq Equity 4, Rule 4703(h): "A new timestamp is created for the replenished
    portion of the order each time it is replenished from reserve."
  - Deutsche Boerse T7 market model: a new peak is entered with a new timestamp, and
    orders at the same limit are executed before the new peak.
  Native routing therefore removes the *client round trip*, not the queue reset.
* Nasdaq Rule 4703(h) requires the displayed size to be one or more round lots at entry
  and rounds a mixed lot down, hence ``min_display_quantity`` / ``lot_size``.
* Deutsche Boerse T7 supports *native* randomised peak replenishment, so display
  randomisation is not inherently a client-side-only capability.
* Binance spot: "Any order with an icebergQty MUST have timeInForce set to GTC", hence
  ``requires_gtc_for_iceberg``.

See ``references/standards.md`` for the full citation list.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

VALID_SIDES = ("BUY", "SELL")

#: Hard ceiling on planned child slices. A synthetic iceberg that needs more orders than
#: this is a message-rate and order-to-trade-ratio problem, not an execution plan.
DEFAULT_MAX_CHILD_SLICES = 500


class IcebergSupport(str, Enum):
    """Where the hidden reserve quantity actually rests."""

    NATIVE_EXCHANGE = "NATIVE_EXCHANGE"
    BROKER_SIMULATED = "BROKER_SIMULATED"
    UNSUPPORTED = "UNSUPPORTED"


class ExecutionMode(str, Enum):
    """How this plan intends the parent quantity to reach the book."""

    NATIVE_ICEBERG = "NATIVE_ICEBERG"
    BROKER_SIMULATED_ICEBERG = "BROKER_SIMULATED_ICEBERG"
    SYNTHETIC_SIMULATION = "SYNTHETIC_SIMULATION"
    STANDARD_LIMIT_ORDER = "STANDARD_LIMIT_ORDER"


class DisplayRandomization(str, Enum):
    """Which layer, if any, varies the displayed peak between refills."""

    NATIVE_EXCHANGE = "NATIVE_EXCHANGE"
    CLIENT_SIDE = "CLIENT_SIDE"
    NONE = "NONE"


@dataclass(frozen=True)
class BrokerVenueConfig:
    """Capabilities of one broker/venue pair.

    ``iceberg_support`` must describe the *broker and exchange combination actually being
    routed to*, not the broker in general: a broker can expose one display-size field
    that is native on some exchanges and simulated on others.

    Attributes:
        min_display_quantity: Smallest displayed peak the venue accepts. Venue-specific
            (Nasdaq: one round lot; T7: per-security minimum peak; CME: per-product).
            Leave at 1 only for venues with no documented minimum.
        lot_size: Displayed slices are aligned to this multiple.
        supports_native_display_randomization: True where the venue itself randomises
            the peak on replenishment (e.g. T7 min/max peak volume).
        client_refill_round_trip_ms: Operator-supplied estimate of the full
            fill-notification-in plus replacement-order-out round trip. Used only when
            the *client* performs the refill. Not a measured constant - calibrate it
            from your own latency telemetry.
        requires_gtc_for_iceberg: True where the native iceberg parameter is only valid
            with a GTC time-in-force (e.g. Binance spot ``icebergQty``).
    """

    broker_name: str
    iceberg_support: IcebergSupport
    native_parameter_name: Optional[str] = None
    min_display_quantity: int = 1
    lot_size: int = 1
    supports_native_display_randomization: bool = False
    client_refill_round_trip_ms: float = 0.0
    requires_gtc_for_iceberg: bool = False

    def __post_init__(self) -> None:
        if not self.broker_name or not self.broker_name.strip():
            raise ValueError("broker_name must be a non-empty string.")
        # Routing compares with `is`, so a bare string would silently miss every branch.
        # Coerce a valid name, reject anything else rather than defaulting.
        if isinstance(self.iceberg_support, str) and not isinstance(self.iceberg_support, IcebergSupport):
            try:
                object.__setattr__(self, "iceberg_support", IcebergSupport(self.iceberg_support))
            except ValueError:
                raise ValueError(
                    f"iceberg_support {self.iceberg_support!r} is not one of "
                    f"{[m.value for m in IcebergSupport]}."
                ) from None
        if not isinstance(self.iceberg_support, IcebergSupport):
            raise TypeError("iceberg_support must be an IcebergSupport member.")
        if self.min_display_quantity < 1:
            raise ValueError("min_display_quantity must be >= 1.")
        if self.lot_size < 1:
            raise ValueError("lot_size must be >= 1.")
        if (
            not math.isfinite(self.client_refill_round_trip_ms)
            or self.client_refill_round_trip_ms < 0
        ):
            raise ValueError("client_refill_round_trip_ms must be finite and >= 0.")
        if self.iceberg_support is not IcebergSupport.UNSUPPORTED and not self.native_parameter_name:
            raise ValueError(
                "native_parameter_name is required when iceberg_support is "
                "NATIVE_EXCHANGE or BROKER_SIMULATED (e.g. 'displaySize', 'icebergQty', "
                "'DisplayQty')."
            )


@dataclass(frozen=True)
class IcebergOrderRequest:
    """A parent order to be worked as an iceberg."""

    symbol: str
    side: str
    total_quantity: int
    target_display_quantity: int
    limit_price: float
    slice_randomization_pct: float = 0.20
    time_in_force: str = "GTC"

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        if self.side not in VALID_SIDES:
            raise ValueError(f"side must be one of {VALID_SIDES}, got {self.side!r}.")
        for name in ("total_quantity", "target_display_quantity"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
            if value <= 0:
                raise ValueError(f"{name} must be > 0, got {value}.")
        if not isinstance(self.limit_price, (int, float)) or isinstance(self.limit_price, bool):
            raise TypeError("limit_price must be a real number.")
        if not math.isfinite(self.limit_price) or self.limit_price <= 0:
            raise ValueError(f"limit_price must be finite and > 0, got {self.limit_price!r}.")
        if (
            not math.isfinite(self.slice_randomization_pct)
            or not 0.0 <= self.slice_randomization_pct < 1.0
        ):
            raise ValueError(
                "slice_randomization_pct must be in [0.0, 1.0); a value of 1.0 or more "
                f"collapses the lower bound to a single lot. Got {self.slice_randomization_pct!r}."
            )


@dataclass(frozen=True)
class IcebergChildSlice:
    """One planned child order. Nothing here has been sent, acknowledged, or filled."""

    slice_id: str
    sequence: int
    slice_quantity: int
    limit_price: float
    status: str = "PLANNED"


@dataclass(frozen=True)
class IcebergExecutionReport:
    """The routing decision plus the payload or schedule it implies.

    ``client_refill_latency_ms_total`` is ``None`` - not ``0.0`` - whenever the client
    does not perform the refills. The venue-internal replenishment still costs time; it
    is simply not observable from here, and reporting it as zero has historically been
    read as "instant".
    """

    symbol: str
    side: str
    broker_name: str
    execution_mode: ExecutionMode
    total_quantity: int
    target_display_quantity: int
    effective_display_quantity: int
    native_order_parameters: Dict[str, object]
    planned_child_slices: List[IcebergChildSlice]
    planned_slice_count: int
    estimated_display_refills: int
    client_refill_latency_ms_total: Optional[float]
    loses_time_priority_on_refill: bool
    display_randomization: DisplayRandomization
    status: str
    warnings: List[str] = field(default_factory=list)
    audit_notes: str = ""


class IcebergExecutionRouterEngine:
    """Chooses between native, broker-simulated, and client-side iceberg execution.

    The engine is deterministic for a fixed seed: pass ``seed`` (or an existing
    ``random.Random``) so a synthetic slice schedule can be reproduced in a backtest,
    a regression test, or a post-trade investigation.

    Not thread-safe: the RNG is instance state, so concurrent calls interleave draws and
    neither schedule replays from the seed. Use one engine per worker.
    """

    def __init__(
        self,
        seed: Optional[int] = None,
        rng: Optional[random.Random] = None,
        max_child_slices: int = DEFAULT_MAX_CHILD_SLICES,
    ) -> None:
        if rng is not None and seed is not None:
            raise ValueError("Pass either seed or rng, not both.")
        if max_child_slices < 1:
            raise ValueError("max_child_slices must be >= 1.")
        self._rng: random.Random = rng if rng is not None else random.Random(seed)
        self.max_child_slices = max_child_slices

    # ------------------------------------------------------------------ helpers

    def resolve_effective_display_quantity(
        self, req: IcebergOrderRequest, venue: BrokerVenueConfig
    ) -> int:
        """Snap the requested peak to the venue's lot size and minimum display size.

        Nasdaq Rule 4703(h) rounds a mixed-lot displayed size *down* to the nearest round
        lot, so rounding down is the safe default; the result is then raised to the
        venue minimum if the round-down took it below.
        """
        lot = venue.lot_size
        display = (req.target_display_quantity // lot) * lot
        if display < venue.min_display_quantity:
            display = int(math.ceil(venue.min_display_quantity / lot)) * lot
        if display > req.total_quantity:
            display = req.total_quantity
        return display

    def _slice_bounds(
        self, display: int, randomization_pct: float, venue: BrokerVenueConfig
    ) -> Tuple[int, int]:
        """Lot-aligned ``[min, max]`` band for a randomised child slice."""
        lot = venue.lot_size
        floor_qty = max(venue.min_display_quantity, lot, int(display * (1.0 - randomization_pct)))
        ceil_qty = max(floor_qty, int(display * (1.0 + randomization_pct)))
        min_q = int(math.ceil(floor_qty / lot)) * lot
        max_q = (ceil_qty // lot) * lot
        if max_q < min_q:
            max_q = min_q
        return min_q, max_q

    def draw_synthetic_slice_size(
        self, display: int, randomization_pct: float, venue: BrokerVenueConfig
    ) -> int:
        """Draw one lot-aligned child slice size from the randomisation band.

        Randomisation raises the cost of recognising an identical repeated size. It does
        not hide the iceberg: cumulative traded volume versus displayed depth, and refill
        counting at a price level, both survive it (see
        ``iceberg-order-simulation-and-detection``).
        """
        min_q, max_q = self._slice_bounds(display, randomization_pct, venue)
        steps = (max_q - min_q) // venue.lot_size
        return min_q + self._rng.randint(0, steps) * venue.lot_size

    def plan_synthetic_slices(
        self, req: IcebergOrderRequest, venue: BrokerVenueConfig, display: int
    ) -> List[IcebergChildSlice]:
        """Build the full child-slice schedule for a client-side synthetic iceberg.

        The tail is the part that leaks: a schedule ending in a lone 51-share child order
        advertises both that a parent existed and that it is now exhausted. Any final
        slice below the venue's minimum display size is merged into its predecessor
        instead of being sent on its own.
        """
        min_q, _ = self._slice_bounds(display, req.slice_randomization_pct, venue)
        worst_case = math.ceil(req.total_quantity / min_q)
        if worst_case > self.max_child_slices:
            raise ValueError(
                f"Synthetic schedule needs up to {worst_case} child orders "
                f"(total={req.total_quantity:,}, min slice={min_q:,}), exceeding "
                f"max_child_slices={self.max_child_slices}. Raise the display quantity, "
                f"work the order over multiple sessions, or raise the limit deliberately - "
                f"this many messages per parent order is an order-to-trade-ratio risk."
            )

        quantities: List[int] = []
        remaining = req.total_quantity
        while remaining > 0:
            qty = min(
                self.draw_synthetic_slice_size(display, req.slice_randomization_pct, venue),
                remaining,
            )
            quantities.append(qty)
            remaining -= qty

        if len(quantities) > 1 and quantities[-1] < venue.min_display_quantity:
            # Pop first, then add. `quantities[-2] += quantities.pop()` reads the element
            # before the pop and writes it back after, so it credits the wrong slot and
            # silently loses the tail quantity.
            tail = quantities.pop()
            quantities[-1] += tail

        return [
            IcebergChildSlice(
                slice_id=f"SYNTH_{req.symbol}_{i:03d}",
                sequence=i,
                slice_quantity=qty,
                limit_price=req.limit_price,
                status="PLANNED",
            )
            for i, qty in enumerate(quantities, start=1)
        ]

    # -------------------------------------------------------------------- route

    def route_iceberg_order(
        self, req: IcebergOrderRequest, venue: BrokerVenueConfig
    ) -> IcebergExecutionReport:
        """Select an execution mode and produce the payload or schedule it requires.

        Returns a plan. It submits nothing, which is why every ``status`` value ends in
        ``PREPARED``.
        """
        warnings: List[str] = []
        display = self.resolve_effective_display_quantity(req, venue)
        if display != req.target_display_quantity:
            warnings.append(
                f"Display quantity adjusted {req.target_display_quantity:,} -> {display:,} "
                f"to satisfy {venue.broker_name} lot_size={venue.lot_size:,} / "
                f"min_display_quantity={venue.min_display_quantity:,}."
            )

        if display >= req.total_quantity:
            notes = (
                f"STANDARD LIMIT PREPARED [{req.symbol}]: effective display quantity "
                f"({display:,}) covers the full parent quantity ({req.total_quantity:,}); "
                f"nothing would be hidden, so no iceberg is used."
            )
            logger.info(notes)
            return IcebergExecutionReport(
                symbol=req.symbol,
                side=req.side,
                broker_name=venue.broker_name,
                execution_mode=ExecutionMode.STANDARD_LIMIT_ORDER,
                total_quantity=req.total_quantity,
                target_display_quantity=req.target_display_quantity,
                effective_display_quantity=req.total_quantity,
                native_order_parameters={},
                planned_child_slices=[],
                planned_slice_count=1,
                estimated_display_refills=0,
                client_refill_latency_ms_total=None,
                loses_time_priority_on_refill=False,
                display_randomization=DisplayRandomization.NONE,
                status="STANDARD_LIMIT_PAYLOAD_PREPARED",
                warnings=warnings,
                audit_notes=notes,
            )

        if venue.iceberg_support is IcebergSupport.UNSUPPORTED:
            return self._plan_synthetic(req, venue, display, warnings)
        return self._prepare_venue_side(req, venue, display, warnings)

    # -------------------------------------------------------------------- modes

    def _prepare_venue_side(
        self,
        req: IcebergOrderRequest,
        venue: BrokerVenueConfig,
        display: int,
        warnings: List[str],
    ) -> IcebergExecutionReport:
        """Native-exchange or broker-simulated iceberg: one parent order, no client refills."""
        native = venue.iceberg_support is IcebergSupport.NATIVE_EXCHANGE
        mode = ExecutionMode.NATIVE_ICEBERG if native else ExecutionMode.BROKER_SIMULATED_ICEBERG

        params: Dict[str, object] = {
            "symbol": req.symbol,
            "side": req.side,
            "quantity": req.total_quantity,
            "price": req.limit_price,
            venue.native_parameter_name: display,
        }

        if venue.requires_gtc_for_iceberg:
            if req.time_in_force != "GTC":
                raise ValueError(
                    f"{venue.broker_name} accepts {venue.native_parameter_name} only with "
                    f"timeInForce=GTC; request specifies {req.time_in_force!r}. Submitting "
                    f"it would be rejected - set GTC, or route this order without an "
                    f"iceberg parameter."
                )
            params["timeInForce"] = "GTC"

        if venue.supports_native_display_randomization:
            randomization = DisplayRandomization.NATIVE_EXCHANGE
        else:
            randomization = DisplayRandomization.NONE
            if req.slice_randomization_pct > 0:
                warnings.append(
                    f"slice_randomization_pct={req.slice_randomization_pct:.0%} is ignored: "
                    f"{venue.broker_name} refills at a fixed peak and exposes no native "
                    f"randomisation parameter."
                )

        if not native:
            warnings.append(
                "Broker-simulated iceberg: the reserve quantity rests at the broker, not "
                "at the exchange. It is exposed to broker outages and to broker-side "
                "refill latency, and it is not covered by exchange order-handling rules."
            )

        # Valid only if the venue refills at exactly `display` and no partial fills occur.
        refills = max(0, -(-req.total_quantity // display) - 1)

        notes = (
            f"{'NATIVE' if native else 'BROKER-SIMULATED'} ICEBERG PREPARED "
            f"[{req.symbol} on {venue.broker_name}]: one parent order "
            f"(total={req.total_quantity:,}, {venue.native_parameter_name}={display:,}). "
            f"No client round trip per refill. Each replenishment still receives a new "
            f"timestamp and goes to the back of the queue at that price level."
        )
        logger.info(notes)

        return IcebergExecutionReport(
            symbol=req.symbol,
            side=req.side,
            broker_name=venue.broker_name,
            execution_mode=mode,
            total_quantity=req.total_quantity,
            target_display_quantity=req.target_display_quantity,
            effective_display_quantity=display,
            native_order_parameters=params,
            planned_child_slices=[],
            planned_slice_count=1,
            estimated_display_refills=refills,
            client_refill_latency_ms_total=None,
            loses_time_priority_on_refill=True,
            display_randomization=randomization,
            status="NATIVE_PAYLOAD_PREPARED" if native else "BROKER_SIMULATED_PAYLOAD_PREPARED",
            warnings=warnings,
            audit_notes=notes,
        )

    def _plan_synthetic(
        self,
        req: IcebergOrderRequest,
        venue: BrokerVenueConfig,
        display: int,
        warnings: List[str],
    ) -> IcebergExecutionReport:
        """Client-side synthetic iceberg: one child order per displayed slice."""
        slices = self.plan_synthetic_slices(req, venue, display)
        refills = len(slices) - 1
        latency_total = round(refills * venue.client_refill_round_trip_ms, 2)

        if venue.client_refill_round_trip_ms <= 0:
            warnings.append(
                "client_refill_round_trip_ms is 0, so the refill latency total is 0 ms. "
                "That is a missing estimate, not a measured result - calibrate it from "
                "your own fill-to-acknowledgement telemetry."
            )
        warnings.append(
            f"Synthetic iceberg sends {len(slices)} order messages for one parent order. "
            f"Check this against venue order-to-trade-ratio limits and message-rate fees."
        )
        warnings.append(
            "The parent quantity is only worked while the client is connected. A "
            "disconnect between slices leaves the remainder unworked and the last child "
            "order live - reconcile open orders on reconnect before sending the next slice."
        )

        # A non-zero percentage does not guarantee varied sizes: once the band is snapped
        # to lot_size and floored at min_display_quantity it can collapse to a single
        # value. Report what the schedule actually does, not what was requested.
        band_min, band_max = self._slice_bounds(display, req.slice_randomization_pct, venue)
        randomization = (
            DisplayRandomization.CLIENT_SIDE
            if req.slice_randomization_pct > 0 and band_max > band_min
            else DisplayRandomization.NONE
        )
        if randomization is DisplayRandomization.NONE:
            if req.slice_randomization_pct > 0:
                warnings.append(
                    f"slice_randomization_pct={req.slice_randomization_pct:.0%} collapses to a "
                    f"single size of {band_min:,} once snapped to lot_size={venue.lot_size:,} "
                    f"and floored at min_display_quantity={venue.min_display_quantity:,}. "
                    f"Every child order will be identical."
                )
            else:
                warnings.append(
                    "slice_randomization_pct=0 emits identical child sizes, the easiest "
                    "synthetic-iceberg signature to recognise."
                )

        notes = (
            f"SYNTHETIC ICEBERG PLANNED [{req.symbol} on {venue.broker_name}]: venue has no "
            f"iceberg support, so the client slices. {len(slices)} child orders "
            f"(display={display:,} +/- {req.slice_randomization_pct:.0%}, lot={venue.lot_size:,}), "
            f"{refills} client refills, estimated {latency_total:.1f} ms total refill latency "
            f"at {venue.client_refill_round_trip_ms:.1f} ms per round trip. No order has been sent."
        )
        logger.warning(notes)

        return IcebergExecutionReport(
            symbol=req.symbol,
            side=req.side,
            broker_name=venue.broker_name,
            execution_mode=ExecutionMode.SYNTHETIC_SIMULATION,
            total_quantity=req.total_quantity,
            target_display_quantity=req.target_display_quantity,
            effective_display_quantity=display,
            native_order_parameters={},
            planned_child_slices=slices,
            planned_slice_count=len(slices),
            estimated_display_refills=refills,
            client_refill_latency_ms_total=latency_total,
            loses_time_priority_on_refill=True,
            display_randomization=randomization,
            status="SYNTHETIC_PLAN_PREPARED",
            warnings=warnings,
            audit_notes=notes,
        )
