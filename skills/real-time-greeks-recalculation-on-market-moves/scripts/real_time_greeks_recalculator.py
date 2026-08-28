"""
real-time-greeks-recalculation-on-market-moves: event-driven Greeks refresh for an
options book, with an explicit re-anchor policy.

The module does one job. Given a stream of underlying ticks it decides, per position,
whether the cached Greeks may be advanced by a cheap delta-gamma (Taylor) step or
must be fully revalued with Black-Scholes-Merton, then reports the resulting
single-underlying nets together with the evidence needed to tell whether the
threshold is calibrated.

Why the policy, and not just "recompute everything":

    A second-order Taylor expansion in spot,

        V(S0 + h) ~= V(S0) + delta_0 * h + 0.5 * gamma_0 * h^2
        delta(S0 + h) ~= delta_0 + gamma_0 * h

    is accurate only locally; for large changes in the underlying the approximated
    price departs materially from the true one. That is also why the Basel market-risk
    standard does not accept a delta measure alone for options: the curvature charge
    exists to "calculate the incremental loss for instruments sensitive to that risk
    factor above that already captured by the delta risk capital requirement", and it
    is computed from *repriced* instruments under an up and a down shock, not from a
    sensitivity (BCBS, MAR21.5). Small move -> expand; large move -> reprice. See
    ``references/standards.md``.

The failure mode this module exists to prevent (and which a naive implementation
walks straight into):

    **Anchor drift.** If the baseline for the "how far has spot moved?" test is reset
    to the *last tick*, then a monotone run of sub-threshold ticks never trips the
    threshold. Two hundred consecutive +0.4% ticks against a 0.5% threshold move spot
    +122% while every single tick is individually "small", and the book is carried that
    entire distance on a frozen gamma. The anchor here is the spot of the **last full
    revaluation** (``GreeksAnchor.spot``), never the previous tick, so the test
    measures cumulative drift.

Scaling conventions, identical to ``options-greeks-real-time-portfolio-aggregation``
so the two skills' numbers are comparable:

    position delta units     = qty * multiplier * delta
    position dollar delta    = qty * multiplier * delta * spot
    position gamma units     = qty * multiplier * gamma          # per $1 move
    position dollar gamma    = qty * multiplier * gamma * spot^2 * 0.01
    position theta USD/day   = qty * multiplier * theta          # one CALENDAR day
    position vega USD/vol pt = qty * multiplier * vega           # one vol POINT

``delta``, ``gamma``, ``theta`` and ``vega`` are per unit of the deliverable.
``position_qty`` carries the long/short sign. ``multiplier`` is deliverable units per
contract and is **required with no default**: after a corporate action the OCC leaves
the premium multiplier at 100 and changes the deliverable instead - a 1-for-20 reverse
split adjusts "the option contract ... by changing the deliverable to 5 shares of the
new stock. You can expect the contract multiplier to remain 100" (OIC, *Splits,
Mergers, Spinoffs & Bankruptcies*). Greeks scale with the deliverable, so feeding 100
for that contract overstates its risk 20x. A Deribit BTC option is 1 BTC per contract.

``vega`` is per **one percentage point** of implied vol - "Vega measures the amount of
increase or decrease in premium based on a 1% (100 basis points) change in the implied
volatility assumption" (OIC, *Vega*) - hence the /100 in the closed form. ``theta`` is
per **one calendar day**, hence the /365.

Limitations (deliberate, documented):

- **One underlying per call.** A tick is one price for one underlying. Positions on
  any other underlying are excluded from the recalculation and from the nets, and
  counted in ``positions_skipped_other_underlying``. Cross-underlying netting is
  ``options-greeks-real-time-portfolio-aggregation``'s job, not this one's.
- **Monitoring only.** The report is an observation. It generates no hedge
  (``greeks-based-portfolio-hedging-automation``) and halts nothing
  (``kill-switch-and-drawdown-circuit-breakers``).
- **European, continuous-dividend Black-Scholes-Merton.** No early exercise, no
  discrete dividends, no smile dynamics: the reval uses the implied vol handed in for
  that strike, so it inherits whatever surface produced it
  (``options-implied-volatility-surface-construction``,
  ``american-vs-european-style-option-exercise-handling``).
- **Not thread-safe, and order-sensitive by construction.** The anchor cache is
  mutable engine state. Serialise ticks per engine instance; an out-of-order tick is
  rejected rather than applied backwards.
- **Near expiry, the policy degrades to "always reprice".** Delta through an
  at-the-money strike at expiry is discontinuous and no expansion of it is stable -
  see ``options-pin-risk-management-at-expiry``.
- **Validation runs per tick, and on the cheap path it costs more than the step it
  guards.** A Taylor step is a handful of floating-point operations; validating a leg
  is a couple of dozen type and range checks. That trade is deliberate - a risk engine
  that publishes a NaN quickly has published nothing - but it means the CPU saving
  here comes from skipping *revaluation*, not from skipping work altogether. A caller
  that owns its position objects and mutates them only on a book change can get the
  rest by validating once at book load: ``validate()`` is idempotent and normalises in
  place, so a stable, already-validated list re-validates to itself. No numbers are
  claimed for either path; measure on the target host.
"""
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Deliverable of a standard, unadjusted US equity/ETP option. Exposed for readable
#: caller code and deliberately NOT used as a default (adjusted and non-US contracts
#: differ; see the module docstring).
STANDARD_US_EQUITY_OPTION_MULTIPLIER = 100.0

#: Calendar days per year. Theta is quoted per calendar day, and pricing models decay
#: seven days over a five-day trading week - a Friday reading understates the decay
#: realised by Monday's open.
CALENDAR_DAYS_PER_YEAR = 365.0

#: Vega is quoted per one percentage point of implied vol, not per 1.00 of vol.
VOL_POINTS_PER_UNIT_VOL = 100.0

#: Spot move used to normalise gamma across underlyings: dollar gamma is the dollar
#: delta gained on a +1% move.
DOLLAR_GAMMA_MOVE_FRACTION = 0.01

OPTION_TYPE_CALL = "CALL"
OPTION_TYPE_PUT = "PUT"
_VALID_OPTION_TYPES = (OPTION_TYPE_CALL, OPTION_TYPE_PUT)

METHOD_TAYLOR = "TAYLOR_EXPANSION"
METHOD_FULL_BS = "FULL_BLACK_SCHOLES"

#: Why a position was fully revalued instead of stepped. Every full reval carries
#: exactly one of these, so a threshold can be calibrated from the reasons observed.
TRIGGER_NO_ANCHOR = "NO_ANCHOR"
TRIGGER_SPOT_MOVE = "SPOT_MOVE_THRESHOLD"
TRIGGER_IV_MOVE = "IV_MOVE_THRESHOLD"
TRIGGER_ANCHOR_AGE = "ANCHOR_AGE"
TRIGGER_NEAR_EXPIRY = "NEAR_EXPIRY"
TRIGGER_DELTA_OUT_OF_BOUNDS = "TAYLOR_DELTA_OUT_OF_BOUNDS"
TRIGGER_FORCED = "CALLER_FORCED"
TRIGGER_NONE = "NONE"

STATUS_RECALCULATED = "GREEKS_RECALCULATED"
STATUS_NO_POSITIONS = "NO_POSITIONS_FOR_UNDERLYING"

_ROUND_DP = 2
_GREEK_DP = 6


def _require_finite(value: float, label: str, context: str) -> float:
    """
    Reject NaN/Inf and non-numeric input before it can reach a Greek or a total.

    A single NaN poisons every sum it touches and, worse, silently passes threshold
    tests: ``abs(nan) > threshold`` is ``False``, so a NaN spot would report as a
    *small* move and keep the book on the Taylor path forever. Corrupt data must
    raise, never propagate.

    The type check matters because vendor JSON routinely carries numbers as strings;
    ``float("0.6")`` would pass a value-only check while the field stayed a ``str``
    and failed later as an opaque ``TypeError`` from the arithmetic.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{context}: {label} must be a real number, got "
            f"{type(value).__name__} {value!r}"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{context}: {label} must be finite, got {value!r}")
    return numeric


def _require_positive(value: float, label: str, context: str) -> float:
    """Finite and strictly positive. Used for spot, strike, vol, expiry, multiplier."""
    numeric = _require_finite(value, label, context)
    if numeric <= 0.0:
        raise ValueError(f"{context}: {label} must be > 0, got {numeric!r}")
    return numeric


def _require_symbol(value: str, label: str, context: str) -> str:
    """Reject a blank or non-string symbol before it becomes a cache key."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: {label} must be a non-empty string, got {value!r}")
    return value.strip()


def _normalise_option_type(value: str, context: str) -> str:
    """
    Map to ``CALL``/``PUT``, raising on anything else.

    An ``if type == "CALL": ... else: # PUT`` branch turns every typo - ``"C"``,
    ``"Call "``, ``""`` - into a put, which flips the sign of delta. A mis-signed
    delta does not look wrong; it looks like the other side of the book.
    """
    text = _require_symbol(value, "option_type", context).upper()
    if text not in _VALID_OPTION_TYPES:
        raise ValueError(
            f"{context}: option_type must be one of {_VALID_OPTION_TYPES}, got {value!r}"
        )
    return text


def norm_cdf(x: float) -> float:
    """Standard normal CDF, via ``math.erf`` (no SciPy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class BlackScholesGreeks:
    """
    A full Black-Scholes-Merton revaluation, per unit of the deliverable.

    ``vega`` is per one vol point and ``theta`` is per one calendar day, matching the
    market quoting conventions the rest of the repo uses.
    """
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    d1: float
    d2: float


def black_scholes_merton_greeks(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    implied_vol: float,
    option_type: str,
    risk_free_rate: float = 0.0,
    dividend_yield: float = 0.0,
) -> BlackScholesGreeks:
    """
    Closed-form European option price and Greeks under Black-Scholes-Merton with a
    continuous dividend yield ``q`` (Merton, 1973).

        d1 = [ln(S/K) + (r - q + sigma^2/2) T] / (sigma sqrt(T)),  d2 = d1 - sigma sqrt(T)

        call:  price = S e^-qT N(d1) - K e^-rT N(d2),   delta = e^-qT N(d1)
        put:   price = K e^-rT N(-d2) - S e^-qT N(-d1), delta = -e^-qT N(-d1)

        gamma = e^-qT phi(d1) / (S sigma sqrt(T))              # shared
        vega  = S e^-qT phi(d1) sqrt(T) / 100                  # per 1 vol point
        theta_call = [-S phi(d1) sigma e^-qT / (2 sqrt(T)) + q S e^-qT N(d1)
                      - r K e^-rT N(d2)] / 365                 # per calendar day
        theta_put  = [-S phi(d1) sigma e^-qT / (2 sqrt(T)) - q S e^-qT N(-d1)
                      + r K e^-rT N(-d2)] / 365

    ``q`` and ``r`` default to 0.0 so that an omitted input is visibly a zero
    assumption rather than a silently-inherited house number. Supplying the wrong
    ``q`` biases delta by roughly ``q * T`` - about one delta point on a one-year
    option on a 1%-yielding name - which is small per position and systematic across
    the book.

    Raises ``ValueError`` on any non-finite or non-positive S, K, sigma or T. T is not
    clamped: an expired or zero-vol contract has no Black-Scholes delta, and quietly
    substituting ``max(1e-4, T)`` reports a number for a position that has none.
    """
    ctx = "black_scholes_merton_greeks"
    kind = _normalise_option_type(option_type, ctx)
    S = _require_positive(spot, "spot", ctx)
    K = _require_positive(strike, "strike", ctx)
    T = _require_positive(time_to_expiry_years, "time_to_expiry_years", ctx)
    sigma = _require_positive(implied_vol, "implied_vol", ctx)
    r = _require_finite(risk_free_rate, "risk_free_rate", ctx)
    q = _require_finite(dividend_yield, "dividend_yield", ctx)

    sqrt_t = math.sqrt(T)
    vol_sqrt_t = sigma * sqrt_t
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t

    disc_q = math.exp(-q * T)
    disc_r = math.exp(-r * T)
    pdf_d1 = norm_pdf(d1)

    gamma = disc_q * pdf_d1 / (S * vol_sqrt_t)
    vega = S * disc_q * pdf_d1 * sqrt_t / VOL_POINTS_PER_UNIT_VOL
    decay = -(S * pdf_d1 * sigma * disc_q) / (2.0 * sqrt_t)

    if kind == OPTION_TYPE_CALL:
        price = S * disc_q * norm_cdf(d1) - K * disc_r * norm_cdf(d2)
        delta = disc_q * norm_cdf(d1)
        theta_annual = (
            decay + q * S * disc_q * norm_cdf(d1) - r * K * disc_r * norm_cdf(d2)
        )
    else:
        price = K * disc_r * norm_cdf(-d2) - S * disc_q * norm_cdf(-d1)
        delta = -disc_q * norm_cdf(-d1)
        theta_annual = (
            decay - q * S * disc_q * norm_cdf(-d1) + r * K * disc_r * norm_cdf(-d2)
        )

    return BlackScholesGreeks(
        price=price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta_annual / CALENDAR_DAYS_PER_YEAR,
        d1=d1,
        d2=d2,
    )


@dataclass
class OptionPosition:
    """
    One leg of the book, carrying its contract terms and its **current** market
    inputs.

    Spot is deliberately absent: it arrives with the tick, once, for the whole
    underlying. Holding a per-position spot is how books end up netting Greeks
    computed at three different prices for the same name.

    ``implied_vol`` and ``time_to_expiry_years`` are refreshed by the caller on each
    tick. The engine compares them against the anchor to decide whether the cached
    Greeks are still usable.
    """
    symbol: str
    underlying_symbol: str
    option_type: str             # 'CALL' or 'PUT'
    strike: float
    position_qty: float          # Signed: +10 long 10 contracts, -10 short 10
    multiplier: float            # Deliverable units per contract; no default (see module docstring)
    implied_vol: float           # As of this tick, e.g. 0.20 for 20 vol
    time_to_expiry_years: float  # As of this tick, strictly > 0
    risk_free_rate: float = 0.0
    dividend_yield: float = 0.0

    def validate(self) -> None:
        """Raise ``ValueError`` on any input that would corrupt a Greek or a total."""
        ctx = f"position {self.symbol!r}"
        self.symbol = _require_symbol(self.symbol, "symbol", ctx)
        self.underlying_symbol = _require_symbol(
            self.underlying_symbol, "underlying_symbol", ctx)
        self.option_type = _normalise_option_type(self.option_type, ctx)
        self.strike = _require_positive(self.strike, "strike", ctx)
        self.position_qty = _require_finite(self.position_qty, "position_qty", ctx)
        self.multiplier = _require_positive(self.multiplier, "multiplier", ctx)
        self.implied_vol = _require_positive(self.implied_vol, "implied_vol", ctx)
        self.time_to_expiry_years = _require_positive(
            self.time_to_expiry_years, "time_to_expiry_years", ctx)
        self.risk_free_rate = _require_finite(self.risk_free_rate, "risk_free_rate", ctx)
        self.dividend_yield = _require_finite(self.dividend_yield, "dividend_yield", ctx)

    @property
    def scaled_qty(self) -> float:
        """Signed deliverable units: contracts x deliverable per contract."""
        return self.position_qty * self.multiplier


@dataclass(frozen=True)
class GreeksAnchor:
    """
    The state of the last **full revaluation** for one contract.

    Every trigger test measures the current tick against this, never against the
    previous tick - that is the whole point (see the module docstring on anchor
    drift). ``price`` is retained so the engine can report the realised error of the
    delta-gamma step at the moment it re-anchors.
    """
    symbol: str
    spot: float
    implied_vol: float
    time_to_expiry_years: float
    timestamp_s: float
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float


@dataclass(frozen=True)
class RecalculationTriggerConfig:
    """
    When a cached Greek stops being good enough.

    **None of these are an industry or regulatory standard.** No regulator publishes a
    revaluation threshold; the Basel curvature charge specifies stress *shocks*, not a
    refresh cadence. Calibrate each against the observed
    ``spot_taylor_value_error_per_unit`` in the reports, the book's gamma profile and
    the CPU budget, and record the rationale.

    - ``full_recalc_spot_move_pct``: fractional move of spot away from the **anchor**
      that forces a reval. 0.005 = 0.5%.
    - ``full_recalc_iv_move_abs``: absolute move in implied vol that forces a reval.
      0.005 = half a vol point. Cached gamma and vega are functions of vol, so a vol
      move invalidates them even when spot has not moved at all.
    - ``max_anchor_age_seconds``: staleness cap on the caller's clock. Theta and charm
      move the Greeks with no spot tick at all; without this a quiet book drifts
      unbounded.
    - ``near_expiry_years``: inside this horizon every position is fully revalued.
      Gamma explodes and delta becomes discontinuous through the strike, so a frozen
      gamma is not merely inaccurate, it is the wrong shape.
    - ``enforce_delta_bounds``: if a Taylor step puts delta outside the type's
      admissible band ([0, 1] for a call, [-1, 0] for a put), reval instead of
      reporting an impossible delta.
    """
    full_recalc_spot_move_pct: float = 0.005
    full_recalc_iv_move_abs: float = 0.005
    max_anchor_age_seconds: float = 60.0
    near_expiry_years: float = 1.0 / CALENDAR_DAYS_PER_YEAR
    enforce_delta_bounds: bool = True

    def __post_init__(self) -> None:
        ctx = "RecalculationTriggerConfig"
        for label in ("full_recalc_spot_move_pct", "full_recalc_iv_move_abs",
                      "max_anchor_age_seconds", "near_expiry_years"):
            value = _require_finite(getattr(self, label), label, ctx)
            if value <= 0.0:
                raise ValueError(
                    f"{ctx}: {label} must be > 0, got {value!r}. A zero or negative "
                    f"threshold disables the trigger silently - to revalue on every "
                    f"tick, set full_recalc_spot_move_pct to a tiny positive number."
                )
        if not isinstance(self.enforce_delta_bounds, bool):
            raise ValueError(f"{ctx}: enforce_delta_bounds must be a bool")


@dataclass(frozen=True)
class PositionGreeksResult:
    """Per-position outcome of one tick, with the reason it was produced that way."""
    symbol: str
    underlying_symbol: str
    spot: float
    method: str                  # METHOD_TAYLOR | METHOD_FULL_BS
    trigger_reason: str          # TRIGGER_* ; TRIGGER_NONE for a Taylor step
    spot_drift_from_anchor_pct: float
    delta: float                 # Per deliverable unit
    gamma: float
    vega: float
    theta: float
    position_delta_units: float
    position_dollar_delta: float
    position_gamma_units: float
    position_dollar_gamma: float
    position_vega_usd: float
    position_theta_daily_usd: float
    taylor_value_change_per_unit: float
    #: Realised error of the delta-gamma step in spot, available only when the engine
    #: re-anchors (it needs the true price). ``None`` on a Taylor tick and on the
    #: first sight of a contract. Feed it back into the threshold.
    spot_taylor_value_error_per_unit: Optional[float]


@dataclass(frozen=True)
class RealTimeGreeksReport:
    """One tick's single-underlying Greeks snapshot and recalculation accounting."""
    underlying_symbol: str
    spot: float
    tick_timestamp_s: float
    max_spot_drift_from_anchor_pct: float
    positions_full_revalued: int
    positions_taylor_updated: int
    positions_skipped_other_underlying: int
    triggers: List[str]
    net_delta_units: float
    net_dollar_delta: float
    net_gamma_units: float
    net_dollar_gamma: float
    net_vega_usd: float
    net_theta_daily_usd: float
    position_results: List[PositionGreeksResult]
    status: str
    audit_notes: str


class RealTimeGreeksRecalculationEngine:
    """
    Event-driven Greeks refresh for one or more underlyings, one tick at a time.

    The engine owns the anchor cache. It is mutable, per-instance state: use one
    engine per serialised tick stream, and call :meth:`reset` when a position is
    closed or a feed gaps (a gap means the anchor's provenance is unknown).
    """

    def __init__(self, config: Optional[RecalculationTriggerConfig] = None) -> None:
        self.config = config if config is not None else RecalculationTriggerConfig()
        if not isinstance(self.config, RecalculationTriggerConfig):
            raise ValueError(
                "config must be a RecalculationTriggerConfig, got "
                f"{type(self.config).__name__}"
            )
        self._anchors: Dict[str, GreeksAnchor] = {}
        self._last_tick_ts: Dict[str, float] = {}

    # -- anchor cache -----------------------------------------------------------

    def anchor_for(self, symbol: str) -> Optional[GreeksAnchor]:
        """The last full revaluation for ``symbol``, or ``None`` if never revalued."""
        return self._anchors.get(_require_symbol(symbol, "symbol", "anchor_for"))

    def reset(self, symbol: Optional[str] = None) -> None:
        """
        Drop one contract's anchor, or the whole cache.

        Call this on a feed gap, a session boundary or a position close. A stale
        anchor is worse than no anchor: no anchor forces a reval, a stale one does
        not.
        """
        if symbol is None:
            self._anchors.clear()
            self._last_tick_ts.clear()
            return
        self._anchors.pop(_require_symbol(symbol, "symbol", "reset"), None)

    # -- trigger policy ---------------------------------------------------------

    def _full_reval_trigger(
        self,
        pos: OptionPosition,
        anchor: Optional[GreeksAnchor],
        spot: float,
        tick_timestamp_s: float,
        force_full_reval: bool,
    ) -> Tuple[Optional[str], float]:
        """
        Decide whether ``pos`` must be fully revalued, and why.

        Returns ``(trigger_or_None, drift_pct)``. ``drift_pct`` is measured against the
        anchor spot, so it is cumulative across every Taylor tick since the last
        reval - the check that a last-tick baseline silently destroys.

        Order matters only for reporting: the first condition that fires names the
        trigger. Expiry is checked before spot because inside the pin horizon the
        expansion is invalid regardless of how small the move was.
        """
        if anchor is None:
            return TRIGGER_NO_ANCHOR, 0.0

        drift_pct = abs(spot - anchor.spot) / anchor.spot

        if force_full_reval:
            return TRIGGER_FORCED, drift_pct
        if pos.time_to_expiry_years <= self.config.near_expiry_years:
            return TRIGGER_NEAR_EXPIRY, drift_pct
        if drift_pct > self.config.full_recalc_spot_move_pct:
            return TRIGGER_SPOT_MOVE, drift_pct
        if abs(pos.implied_vol - anchor.implied_vol) > self.config.full_recalc_iv_move_abs:
            return TRIGGER_IV_MOVE, drift_pct
        if tick_timestamp_s - anchor.timestamp_s > self.config.max_anchor_age_seconds:
            return TRIGGER_ANCHOR_AGE, drift_pct
        return None, drift_pct

    @staticmethod
    def _delta_in_bounds(delta: float, option_type: str) -> bool:
        """A call delta lives in [0, 1]; a put delta in [-1, 0]. No exceptions."""
        if option_type == OPTION_TYPE_CALL:
            return 0.0 <= delta <= 1.0
        return -1.0 <= delta <= 0.0

    # -- main entry point -------------------------------------------------------

    def recalculate_portfolio_greeks(
        self,
        underlying_symbol: str,
        new_spot_price: float,
        positions: Sequence[OptionPosition],
        tick_timestamp_s: float = 0.0,
        force_full_reval: bool = False,
    ) -> RealTimeGreeksReport:
        """
        Process one underlying tick and return the refreshed single-underlying nets.

        ``positions`` may contain legs on other underlyings; they are **excluded**,
        not repriced at this underlying's spot, and counted in
        ``positions_skipped_other_underlying``. Netting several underlyings is
        ``options-greeks-real-time-portfolio-aggregation``'s job.

        ``tick_timestamp_s`` is a caller-supplied monotonic clock in seconds. It is a
        required input in substance - the staleness trigger is meaningless without it -
        and defaults to 0.0 only so a caller that ticks purely on price can ignore it
        (with a constant timestamp the age trigger never fires, by construction).

        Raises ``ValueError`` on an invalid position, a non-positive spot, a duplicate
        symbol within one tick, or a tick that arrives before the previous one for the
        same underlying. Rejecting the whole tick is deliberate: a partial Greeks
        snapshot is a risk number with an unknown amount of the book missing from it,
        which is worse than no number because it looks like one.
        """
        ctx = "recalculate_portfolio_greeks"
        underlying = _require_symbol(underlying_symbol, "underlying_symbol", ctx)
        spot = _require_positive(new_spot_price, "new_spot_price", ctx)
        timestamp = _require_finite(tick_timestamp_s, "tick_timestamp_s", ctx)

        previous_ts = self._last_tick_ts.get(underlying)
        if previous_ts is not None and timestamp < previous_ts:
            raise ValueError(
                f"{ctx}: out-of-order tick for {underlying} - timestamp {timestamp} "
                f"precedes the last processed tick {previous_ts}. Applying it would "
                f"advance the anchor backwards; resequence the feed or call reset()."
            )

        relevant: List[OptionPosition] = []
        seen: Dict[str, str] = {}
        for pos in positions:
            if not isinstance(pos, OptionPosition):
                raise ValueError(
                    f"{ctx}: positions must be OptionPosition instances, got "
                    f"{type(pos).__name__}"
                )
            pos.validate()
            if pos.symbol in seen:
                raise ValueError(
                    f"{ctx}: duplicate position symbol {pos.symbol!r}. A repeated leg "
                    f"double-counts the exposure and corrupts that contract's anchor; "
                    f"net the book before recalculating."
                )
            seen[pos.symbol] = pos.underlying_symbol
            if pos.underlying_symbol.upper() == underlying.upper():
                relevant.append(pos)

        skipped = len(seen) - len(relevant)
        self._last_tick_ts[underlying] = timestamp

        if not relevant:
            notes = (
                f"REAL-TIME GREEKS [{underlying} @ {spot:.4f}]: no positions on this "
                f"underlying ({skipped} position(s) on other underlyings skipped)."
            )
            logger.info(notes)
            return RealTimeGreeksReport(
                underlying_symbol=underlying,
                spot=spot,
                tick_timestamp_s=timestamp,
                max_spot_drift_from_anchor_pct=0.0,
                positions_full_revalued=0,
                positions_taylor_updated=0,
                positions_skipped_other_underlying=skipped,
                triggers=[],
                net_delta_units=0.0,
                net_dollar_delta=0.0,
                net_gamma_units=0.0,
                net_dollar_gamma=0.0,
                net_vega_usd=0.0,
                net_theta_daily_usd=0.0,
                position_results=[],
                status=STATUS_NO_POSITIONS,
                audit_notes=notes,
            )

        results = [
            self._recalculate_one(pos, spot, timestamp, force_full_reval)
            for pos in relevant
        ]

        net_delta_units = math.fsum(r.position_delta_units for r in results)
        net_dollar_delta = math.fsum(r.position_dollar_delta for r in results)
        net_gamma_units = math.fsum(r.position_gamma_units for r in results)
        net_dollar_gamma = math.fsum(r.position_dollar_gamma for r in results)
        net_vega = math.fsum(r.position_vega_usd for r in results)
        net_theta = math.fsum(r.position_theta_daily_usd for r in results)

        full_revalued = sum(1 for r in results if r.method == METHOD_FULL_BS)
        triggers = sorted(
            {r.trigger_reason for r in results if r.trigger_reason != TRIGGER_NONE})
        max_drift = max(r.spot_drift_from_anchor_pct for r in results)

        notes = (
            f"REAL-TIME GREEKS [{underlying} @ {spot:.4f}]: "
            f"{full_revalued} full reval / {len(results) - full_revalued} Taylor"
            f"{' / ' + str(skipped) + ' skipped' if skipped else ''}, "
            f"max drift from anchor {max_drift * 100.0:.3f}%"
            f"{' (' + ', '.join(triggers) + ')' if triggers else ''}. "
            f"Net delta {net_delta_units:+.2f} units / {net_dollar_delta:+.2f} ccy, "
            f"net gamma {net_gamma_units:+.4f} units, "
            f"net vega {net_vega:+.2f}/pt, net theta {net_theta:+.2f}/day."
        )
        logger.info(notes)

        return RealTimeGreeksReport(
            underlying_symbol=underlying,
            spot=spot,
            tick_timestamp_s=timestamp,
            max_spot_drift_from_anchor_pct=round(max_drift, _GREEK_DP),
            positions_full_revalued=full_revalued,
            positions_taylor_updated=len(results) - full_revalued,
            positions_skipped_other_underlying=skipped,
            triggers=triggers,
            net_delta_units=round(net_delta_units, _ROUND_DP),
            net_dollar_delta=round(net_dollar_delta, _ROUND_DP),
            net_gamma_units=round(net_gamma_units, _GREEK_DP),
            net_dollar_gamma=round(net_dollar_gamma, _ROUND_DP),
            net_vega_usd=round(net_vega, _ROUND_DP),
            net_theta_daily_usd=round(net_theta, _ROUND_DP),
            position_results=results,
            status=STATUS_RECALCULATED,
            audit_notes=notes,
        )

    def _recalculate_one(
        self,
        pos: OptionPosition,
        spot: float,
        timestamp: float,
        force_full_reval: bool,
    ) -> PositionGreeksResult:
        """Dispatch one position to a Taylor step or a full reval, and re-anchor."""
        anchor = self._anchors.get(pos.symbol)
        trigger, drift_pct = self._full_reval_trigger(
            pos, anchor, spot, timestamp, force_full_reval)

        taylor_value_change = 0.0
        taylor_delta = 0.0
        if anchor is not None:
            move = spot - anchor.spot
            taylor_delta = anchor.delta + anchor.gamma * move
            taylor_value_change = anchor.delta * move + 0.5 * anchor.gamma * move * move
            if (trigger is None and self.config.enforce_delta_bounds
                    and not self._delta_in_bounds(taylor_delta, pos.option_type)):
                # The expansion has left the admissible band: a call cannot have a
                # delta of 1.4. Reprice rather than publish an impossible Greek.
                trigger = TRIGGER_DELTA_OUT_OF_BOUNDS

        spot_error: Optional[float] = None
        if trigger is None:
            # Taylor step. ``anchor`` is necessarily not None here (a missing anchor
            # returns TRIGGER_NO_ANCHOR). Gamma, vega and theta are carried forward
            # frozen: their own drift (speed, vanna, charm) is second order over a
            # sub-threshold move and is exactly what the triggers above bound.
            delta = taylor_delta
            gamma, vega, theta = anchor.gamma, anchor.vega, anchor.theta
            method = METHOD_TAYLOR
            reason = TRIGGER_NONE
        else:
            greeks = black_scholes_merton_greeks(
                spot=spot,
                strike=pos.strike,
                time_to_expiry_years=pos.time_to_expiry_years,
                implied_vol=pos.implied_vol,
                option_type=pos.option_type,
                risk_free_rate=pos.risk_free_rate,
                dividend_yield=pos.dividend_yield,
            )
            delta, gamma = greeks.delta, greeks.gamma
            vega, theta = greeks.vega, greeks.theta
            method = METHOD_FULL_BS
            reason = trigger
            spot_error = self._spot_taylor_error(pos, anchor, spot, taylor_value_change)
            self._anchors[pos.symbol] = GreeksAnchor(
                symbol=pos.symbol,
                spot=spot,
                implied_vol=pos.implied_vol,
                time_to_expiry_years=pos.time_to_expiry_years,
                timestamp_s=timestamp,
                price=greeks.price,
                delta=delta,
                gamma=gamma,
                vega=vega,
                theta=theta,
            )

        scaled = pos.scaled_qty
        return PositionGreeksResult(
            symbol=pos.symbol,
            underlying_symbol=pos.underlying_symbol,
            spot=spot,
            method=method,
            trigger_reason=reason,
            spot_drift_from_anchor_pct=round(drift_pct, _GREEK_DP),
            delta=round(delta, _GREEK_DP),
            gamma=round(gamma, _GREEK_DP),
            vega=round(vega, _GREEK_DP),
            theta=round(theta, _GREEK_DP),
            position_delta_units=round(scaled * delta, _ROUND_DP),
            position_dollar_delta=round(scaled * delta * spot, _ROUND_DP),
            position_gamma_units=round(scaled * gamma, _GREEK_DP),
            position_dollar_gamma=round(
                scaled * gamma * spot * spot * DOLLAR_GAMMA_MOVE_FRACTION, _ROUND_DP),
            position_vega_usd=round(scaled * vega, _ROUND_DP),
            position_theta_daily_usd=round(scaled * theta, _ROUND_DP),
            taylor_value_change_per_unit=round(taylor_value_change, _GREEK_DP),
            spot_taylor_value_error_per_unit=spot_error,
        )

    @staticmethod
    def _spot_taylor_error(
        pos: OptionPosition,
        anchor: Optional[GreeksAnchor],
        spot: float,
        taylor_value_change: float,
    ) -> Optional[float]:
        """
        How wrong the delta-gamma step would have been, in spot alone.

        Reprices at the new spot but at the **anchor's** vol and expiry, so the
        comparison isolates the spot approximation instead of blaming it for vol and
        time drift that the expansion never claimed to capture. ``None`` when there is
        no anchor to compare against.

        This is the number to calibrate ``full_recalc_spot_move_pct`` on: if it stays
        negligible relative to the tick value of the book, the threshold is too tight
        and CPU is being burned; if it is material, the threshold is too loose and the
        published delta was wrong between revals.
        """
        if anchor is None:
            return None
        true_at_anchor_vol = black_scholes_merton_greeks(
            spot=spot,
            strike=pos.strike,
            time_to_expiry_years=anchor.time_to_expiry_years,
            implied_vol=anchor.implied_vol,
            option_type=pos.option_type,
            risk_free_rate=pos.risk_free_rate,
            dividend_yield=pos.dividend_yield,
        ).price
        return round((true_at_anchor_vol - anchor.price) - taylor_value_change, _GREEK_DP)
