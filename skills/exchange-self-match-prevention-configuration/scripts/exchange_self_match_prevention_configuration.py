"""Pre-trade Self-Match Prevention (SMP / STP) configuration and collision audit.

This module does two separate things, and conflating them is the most common way
to misuse it:

1. **Encoding** -- map an internal SMP instruction onto the *wire* field and value
   a specific venue actually accepts (CME iLink 2 tag 7928/8000, iLink 3 tag 2362
   with tag 8000, FIX 5.0 SP2 tag 2362/2964, Coinbase Exchange ``stp``). The venue
   enforces SMP; this module only produces the fields that turn it on.

2. **Auditing** -- predict, from a local order-book snapshot, whether the order
   about to be sent would trigger SMP against the firm's own resting orders, and
   what the venue would do about it.

The audit is a *prediction* against a snapshot that is already stale by the time
the order reaches the matching engine. It is a pre-trade risk and surveillance
signal, never a substitute for the venue's SMP flags and never a licence for the
client to issue its own cancels -- doing so races the exchange's own SMP cancel.

References: see ``../references/standards.md``.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Instruction vocabulary
# --------------------------------------------------------------------------

CANCEL_RESTING = "CANCEL_RESTING"
CANCEL_AGGRESSIVE = "CANCEL_AGGRESSIVE"
CANCEL_BOTH = "CANCEL_BOTH"
DECREMENT_AND_CANCEL = "DECREMENT_AND_CANCEL"

SMP_INSTRUCTIONS: Tuple[str, ...] = (
    CANCEL_RESTING,
    CANCEL_AGGRESSIVE,
    CANCEL_BOTH,
    DECREMENT_AND_CANCEL,
)

# Decrement models. Venues that support DECREMENT_AND_CANCEL do not agree on it.
DECREMENT_SYMMETRIC = "SYMMETRIC"
"""min(aggressor, resting) is removed from both; the smaller side cancels in full
(Nasdaq 'decrement' behaviour)."""

DECREMENT_CANCEL_AGGRESSOR = "CANCEL_AGGRESSOR_DECREMENT_RESTING"
"""The aggressor cancels in full and the resting order decrements by the
aggressor's quantity (Coinbase Exchange ``stp='dc'`` behaviour)."""

BUY = "BUY"
SELL = "SELL"


class SmpConfigurationError(ValueError):
    """Raised for an unusable SMP configuration or malformed order input.

    Never caught-and-defaulted inside this module: silently substituting a
    fallback instruction is how a typo disables a wash-trade control.
    """


# --------------------------------------------------------------------------
# Venue wire encoding
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SmpVenueProfile:
    """How one venue names and values its SMP fields.

    ``wire_values`` is the whole supported set: an instruction absent from it is
    rejected rather than silently downgraded, because a venue that does not offer
    (say) cancel-both will not behave "close enough" to it.
    """

    venue: str
    smp_id_tag: str
    smp_instruction_tag: str
    wire_values: Mapping[str, str]
    default_instruction: Optional[str]
    decrement_model: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        # The dataclass is frozen but a plain dict is not: without this a caller
        # could add CANCEL_BOTH to the shipped Globex profile at runtime and the
        # engine would happily encode an instruction the venue rejects.
        object.__setattr__(self, "wire_values", MappingProxyType(dict(self.wire_values)))

    def supported_instructions(self) -> Tuple[str, ...]:
        return tuple(i for i in SMP_INSTRUCTIONS if i in self.wire_values)


CME_ILINK2 = SmpVenueProfile(
    venue="CME_ILINK2",
    smp_id_tag="7928",
    smp_instruction_tag="8000",
    # Globex offers cancel-resting ("O", Old) and cancel-aggressing ("N") only.
    wire_values={CANCEL_RESTING: "O", CANCEL_AGGRESSIVE: "N"},
    default_instruction=CANCEL_RESTING,
    notes=(
        "Globex cancels the resting order(s) by default when tag 8000 is absent. "
        "SMP does not operate during the pre-open / opening match (CME MRAN RA1614-5)."
    ),
)

CME_ILINK3 = SmpVenueProfile(
    venue="CME_ILINK3",
    smp_id_tag="2362",
    smp_instruction_tag="8000",
    wire_values={CANCEL_RESTING: "O", CANCEL_AGGRESSIVE: "N"},
    default_instruction=CANCEL_RESTING,
    notes="iLink 3 carries the SMP ID in tag 2362; the instruction stays in tag 8000.",
)

FIX_LATEST = SmpVenueProfile(
    venue="FIX_LATEST",
    smp_id_tag="2362",
    smp_instruction_tag="2964",
    # FIX 5.0 SP2 EP299 SelfMatchPreventionInstruction(2964) is an int enum.
    wire_values={CANCEL_AGGRESSIVE: "1", CANCEL_RESTING: "2", CANCEL_BOTH: "3"},
    default_instruction=None,
    notes="Base FIX defines no decrement value; decrement is a venue extension.",
)

COINBASE_EXCHANGE = SmpVenueProfile(
    venue="COINBASE_EXCHANGE",
    smp_id_tag="profile_id",
    smp_instruction_tag="stp",
    wire_values={
        DECREMENT_AND_CANCEL: "dc",
        CANCEL_RESTING: "co",
        CANCEL_AGGRESSIVE: "cn",
        CANCEL_BOTH: "cb",
    },
    default_instruction=DECREMENT_AND_CANCEL,
    decrement_model=DECREMENT_CANCEL_AGGRESSOR,
    notes=(
        "Self-trade scope is the account/profile, not a client-chosen group ID. "
        "'dc' cancels the taker and decrements the maker - not the symmetric model."
    ),
)

NASDAQ_INET = SmpVenueProfile(
    venue="NASDAQ_INET",
    smp_id_tag="group_id",
    smp_instruction_tag="stp_action",
    # Nordic OUCH 5 STP Action: 1 cancel passive, 2 cancel aggressive, 3 cancel both.
    wire_values={CANCEL_RESTING: "1", CANCEL_AGGRESSIVE: "2", CANCEL_BOTH: "3"},
    default_instruction=None,
    decrement_model=DECREMENT_SYMMETRIC,
    notes=(
        "STP is configured per MPID/port; a Group ID only narrows it further. "
        "The decrement variant is a port-level configuration with no per-order "
        "STP Action value, so DECREMENT_AND_CANCEL is deliberately not in "
        "wire_values - simulate it against a profile you have confirmed."
    ),
)

SMP_VENUE_PROFILES: Dict[str, SmpVenueProfile] = {
    p.venue: p
    for p in (CME_ILINK2, CME_ILINK3, FIX_LATEST, COINBASE_EXCHANGE, NASDAQ_INET)
}


@dataclass(frozen=True)
class SmpWireFields:
    """The venue-specific field/value pairs to attach to the outbound order."""

    venue: str
    smp_id_tag: str
    smp_id_wire_value: str
    smp_instruction_tag: str
    smp_instruction_wire_value: str


# --------------------------------------------------------------------------
# Order models
# --------------------------------------------------------------------------


@dataclass
class RestingBookOrder:
    """One of the firm's own working orders, as last known locally.

    ``entry_seq`` is the venue-assigned time-priority sequence within a price
    level, lowest first. Left at ``None`` the audit falls back to the position of
    the order in the input sequence, which is only as reliable as the caller's
    ordering.
    """

    cl_ord_id: str
    symbol: str
    side: str
    order_qty: int
    price: float
    smp_id: str
    entry_seq: Optional[int] = None


@dataclass
class SmpOrderRequest:
    """The order about to be sent.

    ``price = None`` means an unpriced (market) order, which crosses every
    opposing price level and therefore collides with *every* resting order
    carrying the same SMP ID.

    ``smp_instruction`` may be left blank to inherit the engine default.
    """

    cl_ord_id: str
    symbol: str
    side: str
    order_qty: int
    price: Optional[float]
    smp_id: str
    smp_instruction: str = ""


@dataclass(frozen=True)
class SmpCollision:
    """One predicted SMP event between the incoming order and a resting order."""

    resting_cl_ord_id: str
    resting_side: str
    resting_price: float
    resting_qty: int
    resting_qty_cancelled: int
    resting_qty_remaining: int
    aggressor_qty_cancelled: int


@dataclass(frozen=True)
class SmpAuditReport:
    """Pre-trade prediction of the venue's SMP outcome for one order."""

    cl_ord_id: str
    symbol: str
    smp_id: str
    smp_instruction: str
    instruction_source: str  # 'REQUEST' or 'ENGINE_DEFAULT'
    venue: str
    has_self_collision: bool
    collisions: Tuple[SmpCollision, ...]
    colliding_resting_ord_id: Optional[str]
    is_order_dispatched: bool
    dispatched_qty: int
    resting_order_cancelled: bool
    resting_cl_ord_ids_cancelled: Tuple[str, ...]
    smp_id_tag: str
    smp_id_wire_value: str
    smp_instruction_tag: str
    smp_instruction_wire_value: str
    audit_notes: str


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------


class ExchangeSelfMatchPreventionEngine:
    """Encodes venue SMP fields and audits an order against the firm's own book.

    Args:
        default_smp_instruction: applied when a request leaves ``smp_instruction``
            blank. Must be supported by ``venue``. Pass ``None`` to make the
            instruction mandatory on every request.
        venue: key into :data:`SMP_VENUE_PROFILES`, or an ``SmpVenueProfile``.
        require_smp_id: when true (the default) a blank ``smp_id`` is rejected.
            A blank SMP ID means SMP is *off* at the venue, so it must be an
            explicit decision rather than a missing field.
    """

    def __init__(
        self,
        default_smp_instruction: Optional[str] = CANCEL_RESTING,
        venue: object = CME_ILINK2,
        require_smp_id: bool = True,
    ) -> None:
        self.profile = self._resolve_profile(venue)
        self.require_smp_id = bool(require_smp_id)

        if default_smp_instruction is None:
            self.default_smp_instruction: Optional[str] = None
        else:
            normalised = self._normalise_instruction(default_smp_instruction)
            self._require_supported(normalised)
            self.default_smp_instruction = normalised

    # -- configuration -----------------------------------------------------

    @staticmethod
    def _resolve_profile(venue: object) -> SmpVenueProfile:
        if isinstance(venue, SmpVenueProfile):
            return venue
        if isinstance(venue, str):
            key = venue.strip().upper()
            if key in SMP_VENUE_PROFILES:
                return SMP_VENUE_PROFILES[key]
            raise SmpConfigurationError(
                f"unknown venue {venue!r}; known venues: {sorted(SMP_VENUE_PROFILES)}"
            )
        raise SmpConfigurationError(
            f"venue must be a profile key or SmpVenueProfile, got {type(venue).__name__}"
        )

    @staticmethod
    def _normalise_instruction(instruction: object) -> str:
        if not isinstance(instruction, str):
            raise SmpConfigurationError(
                f"smp_instruction must be a string, got {type(instruction).__name__}"
            )
        value = instruction.strip().upper()
        if value not in SMP_INSTRUCTIONS:
            raise SmpConfigurationError(
                f"unknown SMP instruction {instruction!r}; "
                f"expected one of {list(SMP_INSTRUCTIONS)}"
            )
        return value

    def _require_supported(self, instruction: str) -> None:
        if instruction not in self.profile.wire_values:
            raise SmpConfigurationError(
                f"{self.profile.venue} does not support {instruction}; "
                f"supported: {list(self.profile.supported_instructions())}"
            )

    def encode_smp_fields(self, smp_id: str, instruction: str) -> SmpWireFields:
        """Map an SMP ID and instruction onto this venue's wire fields.

        Raises:
            SmpConfigurationError: unknown instruction, an instruction the venue
                does not offer, or a blank SMP ID while ``require_smp_id``.
        """
        clean_id = self._validate_smp_id(smp_id)
        normalised = self._normalise_instruction(instruction)
        self._require_supported(normalised)
        return SmpWireFields(
            venue=self.profile.venue,
            smp_id_tag=self.profile.smp_id_tag,
            smp_id_wire_value=clean_id,
            smp_instruction_tag=self.profile.smp_instruction_tag,
            smp_instruction_wire_value=self.profile.wire_values[normalised],
        )

    def _validate_smp_id(self, smp_id: object) -> str:
        if smp_id is None:
            clean = ""
        elif isinstance(smp_id, str):
            clean = smp_id.strip()
        else:
            raise SmpConfigurationError(
                f"smp_id must be a string, got {type(smp_id).__name__}"
            )
        if not clean and self.require_smp_id:
            raise SmpConfigurationError(
                "blank smp_id: self-match prevention would be disabled at the venue. "
                "Set require_smp_id=False to route without SMP deliberately."
            )
        return clean

    # -- input validation --------------------------------------------------

    @staticmethod
    def _validate_side(side: object, label: str) -> str:
        if not isinstance(side, str):
            raise SmpConfigurationError(
                f"{label} side must be a string, got {type(side).__name__}"
            )
        value = side.strip().upper()
        if value not in (BUY, SELL):
            raise SmpConfigurationError(
                f"{label} side must be {BUY!r} or {SELL!r}, got {side!r}"
            )
        return value

    @staticmethod
    def _validate_qty(qty: object, label: str) -> int:
        if isinstance(qty, bool) or not isinstance(qty, int):
            raise SmpConfigurationError(
                f"{label} order_qty must be an int, got {type(qty).__name__}"
            )
        if qty <= 0:
            raise SmpConfigurationError(f"{label} order_qty must be > 0, got {qty}")
        return qty

    @staticmethod
    def _validate_price(
        price: object, label: str, allow_market: bool
    ) -> Optional[float]:
        if price is None:
            if allow_market:
                return None
            raise SmpConfigurationError(f"{label} price must not be None")
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise SmpConfigurationError(
                f"{label} price must be numeric, got {type(price).__name__}"
            )
        value = float(price)
        if not math.isfinite(value):
            raise SmpConfigurationError(f"{label} price must be finite, got {price!r}")
        if value <= 0.0:
            raise SmpConfigurationError(f"{label} price must be > 0, got {price!r}")
        return value

    @staticmethod
    def _validate_id(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SmpConfigurationError(f"{label} must be a non-empty string")
        return value.strip()

    # -- collision audit ---------------------------------------------------

    def _crosses(
        self,
        aggressor_side: str,
        aggressor_price: Optional[float],
        resting_price: float,
    ) -> bool:
        """True when the incoming order is priced to reach the resting order.

        An unpriced (market) aggressor crosses every level on the opposite side.
        Prices are compared exactly; feed tick-aligned values, because a float
        one ULP short of the resting price silently reports "no collision".
        """
        if aggressor_price is None:
            return True
        if aggressor_side == BUY:
            return aggressor_price >= resting_price
        return aggressor_price <= resting_price

    def _find_collisions(
        self, req: SmpOrderRequest, resting_orders: Sequence[RestingBookOrder]
    ) -> List[RestingBookOrder]:
        """Every own-book order the incoming order would reach, in match order.

        Match order is price priority first (the best price the aggressor reaches
        first), then time priority within a level. Returning only the first hit
        would understate a sweep: venues cancel *every* resting order carrying
        the matching SMP ID at each executable price level.
        """
        side = self._validate_side(req.side, "request")
        symbol = self._validate_id(req.symbol, "request symbol")
        price = self._validate_price(req.price, "request", allow_market=True)
        smp_id = self._validate_smp_id(req.smp_id)
        opposite = SELL if side == BUY else BUY

        matches: List[Tuple[float, int, int, RestingBookOrder]] = []
        for index, r_ord in enumerate(resting_orders):
            if not isinstance(r_ord, RestingBookOrder):
                raise SmpConfigurationError(
                    f"resting_orders[{index}] must be a RestingBookOrder, "
                    f"got {type(r_ord).__name__}"
                )
            r_symbol = self._validate_id(r_ord.symbol, f"resting_orders[{index}] symbol")
            r_side = self._validate_side(r_ord.side, f"resting_orders[{index}]")
            r_price = self._validate_price(
                r_ord.price, f"resting_orders[{index}]", allow_market=False
            )
            self._validate_qty(r_ord.order_qty, f"resting_orders[{index}]")
            r_smp = r_ord.smp_id.strip() if isinstance(r_ord.smp_id, str) else ""

            if r_symbol != symbol or r_side != opposite:
                continue
            # A blank SMP ID means SMP is off for that order; two blanks are not
            # a group and must never be treated as a self-match.
            if not r_smp or not smp_id or r_smp != smp_id:
                continue
            if not self._crosses(side, price, r_price):
                continue

            # Best price for the aggressor first: lowest ask for a buy, highest
            # bid for a sell. entry_seq (else input order) breaks the tie.
            price_key = r_price if side == BUY else -r_price
            seq = r_ord.entry_seq if r_ord.entry_seq is not None else index
            matches.append((price_key, seq, index, r_ord))

        matches.sort(key=lambda m: (m[0], m[1], m[2]))
        return [m[3] for m in matches]

    def audit_and_apply_smp(
        self,
        req: SmpOrderRequest,
        resting_orders: Optional[Sequence[RestingBookOrder]] = None,
    ) -> SmpAuditReport:
        """Predict the venue's SMP outcome and produce the wire fields.

        The prediction is made against the snapshot in ``resting_orders`` and is
        therefore advisory: the venue, not this engine, performs the cancels.

        Raises:
            SmpConfigurationError: on any malformed input, an unknown or
                venue-unsupported instruction, or a blank SMP ID while
                ``require_smp_id`` is set. Nothing is silently defaulted.
        """
        resting_orders = list(resting_orders or [])

        cl_ord_id = self._validate_id(req.cl_ord_id, "request cl_ord_id")
        symbol = self._validate_id(req.symbol, "request symbol")
        self._validate_side(req.side, "request")
        order_qty = self._validate_qty(req.order_qty, "request")
        smp_id = self._validate_smp_id(req.smp_id)

        raw = req.smp_instruction if isinstance(req.smp_instruction, str) else ""
        if raw.strip():
            instruction = self._normalise_instruction(raw)
            instruction_source = "REQUEST"
        elif self.default_smp_instruction is not None:
            instruction = self.default_smp_instruction
            instruction_source = "ENGINE_DEFAULT"
        else:
            raise SmpConfigurationError(
                f"[{cl_ord_id}] smp_instruction is required: the engine has no "
                f"default for venue {self.profile.venue}."
            )
        self._require_supported(instruction)

        if instruction == DECREMENT_AND_CANCEL and self.profile.decrement_model is None:
            raise SmpConfigurationError(
                f"{self.profile.venue} declares no decrement model; "
                f"{DECREMENT_AND_CANCEL} cannot be simulated for this venue."
            )

        wire = self.encode_smp_fields(smp_id, instruction)
        colliding = self._find_collisions(req, resting_orders)

        if not colliding:
            notes = (
                f"SMP CLEAR [{cl_ord_id}]: no own-book collision in the snapshot. "
                f"Routed to {self.profile.venue} with "
                f"{wire.smp_id_tag}={wire.smp_id_wire_value}, "
                f"{wire.smp_instruction_tag}={wire.smp_instruction_wire_value}."
            )
            logger.info(
                "smp_audit cl_ord_id=%s venue=%s instruction=%s collisions=0 "
                "dispatched_qty=%d",
                cl_ord_id,
                self.profile.venue,
                instruction,
                order_qty,
            )
            return SmpAuditReport(
                cl_ord_id=cl_ord_id,
                symbol=symbol,
                smp_id=smp_id,
                smp_instruction=instruction,
                instruction_source=instruction_source,
                venue=self.profile.venue,
                has_self_collision=False,
                collisions=(),
                colliding_resting_ord_id=None,
                is_order_dispatched=True,
                dispatched_qty=order_qty,
                resting_order_cancelled=False,
                resting_cl_ord_ids_cancelled=(),
                smp_id_tag=wire.smp_id_tag,
                smp_id_wire_value=wire.smp_id_wire_value,
                smp_instruction_tag=wire.smp_instruction_tag,
                smp_instruction_wire_value=wire.smp_instruction_wire_value,
                audit_notes=notes,
            )

        collisions, dispatched_qty = self._apply_instruction(
            instruction, order_qty, colliding
        )
        cancelled_ids = tuple(
            c.resting_cl_ord_id for c in collisions if c.resting_qty_cancelled > 0
        )

        notes = (
            f"SMP COLLISION [{cl_ord_id}]: {instruction} against {len(collisions)} own "
            f"resting order(s) {[c.resting_cl_ord_id for c in collisions]}. "
            f"Predicted outcome: {dispatched_qty}/{order_qty} of the incoming order "
            f"survives; resting cancelled/decremented: {list(cancelled_ids)}. "
            f"The venue performs these cancels - do not issue them locally."
        )
        logger.warning(
            "smp_audit cl_ord_id=%s venue=%s instruction=%s collisions=%d "
            "dispatched_qty=%d of %d resting_cancelled=%s",
            cl_ord_id,
            self.profile.venue,
            instruction,
            len(collisions),
            dispatched_qty,
            order_qty,
            list(cancelled_ids),
        )

        return SmpAuditReport(
            cl_ord_id=cl_ord_id,
            symbol=symbol,
            smp_id=smp_id,
            smp_instruction=instruction,
            instruction_source=instruction_source,
            venue=self.profile.venue,
            has_self_collision=True,
            collisions=tuple(collisions),
            colliding_resting_ord_id=collisions[0].resting_cl_ord_id,
            is_order_dispatched=dispatched_qty > 0,
            dispatched_qty=dispatched_qty,
            resting_order_cancelled=bool(cancelled_ids),
            resting_cl_ord_ids_cancelled=cancelled_ids,
            smp_id_tag=wire.smp_id_tag,
            smp_id_wire_value=wire.smp_id_wire_value,
            smp_instruction_tag=wire.smp_instruction_tag,
            smp_instruction_wire_value=wire.smp_instruction_wire_value,
            audit_notes=notes,
        )

    def _apply_instruction(
        self,
        instruction: str,
        order_qty: int,
        colliding: Sequence[RestingBookOrder],
    ) -> Tuple[List[SmpCollision], int]:
        """Simulate the venue action. Returns (collisions, surviving aggressor qty)."""
        collisions: List[SmpCollision] = []

        if instruction == CANCEL_RESTING:
            # Every own resting order the aggressor reaches is cancelled in full;
            # the aggressor continues and may still trade third-party liquidity.
            for r in colliding:
                collisions.append(
                    SmpCollision(
                        resting_cl_ord_id=r.cl_ord_id,
                        resting_side=r.side.strip().upper(),
                        resting_price=float(r.price),
                        resting_qty=r.order_qty,
                        resting_qty_cancelled=r.order_qty,
                        resting_qty_remaining=0,
                        aggressor_qty_cancelled=0,
                    )
                )
            return collisions, order_qty

        if instruction in (CANCEL_AGGRESSIVE, CANCEL_BOTH):
            # The aggressor is pulled at its first own-book contact, so resting
            # orders deeper in the book are never reached and are not reported.
            first = colliding[0]
            cancel_resting = first.order_qty if instruction == CANCEL_BOTH else 0
            collisions.append(
                SmpCollision(
                    resting_cl_ord_id=first.cl_ord_id,
                    resting_side=first.side.strip().upper(),
                    resting_price=float(first.price),
                    resting_qty=first.order_qty,
                    resting_qty_cancelled=cancel_resting,
                    resting_qty_remaining=first.order_qty - cancel_resting,
                    aggressor_qty_cancelled=order_qty,
                )
            )
            return collisions, 0

        # DECREMENT_AND_CANCEL
        if self.profile.decrement_model == DECREMENT_CANCEL_AGGRESSOR:
            first = colliding[0]
            taken = min(order_qty, first.order_qty)
            collisions.append(
                SmpCollision(
                    resting_cl_ord_id=first.cl_ord_id,
                    resting_side=first.side.strip().upper(),
                    resting_price=float(first.price),
                    resting_qty=first.order_qty,
                    resting_qty_cancelled=taken,
                    resting_qty_remaining=first.order_qty - taken,
                    aggressor_qty_cancelled=order_qty,
                )
            )
            return collisions, 0

        # DECREMENT_SYMMETRIC: min(aggressor, resting) leaves both sides and the
        # aggressor walks the book until exhausted.
        remaining = order_qty
        for r in colliding:
            if remaining <= 0:
                break
            taken = min(remaining, r.order_qty)
            remaining -= taken
            collisions.append(
                SmpCollision(
                    resting_cl_ord_id=r.cl_ord_id,
                    resting_side=r.side.strip().upper(),
                    resting_price=float(r.price),
                    resting_qty=r.order_qty,
                    resting_qty_cancelled=taken,
                    resting_qty_remaining=r.order_qty - taken,
                    aggressor_qty_cancelled=taken,
                )
            )
        return collisions, remaining
