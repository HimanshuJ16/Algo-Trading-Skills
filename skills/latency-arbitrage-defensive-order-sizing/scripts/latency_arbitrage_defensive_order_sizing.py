"""
latency-arbitrage-defensive-order-sizing: defensive sizing for a *passive* quote
that is exposed to stale-quote sniping across a cross-venue cancel latency gap.

The scenario is the one modelled by Budish, Cramton & Shim (2015): price discovery
happens on a lead venue, a liquidity provider rests a quote on a slower secondary
venue, and when the lead venue moves, faster participants race the liquidity
provider's cancel with an aggressive order against the now-stale quote. This module
answers one narrow question -- **how much size should be showing given that race** --
and returns a size, a spread multiplier, and a cancel directive.

    P_snipe = 1 - exp(-h * dt),   h = lambda_scaling * sigma_annualized
    Q_def   = floor_to_lot( Q_0 * (1 - P_snipe) )
    W       = 1 + SPREAD_WIDENING_SENSITIVITY * P_snipe

Model provenance, and where this departs from it (read before calibrating)
-------------------------------------------------------------------------
The exponential form is the right shape. In Budish, Cramton & Shim the fundamental
value follows a **compound Poisson jump process** with arrival rate ``lambda_jump``
and jump-size distribution ``F_jump``; a resting quote is snipe-able only when a
jump exceeds half the bid-ask spread, so the intensity of snipe-able events is
``lambda_jump * Pr(J > s/2)`` and the probability of at least one such event inside
an exposure window ``dt`` is ``1 - exp(-lambda_jump * Pr(J > s/2) * dt)``.
(BCS 2015, QJE 130(4):1547-1621, sec. 6.1-6.2.3, eqs. 6.1-6.3.)

Three deliberate departures, none of them hidden:

1. **``lambda_scaling * sigma`` is an uncalibrated proxy for that intensity, not a
   derivation of it.** Its units are "snipe-able events per millisecond per unit of
   annualized volatility" -- an unusual mixture, and the reason the shipped default
   must not be inherited. ``sigma_annualized`` is *not* time-scaled to the window;
   if it were, it would not produce these numbers. At 20% annualized volatility a
   1 ms window carries a diffusive standard deviation of ~0.026 bps, which puts a
   1 bp half-spread roughly 38 sigma away. A 9.5% chance of being picked off in 1 ms
   is therefore not a diffusion quantity at all -- it is a jump quantity, which is
   exactly BCS's point. Calibrate ``lambda_scaling`` against **realized** fill
   toxicity (markouts on your own passive fills), never against sigma.
2. **P_snipe is spread-independent, so the widening defence does not feed back into
   it.** In BCS, widening the spread lowers ``Pr(J > s/2)`` and so lowers the sniping
   intensity directly. Here the spread multiplier is an *output* and never re-enters
   the probability, which means a widened quote's risk is over-stated relative to the
   mechanism the model is named after. Treat W as a directive to the quoter, not as
   a risk reduction this engine has already accounted for.
3. **The race is assumed lost.** BCS give the liquidity provider a ``1/N`` chance of
   winning the cancel race against ``N-1`` snipers; this module conditions on losing.
   For an engine that models the race explicitly (cancel-vs-sweep margin, lead-venue
   imbalance), see ``cross-venue-latency-arbitrage-defensive-design``.

Regulatory constraints on the *output* (jurisdiction-specific)
--------------------------------------------------------------
- **EU / MiFID II.** A firm inside a market making agreement must post "firm,
  simultaneous two-way quotes of comparable size and competitive prices ... for at
  least 50% of the daily trading hours of continuous trading" (Commission Delegated
  Regulation (EU) 2017/578, RTS 8, Art. 1(1)). Art. 1(2)(c): "two quotes shall be
  deemed of comparable size when their sizes do not diverge by more than 50% from
  each other." Shrinking one side by ``P_snipe`` while the other side is unscaled
  therefore breaches comparable size once the divergence passes 50% -- see
  ``COMPARABLE_SIZE_MAX_DIVERGENCE``. Art. 3's exhaustive list of exceptional
  circumstances (extreme volatility triggering volatility mechanisms; war, industrial
  action, civil unrest, cyber sabotage; disorderly trading conditions; inability to
  maintain prudent risk management; non-equity suspensions) does **not** include
  "the sizing engine scored elevated sniping risk". A routine defensive pull is not
  an Art. 3 event.
- **US / Reg NMS.** ``min_lot_size`` should track the instrument's actual round lot.
  For NMS stocks the round lot is **price-tiered**, not a flat 100 shares: 100 / 40 /
  10 / 1 shares by prior-Evaluation-Period average closing price (17 CFR
  242.600(b)(93)) -- see ``round_lot_for_nms_price``. Rule 600(b)(16) defines a "bid
  or offer" as a price at which a member is willing to buy or sell "one or more round
  lots", so a defensively shrunk sub-round-lot quote is disseminated as odd-lot
  information (Rule 600(b)(69)) rather than standing as a protected quotation.

Limitations
-----------
- **One quote, one side.** The engine never sees the contra side, so it cannot
  enforce two-sided comparable size itself. It reports the divergence its own
  reduction would create; the caller must apply it to both sides or check the flag.
- **Not an order router.** It returns directives. It never sends, amends, or cancels.
- **Point estimates only.** ``latency_gap_ms`` is consumed as a scalar. Pick-off risk
  lives in the tail of the latency distribution -- feed a measured high percentile,
  not a mean.
- **No queue-position or fill model.** Nothing here says whether the surviving size
  will fill. See ``queue-position-modeling-for-passive-orders``.
"""
import logging
import math
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

#: Sniping hazard per millisecond per unit of annualized volatility. **This is a
#: placeholder, not a published or calibrated value**, and no venue, regulator, or
#: paper endorses it. It is retained as the default only so the shipped verification
#: numbers stay reproducible. At this value the engine cancels outright above
#: ~6.93 ms at sigma = 0.20 and above ~1.73 ms at sigma = 0.80 -- i.e. on a venue
#: whose cancel path is 3 ms it would never quote a volatile name. Calibrate against
#: realized passive-fill markouts before live use.
DEFAULT_SNIPING_HAZARD_PER_MS_PER_VOL: float = 0.50

#: P_snipe at or above which the quote is pulled entirely rather than shrunk.
DEFAULT_MAX_SNIPING_PROBABILITY: float = 0.50

#: Multiplier applied to P_snipe when widening the spread: W = 1 + k * P_snipe.
#: An engineering choice of this skill; it is not derived from BCS and is not a
#: published constant.
SPREAD_WIDENING_SENSITIVITY: float = 2.0

#: MiFID II RTS 8 Art. 1(2)(c): quotes are "of comparable size" only where "their
#: sizes do not diverge by more than 50% from each other". The Article does not say
#: which of the two sizes is the denominator, so this module takes the conservative
#: reading and measures divergence against the **smaller** quote.
COMPARABLE_SIZE_MAX_DIVERGENCE: float = 0.50

#: Decimal places P_snipe is reported to. Rounding is applied before the cancel
#: threshold is tested, which can only ever move a borderline case toward cancelling.
SNIPING_PROBABILITY_DECIMALS: int = 4

#: 17 CFR 242.600(b)(93): the NMS round lot, by the average closing price on the
#: primary listing exchange during the prior Evaluation Period. Ordered
#: (inclusive upper price bound, round lot in shares); the final tier is unbounded.
NMS_ROUND_LOT_TIERS: Tuple[Tuple[float, int], ...] = (
    (250.00, 100),
    (1000.00, 40),
    (10000.00, 10),
)

#: Round lot above the highest bounded tier in :data:`NMS_ROUND_LOT_TIERS`.
NMS_ROUND_LOT_ABOVE_TOP_TIER: int = 1

#: Quote sized down and left resting.
STATUS_DEFENSIVELY_SIZED = "QUOTE_DEFENSIVELY_SIZED"

#: Sniping probability at or above the configured threshold; quote pulled.
STATUS_HIGH_SNIPING_RISK_CANCEL = "HIGH_SNIPING_RISK_CANCEL"

#: Surviving size fell below the instrument's minimum lot; quote pulled.
STATUS_MIN_LOT_CANCEL = "MIN_LOT_CANCEL"

#: A market measurement was missing or unusable, so the engine fails **closed**.
#: A defensive sizer that treats an unreadable latency probe as "no risk" posts full
#: size into precisely the condition it exists to detect.
STATUS_INVALID_INPUT_CANCEL = "INVALID_INPUT_CANCEL"


def round_lot_for_nms_price(average_closing_price: float) -> int:
    """
    Return the Reg NMS round lot for an NMS stock at ``average_closing_price``.

    Implements the price tiers of 17 CFR 242.600(b)(93): 100 shares at or below
    $250.00, 40 shares to $1,000.00, 10 shares to $10,000.00, and 1 share above
    that. The rule keys off the average closing price on the primary listing
    exchange during the **prior Evaluation Period**, not the current price -- pass
    the former if you have it, and treat the result as an approximation if not.

    Applies to NMS stocks only. Futures, options, FX, and non-US venues carry their
    own lot conventions; do not use this for them.

    Raises:
        ValueError: if the price is not finite and positive.
    """
    if not math.isfinite(average_closing_price) or average_closing_price <= 0.0:
        raise ValueError(
            f"average_closing_price must be finite and positive, got {average_closing_price!r}"
        )
    for upper_bound, lot in NMS_ROUND_LOT_TIERS:
        if average_closing_price <= upper_bound:
            return lot
    return NMS_ROUND_LOT_ABOVE_TOP_TIER


@dataclass
class MarketStateSpec:
    """
    One passive quote on one side of one instrument, plus the measurements that
    describe its cross-venue exposure.

    Structural fields (``symbol``, ``base_quote_qty``, ``spread_bps``,
    ``min_lot_size``, ``lot_increment``) are validated on construction: a bad value
    there is a configuration error and should surface immediately. The two
    *measurement* fields (``latency_gap_ms``, ``volatility_annualized``) are not,
    because a stale or dropped telemetry sample is an expected production event --
    those are handled by failing closed inside the engine, so the caller gets an
    auditable cancel report rather than an exception in the quoting path.
    """

    symbol: str
    base_quote_qty: int                 # Target passive quantity before defence, e.g. 1000
    latency_gap_ms: float               # Cross-venue cancel-vs-sweep exposure window, ms
    volatility_annualized: float        # Annualized volatility, e.g. 0.25 == 25%
    spread_bps: float                   # Baseline bid-ask spread, bps, before widening
    min_lot_size: int = 100             # Smallest quantity the venue will rest; see
                                        # round_lot_for_nms_price -- 100 is the NMS
                                        # round lot only at or below $250.00/share
    lot_increment: int = 1              # Tradable size increment; sizes are floored
                                        # to a multiple. 1 leaves sizes unrounded.

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.base_quote_qty, int) or isinstance(self.base_quote_qty, bool):
            raise ValueError(f"base_quote_qty must be an int, got {self.base_quote_qty!r}")
        if self.base_quote_qty <= 0:
            raise ValueError(f"base_quote_qty must be positive, got {self.base_quote_qty!r}")
        if not isinstance(self.min_lot_size, int) or isinstance(self.min_lot_size, bool):
            raise ValueError(f"min_lot_size must be an int, got {self.min_lot_size!r}")
        if self.min_lot_size < 1:
            raise ValueError(f"min_lot_size must be >= 1, got {self.min_lot_size!r}")
        if not isinstance(self.lot_increment, int) or isinstance(self.lot_increment, bool):
            raise ValueError(f"lot_increment must be an int, got {self.lot_increment!r}")
        if self.lot_increment < 1:
            raise ValueError(f"lot_increment must be >= 1, got {self.lot_increment!r}")
        if not math.isfinite(self.spread_bps) or self.spread_bps < 0.0:
            raise ValueError(
                f"spread_bps must be finite and non-negative, got {self.spread_bps!r}"
            )


@dataclass
class DefensiveSizingReport:
    """Directives for one quote, plus the diagnostics needed to audit the call."""

    symbol: str
    base_quote_qty: int
    defensive_quote_qty: int            # Size to show; 0 means pull the quote
    sniping_probability: float          # P_snipe in [0, 1]
    spread_multiplier: float            # W; multiply the baseline spread by this
    defensive_spread_bps: float         # spread_bps * W, already applied
    sniping_hazard_per_ms: float        # h = lambda_scaling * sigma, the intensity
    size_divergence_ratio: float        # (base - defensive) / defensive, RTS 8 reading
    breaches_comparable_size_one_sided: bool  # True if applying this to one side only
                                              # would breach RTS 8 Art. 1(2)(c)
    is_quote_canceled: bool
    status: str                         # One of the STATUS_* constants
    audit_notes: str


class LatencyArbitrageDefensiveSizingEngine:
    """
    Scores stale-quote sniping risk over a cross-venue cancel latency gap and returns
    a defensive size, a spread multiplier, and a cancel directive for a single
    passive quote.

    Read the module docstring before calibrating ``lambda_scaling``: it is an
    uncalibrated proxy for the Budish-Cramton-Shim sniping intensity, not a
    derivation of it, and the shipped default is a placeholder.
    """

    def __init__(
        self,
        max_sniping_prob_threshold: float = DEFAULT_MAX_SNIPING_PROBABILITY,
        lambda_scaling: float = DEFAULT_SNIPING_HAZARD_PER_MS_PER_VOL,
    ) -> None:
        if not math.isfinite(max_sniping_prob_threshold) or not (
            0.0 < max_sniping_prob_threshold <= 1.0
        ):
            raise ValueError(
                "max_sniping_prob_threshold must be finite and in (0, 1], got "
                f"{max_sniping_prob_threshold!r}"
            )
        if not math.isfinite(lambda_scaling) or lambda_scaling < 0.0:
            raise ValueError(
                f"lambda_scaling must be finite and non-negative, got {lambda_scaling!r}"
            )
        self.max_sniping_prob_threshold: float = max_sniping_prob_threshold
        self.lambda_scaling: float = lambda_scaling

    @staticmethod
    def _invalid_measurement_reason(
        latency_gap_ms: float, volatility: float
    ) -> Optional[str]:
        """
        Name the reason a measurement pair is unusable, or ``None`` if it is usable.

        A non-finite value means the telemetry is missing, stale, or corrupt. A
        negative volatility is not a measurement any feed can legitimately produce.
        A *negative* latency gap is legitimate -- it means the cancel beats the
        sweep -- and carries no sniping exposure, so it is not rejected here.
        """
        if not math.isfinite(latency_gap_ms):
            return f"latency_gap_ms is not finite ({latency_gap_ms!r})"
        if not math.isfinite(volatility):
            return f"volatility_annualized is not finite ({volatility!r})"
        if volatility < 0.0:
            return f"volatility_annualized is negative ({volatility!r})"
        return None

    def compute_sniping_probability(
        self, latency_gap_ms: float, volatility: float
    ) -> float:
        """
        Probability that at least one snipe-able event lands inside the exposure
        window, under a Poisson hazard ``h = lambda_scaling * volatility``:

            P_snipe = 1 - exp(-h * latency_gap_ms)

        Returns 0.0 when there is no exposure (a non-positive latency gap, meaning
        the cancel wins the race, or zero volatility).

        **Fails closed.** An unusable measurement returns 1.0, not 0.0. Returning
        0.0 would have this engine wave through full size on exactly the degraded
        telemetry it exists to protect against; see ``STATUS_INVALID_INPUT_CANCEL``.
        """
        reason = self._invalid_measurement_reason(latency_gap_ms, volatility)
        if reason is not None:
            logger.error(
                "Unusable sniping inputs (%s); failing closed with P_snipe = 1.0", reason
            )
            return 1.0

        if latency_gap_ms <= 0.0 or volatility <= 0.0:
            return 0.0

        hazard = self.lambda_scaling * volatility
        p_snipe = 1.0 - math.exp(-hazard * latency_gap_ms)
        return min(1.0, max(0.0, round(p_snipe, SNIPING_PROBABILITY_DECIMALS)))

    @staticmethod
    def _floor_to_lot(quantity: int, lot_increment: int) -> int:
        """Floor a size to a multiple of ``lot_increment``. Down, never up: rounding
        a defensive size *up* would show more risk than the model just authorised."""
        if lot_increment <= 1:
            return quantity
        return (quantity // lot_increment) * lot_increment

    @staticmethod
    def _size_divergence(base_qty: int, defensive_qty: int) -> float:
        """
        Divergence between the unscaled and the scaled size, on the conservative
        reading of MiFID II RTS 8 Art. 1(2)(c) -- measured against the **smaller**
        of the two, since the Article does not name a denominator.

        A fully pulled quote diverges without bound; ``inf`` is returned so the
        caller cannot mistake it for a small number.
        """
        if defensive_qty <= 0:
            return math.inf
        return (base_qty - defensive_qty) / float(defensive_qty)

    def _build_report(
        self,
        spec: MarketStateSpec,
        p_snipe: float,
        defensive_qty: int,
        status: str,
        notes: str,
    ) -> DefensiveSizingReport:
        spread_mult = round(1.0 + (p_snipe * SPREAD_WIDENING_SENSITIVITY), 2)
        divergence = self._size_divergence(spec.base_quote_qty, defensive_qty)
        return DefensiveSizingReport(
            symbol=spec.symbol,
            base_quote_qty=spec.base_quote_qty,
            defensive_quote_qty=defensive_qty,
            sniping_probability=p_snipe,
            spread_multiplier=spread_mult,
            defensive_spread_bps=round(spec.spread_bps * spread_mult, 4),
            sniping_hazard_per_ms=round(
                self.lambda_scaling * max(0.0, spec.volatility_annualized), 6
            )
            if math.isfinite(spec.volatility_annualized)
            else math.nan,
            size_divergence_ratio=divergence,
            breaches_comparable_size_one_sided=divergence > COMPARABLE_SIZE_MAX_DIVERGENCE,
            is_quote_canceled=defensive_qty <= 0,
            status=status,
            audit_notes=notes,
        )

    def calculate_defensive_sizing(self, spec: MarketStateSpec) -> DefensiveSizingReport:
        """
        Size one passive quote against its sniping exposure.

        Precedence, highest first -- the order is deliberate and each stage returns:

        1. **Unusable measurement** -> ``INVALID_INPUT_CANCEL`` at ``P_snipe = 1.0``.
           Checked first so a corrupt latency probe can never reach the sizing
           arithmetic and emerge as an approved full-size quote.
        2. **``P_snipe >= max_sniping_prob_threshold``** -> ``HIGH_SNIPING_RISK_CANCEL``.
           Above the threshold the answer is "not on the book", not "smaller".
        3. **Surviving size below ``min_lot_size``** -> ``MIN_LOT_CANCEL``. Resting a
           sub-lot residual pays the fee without holding a usable quote, and on an
           NMS stock it is odd-lot information rather than a protected quotation.
        4. Otherwise -> ``QUOTE_DEFENSIVELY_SIZED``.

        The report is returned in every case, including the cancels, so the decision
        is auditable rather than silent.
        """
        reason = self._invalid_measurement_reason(
            spec.latency_gap_ms, spec.volatility_annualized
        )
        if reason is not None:
            notes = (
                f"DEFENSIVE CANCEL [{spec.symbol}]: unusable market measurement -- {reason}. "
                "Failing closed: quote pulled rather than sized on unreadable telemetry."
            )
            logger.error(notes)
            return self._build_report(
                spec, 1.0, 0, STATUS_INVALID_INPUT_CANCEL, notes
            )

        p_snipe = self.compute_sniping_probability(
            spec.latency_gap_ms, spec.volatility_annualized
        )

        if p_snipe >= self.max_sniping_prob_threshold:
            notes = (
                f"DEFENSIVE CANCEL [{spec.symbol}]: sniping probability ({p_snipe:.2%}) "
                f"at or above threshold ({self.max_sniping_prob_threshold:.2%}) over a "
                f"{spec.latency_gap_ms:.1f}ms latency gap. Quote pulled."
            )
            logger.warning(notes)
            return self._build_report(
                spec, p_snipe, 0, STATUS_HIGH_SNIPING_RISK_CANCEL, notes
            )

        raw_defensive_qty = int(spec.base_quote_qty * (1.0 - p_snipe))
        defensive_qty = self._floor_to_lot(raw_defensive_qty, spec.lot_increment)

        if defensive_qty < spec.min_lot_size:
            notes = (
                f"DEFENSIVE CANCEL [{spec.symbol}]: defensive qty ({defensive_qty:,}) "
                f"below min lot size ({spec.min_lot_size:,}). Quote pulled."
            )
            logger.warning(notes)
            return self._build_report(
                spec, p_snipe, 0, STATUS_MIN_LOT_CANCEL, notes
            )

        report = self._build_report(
            spec, p_snipe, defensive_qty, STATUS_DEFENSIVELY_SIZED, ""
        )
        report.audit_notes = (
            f"QUOTE DEFENSIVELY SIZED [{spec.symbol}]: base qty {spec.base_quote_qty:,} -> "
            f"defensive qty {defensive_qty:,}. Sniping risk {p_snipe:.2%}, spread "
            f"{spec.spread_bps:.2f}bps -> {report.defensive_spread_bps:.2f}bps "
            f"({report.spread_multiplier:.2f}x), latency gap {spec.latency_gap_ms:.1f}ms."
        )
        logger.info(report.audit_notes)

        if report.breaches_comparable_size_one_sided:
            logger.warning(
                "[%s] one-sided size divergence %.2f exceeds %.0f%% -- applying this "
                "reduction to a single side would breach MiFID II RTS 8 Art. 1(2)(c) "
                "comparable size for a firm inside a market making agreement.",
                spec.symbol,
                report.size_divergence_ratio,
                COMPARABLE_SIZE_MAX_DIVERGENCE * 100.0,
            )

        return report
