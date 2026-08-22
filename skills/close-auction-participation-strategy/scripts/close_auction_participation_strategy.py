"""close-auction-participation-strategy.

Parses closing-auction imbalance feeds (Nasdaq NOII / NYSE closing imbalance
publication) and derives contra-side Limit-On-Close (LOC) orders that provide
liquidity against a published imbalance.

Venue timing rules encoded here (all times US/Eastern):

Nasdaq Closing Cross (Nasdaq Equity Rule 4754; Nasdaq Opening & Closing Crosses
FAQ, 2025):
  * NOII dissemination for the Closing Cross begins at 15:50, every 10 seconds
    until 15:55, then every second until 16:00.
  * Near and Far Indicative Clearing Prices are only disseminated from 15:55
    onward. Before 15:55 the NOII carries Current Reference Price, Paired
    Shares, Imbalance Shares and Imbalance Direction only.
  * MOC orders must be received before 15:55; LOC orders before 15:58. An LOC
    entered after 15:55 is accepted at its limit price unless that limit is more
    aggressive than the 15:50 / 15:55 Reference Prices, in which case it is
    re-priced; if there is no 15:55 Reference Price it is not accepted at all.
  * On-close orders may only be modified or cancelled before 15:50.

NYSE Closing Auction (NYSE Rule 7.35B; NYSE Opening and Closing Auctions fact
sheet, 2024):
  * MOC/LOC may be entered, modified or cancelled until 15:50.
  * After 15:50 they may be entered only on the contra side of a published
    MOC/LOC Significant Imbalance, until 16:00, and may no longer be modified
    or cancelled.
  * Imbalance information is published from 15:50 every second when changed,
    including paired quantity, total imbalance quantity, the closing-only
    interest price (far) and the continuous book clearing price (near).

The 15:50 cancel/modify freeze is the dominant risk of this strategy: once the
contra-side order is working it cannot be pulled, so sizing and price
protection must be correct at submission time.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

US_EASTERN = ZoneInfo("America/New_York")

# ITCH 5.0 NOII Imbalance Direction codes (Nasdaq TotalView-ITCH 5.0 spec,
# section 1.6). 'O' and 'P' are non-actionable states, not imbalance sides.
IMBALANCE_DIRECTIONS = frozenset({"B", "S", "N", "O", "P"})
ACTIONABLE_DIRECTIONS = frozenset({"B", "S"})

# ITCH 5.0 NOII Cross Type codes. Only 'C' (Nasdaq Closing Cross) is in scope
# for this skill; 'O' (opening), 'H' (halt/IPO) and 'A' (Extended Trading
# Close) crosses have different order types and cutoffs.
CLOSING_CROSS_TYPE = "C"


class AuctionVenue(Enum):
    """Primary listing venue whose closing-auction rules apply."""

    NASDAQ = "NASDAQ"
    NYSE = "NYSE"


class PriceBasis(Enum):
    """Which indicative clearing price the LOC limit is derived from."""

    FAR = "FAR"    # auction-only book (Nasdaq Far Price / NYSE closing-only interest price)
    NEAR = "NEAR"  # auction book plus continuous book


class DecisionReason(Enum):
    """Why an auction order was or was not generated."""

    ORDER_GENERATED = "ORDER_GENERATED"
    WRONG_CROSS_TYPE = "WRONG_CROSS_TYPE"
    PAST_ENTRY_CUTOFF = "PAST_ENTRY_CUTOFF"
    ENTRY_FROZEN_NO_PUBLISHED_IMBALANCE = "ENTRY_FROZEN_NO_PUBLISHED_IMBALANCE"
    SECURITY_PAUSED = "SECURITY_PAUSED"
    NO_ACTIONABLE_IMBALANCE = "NO_ACTIONABLE_IMBALANCE"
    IMBALANCE_BELOW_MIN_SHARES = "IMBALANCE_BELOW_MIN_SHARES"
    IMBALANCE_RATIO_BELOW_THRESHOLD = "IMBALANCE_RATIO_BELOW_THRESHOLD"
    INDICATIVE_PRICE_UNAVAILABLE = "INDICATIVE_PRICE_UNAVAILABLE"
    STALE_IMBALANCE_DATA = "STALE_IMBALANCE_DATA"
    QUANTITY_ROUNDS_TO_ZERO = "QUANTITY_ROUNDS_TO_ZERO"


@dataclass(frozen=True)
class VenueAuctionRules:
    """Venue-specific closing-auction timing rules (US/Eastern)."""

    venue: AuctionVenue
    # Latest time a new MOC order is accepted (exclusive bound).
    moc_entry_cutoff_et: time
    # Latest time a new LOC order is accepted without a contra-side condition.
    loc_entry_cutoff_et: time
    # Latest time a LOC contra to a *published* imbalance is accepted. Equal to
    # loc_entry_cutoff_et on venues with no contra-side carve-out.
    contra_side_entry_cutoff_et: time
    # After this time on-close orders can no longer be modified or cancelled.
    cancel_modify_freeze_et: time
    # First time the venue disseminates indicative clearing prices (near/far).
    indicative_price_available_from_et: time
    # True when the venue accepts contra-side entry after the freeze, provided a
    # significant imbalance has been published (NYSE Rule 7.35B).
    contra_side_entry_after_freeze: bool
    cross_time_et: time = time(16, 0)


NASDAQ_CLOSING_RULES = VenueAuctionRules(
    venue=AuctionVenue.NASDAQ,
    moc_entry_cutoff_et=time(15, 55),
    loc_entry_cutoff_et=time(15, 58),
    contra_side_entry_cutoff_et=time(15, 58),
    cancel_modify_freeze_et=time(15, 50),
    indicative_price_available_from_et=time(15, 55),
    contra_side_entry_after_freeze=False,
)

NYSE_CLOSING_RULES = VenueAuctionRules(
    venue=AuctionVenue.NYSE,
    moc_entry_cutoff_et=time(15, 50),
    loc_entry_cutoff_et=time(15, 50),
    contra_side_entry_cutoff_et=time(16, 0),
    cancel_modify_freeze_et=time(15, 50),
    indicative_price_available_from_et=time(15, 50),
    contra_side_entry_after_freeze=True,
)

VENUE_RULES = {
    AuctionVenue.NASDAQ: NASDAQ_CLOSING_RULES,
    AuctionVenue.NYSE: NYSE_CLOSING_RULES,
}

# A Nasdaq LOC entered at or after this time is accepted at its limit price only
# if that limit is no more aggressive than the 15:50 / 15:55 Reference Prices;
# otherwise it is re-priced, and it is rejected outright if no 15:55 Reference
# Price exists (Nasdaq Opening & Closing Crosses FAQ, "On-Close Orders").
NASDAQ_LATE_LOC_REPRICE_FROM_ET = time(15, 55)


@dataclass
class NoiiMessage:
    """One closing-auction imbalance observation.

    Field names follow the Nasdaq TotalView-ITCH 5.0 NOII message (section 1.6).
    NYSE callers map: paired quantity -> ``paired_shares``, total imbalance
    quantity -> ``imbalance_shares``, closing-only interest price ->
    ``far_price``, continuous book clearing price -> ``near_price``.

    Attributes:
        timestamp: Feed timestamp. MUST be timezone-aware; it is converted to
            US/Eastern internally. Naive datetimes are rejected rather than
            assumed to be ET.
        imbalance_direction: 'B' buy imbalance, 'S' sell imbalance, 'N' no
            imbalance, 'O' insufficient orders to calculate, 'P' paused.
        far_price: Hypothetical clearing price for cross-eligible orders only.
            Non-positive means "not disseminated" -- Nasdaq publishes no
            near/far price for the Closing Cross before 15:55 ET, and the ITCH
            price fields are unsigned with no explicit sentinel, so a 0 must be
            treated as absent rather than as a price of zero.
        near_price: Hypothetical clearing price for cross plus continuous
            orders. Same non-positive convention as ``far_price``.
        reference_price: Current Reference Price (the price at which the paired
            and imbalance share counts are calculated).
        cross_type: ITCH Cross Type; only 'C' (Closing Cross) is actionable.
        significant_imbalance_published: NYSE only. True when the venue has
            published a MOC/LOC Significant Imbalance for this security, which
            is what permits contra-side entry during the 15:50 freeze
            (NYSE Rule 7.35B).
    """

    symbol: str
    timestamp: datetime
    paired_shares: int
    imbalance_shares: int
    imbalance_direction: str
    far_price: float
    near_price: float
    reference_price: float
    cross_type: str = CLOSING_CROSS_TYPE
    significant_imbalance_published: bool = False


@dataclass
class LocOrder:
    """A contra-side Limit-On-Close order ready for submission."""

    symbol: str
    side: str          # 'BUY' or 'SELL'
    quantity: int
    limit_price: float
    order_type: str = "LOC"
    venue: Optional[AuctionVenue] = None
    price_basis: PriceBasis = PriceBasis.FAR
    imbalance_ratio: Optional[float] = None
    # True when the order is entered inside Nasdaq's late-LOC window (>= 15:55),
    # where the exchange may re-price the limit to a Reference Price or reject
    # the order if no 15:55 Reference Price exists.
    late_entry_reprice_risk: bool = False


@dataclass
class AuctionDecision:
    """Outcome of evaluating one NOII message."""

    reason: DecisionReason
    order: Optional[LocOrder] = None
    imbalance_ratio: Optional[float] = None
    detail: str = ""


def to_eastern(moment: datetime, field_name: str = "timestamp") -> datetime:
    """Return ``moment`` expressed in US/Eastern.

    Raises:
        ValueError: if ``moment`` is naive. A naive datetime cannot be compared
            against an Eastern-time exchange cutoff without silently assuming a
            timezone, which is how orders end up submitted after the cutoff.
    """
    if not isinstance(moment, datetime):
        raise ValueError(f"{field_name} must be a datetime.")
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(
            f"{field_name} must be timezone-aware so it can be converted to "
            "US/Eastern; naive datetimes are rejected."
        )
    return moment.astimezone(US_EASTERN)


def _is_finite_number(value: object) -> bool:
    """True for a real, finite int/float (bools and NaN/Inf excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def imbalance_ratio(paired_shares: int, imbalance_shares: int) -> float:
    """Imbalance shares as a fraction of total interest at the reference price.

    ``imbalance_shares / (paired_shares + imbalance_shares)``. Returns 0.0 when
    there is no interest at all, so callers never see a ZeroDivisionError or a
    NaN ratio from an empty book.
    """
    total = paired_shares + imbalance_shares
    if total <= 0:
        return 0.0
    return imbalance_shares / total


class CloseAuctionParticipationStrategy:
    """Generates contra-side LOC orders against a published closing imbalance.

    The strategy provides liquidity: it sells into a buy imbalance and buys into
    a sell imbalance, priced off an indicative clearing price so the fill is
    bounded rather than unpriced (an MOC has no price protection at all).

    Args:
        max_participation_pct: Fraction of the *imbalance* the strategy is
            willing to absorb.
        cutoff_time: Optional hard override of the venue entry cutoff. When
            omitted the venue rules are used (Nasdaq LOC 15:58, NYSE 15:50).
            An override is applied only when it is *stricter* than the venue
            rule, so a caller can never configure a cutoff the exchange itself
            would reject.
        venue: Which venue's closing-auction rules apply.
        safety_buffer_seconds: Submission must complete this many seconds before
            the effective cutoff, covering network and broker latency.
        max_message_age_seconds: Refuse to act on an imbalance observation older
            than this at submission time. Closing-cross imbalance data updates
            every 1-10 seconds, so a materially older message means the feed
            stalled and the book has moved on.
        min_imbalance_shares: Ignore imbalances smaller than this.
        min_imbalance_ratio: Ignore imbalances whose ratio is below this.
        max_auction_volume_pct: Independent cap on participation as a fraction
            of predicted auction volume (paired + imbalance shares).
        price_basis: Which indicative clearing price the limit is derived from.
        price_concession_bps: Basis points of concession applied *toward*
            execution (lower sell limit / higher buy limit) to trade price
            improvement for fill probability. 0.0 prices exactly at the basis.
        tick_size: Instrument minimum price variation. Limits are rounded away
            from the aggressive side so rounding never makes the order worse
            than intended (sell limits up, buy limits down). The default of
            $0.01 is the usual US equity increment at or above $1.00; sub-dollar
            names need a smaller value.
    """

    def __init__(
        self,
        max_participation_pct: float = 0.10,
        cutoff_time: Optional[time] = None,
        venue: AuctionVenue = AuctionVenue.NASDAQ,
        safety_buffer_seconds: float = 5.0,
        max_message_age_seconds: float = 60.0,
        min_imbalance_shares: int = 0,
        min_imbalance_ratio: float = 0.0,
        max_auction_volume_pct: float = 0.15,
        price_basis: PriceBasis = PriceBasis.FAR,
        price_concession_bps: float = 0.0,
        tick_size: float = 0.01,
    ) -> None:
        if not 0.0 < max_participation_pct <= 1.0:
            raise ValueError("max_participation_pct must be within (0.0, 1.0].")
        if not 0.0 < max_auction_volume_pct <= 1.0:
            raise ValueError("max_auction_volume_pct must be within (0.0, 1.0].")
        if not isinstance(venue, AuctionVenue):
            raise ValueError("venue must be an AuctionVenue.")
        if safety_buffer_seconds < 0:
            raise ValueError("safety_buffer_seconds must be non-negative.")
        if max_message_age_seconds <= 0:
            raise ValueError("max_message_age_seconds must be strictly positive.")
        if min_imbalance_shares < 0:
            raise ValueError("min_imbalance_shares must be non-negative.")
        if not 0.0 <= min_imbalance_ratio <= 1.0:
            raise ValueError("min_imbalance_ratio must be within [0.0, 1.0].")
        if not isinstance(price_basis, PriceBasis):
            raise ValueError("price_basis must be a PriceBasis.")
        if not 0.0 <= price_concession_bps < 10_000.0:
            raise ValueError("price_concession_bps must be within [0.0, 10000.0).")
        if tick_size <= 0:
            raise ValueError("tick_size must be strictly positive.")
        if cutoff_time is not None and not isinstance(cutoff_time, time):
            raise ValueError("cutoff_time must be a datetime.time or None.")

        self.max_participation_pct = max_participation_pct
        self.venue = venue
        self.rules = VENUE_RULES[venue]
        self.cutoff_time = cutoff_time
        self.safety_buffer_seconds = safety_buffer_seconds
        self.max_message_age_seconds = max_message_age_seconds
        self.min_imbalance_shares = min_imbalance_shares
        self.min_imbalance_ratio = min_imbalance_ratio
        self.max_auction_volume_pct = max_auction_volume_pct
        self.price_basis = price_basis
        self.price_concession_bps = price_concession_bps
        self.tick_size = tick_size

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------
    @property
    def effective_entry_cutoff_et(self) -> time:
        """Unconditional entry cutoff enforced: stricter of venue rule and override."""
        return self._apply_override(self.rules.loc_entry_cutoff_et)

    @property
    def effective_contra_side_cutoff_et(self) -> time:
        """Cutoff for entry contra to a published imbalance (NYSE: 16:00)."""
        return self._apply_override(self.rules.contra_side_entry_cutoff_et)

    def _apply_override(self, venue_cutoff: time) -> time:
        if self.cutoff_time is None:
            return venue_cutoff
        return min(self.cutoff_time, venue_cutoff)

    def can_cancel_or_modify(self, moment: datetime) -> bool:
        """True while on-close orders can still be cancelled or modified.

        Both Nasdaq and NYSE freeze cancel/modify of on-close orders at 15:50
        ET. After that the contra-side order is committed to the auction, which
        is the primary risk this strategy carries.
        """
        return to_eastern(moment, "moment").time() < self.rules.cancel_modify_freeze_et

    def _entry_block_reason(
        self, submission_et: datetime, noii: NoiiMessage
    ) -> Optional[DecisionReason]:
        """Return the reason entry is blocked at ``submission_et``, else None."""
        if submission_et < self._deadline(submission_et, self.effective_entry_cutoff_et):
            # Inside the window where new on-close orders are accepted
            # unconditionally.
            return None

        if not self.rules.contra_side_entry_after_freeze:
            # Nasdaq has no contra-side carve-out: entry simply ends here.
            return DecisionReason.PAST_ENTRY_CUTOFF

        # NYSE Rule 7.35B: after the 15:50 freeze, only orders contra to a
        # *published* MOC/LOC Significant Imbalance are accepted, until 16:00.
        if submission_et >= self._deadline(
            submission_et, self.effective_contra_side_cutoff_et
        ):
            return DecisionReason.PAST_ENTRY_CUTOFF
        if not noii.significant_imbalance_published:
            return DecisionReason.ENTRY_FROZEN_NO_PUBLISHED_IMBALANCE
        return None

    def _deadline(self, submission_et: datetime, cutoff: time) -> datetime:
        """Cutoff on the submission's own trade date, less the safety buffer."""
        return datetime.combine(
            submission_et.date(), cutoff, tzinfo=US_EASTERN
        ) - timedelta(seconds=self.safety_buffer_seconds)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @staticmethod
    def _validate(noii: NoiiMessage) -> datetime:
        """Validate a NOII message and return its timestamp in US/Eastern."""
        if not isinstance(noii, NoiiMessage):
            raise ValueError("noii must be a NoiiMessage.")
        if not isinstance(noii.symbol, str) or not noii.symbol.strip():
            raise ValueError("symbol must be a non-empty string.")
        for name in ("paired_shares", "imbalance_shares"):
            value = getattr(noii, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an int.")
            if value < 0:
                raise ValueError(f"{name} must be non-negative, got {value}.")
        if noii.imbalance_direction not in IMBALANCE_DIRECTIONS:
            raise ValueError(
                f"imbalance_direction must be one of {sorted(IMBALANCE_DIRECTIONS)}, "
                f"got {noii.imbalance_direction!r}."
            )
        for name in ("far_price", "near_price", "reference_price"):
            value = getattr(noii, name)
            if not _is_finite_number(value):
                raise ValueError(f"{name} must be a finite number, got {value!r}.")
            if value < 0:
                raise ValueError(f"{name} must be non-negative, got {value!r}.")
        return to_eastern(noii.timestamp, "noii.timestamp")

    # ------------------------------------------------------------------
    # Pricing and sizing
    # ------------------------------------------------------------------
    def _basis_price(self, noii: NoiiMessage, message_et: datetime) -> Optional[float]:
        """Return the indicative clearing price to price off, or None if absent."""
        if message_et.time() < self.rules.indicative_price_available_from_et:
            return None
        price = noii.far_price if self.price_basis is PriceBasis.FAR else noii.near_price
        # Non-positive means the venue has not disseminated the price; treating a
        # 0 as a limit price would submit a grossly mispriced or unfillable order.
        return price if price > 0 else None

    def _limit_price(self, basis_price: float, side: str) -> float:
        """Apply the concession and round away from the aggressive side."""
        concession = basis_price * (self.price_concession_bps / 10_000.0)
        if side == "SELL":
            # Never round a sell limit down to zero: the limit must stay a
            # tradable price even with an extreme concession.
            ticks = max(math.ceil((basis_price - concession) / self.tick_size), 1)
        else:
            ticks = math.floor((basis_price + concession) / self.tick_size)
        return round(ticks * self.tick_size, 10)

    def _quantity(self, noii: NoiiMessage, target_qty: Optional[int]) -> int:
        """Smallest of the imbalance cap, the auction-volume cap and the target."""
        predicted_auction_volume = noii.paired_shares + noii.imbalance_shares
        caps = [
            self.max_participation_pct * noii.imbalance_shares,
            self.max_auction_volume_pct * predicted_auction_volume,
        ]
        if target_qty is not None:
            caps.append(float(target_qty))
        return int(math.floor(min(caps)))

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def evaluate(
        self,
        noii: NoiiMessage,
        submission_time: Optional[datetime] = None,
        target_qty: Optional[int] = None,
    ) -> AuctionDecision:
        """Evaluate one NOII message and return the resulting decision.

        Args:
            noii: The imbalance observation. Must be structurally valid; invalid
                messages raise ValueError rather than being silently skipped.
            submission_time: When the order would actually reach the exchange.
                Defaults to the message timestamp, but a real pipeline should
                pass "now" so feed lag cannot push the order past the cutoff.
                Must be timezone-aware and not earlier than the message.
            target_qty: Optional parent-order quantity still to execute. The
                generated quantity never exceeds it.

        Returns:
            An AuctionDecision. ``decision.order`` is None whenever a rule, a
            threshold, stale data, or a missing indicative price prevented order
            generation; the reason is always populated.

        Raises:
            ValueError: on a structurally invalid message, a naive datetime, a
                submission time before the message timestamp, or a non-positive
                ``target_qty``.
        """
        message_et = self._validate(noii)
        if target_qty is not None:
            if not isinstance(target_qty, int) or isinstance(target_qty, bool):
                raise ValueError("target_qty must be an int or None.")
            if target_qty <= 0:
                raise ValueError("target_qty must be strictly positive when supplied.")

        if submission_time is None:
            submission_et = message_et
        else:
            submission_et = to_eastern(submission_time, "submission_time")
            if submission_et < message_et:
                raise ValueError("submission_time must not precede noii.timestamp.")

        if noii.cross_type != CLOSING_CROSS_TYPE:
            return self._reject(
                noii,
                DecisionReason.WRONG_CROSS_TYPE,
                f"cross_type {noii.cross_type!r} is not the closing cross",
            )

        if noii.imbalance_direction == "P":
            return self._reject(
                noii, DecisionReason.SECURITY_PAUSED, "security is halted or paused"
            )

        block = self._entry_block_reason(submission_et, noii)
        if block is not None:
            return self._reject(
                noii,
                block,
                f"submission at {submission_et.time()} ET vs cutoff "
                f"{self.effective_entry_cutoff_et} ET "
                f"(buffer {self.safety_buffer_seconds}s, venue {self.venue.value})",
            )

        age_seconds = (submission_et - message_et).total_seconds()
        if age_seconds > self.max_message_age_seconds:
            return self._reject(
                noii,
                DecisionReason.STALE_IMBALANCE_DATA,
                f"imbalance observation is {age_seconds:.1f}s old at submission "
                f"(limit {self.max_message_age_seconds}s)",
            )

        if noii.imbalance_direction not in ACTIONABLE_DIRECTIONS or noii.imbalance_shares <= 0:
            return self._reject(
                noii,
                DecisionReason.NO_ACTIONABLE_IMBALANCE,
                f"direction {noii.imbalance_direction!r}, "
                f"{noii.imbalance_shares} imbalance shares",
            )

        if noii.imbalance_shares < self.min_imbalance_shares:
            return self._reject(
                noii,
                DecisionReason.IMBALANCE_BELOW_MIN_SHARES,
                f"{noii.imbalance_shares} < {self.min_imbalance_shares}",
            )

        ratio = imbalance_ratio(noii.paired_shares, noii.imbalance_shares)
        if ratio < self.min_imbalance_ratio:
            return self._reject(
                noii,
                DecisionReason.IMBALANCE_RATIO_BELOW_THRESHOLD,
                f"ratio {ratio:.4f} < {self.min_imbalance_ratio}",
                ratio=ratio,
            )

        basis_price = self._basis_price(noii, message_et)
        if basis_price is None:
            return self._reject(
                noii,
                DecisionReason.INDICATIVE_PRICE_UNAVAILABLE,
                f"{self.price_basis.value} price not disseminated at "
                f"{message_et.time()} ET on {self.venue.value}",
                ratio=ratio,
            )

        # Provide liquidity against the imbalance: sell into a buy imbalance.
        side = "SELL" if noii.imbalance_direction == "B" else "BUY"
        quantity = self._quantity(noii, target_qty)
        if quantity <= 0:
            return self._reject(
                noii,
                DecisionReason.QUANTITY_ROUNDS_TO_ZERO,
                "computed quantity is 0",
                ratio=ratio,
            )

        limit_price = self._limit_price(basis_price, side)
        late_reprice = (
            self.venue is AuctionVenue.NASDAQ
            and submission_et.time() >= NASDAQ_LATE_LOC_REPRICE_FROM_ET
        )
        if late_reprice:
            logger.warning(
                "%s: LOC entered at %s ET is inside the Nasdaq late-LOC window; the "
                "exchange may re-price it to a Reference Price, or reject it if no "
                "15:55 Reference Price exists.",
                noii.symbol,
                submission_et.time(),
            )

        order = LocOrder(
            symbol=noii.symbol,
            side=side,
            quantity=quantity,
            limit_price=limit_price,
            order_type="LOC",
            venue=self.venue,
            price_basis=self.price_basis,
            imbalance_ratio=ratio,
            late_entry_reprice_risk=late_reprice,
        )
        logger.info(
            "%s: %s LOC %s shares @ %.4f (%s basis %.4f, imbalance ratio %.4f, venue %s)",
            noii.symbol,
            side,
            quantity,
            limit_price,
            self.price_basis.value,
            basis_price,
            ratio,
            self.venue.value,
        )
        return AuctionDecision(
            reason=DecisionReason.ORDER_GENERATED, order=order, imbalance_ratio=ratio
        )

    def generate_auction_order(
        self,
        noii: NoiiMessage,
        submission_time: Optional[datetime] = None,
        target_qty: Optional[int] = None,
    ) -> Optional[LocOrder]:
        """Convenience wrapper around :meth:`evaluate` returning only the order."""
        return self.evaluate(
            noii, submission_time=submission_time, target_qty=target_qty
        ).order

    @staticmethod
    def _reject(
        noii: NoiiMessage,
        reason: DecisionReason,
        detail: str,
        ratio: Optional[float] = None,
    ) -> AuctionDecision:
        logger.info("%s: no auction order (%s: %s).", noii.symbol, reason.value, detail)
        return AuctionDecision(reason=reason, order=None, imbalance_ratio=ratio, detail=detail)
