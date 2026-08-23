import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Reason codes attached to a defensive decision. Callers should branch on these
# rather than on the free-text recommendations: a toxicity trigger calls for
# skewing quotes away from the exposed side, while a lost latency race calls for
# not quoting at all, and the two used to be reported as one blended message.
REASON_TOXICITY_TICKS = "TOXICITY_TICKS"
REASON_TOXICITY_NORMALIZED = "TOXICITY_NORMALIZED"
REASON_RACE_LOST = "LATENCY_RACE_LOST"
REASON_LOCKED_BOOK = "LOCKED_BOOK"
REASON_CROSSED_BOOK = "CROSSED_BOOK"

SIDE_NONE = "NONE"
SIDE_BID = "BID"
SIDE_ASK = "ASK"


@dataclass
class PrimaryBookState:
    """Top-of-book snapshot of the lead venue."""

    bid_price: float
    ask_price: float
    bid_volume: float
    ask_volume: float
    tick_size: float = 0.01


@dataclass
class LatencyProfile:
    """
    Latency inputs for the cancel-versus-sweep race, all in microseconds.

    ``cancel_rtt_us`` is used as the ONE-WAY time for the cancel to reach the
    secondary venue's matching engine. Supplying a full round trip (which also
    includes the venue's acknowledgement coming back) makes the check
    conservative - it will trigger defenses earlier than strictly necessary -
    but it is not the quantity the race actually depends on. Name retained for
    backward compatibility.

    ``hft_sweep_latency_us`` is the competitor's one-way time from the lead-venue
    event to their aggressive order reaching the same secondary venue.
    """

    cancel_rtt_us: float
    hft_sweep_latency_us: float
    lead_event_timestamp_us: float


@dataclass
class LatencyArbitrageDefenseReport:
    micro_price: float
    mid_price: float
    toxicity_index_ticks: float
    latency_margin_us: float            # Positive => MM wins race, <= 0 => picked off
    is_preemptive_cancel_triggered: bool
    defensive_bid_spread_ticks: float
    defensive_ask_spread_ticks: float
    defensive_quote_size: int
    recommendations: List[str]
    toxicity_index_normalized: float = 0.0   # |weighted mid - mid| / half-spread, in [0, 1]
    half_spread_ticks: float = 0.0
    toxic_side: str = SIDE_NONE              # side at risk of being picked off
    trigger_reasons: List[str] = field(default_factory=list)


class CrossVenueLatencyArbitrageDefense:
    """
    Defensive market-making engine for cross-venue latency arbitrage: it scores
    top-of-book imbalance on the lead venue, evaluates whether a quote cancel can
    beat a competitor's sweep to the secondary venue, and returns defensive
    quoting directives.

    TERMINOLOGY: ``calculate_micro_price`` returns the **imbalance-weighted mid**
    (V_ask*P_bid + V_bid*P_ask) / (V_bid + V_ask). This is NOT Stoikov's
    micro-price, which is defined as the long-run expectation of the mid
    conditional on available information and which that paper reports as a
    better short-horizon predictor than the weighted mid. The method name is
    retained for API compatibility; the quantity is the weighted mid.

    SCOPE: this is a signal-and-policy layer. It does not send or cancel orders,
    does not model queue position, and treats latency as a point estimate rather
    than the distribution it actually is - a margin computed from mean latencies
    says nothing about the tail that determines how often you are picked off.
    """

    def __init__(
        self,
        toxicity_threshold_ticks: float = 1.0,
        base_spread_ticks: float = 1.0,
        base_quote_size: int = 100,
        toxicity_threshold_normalized: Optional[float] = None,
        latency_safety_margin_us: float = 0.0,
        spread_widening_factor: float = 1.0,
    ):
        """
        ``toxicity_threshold_ticks``: legacy tick-denominated trigger. Note the
        hard bound below - in a one-tick-wide market this measure cannot exceed
        0.5, so any threshold above that can never fire there.

        ``toxicity_threshold_normalized``: scale-free trigger in [0, 1] on
        |weighted mid - mid| / half-spread. ``None`` (default) disables it: no
        universal value exists, and it must be calibrated against the firm's own
        realized adverse-selection data.

        ``latency_safety_margin_us``: defenses trigger when the cancel is not
        ahead of the sweep by at least this many microseconds. A margin of
        exactly zero is a LOSS (the venue processes in arrival order), so the
        comparison is ``margin <= safety_margin``.

        ``spread_widening_factor`` (k): exposed-side spread becomes
        ``base * (1 + k * normalized_toxicity)``, bounded at ``base * (1 + k)``.
        """
        self._validate_positive("base_spread_ticks", base_spread_ticks)
        self._validate_finite_non_negative("toxicity_threshold_ticks", toxicity_threshold_ticks)
        self._validate_finite_non_negative("spread_widening_factor", spread_widening_factor)
        self._validate_finite("latency_safety_margin_us", latency_safety_margin_us)
        if not isinstance(base_quote_size, int) or isinstance(base_quote_size, bool) or base_quote_size <= 0:
            raise ValueError(f"base_quote_size must be a positive int, got {base_quote_size!r}")
        if toxicity_threshold_normalized is not None:
            self._validate_finite("toxicity_threshold_normalized", toxicity_threshold_normalized)
            if not 0.0 <= toxicity_threshold_normalized <= 1.0:
                raise ValueError(
                    "toxicity_threshold_normalized must lie in [0, 1] (it is a "
                    f"fraction of the half-spread), got {toxicity_threshold_normalized!r}"
                )

        self.toxicity_threshold_ticks = float(toxicity_threshold_ticks)
        self.base_spread_ticks = float(base_spread_ticks)
        self.base_quote_size = base_quote_size
        self.toxicity_threshold_normalized = (
            None if toxicity_threshold_normalized is None else float(toxicity_threshold_normalized)
        )
        self.latency_safety_margin_us = float(latency_safety_margin_us)
        self.spread_widening_factor = float(spread_widening_factor)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_finite(name: str, value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a real number, got {type(value).__name__}")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{name} must be finite, got {value!r}")
        return numeric

    @classmethod
    def _validate_finite_non_negative(cls, name: str, value: object) -> float:
        numeric = cls._validate_finite(name, value)
        if numeric < 0:
            raise ValueError(f"{name} must be non-negative, got {value!r}")
        return numeric

    @classmethod
    def _validate_positive(cls, name: str, value: object) -> float:
        numeric = cls._validate_finite(name, value)
        if numeric <= 0:
            raise ValueError(f"{name} must be strictly positive, got {value!r}")
        return numeric

    def _validate_book(self, book: PrimaryBookState) -> None:
        if not isinstance(book, PrimaryBookState):
            raise TypeError(f"book must be PrimaryBookState, got {type(book).__name__}")
        self._validate_positive("book.tick_size", book.tick_size)
        self._validate_finite("book.bid_price", book.bid_price)
        self._validate_finite("book.ask_price", book.ask_price)
        # Negative depth is not a thin book, it is corrupt data: it can flip the
        # sign of the weighted mid and push it outside [bid, ask] entirely.
        self._validate_finite_non_negative("book.bid_volume", book.bid_volume)
        self._validate_finite_non_negative("book.ask_volume", book.ask_volume)

    def _validate_latency(
        self, latency: LatencyProfile, cancel_sent_timestamp_us: float
    ) -> None:
        if not isinstance(latency, LatencyProfile):
            raise TypeError(f"latency must be LatencyProfile, got {type(latency).__name__}")
        self._validate_finite_non_negative("latency.cancel_rtt_us", latency.cancel_rtt_us)
        self._validate_finite_non_negative(
            "latency.hft_sweep_latency_us", latency.hft_sweep_latency_us)
        self._validate_finite("latency.lead_event_timestamp_us", latency.lead_event_timestamp_us)
        self._validate_finite("cancel_sent_timestamp_us", cancel_sent_timestamp_us)
        if cancel_sent_timestamp_us < latency.lead_event_timestamp_us:
            # The cancel cannot be sent in reaction to an event that has not
            # happened yet. Left unchecked this inflates the margin and reports
            # a race as won when the two timestamps came from different clocks.
            raise ValueError(
                f"cancel_sent_timestamp_us ({cancel_sent_timestamp_us}) precedes "
                f"lead_event_timestamp_us ({latency.lead_event_timestamp_us}); the two "
                "timestamps must come from the same synchronized clock domain."
            )

    # ------------------------------------------------------------------
    # Book metrics
    # ------------------------------------------------------------------

    def calculate_micro_price(self, book: PrimaryBookState) -> Tuple[float, float]:
        """
        Returns ``(imbalance_weighted_mid, mid)``.

        ``weighted_mid = (V_ask * P_bid + V_bid * P_ask) / (V_bid + V_ask)`` - a
        convex combination of the two touch prices, so it always lies within
        [P_bid, P_ask] and its distance from the mid is bounded by the
        half-spread. With no displayed depth on either side there is no
        imbalance information, so the mid is returned unchanged.

        Values are NOT rounded. Rounding the weighted mid to 4 decimals - as an
        earlier version did - erases the entire signal on instruments quoted
        more finely: at a 0.00001 FX tick, a 100:1 imbalance rounded to 4dp
        reports zero toxicity.
        """
        self._validate_book(book)
        mid = (book.bid_price + book.ask_price) / 2.0
        total_vol = book.bid_volume + book.ask_volume
        if total_vol <= 0:
            return mid, mid
        weighted_mid = (
            book.ask_volume * book.bid_price + book.bid_volume * book.ask_price
        ) / total_vol
        return weighted_mid, mid

    # ------------------------------------------------------------------
    # Defensive evaluation
    # ------------------------------------------------------------------

    def evaluate_defense(
        self,
        book: PrimaryBookState,
        latency: LatencyProfile,
        cancel_sent_timestamp_us: float
    ) -> LatencyArbitrageDefenseReport:
        """
        Scores top-of-book toxicity and the cancel-versus-sweep race, and returns
        defensive quoting directives.

        Decision points:
          - **Locked or crossed book** (bid >= ask) short-circuits to the maximum
            defensive posture. The weighted mid and the mid coincide there, so a
            toxicity score of 0.0 would otherwise report the single most
            dangerous book state as benign.
          - **Toxicity** is reported two ways. ``toxicity_index_ticks`` is the
            legacy |weighted mid - mid| / tick, which is bounded above by the
            half-spread in ticks: in a one-tick market it cannot exceed 0.5
            regardless of imbalance, so a tick threshold above that never fires.
            ``toxicity_index_normalized`` divides by the half-spread instead,
            giving a scale-free score in [0, 1] comparable across instruments.
          - **Latency margin** = (lead event + sweep latency) - (cancel sent +
            cancel delivery). A margin of exactly zero is a LOSS, so defenses
            trigger on ``margin <= latency_safety_margin_us``.
          - **A lost race sets the recommended size to 0.** Widening a spread you
            cannot cancel in time does not protect you; the only defense left is
            not being on the book. Previously the engine reported "pulling
            quotes" while simultaneously recommending a positive size.
          - **Widening is asymmetric.** The weighted mid identifies which side is
            about to be picked off (weighted mid above the mid means buying
            pressure and an exposed ASK); only that side is widened, which is the
            entire point of an imbalance signal.
        """
        self._validate_book(book)
        self._validate_latency(latency, cancel_sent_timestamp_us)

        weighted_mid, mid_price = self.calculate_micro_price(book)
        spread = book.ask_price - book.bid_price
        half_spread_ticks = (spread / 2.0) / book.tick_size

        hft_arrival = latency.lead_event_timestamp_us + latency.hft_sweep_latency_us
        mm_cancel_arrival = cancel_sent_timestamp_us + latency.cancel_rtt_us
        latency_margin_us = hft_arrival - mm_cancel_arrival
        race_lost = latency_margin_us <= self.latency_safety_margin_us

        trigger_reasons: List[str] = []
        recommendations: List[str] = []

        # --- Locked / crossed book: maximum defensive posture -------------
        if book.bid_price >= book.ask_price:
            reason = REASON_CROSSED_BOOK if book.bid_price > book.ask_price else REASON_LOCKED_BOOK
            trigger_reasons.append(reason)
            if race_lost:
                trigger_reasons.append(REASON_RACE_LOST)
            msg = (
                f"{reason}: bid {book.bid_price} >= ask {book.ask_price}. Imbalance "
                f"scoring is undefined on a locked/crossed book; pulling quotes."
            )
            recommendations.append(msg)
            logger.critical(msg)
            return LatencyArbitrageDefenseReport(
                micro_price=weighted_mid,
                mid_price=mid_price,
                toxicity_index_ticks=0.0,
                latency_margin_us=latency_margin_us,
                is_preemptive_cancel_triggered=True,
                defensive_bid_spread_ticks=self.base_spread_ticks * (1.0 + self.spread_widening_factor),
                defensive_ask_spread_ticks=self.base_spread_ticks * (1.0 + self.spread_widening_factor),
                defensive_quote_size=0,
                recommendations=recommendations,
                toxicity_index_normalized=1.0,
                half_spread_ticks=half_spread_ticks,
                toxic_side=SIDE_NONE,
                trigger_reasons=trigger_reasons,
            )

        # The deviation of the weighted mid from the mid is algebraically
        #     (V_bid - V_ask) / (V_bid + V_ask) * spread / 2,
        # so the normalized score is just the signed depth imbalance. Computing
        # it from the volumes rather than differencing two nearly equal prices
        # keeps a balanced book at exactly zero: (500*100.00 + 500*100.02)/1000
        # is not bit-identical to (100.00 + 100.02)/2, and that 1e-14 residue
        # was enough to report a toxic side - and shave a lot off the quote
        # size - on a perfectly balanced book.
        total_vol = book.bid_volume + book.ask_volume
        imbalance = 0.0 if total_vol <= 0 else (book.bid_volume - book.ask_volume) / total_vol
        toxicity_normalized = min(1.0, abs(imbalance))
        toxicity_ticks = toxicity_normalized * half_spread_ticks

        if imbalance > 0:
            toxic_side = SIDE_ASK      # buying pressure: a resting ask gets lifted
        elif imbalance < 0:
            toxic_side = SIDE_BID      # selling pressure: a resting bid gets hit
        else:
            toxic_side = SIDE_NONE

        if toxicity_ticks >= self.toxicity_threshold_ticks:
            trigger_reasons.append(REASON_TOXICITY_TICKS)
        if (
            self.toxicity_threshold_normalized is not None
            and toxicity_normalized >= self.toxicity_threshold_normalized
        ):
            trigger_reasons.append(REASON_TOXICITY_NORMALIZED)
        if race_lost:
            trigger_reasons.append(REASON_RACE_LOST)

        is_cancel_triggered = bool(trigger_reasons)

        if race_lost:
            # Distinguish an outright loss from a lead too thin to survive the
            # jitter buffer; reporting a positive margin as "Xus after the
            # sweep" reads as a loss that did not happen.
            if latency_margin_us < 0:
                detail = f"cancel reaches the venue {-latency_margin_us:.2f}us after the sweep"
            else:
                detail = (
                    f"cancel leads the sweep by only {latency_margin_us:.2f}us, inside the "
                    f"{self.latency_safety_margin_us:.2f}us safety buffer"
                )
            msg = (
                f"LATENCY RACE LOST: {detail}. Quoting size set to 0 - widening cannot "
                "protect a quote that cannot be pulled."
            )
            recommendations.append(msg)
            logger.critical(msg)
        if REASON_TOXICITY_TICKS in trigger_reasons or REASON_TOXICITY_NORMALIZED in trigger_reasons:
            msg = (
                f"TOXIC IMBALANCE: weighted mid is {toxicity_ticks:.2f} ticks "
                f"({toxicity_normalized:.2f} of the half-spread) from the mid; the "
                f"{toxic_side} side is exposed. Skewing quotes away from it."
            )
            recommendations.append(msg)
            logger.warning(msg)

        # --- Defensive quote geometry -------------------------------------
        # Widening scales on the NORMALIZED score. Scaling on the tick score
        # made the adjustment spread-dependent and unbounded: a 25-tick-wide
        # book produced a 13.5x base spread from a single imbalance reading.
        widened = self.base_spread_ticks * (
            1.0 + self.spread_widening_factor * toxicity_normalized
        )
        defensive_bid_spread = widened if toxic_side == SIDE_BID else self.base_spread_ticks
        defensive_ask_spread = widened if toxic_side == SIDE_ASK else self.base_spread_ticks

        if race_lost:
            defensive_size = 0
        else:
            # Linear taper to zero as the weighted mid pins against the touch.
            # Q / (1 + tau_normalized) would bottom out at half the base size,
            # which is not a defense at maximum toxicity.
            defensive_size = max(0, int(self.base_quote_size * (1.0 - toxicity_normalized)))

        if not recommendations:
            recommendations.append(
                "Order book balanced and latency margin safe. Normal market making."
            )

        return LatencyArbitrageDefenseReport(
            micro_price=weighted_mid,
            mid_price=mid_price,
            toxicity_index_ticks=toxicity_ticks,
            latency_margin_us=latency_margin_us,
            is_preemptive_cancel_triggered=is_cancel_triggered,
            defensive_bid_spread_ticks=defensive_bid_spread,
            defensive_ask_spread_ticks=defensive_ask_spread,
            defensive_quote_size=defensive_size,
            recommendations=recommendations,
            toxicity_index_normalized=toxicity_normalized,
            half_spread_ticks=half_spread_ticks,
            toxic_side=toxic_side,
            trigger_reasons=trigger_reasons,
        )
