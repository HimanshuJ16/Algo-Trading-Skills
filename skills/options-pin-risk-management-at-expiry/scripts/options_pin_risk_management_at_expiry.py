"""Pin risk management for expiring option positions.

Pin risk is the writer's problem of not knowing, at the close of trading on
expiration day, what position they will actually hold when the market reopens.
It exists because three deadlines are separated in time:

* Trading in standard US equity options ceases at 4:00 p.m. ET on the
  expiration date (Cboe equity options specifications: 8:30 a.m. - 3:00 p.m.
  CT). That is the last moment a position can be closed in the market.
* The holder's **final exercise decision** may be made until 5:30 p.m. ET on
  the expiration business day (FINRA Rule 2360(b)(23)(A); FINRA Information
  Notice, 3 Feb 2021). Members may impose an earlier deadline but may not
  accept instructions after 5:30 p.m. ET.
* The contract itself does not expire until the OCC expiration time, 11:59
  p.m. ET on the expiration date (OCC By-Laws).

In that window the underlying keeps trading after hours while the writer
cannot. A holder who watches the stock move through the strike after the close
can submit a Contrary Exercise Advice either to *cancel* the automatic exercise
of an in-the-money option or to *exercise* one that would otherwise be
abandoned. Both directions are live risks for a writer, and they are not
symmetric:

* **In-the-money short.** OCC Rule 805 exercise-by-exception exercises an
  expiring equity option that is in the money by $0.01 or more per share at the
  close unless contrary instructions are given. The writer's default
  expectation is assignment -- but a contrary instruction can cancel it, so a
  writer who hedges by pre-buying the stock can end up long the hedge and
  unassigned.
* **Out-of-the-money short.** The default is abandonment, but a holder may
  still submit a contrary instruction to exercise. The writer wakes up with an
  unhedged share position they had no reason to expect.

The engine therefore never treats the pin band as a single symmetric
condition: it computes signed moneyness against the exercise-by-exception
threshold, records which default outcome applies, and states which direction
of surprise the position is exposed to.

Scope and deliberate non-claims
-------------------------------
* **It is not a probability model.** Whether *this* account is assigned depends
  on OCC allocation to the clearing member and then the member's own FIFO /
  random / equally-random allocation across its short open interest (FINRA Rule
  2360(b)(23)(C)). None of that is an input here.
* **It is not a clock or a calendar.** ``hours_to_trading_close`` is a number
  the caller supplies. The engine does not know the venue's session, the
  holiday calendar, or the broker's exercise cutoff -- which is routinely
  earlier than the 5:30 p.m. ET regulatory deadline and is the deadline that
  actually binds a customer.
* **It is not a settlement classifier.** ``settlement_type`` and
  ``contract_multiplier`` are contract terms the caller must source from
  reference data. Corporate actions change the deliverable: OCC publishes an
  adjustment memo, and an adjusted contract may deliver a non-round share
  count, cash in lieu, or a basket rather than 100 shares.
* **Cash-settled contracts have no assignment ambiguity.** A cash-settled
  option settles in cash at the exercise-settlement value; there is no share
  position to be surprised by. Those positions are reported with a zero share
  delta -- their expiry exposure is settlement-value determination (AM- vs
  PM-settled), which belongs to ``physical-vs-cash-settlement-handling`` and
  ``options-chain-expiry-cycle-conventions-by-exchange``.

Input convention that materially changes the answer
---------------------------------------------------
OCC applies the exercise-by-exception test to the **closing price** of the
underlying. Before the close, ``spot_price`` is a provisional last price and
every moneyness verdict derived from it is provisional too. Set
``price_is_official_close=True`` only once the official close is known; until
then the report carries a ``PROVISIONAL_PRICE_NOT_OFFICIAL_CLOSE`` flag.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# --- Sourced market-structure constant ---------------------------------------

# OCC Rule 805 "exercise by exception": an expiring standardized equity option
# in the money by $0.01 or more per share at the close is exercised
# automatically unless the clearing member submits contrary instructions. This
# is the only absolute constant in this module that is not a tunable default.
OCC_EX_BY_EX_THRESHOLD_USD = 0.01

# --- Engineering defaults (tunable; none of these are regulatory constants) ---

# Pin band expressed as a percentage of spot. There is no published or mandated
# pin band: what matters is how far the underlying can plausibly travel between
# the close and the 5:30 p.m. ET decision deadline, which is a volatility
# question rather than a fixed percentage.
DEFAULT_PIN_DISTANCE_PCT = 1.0
# Hours before the close of trading at which pin risk becomes actionable.
DEFAULT_PIN_CUTOFF_HOURS = 2.0

VALID_OPTION_TYPES = ("CALL", "PUT")
VALID_SETTLEMENT_TYPES = ("PHYSICAL", "CASH")

# Recommended actions
ACTION_HOLD = "HOLD_TO_EXPIRY"
ACTION_CLOSE = "CLOSE_POSITION_BEFORE_EXPIRY"
ACTION_HOLDER_ELECTS = "NO_ACTION_HOLDER_ELECTS"
ACTION_CASH_REVIEW = "REVIEW_CASH_SETTLEMENT_EXPOSURE"
ACTION_POST_CLOSE = "POST_CLOSE_EXPOSURE_REVIEW"

# Report statuses
STATUS_HIGH = "HIGH_PIN_RISK_ACTION_REQUIRED"
STATUS_HOLDER_ELECTION = "PIN_ZONE_HOLDER_ELECTION"
STATUS_CASH_SETTLED = "CASH_SETTLED_NO_ASSIGNMENT_AMBIGUITY"
STATUS_LOW = "LOW_PIN_RISK_SAFE"

# Default expiry outcomes under OCC Rule 805 absent contrary instructions
OUTCOME_AUTO_EXERCISED = "AUTO_EXERCISED"
OUTCOME_EXPIRES_WORTHLESS = "EXPIRES_WORTHLESS"


def _require_finite_positive(value: float, name: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{name} must be a finite positive number, got {value!r}.")
    return numeric


@dataclass
class ExpiryOptionPosition:
    """A single expiring option position.

    ``hours_to_trading_close`` is hours until trading in *this option* ceases --
    the last moment the position can be closed in the market. It is deliberately
    not "hours to expiry": the OCC expiration time is 11:59 p.m. ET, roughly
    eight hours after the equity option close and six hours after the exercise
    decision deadline, so a cutoff measured against expiry would report hours
    remaining on a position that can no longer be traded out of. A negative
    value is accepted and means trading has already closed.
    """

    symbol: str
    underlying_symbol: str
    strike: float
    option_type: str                        # 'CALL' | 'PUT'
    position_qty: int                       # + long, - short; must be non-zero
    spot_price: float
    hours_to_trading_close: float
    contract_multiplier: float = 100.0      # shares per contract; 100 is standard
    settlement_type: str = "PHYSICAL"       # 'PHYSICAL' | 'CASH'
    price_is_official_close: bool = False

    def __post_init__(self) -> None:
        self.strike = _require_finite_positive(self.strike, "strike")
        self.spot_price = _require_finite_positive(self.spot_price, "spot_price")
        self.contract_multiplier = _require_finite_positive(
            self.contract_multiplier, "contract_multiplier"
        )

        hours = float(self.hours_to_trading_close)
        if not math.isfinite(hours):
            raise ValueError(
                "hours_to_trading_close must be finite, got "
                f"{self.hours_to_trading_close!r}."
            )
        self.hours_to_trading_close = hours

        # Reject a fractional contract count rather than truncating it. int(2.9)
        # is 2, which would silently size the position 30% light.
        if isinstance(self.position_qty, float) and not self.position_qty.is_integer():
            raise ValueError(
                f"position_qty must be a whole number of contracts, "
                f"got {self.position_qty!r}."
            )
        qty = int(self.position_qty)
        if qty == 0:
            raise ValueError(
                "position_qty must be non-zero; a flat position carries no pin risk "
                "and must not be reported as requiring action."
            )
        self.position_qty = qty

        option_type = str(self.option_type).strip().upper()
        if option_type not in VALID_OPTION_TYPES:
            raise ValueError(
                f"option_type must be one of {VALID_OPTION_TYPES}, "
                f"got {self.option_type!r}."
            )
        self.option_type = option_type

        settlement = str(self.settlement_type).strip().upper()
        if settlement not in VALID_SETTLEMENT_TYPES:
            raise ValueError(
                f"settlement_type must be one of {VALID_SETTLEMENT_TYPES}, "
                f"got {self.settlement_type!r}."
            )
        self.settlement_type = settlement

    @property
    def contracts(self) -> int:
        """Absolute contract count."""
        return abs(self.position_qty)

    @property
    def side(self) -> str:
        return "SHORT" if self.position_qty < 0 else "LONG"


@dataclass
class PinRiskPolicyConfig:
    """Pin-detection policy. Every field here is a desk parameter, not a rule."""

    pin_distance_pct: float = DEFAULT_PIN_DISTANCE_PCT
    pin_cutoff_hours: float = DEFAULT_PIN_CUTOFF_HOURS
    # Optional absolute pin band in currency units per share, applied with OR
    # alongside the percentage band. A percentage-only band scales the wrong
    # way: 1% of a $5 underlying is $0.05 (too tight to catch a realistic
    # after-hours move), while 1% of a $600 underlying is $6.00 (wide enough to
    # flag positions that are not remotely pinned). Left at None the engine
    # uses the percentage band alone and invents no threshold.
    pin_distance_abs_usd: Optional[float] = None
    ex_by_ex_threshold_usd: float = OCC_EX_BY_EX_THRESHOLD_USD

    def __post_init__(self) -> None:
        self.pin_distance_pct = _require_finite_positive(
            self.pin_distance_pct, "pin_distance_pct"
        )
        cutoff = float(self.pin_cutoff_hours)
        if not math.isfinite(cutoff) or cutoff < 0.0:
            raise ValueError(
                "pin_cutoff_hours must be finite and non-negative, got "
                f"{self.pin_cutoff_hours!r}."
            )
        self.pin_cutoff_hours = cutoff
        if self.pin_distance_abs_usd is not None:
            self.pin_distance_abs_usd = _require_finite_positive(
                self.pin_distance_abs_usd, "pin_distance_abs_usd"
            )
        self.ex_by_ex_threshold_usd = _require_finite_positive(
            self.ex_by_ex_threshold_usd, "ex_by_ex_threshold_usd"
        )


@dataclass
class PinRiskReport:
    """Per-position pin risk verdict.

    ``is_pin_risk_high`` means specifically *exercise/assignment ambiguity the
    position holder cannot resolve on their own*. A cash-settled contract in the
    pin band is not "high" by this definition -- it settles in cash with no
    share surprise -- and carries ``STATUS_CASH_SETTLED`` instead. Read
    ``status``, not the boolean alone.
    """

    symbol: str
    underlying_symbol: str
    option_type: str
    position_side: str
    settlement_type: str
    strike: float
    spot_price: float
    pin_distance_pct: float
    pin_distance_usd: float
    moneyness_usd: float                 # signed intrinsic per share; + = ITM
    is_itm_at_ex_by_ex_threshold: bool
    default_expiry_outcome: str
    is_in_pin_zone: bool                 # inside the band AND inside the cutoff
    is_pin_risk_high: bool
    recommended_action: str
    dne_eligible: bool
    intrinsic_forfeited_if_dne_usd: float
    assigned_share_delta: float          # signed shares if exercised/assigned
    assigned_share_notional_usd: float   # |delta| x spot: market value delivered
    assignment_cash_usd: float           # |delta| x strike: cash exchanged
    status: str
    data_quality_flags: List[str]
    audit_notes: str


@dataclass
class UnderlyingPinExposure:
    """Netted pin exposure for one underlying across all supplied legs."""

    underlying_symbol: str
    pinned_position_count: int
    certain_share_delta: float
    min_net_share_delta: float
    max_net_share_delta: float
    share_delta_uncertainty: float
    notional_uncertainty_usd: float
    unpaired_short_shares: Dict[str, float]
    status: str
    notes: str


@dataclass
class PortfolioPinRiskReport:
    position_reports: List[PinRiskReport] = field(default_factory=list)
    underlying_exposures: List[UnderlyingPinExposure] = field(default_factory=list)
    action_required_symbols: List[str] = field(default_factory=list)
    status: str = "NO_ACTION_REQUIRED"


class OptionsPinRiskManagementEngine:
    """Audits expiring option positions for pin risk and emits close directives.

    See the module docstring for the market-structure mechanics this implements
    and for what it deliberately does not claim.
    """

    def __init__(self, config: Optional[PinRiskPolicyConfig] = None) -> None:
        self.config = config or PinRiskPolicyConfig()

    # -- single position ------------------------------------------------

    def audit_position_pin_risk(self, pos: ExpiryOptionPosition) -> PinRiskReport:
        """Audit one expiring option position and resolve a recommended action."""
        cfg = self.config
        flags: List[str] = []
        if not pos.price_is_official_close:
            flags.append("PROVISIONAL_PRICE_NOT_OFFICIAL_CLOSE")

        # Rounded once, then used for both the band decision and the report, so
        # an auditor can always reconcile the two. Comparing an unrounded
        # distance against the band produces verdicts that contradict the
        # published number: a spot of 100/0.99 sits at exactly 1% of the strike
        # but evaluates to 1.0000000000000002% and falls outside a 1.0% band.
        dist_usd = round(abs(pos.spot_price - pos.strike), 6)
        dist_pct = round((dist_usd / pos.spot_price) * 100.0, 6)

        # Signed intrinsic per share. Positive means in the money.
        #
        # Rounded to 6 decimals *before* the threshold comparison. Binary
        # floating point represents an exact one-cent difference as slightly
        # less than $0.01 for most strikes -- 45.01 - 45.0 is
        # 0.00999999999999801, 1234.01 - 1234.0 is 0.009999999999990905 -- so a
        # raw comparison classifies an underlying that closed exactly on the
        # exercise-by-exception boundary as out of the money, which is the one
        # boundary this entire rule turns on. Rounding first also keeps the
        # reported ``moneyness_usd`` consistent with
        # ``is_itm_at_ex_by_ex_threshold``.
        raw_moneyness = (
            pos.spot_price - pos.strike
            if pos.option_type == "CALL"
            else pos.strike - pos.spot_price
        )
        moneyness = round(raw_moneyness, 6)
        itm_auto = moneyness >= cfg.ex_by_ex_threshold_usd
        default_outcome = (
            OUTCOME_AUTO_EXERCISED if itm_auto else OUTCOME_EXPIRES_WORTHLESS
        )

        in_band = dist_pct <= cfg.pin_distance_pct
        if cfg.pin_distance_abs_usd is not None:
            in_band = in_band or dist_usd <= cfg.pin_distance_abs_usd
        within_cutoff = pos.hours_to_trading_close <= cfg.pin_cutoff_hours
        in_pin_zone = in_band and within_cutoff

        trading_closed = pos.hours_to_trading_close < 0.0
        if trading_closed:
            flags.append("TRADING_WINDOW_ALREADY_CLOSED")

        shares = pos.contract_multiplier * pos.contracts
        share_delta = self._share_delta_if_exercised(pos, shares)
        share_notional = abs(share_delta) * pos.spot_price
        assignment_cash = abs(share_delta) * pos.strike

        is_high = False
        dne_eligible = False

        if not in_pin_zone:
            status = STATUS_LOW
            action = ACTION_HOLD
            reason = (
                "outside the pin zone (distance band "
                f"{'hit' if in_band else 'missed'}, cutoff "
                f"{'hit' if within_cutoff else 'missed'})"
            )
        elif pos.settlement_type == "CASH":
            status = STATUS_CASH_SETTLED
            action = ACTION_CASH_REVIEW
            reason = (
                "cash-settled: settles in cash at the exercise-settlement value, so "
                "there is no share position to be surprised by; the residual exposure "
                "is settlement-value determination (AM- vs PM-settled), not assignment"
            )
        elif pos.side == "SHORT":
            is_high = True
            status = STATUS_HIGH
            action = ACTION_POST_CLOSE if trading_closed else ACTION_CLOSE
            reason = (
                "short and in the money at the close: assignment is the default under "
                "OCC Rule 805 exercise-by-exception, but a contrary exercise advice "
                "filed by the holder before 5:30 p.m. ET can cancel it, leaving a "
                "pre-hedged writer holding the hedge and no assignment"
                if itm_auto
                else
                "short and out of the money at the close: abandonment is the default, "
                "but a holder may still file a contrary exercise advice to exercise "
                "before 5:30 p.m. ET, leaving the writer an unhedged share position"
            )
        elif itm_auto:
            # Long, in the money by at least the exercise-by-exception threshold:
            # it will be exercised into a share position unless a do-not-exercise
            # instruction is filed. The exposure is unwanted or unfunded delivery.
            is_high = True
            dne_eligible = True
            status = STATUS_HIGH
            action = ACTION_POST_CLOSE if trading_closed else ACTION_CLOSE
            reason = (
                "long and in the money at the close: exercise-by-exception will deliver "
                f"{abs(share_delta):,.0f} shares (${assignment_cash:,.2f} of cash at "
                "the strike) unless a do-not-exercise instruction is filed"
            )
        else:
            # Long and out of the money: the holder controls the outcome. There is
            # no ambiguity to resolve -- a do-not-exercise instruction is a no-op
            # on a contract that is already abandoned by default.
            status = STATUS_HOLDER_ELECTION
            action = ACTION_HOLDER_ELECTS
            reason = (
                "long and out of the money at the close: expires worthless by default "
                "and the holder elects whether to exercise, so there is no assignment "
                "ambiguity -- only the premium is at risk"
            )

        forfeited = max(0.0, moneyness) * shares if dne_eligible else 0.0

        notes = (
            f"PIN RISK AUDIT [{pos.symbol} {pos.side} {pos.option_type} "
            f"{pos.settlement_type} - {status}]: spot {pos.spot_price:,.4f} vs strike "
            f"{pos.strike:,.4f}, distance {dist_usd:,.4f} ({dist_pct:.4f}%, threshold "
            f"{cfg.pin_distance_pct}%), moneyness {moneyness:+,.4f}/share, default "
            f"outcome {default_outcome}, {pos.hours_to_trading_close:.2f}h to trading "
            f"close (cutoff {cfg.pin_cutoff_hours}h). Share delta if exercised "
            f"{share_delta:+,.0f} (market value ${share_notional:,.2f}, cash at strike "
            f"${assignment_cash:,.2f}). Rationale: {reason}. Action: '{action}'."
        )

        if is_high:
            logger.warning("HIGH PIN RISK: %s", notes)
        else:
            logger.info("%s", notes)

        return PinRiskReport(
            symbol=pos.symbol,
            underlying_symbol=pos.underlying_symbol,
            option_type=pos.option_type,
            position_side=pos.side,
            settlement_type=pos.settlement_type,
            strike=pos.strike,
            spot_price=pos.spot_price,
            pin_distance_pct=dist_pct,
            pin_distance_usd=dist_usd,
            moneyness_usd=moneyness,
            is_itm_at_ex_by_ex_threshold=itm_auto,
            default_expiry_outcome=default_outcome,
            is_in_pin_zone=in_pin_zone,
            is_pin_risk_high=is_high,
            recommended_action=action,
            dne_eligible=dne_eligible,
            intrinsic_forfeited_if_dne_usd=round(forfeited, 2),
            assigned_share_delta=share_delta,
            assigned_share_notional_usd=round(share_notional, 2),
            assignment_cash_usd=round(assignment_cash, 2),
            status=status,
            data_quality_flags=flags,
            audit_notes=notes,
        )

    @staticmethod
    def _share_delta_if_exercised(pos: ExpiryOptionPosition, shares: float) -> float:
        """Signed share delta this position produces if it is exercised/assigned.

        Cash-settled contracts deliver no shares. For physically settled
        contracts a call moves shares to the holder and a put moves shares to
        the writer.
        """
        if pos.settlement_type == "CASH":
            return 0.0
        if pos.option_type == "CALL":
            return -shares if pos.side == "SHORT" else shares
        return shares if pos.side == "SHORT" else -shares

    # -- portfolio ------------------------------------------------------

    def audit_portfolio_pin_risk(
        self, positions: Sequence[ExpiryOptionPosition]
    ) -> PortfolioPinRiskReport:
        """Audit a book of expiring positions and net exposure per underlying.

        A per-position audit cannot see the failure mode that actually costs
        money on expiration Friday: a vertical spread whose short leg is pinned
        while its long leg is far enough out of the money that it will not be
        exercised. The long leg delivers nothing, so the "defined risk" spread
        becomes a naked short over the weekend. This method reports, per
        underlying, the full range of share positions the book can wake up to,
        and the short contracts that have no reliably-exercising long against
        them.
        """
        reports = [self.audit_position_pin_risk(p) for p in positions]

        by_underlying: Dict[str, List[int]] = {}
        for idx, pos in enumerate(positions):
            by_underlying.setdefault(pos.underlying_symbol, []).append(idx)

        exposures: List[UnderlyingPinExposure] = []
        for underlying, indices in by_underlying.items():
            exposures.append(
                self._aggregate_underlying(
                    underlying,
                    [positions[i] for i in indices],
                    [reports[i] for i in indices],
                )
            )

        action_symbols = [r.symbol for r in reports if r.is_pin_risk_high]
        return PortfolioPinRiskReport(
            position_reports=reports,
            underlying_exposures=sorted(exposures, key=lambda e: e.underlying_symbol),
            action_required_symbols=action_symbols,
            status="ACTION_REQUIRED" if action_symbols else "NO_ACTION_REQUIRED",
        )

    def _aggregate_underlying(
        self,
        underlying: str,
        positions: Sequence[ExpiryOptionPosition],
        reports: Sequence[PinRiskReport],
    ) -> UnderlyingPinExposure:
        notes: List[str] = []

        spots = {round(p.spot_price, 6) for p in positions}
        if len(spots) > 1:
            notes.append(
                f"INCONSISTENT_SPOT_PRICE: {len(spots)} distinct spot prices supplied "
                f"for {underlying}; the netted exposure below is only as good as the "
                "least stale of them."
            )
        spot = positions[0].spot_price

        certain = 0.0
        uncertain: List[float] = []
        pinned_count = 0
        # Netted in **shares**, not contracts. Contract counts are not comparable
        # across legs with different multipliers: 10 short contracts of 100
        # against 10 long contracts of 10 is 900 naked shares, not a covered
        # spread.
        pinned_shorts: Dict[str, float] = {"CALL": 0.0, "PUT": 0.0}
        reliable_longs: Dict[str, float] = {"CALL": 0.0, "PUT": 0.0}

        for pos, rep in zip(positions, reports):
            shares = pos.contract_multiplier * pos.contracts
            if rep.is_in_pin_zone and pos.settlement_type == "PHYSICAL":
                pinned_count += 1
                uncertain.append(rep.assigned_share_delta)
                if pos.side == "SHORT":
                    pinned_shorts[pos.option_type] += shares
            elif rep.default_expiry_outcome == OUTCOME_AUTO_EXERCISED:
                # Outside the pin zone the default outcome is treated as certain.
                certain += rep.assigned_share_delta
                if pos.side == "LONG" and pos.settlement_type == "PHYSICAL":
                    reliable_longs[pos.option_type] += shares

        min_delta = certain + sum(d for d in uncertain if d < 0.0)
        max_delta = certain + sum(d for d in uncertain if d > 0.0)
        uncertainty = max_delta - min_delta

        unpaired = {
            opt_type: max(0.0, pinned_shorts[opt_type] - reliable_longs[opt_type])
            for opt_type in VALID_OPTION_TYPES
        }
        unpaired = {k: v for k, v in unpaired.items() if v > 0.0}

        if unpaired:
            status = "UNPAIRED_SHORT_PIN_EXPOSURE"
            notes.append(
                "Pinned short legs without a reliably-exercising long leg of the same "
                f"type, in shares: {unpaired}. A long leg that is not in the money by "
                f"at least ${self.config.ex_by_ex_threshold_usd:.2f} at the close will "
                "not be exercised and therefore hedges nothing over the weekend."
            )
        elif pinned_count:
            status = "PINNED_SHORT_DELIVERY_COVERED"
            notes.append(
                "Every pinned short leg is matched by a long leg that is in the money "
                "beyond the exercise-by-exception threshold and outside the pin band, "
                "so the short's delivery obligation is covered. Coverage is not the "
                "same as a known position: the exercised long leg still lands shares "
                "in the account whenever the short is not assigned, so read the range "
                "below rather than treating the exposure as flat."
            )
        else:
            status = "NO_PIN_EXPOSURE"

        if uncertainty > 0.0:
            notes.append(
                f"Share position at reopen is between {min_delta:+,.0f} and "
                f"{max_delta:+,.0f} shares: {uncertainty:,.0f} shares of uncertainty, "
                f"${uncertainty * spot:,.2f} of unhedged directional exposure."
            )

        return UnderlyingPinExposure(
            underlying_symbol=underlying,
            pinned_position_count=pinned_count,
            certain_share_delta=certain,
            min_net_share_delta=min_delta,
            max_net_share_delta=max_delta,
            share_delta_uncertainty=uncertainty,
            notional_uncertainty_usd=round(uncertainty * spot, 2),
            unpaired_short_shares=unpaired,
            status=status,
            notes=" ".join(notes),
        )
