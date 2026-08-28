"""
options-greeks-real-time-portfolio-aggregation: real-time net portfolio Greeks
aggregator and risk-limit auditor for multi-leg options books.

The module does one job: take per-position Greeks that somebody else computed, scale
them correctly to portfolio dollars, and say whether the book is inside its limits.
It prices nothing and forecasts nothing.

Scaling conventions (getting these wrong is the entire failure mode of this module):

    position delta shares    = qty * multiplier * delta
    position dollar delta    = qty * multiplier * delta * spot
    position gamma (shares)  = qty * multiplier * gamma          # per $1 move
    position dollar gamma    = qty * multiplier * gamma * spot^2 * 0.01
    position theta USD/day   = qty * multiplier * theta
    position vega USD/vol pt = qty * multiplier * vega

``delta``, ``gamma``, ``theta`` and ``vega`` are **per unit of the deliverable**
(per share for an equity option), never per contract. ``position_qty`` carries the
sign: a short position is a negative quantity and the per-unit Greeks stay as quoted.

``multiplier`` is **deliverable units per contract** and is a required field with no
default, because it is not universally 100:

* A standard US equity/ETP option is "Generally, 100 shares of one of the
  exchange-traded products" (Cboe, *Equity Options Product Specifications*).
* After a corporate action the OCC leaves the **premium multiplier at 100** but
  changes the **deliverable**: a 1-for-20 reverse split "causes the option contract
  to be adjusted by changing the deliverable to 5 shares of the new stock. You can
  expect the contract multiplier to remain 100" (OIC, *Splits, Mergers, Spinoffs &
  Bankruptcies*). Greeks scale with the **deliverable**, not with the premium
  multiplier, so feeding 100 for that contract overstates its risk 20x. Take the
  deliverable from the OCC contract adjustment memo / contract master.
* Non-US and crypto products differ outright - a Deribit BTC option is 1 BTC per
  contract, not 100.

``vega`` is per **1 percentage point** of implied volatility: "Vega measures the
amount of increase or decrease in premium based on a 1% (100 basis points) change in
the implied volatility assumption" (OIC, *Vega*). There is no extra factor of 100.

``theta`` is per **one calendar day**: "Theta represents, in theory, how much an
option's premium may decay per day" and "Pricing models take into account weekends,
so options will tend to decay seven days over the course of five trading days"
(OIC, *Theta*). ``net_theta_daily_usd`` is therefore one calendar day of decay, and
a Friday-close reading understates the decay realised by Monday's open.

Cross-underlying aggregation - which totals are meaningful and which are not:

* ``net_dollar_delta_usd``, ``net_theta_daily_usd``, ``net_vega_usd`` and
  ``net_dollar_gamma_usd`` are in currency units and are additive across underlyings.
* ``net_delta_shares`` (deliverable units) and ``net_gamma`` (delta units per $1
  move) are per-underlying quantities. Summing a $500 name's units with a $5 name's
  units is dimensionally meaningless; both fields are reported for continuity but are
  only interpretable when ``is_single_underlying`` is True. Use the currency figures,
  or the ``by_underlying`` breakdown, for a multi-name book.
* Dollar gamma is the standard cross-asset gamma normalisation: the dollar delta the
  book picks up on a +1% move in spot, ``gamma * S^2 * 0.01`` per deliverable unit.
  The two factors of S are not a typo - one sizes the 1% move in dollars (0.01 * S),
  the other converts the delta picked up (in units) into currency.

Limitations (documented, deliberate):

- **Single currency.** Every ``_usd`` field is in whatever currency the inputs were
  quoted in. The engine multiplies numbers; it does not convert them. Mixing a
  USD-quoted SPX book with BTC-quoted Deribit Greeks produces a meaningless total.
  Convert to one currency before aggregating - see
  ``multi-currency-pnl-and-fx-conversion``.
- **Net vega assumes a parallel vol shift.** A single net vega across several
  underlyings is the P&L of every implied vol moving one point *together*. Real vol
  shocks are neither parallel nor equal across names, so this number overstates the
  diversification of a book that is long vol in one name and short vol in another.
- **First-order snapshot.** Greeks are inputs. The engine cannot tell a stale delta
  from a live one, does not revalue on market moves
  (``real-time-greeks-recalculation-on-market-moves``), and second-order cross-Greeks
  (vanna, volga, charm) are out of scope.
- **Monitoring only.** The report is an observation, not an action. It generates no
  hedge (``greeks-based-portfolio-hedging-automation``) and is not a kill switch
  (``kill-switch-and-drawdown-circuit-breakers``).
- **No expiry or pin awareness.** Delta near an at-the-money strike at expiry is
  discontinuous and any snapshot of it is unstable - see
  ``options-pin-risk-management-at-expiry``.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

#: Deliverable of a standard, unadjusted US equity/ETP option (Cboe, *Equity Options
#: Product Specifications*). Exposed for readability in caller code and deliberately
#: NOT used as a default: adjusted contracts and non-US products differ.
STANDARD_US_EQUITY_OPTION_MULTIPLIER = 100.0

#: Delta per unit of the deliverable is bounded by +/-1. A value outside this band
#: almost always means the feed quoted delta in percent (60 instead of 0.60), which
#: would overstate exposure 100x.
MAX_ABS_DELTA_PER_UNIT = 1.0

#: Spot move used to normalise gamma across underlyings: dollar gamma is the dollar
#: delta gained on a +1% move.
DOLLAR_GAMMA_MOVE_FRACTION = 0.01

#: Currency figures are reported and compared against limits at this precision, so a
#: breach can never disagree with the number printed next to it. Delta units use it
#: too, for a consistent report.
_ROUND_DP = 2

#: Raw gamma is small; it needs more places than the currency fields.
_GAMMA_DP = 6

STATUS_HEALTHY = "PORTFOLIO_GREEKS_HEALTHY"
STATUS_DOLLAR_DELTA_BREACH = "DOLLAR_DELTA_BREACH"
STATUS_THETA_BREACH = "THETA_LIMIT_BREACH"
STATUS_VEGA_BREACH = "VEGA_LIMIT_BREACH"
STATUS_DOLLAR_GAMMA_BREACH = "DOLLAR_GAMMA_BREACH"

#: Reporting precedence when several limits are breached at once. ``status`` carries
#: the first hit in this order; ``breaches`` carries every one of them.
_BREACH_PRECEDENCE = (
    STATUS_DOLLAR_DELTA_BREACH,
    STATUS_THETA_BREACH,
    STATUS_VEGA_BREACH,
    STATUS_DOLLAR_GAMMA_BREACH,
)


def _require_finite(value: float, label: str, context: str) -> float:
    """
    Reject NaN/Inf, and anything that is not already a number, before it can reach an
    aggregate.

    A single NaN Greek poisons every sum it touches, and ``abs(nan) > limit`` is
    ``False`` - so an unvalidated NaN book reports as *healthy*. Corrupt data must
    raise, never net.

    The type check matters because vendor JSON routinely carries numbers as strings.
    ``float("0.6")`` would pass a value-only check while the field itself stayed a
    ``str``, and the failure would surface later as an opaque ``TypeError`` from the
    scaling arithmetic rather than as a named bad field here.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{context}: {label} must be a real number, got {type(value).__name__} "
            f"{value!r}"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{context}: {label} must be finite, got {value!r}")
    return numeric


def _require_symbol(value: str, label: str, context: str) -> str:
    """Reject a blank or non-string symbol before it becomes a grouping key."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: {label} must be a non-empty string, got {value!r}")
    return value.strip()


@dataclass
class OptionPosition:
    """
    One leg of the options book.

    Greeks are per unit of the deliverable; ``position_qty`` carries the long/short
    sign; ``multiplier`` is deliverable units per contract and is required (see the
    module docstring for why it must not default to 100).
    """
    symbol: str
    underlying_symbol: str
    position_qty: float          # Signed: +10 long 10 contracts, -10 short 10 contracts
    multiplier: float            # Deliverable units per contract (100 for a standard US equity option)
    spot_price: float            # Underlying spot, same currency as the Greeks
    delta: float                 # Per deliverable unit, -1.0 .. 1.0
    gamma: float                 # Per deliverable unit, per $1 move in spot
    theta: float                 # Per deliverable unit, per calendar day
    vega: float                  # Per deliverable unit, per 1 vol point

    def validate(self) -> None:
        """Raise ``ValueError`` on any input that would corrupt the aggregate."""
        _require_symbol(self.symbol, "symbol", "position")

        ctx = f"position {self.symbol!r}"
        _require_symbol(self.underlying_symbol, "underlying_symbol", ctx)

        _require_finite(self.position_qty, "position_qty", ctx)
        _require_finite(self.gamma, "gamma", ctx)
        _require_finite(self.theta, "theta", ctx)
        _require_finite(self.vega, "vega", ctx)

        delta = _require_finite(self.delta, "delta", ctx)
        if abs(delta) > MAX_ABS_DELTA_PER_UNIT:
            raise ValueError(
                f"{ctx}: delta must be per deliverable unit in [-1, 1], got {delta!r}. "
                "A value like 60 means the feed quoted delta in percent."
            )

        spot = _require_finite(self.spot_price, "spot_price", ctx)
        if spot <= 0.0:
            raise ValueError(f"{ctx}: spot_price must be > 0, got {spot!r}")

        multiplier = _require_finite(self.multiplier, "multiplier", ctx)
        if multiplier <= 0.0:
            raise ValueError(
                f"{ctx}: multiplier must be > 0 (deliverable units per contract, e.g. "
                f"{STANDARD_US_EQUITY_OPTION_MULTIPLIER:.0f} for a standard US equity "
                f"option - but read it from the contract master, adjusted contracts "
                f"differ), got {multiplier!r}"
            )

    @property
    def scaled_qty(self) -> float:
        """Deliverable units held, signed: contracts * deliverable units per contract."""
        return self.position_qty * self.multiplier

    @property
    def delta_shares(self) -> float:
        """Equivalent deliverable units of the underlying (only additive within one name)."""
        return self.scaled_qty * self.delta

    @property
    def dollar_delta_usd(self) -> float:
        """Currency exposure to a $1 move, scaled by spot. Additive across names."""
        return self.delta_shares * self.spot_price

    @property
    def gamma_shares(self) -> float:
        """Delta units gained per $1 move (only additive within one name)."""
        return self.scaled_qty * self.gamma

    @property
    def dollar_gamma_usd(self) -> float:
        """Dollar delta gained on a +1% move: gamma * S^2 * 0.01. Additive across names."""
        return (
            self.gamma_shares
            * self.spot_price
            * self.spot_price
            * DOLLAR_GAMMA_MOVE_FRACTION
        )

    @property
    def theta_daily_usd(self) -> float:
        """Currency decay over one calendar day (negative for a long option)."""
        return self.scaled_qty * self.theta

    @property
    def vega_usd(self) -> float:
        """Currency P&L of a 1-vol-point move in this position's implied volatility."""
        return self.scaled_qty * self.vega


@dataclass
class PortfolioGreeksLimits:
    """
    Portfolio-level Greeks limits.

    Sign conventions differ per limit and are not interchangeable:

    * ``max_dollar_delta_usd`` - a **magnitude** cap, must be > 0. Breached when
      ``abs(net_dollar_delta_usd) > max_dollar_delta_usd``; direction is irrelevant
      because both a long and a short book can be over-exposed.
    * ``max_negative_theta_usd`` - a **signed floor** on one calendar day of decay,
      must be <= 0. Breached when ``net_theta_daily_usd < max_negative_theta_usd``.
      Only decay is capped: a short-premium book *collecting* theta is not the risk
      this limit exists to catch, so positive theta never breaches. A positive value
      here would breach on almost every book and is rejected.
    * ``max_vega_usd`` - a **magnitude** cap, must be > 0. Long vol and short vol are
      both capped.
    * ``max_abs_dollar_gamma_usd`` - optional **magnitude** cap on the dollar delta
      the book picks up per +1% move. ``None`` (the default) leaves dollar gamma
      reported but unaudited, preserving the original three-limit behaviour.

    These defaults are illustrative starting points, not an industry standard. No
    regulator publishes a mandatory portfolio Greeks limit; calibrate against the
    book's capital and mandate.
    """
    max_dollar_delta_usd: float = 500000.0
    max_negative_theta_usd: float = -5000.0
    max_vega_usd: float = 10000.0
    max_abs_dollar_gamma_usd: Optional[float] = None

    def validate(self) -> None:
        """Raise ``ValueError`` on a limit that could never behave as intended."""
        ctx = "portfolio limits"
        max_delta = _require_finite(self.max_dollar_delta_usd, "max_dollar_delta_usd", ctx)
        if max_delta <= 0.0:
            raise ValueError(f"{ctx}: max_dollar_delta_usd must be > 0, got {max_delta!r}")

        max_vega = _require_finite(self.max_vega_usd, "max_vega_usd", ctx)
        if max_vega <= 0.0:
            raise ValueError(f"{ctx}: max_vega_usd must be > 0, got {max_vega!r}")

        max_theta = _require_finite(self.max_negative_theta_usd, "max_negative_theta_usd", ctx)
        if max_theta > 0.0:
            raise ValueError(
                f"{ctx}: max_negative_theta_usd is a signed floor on daily decay and must "
                f"be <= 0 (e.g. -5000.0 for '$5,000/day of decay'), got {max_theta!r}"
            )

        if self.max_abs_dollar_gamma_usd is not None:
            max_gamma = _require_finite(
                self.max_abs_dollar_gamma_usd, "max_abs_dollar_gamma_usd", ctx)
            if max_gamma <= 0.0:
                raise ValueError(
                    f"{ctx}: max_abs_dollar_gamma_usd must be > 0 or None, got {max_gamma!r}")


@dataclass
class PortfolioGreeksReport:
    """
    Aggregated book risk.

    ``net_delta_shares`` and ``net_gamma`` are per-underlying quantities and are only
    interpretable when ``is_single_underlying`` is True; for a multi-name book read
    the currency fields or ``by_underlying``.

    ``status`` names the highest-precedence breach only (dollar delta > theta > vega >
    dollar gamma). ``breaches`` lists every limit that was breached - check that, or
    the ``is_*_breached`` flags, before concluding a limit is clean.
    """
    total_positions: int
    net_delta_shares: float                  # Deliverable units; single-underlying only
    net_dollar_delta_usd: float              # Currency exposure to a $1 move
    net_gamma: float                         # Delta units per $1 move; single-underlying only
    net_theta_daily_usd: float               # Currency decay per calendar day
    net_vega_usd: float                      # Currency P&L per 1 vol point
    by_underlying: Dict[str, Dict[str, float]]
    status: str                              # Highest-precedence breach, or PORTFOLIO_GREEKS_HEALTHY
    audit_notes: str
    net_dollar_gamma_usd: float = 0.0        # Dollar delta gained per +1% move
    breaches: List[str] = field(default_factory=list)
    is_dollar_delta_breached: bool = False
    is_theta_breached: bool = False
    is_vega_breached: bool = False
    is_dollar_gamma_breached: bool = False
    underlying_count: int = 0
    is_single_underlying: bool = False


class OptionsGreeksRealTimePortfolioAggregationEngine:
    """
    Aggregates net portfolio Delta, Dollar Delta, Gamma, Dollar Gamma, Theta and Vega
    across a multi-leg options book and audits them against portfolio limits.

    Every limit is evaluated independently: a book can breach delta, theta and vega
    at once, and reporting only the first would tell a risk operator the other two
    were clean. Invalid positions raise rather than netting into the total, because a
    NaN Greek silently compares as *inside* every limit.

    The engine holds no mutable state between calls - only its validated limits - so
    concurrent ``aggregate_portfolio_greeks`` calls on one instance are safe. The
    ``OptionPosition`` objects handed to it are not copied, so a live book being
    mutated by a feed thread during a call can still produce a torn snapshot; take the
    snapshot in the caller.
    """

    def __init__(self, limits: Optional[PortfolioGreeksLimits] = None) -> None:
        self.limits = limits or PortfolioGreeksLimits()
        self.limits.validate()

    def aggregate_portfolio_greeks(
        self, positions: Iterable[OptionPosition]
    ) -> PortfolioGreeksReport:
        """
        Aggregate the book and audit it against ``self.limits``.

        The input is materialised first: it is traversed several times and a one-shot
        iterator would be exhausted by validation and then aggregate to zero - a
        silently flat book, the worst possible failure for a risk monitor.

        Raises ``ValueError`` if any position is invalid. An empty book is valid and
        aggregates to a flat, healthy report.
        """
        positions = list(positions)
        for position in positions:
            position.validate()

        # fsum, not sum: limit checks are threshold comparisons on these totals, and a
        # large book that nets near a limit must not depend on position ordering.
        net_delta = round(math.fsum(p.delta_shares for p in positions), _ROUND_DP)
        net_dollar_delta = round(math.fsum(p.dollar_delta_usd for p in positions), _ROUND_DP)
        net_gamma = round(math.fsum(p.gamma_shares for p in positions), _GAMMA_DP)
        net_dollar_gamma = round(math.fsum(p.dollar_gamma_usd for p in positions), _ROUND_DP)
        net_theta = round(math.fsum(p.theta_daily_usd for p in positions), _ROUND_DP)
        net_vega = round(math.fsum(p.vega_usd for p in positions), _ROUND_DP)

        by_underlying = self._group_by_underlying(positions)

        # Compared against the same rounded values that are reported, so the status can
        # never disagree with the numbers printed beside it.
        is_delta_breached = abs(net_dollar_delta) > self.limits.max_dollar_delta_usd
        is_theta_breached = net_theta < self.limits.max_negative_theta_usd
        is_vega_breached = abs(net_vega) > self.limits.max_vega_usd
        is_gamma_breached = (
            self.limits.max_abs_dollar_gamma_usd is not None
            and abs(net_dollar_gamma) > self.limits.max_abs_dollar_gamma_usd
        )

        breached = {
            STATUS_DOLLAR_DELTA_BREACH: is_delta_breached,
            STATUS_THETA_BREACH: is_theta_breached,
            STATUS_VEGA_BREACH: is_vega_breached,
            STATUS_DOLLAR_GAMMA_BREACH: is_gamma_breached,
        }
        breaches = [name for name in _BREACH_PRECEDENCE if breached[name]]
        status = breaches[0] if breaches else STATUS_HEALTHY

        underlying_count = len(by_underlying)
        is_single_underlying = underlying_count == 1

        notes = self._build_notes(
            status=status,
            breaches=breaches,
            net_delta=net_delta,
            net_dollar_delta=net_dollar_delta,
            net_gamma=net_gamma,
            net_dollar_gamma=net_dollar_gamma,
            net_theta=net_theta,
            net_vega=net_vega,
            position_count=len(positions),
            underlying_count=underlying_count,
            is_single_underlying=is_single_underlying,
        )

        if breaches:
            logger.warning("GREEKS LIMIT BREACH: %s", notes)
        else:
            logger.info(notes)

        return PortfolioGreeksReport(
            total_positions=len(positions),
            net_delta_shares=net_delta,
            net_dollar_delta_usd=net_dollar_delta,
            net_gamma=net_gamma,
            net_theta_daily_usd=net_theta,
            net_vega_usd=net_vega,
            by_underlying=by_underlying,
            status=status,
            audit_notes=notes,
            net_dollar_gamma_usd=net_dollar_gamma,
            breaches=breaches,
            is_dollar_delta_breached=is_delta_breached,
            is_theta_breached=is_theta_breached,
            is_vega_breached=is_vega_breached,
            is_dollar_gamma_breached=is_gamma_breached,
            underlying_count=underlying_count,
            is_single_underlying=is_single_underlying,
        )

    @staticmethod
    def _group_by_underlying(
        positions: List[OptionPosition],
    ) -> Dict[str, Dict[str, float]]:
        """
        Net Greeks per underlying symbol, case-normalised.

        This breakdown is the only place ``net_delta`` and ``net_gamma`` are
        unambiguously meaningful, since every leg in a group shares one spot price.
        """
        groups: Dict[str, List[OptionPosition]] = {}
        for position in positions:
            groups.setdefault(position.underlying_symbol.strip().upper(), []).append(position)

        return {
            underlying: {
                "net_delta": round(math.fsum(p.delta_shares for p in legs), _ROUND_DP),
                "net_dollar_delta": round(math.fsum(p.dollar_delta_usd for p in legs), _ROUND_DP),
                "net_gamma": round(math.fsum(p.gamma_shares for p in legs), _GAMMA_DP),
                "net_dollar_gamma": round(math.fsum(p.dollar_gamma_usd for p in legs), _ROUND_DP),
                "net_theta": round(math.fsum(p.theta_daily_usd for p in legs), _ROUND_DP),
                "net_vega": round(math.fsum(p.vega_usd for p in legs), _ROUND_DP),
            }
            for underlying, legs in groups.items()
        }

    @staticmethod
    def _build_notes(
        *,
        status: str,
        breaches: List[str],
        net_delta: float,
        net_dollar_delta: float,
        net_gamma: float,
        net_dollar_gamma: float,
        net_theta: float,
        net_vega: float,
        position_count: int,
        underlying_count: int,
        is_single_underlying: bool,
    ) -> str:
        """Human-readable audit line; every breach is named, not just the first."""
        breach_detail = ""
        if len(breaches) > 1:
            breach_detail = f" All breaches: {', '.join(breaches)}."

        cross_asset_caveat = ""
        if not is_single_underlying:
            cross_asset_caveat = (
                " Net Delta (units) and Net Gamma span multiple underlyings and are not "
                "comparable across them - use Dollar Delta / Dollar Gamma or by_underlying."
            )

        return (
            f"PORTFOLIO GREEKS AGGREGATION [{status}]: Net Delta = {net_delta:+,.1f} units, "
            f"Dollar Delta = {net_dollar_delta:+,.2f}, Net Gamma = {net_gamma:+,.4f} units/$1, "
            f"Dollar Gamma = {net_dollar_gamma:+,.2f}/+1% move, "
            f"Daily Theta = {net_theta:+,.2f}/calendar day, "
            f"Net Vega = {net_vega:+,.2f}/vol pt. "
            f"Positions = {position_count} across {underlying_count} underlyings."
            f"{breach_detail}{cross_asset_caveat}"
        )
