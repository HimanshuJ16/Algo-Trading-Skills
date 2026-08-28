"""
options-implied-volatility-surface-construction: Black-Scholes implied-volatility
inversion from market quotes, quadratic-smile slice calibration, and static
arbitrage auditing (calendar spread + butterfly) of the resulting surface.

Scope
-----
This engine answers one question: *given market option quotes, what surface do
they imply, and is that surface free of static arbitrage?* It inverts quoted
prices to implied volatilities, fits a smile slice to them, evaluates the surface
on a strike x expiration grid, and audits the two static no-arbitrage conditions.

For evaluating a *pre-specified* parametric surface and pricing a backtest off
it (with term-structure decay and analytic Greeks), use the sibling skill
``options-backtesting-with-realistic-iv-surface``. That engine deliberately does
not check arbitrage; this one does.

Two moneyness measures, used for two different purposes
-------------------------------------------------------
They are not interchangeable and conflating them is the defect this module
exists to avoid:

- **Spot moneyness** ``m = K / S`` parameterizes the smile, matching the
  documented quadratic form and the sibling skill's ``skew_alpha`` /
  ``smile_beta``.
- **Log-forward moneyness** ``k = ln(K / F_t)``, with ``F_t = S e^{(r - q) t}``,
  is what the calendar-spread condition is stated in. Gatheral & Jacquier,
  "Arbitrage-free SVI volatility surfaces" (Quantitative Finance 18(6), 2014;
  arXiv:1204.0646), define total implied variance as ``w(k, t) = sigma_BS(k,t)^2 t``
  where the strike is ``F_t e^k``, and prove (Lemma 2.1) that the surface is free
  of calendar spread arbitrage **if and only if** ``d/dt w(k, t) >= 0`` for all
  ``k``, assuming dividends proportional to the stock price. The proof compares
  two options *at the same forward moneyness*: ``K_1 / F_{t_1} = K_2 / F_{t_2}``.

  Comparing total variance at a **fixed strike** across expirations is therefore
  the wrong comparison whenever ``r != q``, because the forward moves with ``t``
  and the two points sit at different ``k``. With ``r = 5%``, ``q = 0`` and a
  one-year gap the forward moves ~5%, which is several strikes on a listed chain.

Smile model
-----------
A single slice is a quadratic in spot moneyness::

    x            = m - 1 = K / S - 1
    sigma(m)     = sigma_atm + alpha * x + beta * x^2

``alpha < 0`` reproduces the equity put skew; ``beta > 0`` is the smile
convexity. There is **no hidden scale factor**: the implemented formula is
exactly the one documented in ``SKILL.md`` and ``references/workflows.md``.
There is also **no term-structure decay** of the offset -- ``alpha`` and ``beta``
apply at every expiration unless a per-expiration ATM volatility is supplied via
``atm_vol_by_tte``. For a decaying skew term structure, see the sibling skill.

Static arbitrage audits
-----------------------
Both conditions follow Gatheral & Jacquier (above), Section 2:

- **Calendar spread** (their Definition 2.2): ``w(k, t)`` must be non-decreasing
  in ``t`` at fixed ``k``. Audited by resampling the surface at fixed ``k``
  levels, re-deriving the strike ``K = F_t e^k`` at each expiration.
- **Butterfly** (their Definition 2.3 / Lemma 2.2): the slice is free of butterfly
  arbitrage iff the risk-neutral density is non-negative, which they obtain from
  ``p(k) = d^2 C / dK^2`` -- the Breeden & Litzenberger (1978), "Prices of
  State-Contingent Claims Implicit in Option Prices", Journal of Business 51(4),
  621-651, result -- and which corresponds to call prices being decreasing and
  convex in strike. This module audits the **discrete** form of exactly that
  convexity: for each consecutive strike triple ``K1 < K2 < K3`` at one
  expiration, the (strike-spacing-weighted) butterfly must have non-negative
  value::

      w1 = (K3 - K2) / (K3 - K1)
      w3 = (K2 - K1) / (K3 - K1)
      butterfly = w1 * C(K1) + w3 * C(K3) - C(K2)  >=  0

  This is the tradeable statement of ``d^2 C / dK^2 >= 0``: a negative value is a
  butterfly spread with a strictly negative cost and a non-negative payoff.

Implied volatility inversion
----------------------------
``implied_volatility_from_price`` uses **bisection**, not Newton-Raphson. The
Black-Scholes price is strictly increasing in ``sigma`` (vega > 0 for ``t > 0``),
so a bracketed bisection converges unconditionally on any solvable input. Newton
is faster but its step is ``residual / vega``, and vega collapses towards zero
for deep in- and out-of-the-money strikes and for very short expiries, where the
step explodes and the iteration can leave the domain. Jaeckel, "Let's Be
Rational" (Wilmott 2015), is the production answer to doing this *quickly* and
robustly; unconditional convergence with no dependency beyond the standard
library matters more for a reference implementation than iteration count.

Quotes outside the no-arbitrage price bounds have **no** implied volatility and
raise rather than returning a clamped number:

    call:  max(S e^{-qt} - K e^{-rt}, 0)  <  C  <  S e^{-qt}
    put:   max(K e^{-rt} - S e^{-qt}, 0)  <  P  <  K e^{-rt}

Inside those bounds there is still a regime where the answer is precise-looking
and meaningless. Vega is the conditioning measure: a price identifies sigma only
to about (price resolution) / vega. For a 3-month 40%-out-of-the-money call at 8%
volatility the entire time value is ~2.6e-15 of a dollar, vega is ~1.3e-13, and
*every* volatility within ~0.0075 prices to the same float64 number -- no solver
can do better, and one that returns 8 decimal places is lying about it. Push
further out and the price underflows to exactly the intrinsic bound, at which
point the quote carries no volatility information at all and inversion raises.
``implied_volatility_from_price`` estimates this resolution at the solution and
logs a warning when it exceeds ``IV_CONDITIONING_WARN_SIGMA_RESOLUTION``; exclude
those quotes from the smile fit rather than letting them pull the wings.

Limitations (documented, deliberate)
------------------------------------
- **The quadratic smile violates Lee's moment formula in the far wings.** Lee,
  "The Moment Formula for Implied Volatility at Extreme Strikes" (Mathematical
  Finance 14(3), 2004), proves that absence of arbitrage bounds the tail of the
  implied variance by ``beta |k| / t`` with ``beta <= 2``, i.e. total implied
  variance may grow at most *linearly* in ``|k|``. A quadratic-in-moneyness
  sigma grows total variance like ``|k|^4``. ``MIN_STRIKE_IV`` / ``MAX_STRIKE_IV``
  bound the damage numerically but do not make the wings arbitrage-free -- they
  are a guard, not a model. Do not extrapolate this parameterization far beyond
  the moneyness range the quotes actually cover.
- **The audits sample, they do not prove.** The no-arbitrage conditions are
  statements for all ``k`` in R and all ``t > 0``. This engine checks them on the
  finite grid it is given. A clean report is evidence on that grid, not a proof
  for the surface. Denser grids audit more.
- **European exercise, continuous dividend yield.** Early exercise is not
  modelled, so inverting an American option price (every listed US single-stock
  option) yields a volatility biased by the early-exercise premium. Discrete cash
  dividends must be removed from ``S`` by the caller before inversion.
- **Calendar arbitrage theory assumes proportional dividends** (Gatheral &
  Jacquier Lemma 2.1). Under discrete cash dividends the forward-moneyness
  argument does not hold as stated.
- **Clamping can mask arbitrage.** When ``MIN_STRIKE_IV`` binds, the surface is
  no longer the parametric one, and a wing that would have failed the audit may
  pass. Every clamp is logged; treat a report containing clamp warnings as
  inconclusive in the wings.
- **No calibration of the term structure.** ``calibrate_smile_from_quotes`` fits
  one expiration at a time. Fitting each slice independently gives no guarantee
  that the resulting surface is calendar-arbitrage-free -- which is precisely why
  the audit exists and must be run after calibration.
- **Nothing is rounded.** Rounding an implied volatility to 4dp before squaring
  it into a variance can flip a marginal calendar comparison. Quantize at the
  presentation layer, not here.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Hard bounds on the strike IV the parametric smile may return. The quadratic is
#: unbounded in both directions, so without these the wings produce negative or
#: absurd volatilities. Binding is logged, never silent. A numerical guard, not a
#: published or regulatory threshold.
MIN_STRIKE_IV = 0.05
MAX_STRIKE_IV = 3.0

#: Bracket for the bisection implied-volatility solver. The lower end is above
#: zero because a zero-volatility "solution" is the intrinsic-value boundary, not
#: an interior root; the upper end bounds what the solver will report before it
#: declares the quote unrepresentable.
IV_SOLVER_MIN_VOL = 1e-6
IV_SOLVER_MAX_VOL = 5.0

#: Bisection halts when the bracket is narrower than this in volatility units
#: (1e-10 ~ 1e-8 volatility points) or after this many halvings. 200 iterations
#: is far more than the ~53 needed to exhaust float64 on this bracket; it is a
#: non-termination backstop, not the expected cost.
IV_SOLVER_TOLERANCE = 1e-10
IV_SOLVER_MAX_ITERATIONS = 200

#: Volatility uncertainty above which the inverted IV is reported as poorly
#: identified. Estimated as (float64 price resolution) / vega: where vega
#: collapses, a whole range of volatilities prices to the same float64 number and
#: the solver's answer is precise-looking but arbitrary within that range. In
#: well-conditioned regimes this estimate is ~1e-16; at the money it is ~1e-16,
#: for a 3-month 40%-OTM call at 8% vol it is ~7e-3. 1e-6 separates the two
#: cleanly. A numerical diagnostic, not an economic threshold.
IV_CONDITIONING_WARN_SIGMA_RESOLUTION = 1e-6

#: Absolute tolerance (in total-variance units) for the calendar comparison.
#: w is order 1e-2 to 1e0, so this is pure floating-point slack: a violation of
#: this size is not tradeable. NOT an economic threshold -- do not raise it to
#: silence a real violation.
CALENDAR_VARIANCE_TOLERANCE = 1e-12

#: Absolute tolerance for the butterfly comparison, expressed as a fraction of
#: spot so it scales with the price level. Same intent: floating-point slack.
BUTTERFLY_TOLERANCE_FRACTION_OF_SPOT = 1e-10

#: Minimum quotes needed to identify the three quadratic coefficients.
MIN_QUOTES_FOR_SMILE_FIT = 3

#: Report status values.
STATUS_ARBITRAGE_FREE = "ARBITRAGE_FREE_SURFACE"
STATUS_CALENDAR_VIOLATION = "CALENDAR_ARBITRAGE_VIOLATION"
STATUS_BUTTERFLY_VIOLATION = "BUTTERFLY_ARBITRAGE_VIOLATION"
STATUS_STATIC_VIOLATION = "STATIC_ARBITRAGE_VIOLATION"
#: Neither audit could run on the supplied grid. Not the same as clean.
STATUS_UNAUDITED = "UNAUDITED_SURFACE"

_VALID_OPTION_TYPES = ("CALL", "PUT")


def _require_finite(name: str, value: float) -> float:
    """Rejects NaN/Inf before it can propagate silently into a price or a variance."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return numeric


def _require_positive(name: str, value: float) -> float:
    """Rejects non-positive values where the model divides by or takes a log of them."""
    numeric = _require_finite(name, value)
    if numeric <= 0.0:
        raise ValueError(f"{name} must be strictly positive, got {value!r}")
    return numeric


def _normalize_option_type(option_type: str) -> str:
    """
    Maps caller input to 'CALL' or 'PUT', raising on anything else.

    Treating every non-'CALL' string as a put means a caller passing 'C' silently
    receives a put price, and then a put implied volatility fitted into a call
    smile. Unknown types must raise.
    """
    if not isinstance(option_type, str):
        raise ValueError(f"option_type must be a string, got {type(option_type).__name__}")
    normalized = option_type.strip().upper()
    if normalized not in _VALID_OPTION_TYPES:
        raise ValueError(f"option_type must be one of {_VALID_OPTION_TYPES}, got {option_type!r}")
    return normalized


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _solve_3x3(matrix: List[List[float]], rhs: List[float]) -> Tuple[float, float, float]:
    """
    Solves a 3x3 linear system by Gaussian elimination with partial pivoting.

    Raises ValueError when the system is singular, which for the smile fit means
    the quotes do not span three distinct moneyness levels and the quadratic
    coefficients are not identified.
    """
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]

    for col in range(3):
        pivot_row = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[pivot_row][col]) < 1e-14:
            raise ValueError(
                "Smile design matrix is singular: the quotes do not span three "
                "distinct moneyness levels, so sigma_atm / alpha / beta are not identified."
            )
        a[col], a[pivot_row] = a[pivot_row], a[col]
        for r in range(col + 1, 3):
            factor = a[r][col] / a[col][col]
            for c in range(col, 4):
                a[r][c] -= factor * a[col][c]

    solution = [0.0, 0.0, 0.0]
    for row in range(2, -1, -1):
        acc = a[row][3] - sum(a[row][c] * solution[c] for c in range(row + 1, 3))
        solution[row] = acc / a[row][row]
    return solution[0], solution[1], solution[2]


@dataclass
class OptionMarketQuote:
    """One observed option price, the input to IV inversion and smile calibration."""
    strike: float
    tte_years: float
    market_price: float
    option_type: str                     # 'CALL' or 'PUT'


@dataclass
class IVSurfaceConfig:
    """
    Surface parameters.

    ``skew_alpha`` and ``smile_beta`` are the linear and quadratic coefficients in
    spot moneyness ``m - 1``; they apply at every expiration (no term decay).
    ``dividend_yield`` is the continuous yield q -- it enters the forward, and
    therefore the calendar audit, not only the pricing.
    """
    spot_price: float = 100.0
    risk_free_rate: float = 0.05
    atm_vol: float = 0.20
    skew_alpha: float = -0.30            # Negative slope (put skew)
    smile_beta: float = 0.50             # Convexity (smile)
    dividend_yield: float = 0.0          # Continuous q; discrete dividends NOT handled

    def __post_init__(self) -> None:
        self.spot_price = _require_positive("spot_price", self.spot_price)
        self.risk_free_rate = _require_finite("risk_free_rate", self.risk_free_rate)
        self.atm_vol = _require_positive("atm_vol", self.atm_vol)
        self.skew_alpha = _require_finite("skew_alpha", self.skew_alpha)
        self.smile_beta = _require_finite("smile_beta", self.smile_beta)
        self.dividend_yield = _require_finite("dividend_yield", self.dividend_yield)


@dataclass
class IVSurfacePoint:
    """One evaluated surface node. Nothing here is rounded."""
    strike: float
    tte_years: float
    moneyness: float                     # m = K / S (spot moneyness, parameterizes the smile)
    implied_volatility: float
    total_variance: float                # w = iv^2 * tte
    forward_price: float                 # F = S * exp((r - q) * tte)
    log_forward_moneyness: float         # k = ln(K / F), the calendar-audit coordinate


@dataclass
class CalendarArbitrageViolation:
    """w(k, t) fell as t increased at a fixed log-forward moneyness."""
    log_forward_moneyness: float
    tte_short: float
    tte_long: float
    total_variance_short: float
    total_variance_long: float

    @property
    def shortfall(self) -> float:
        """How far total variance fell, in variance units. Strictly positive."""
        return self.total_variance_short - self.total_variance_long


@dataclass
class ButterflyArbitrageViolation:
    """A strike triple at one expiration priced to a negative-cost butterfly."""
    tte_years: float
    strike_low: float
    strike_mid: float
    strike_high: float
    butterfly_value: float               # Spacing-weighted, in price units. Negative.


@dataclass
class SmileCalibrationResult:
    """Least-squares fit of one expiration's quotes to the quadratic smile."""
    tte_years: float
    atm_vol: float                       # Fitted sigma at m = 1
    skew_alpha: float
    smile_beta: float
    quotes_used: int
    rms_error: float                     # RMS residual in volatility units
    implied_vols: List[Tuple[float, float]] = field(default_factory=list)  # (strike, iv)


@dataclass
class IVSurfaceConstructionReport:
    spot_price: float
    risk_free_rate: float
    total_surface_points: int
    grid_points: List[IVSurfacePoint]
    status: str                          # One of the STATUS_* constants
    audit_notes: str
    calendar_violations: List[CalendarArbitrageViolation] = field(default_factory=list)
    butterfly_violations: List[ButterflyArbitrageViolation] = field(default_factory=list)
    dividend_yield: float = 0.0
    calendar_audit_performed: bool = False   # False when < 2 expirations were supplied
    butterfly_audit_performed: bool = False  # False when < 3 strikes were supplied

    @property
    def is_arbitrage_free(self) -> bool:
        """
        True only when both audits ran and neither found a violation.

        A surface too sparse to audit is *not* arbitrage-free; it is unaudited.
        """
        return (
            self.calendar_audit_performed
            and self.butterfly_audit_performed
            and not self.calendar_violations
            and not self.butterfly_violations
        )


class OptionsIVSurfaceConstructionEngine:
    """
    Inverts Black-Scholes market prices to implied volatilities, calibrates a
    quadratic volatility smile to them, evaluates the surface on a strike x
    expiration grid, and audits calendar-spread and butterfly static arbitrage.

    See the module docstring for the model, the no-arbitrage conditions and their
    sources, and the documented limitations.
    """

    def __init__(self, config: Optional[IVSurfaceConfig] = None) -> None:
        self.config = config or IVSurfaceConfig()

    @staticmethod
    def _norm_cdf(x: float) -> float:
        """Retained for backwards compatibility; delegates to the module helper."""
        return _norm_cdf(x)

    # ------------------------------------------------------------------
    # Forward and pricing
    # ------------------------------------------------------------------

    def forward_price(self, tte: float, spot: Optional[float] = None) -> float:
        """
        Forward ``F = S exp((r - q) t)``.

        This is the reference level for the calendar-spread audit: total implied
        variance must be compared at constant ``K / F``, not at constant ``K``.
        """
        s = self.config.spot_price if spot is None else _require_positive("spot", spot)
        t = _require_finite("tte", tte)
        if t < 0.0:
            raise ValueError(f"tte must be non-negative, got {tte!r}")
        return s * math.exp((self.config.risk_free_rate - self.config.dividend_yield) * t)

    def black_scholes_price(
        self,
        option_type: str,
        spot: float,
        strike: float,
        tte: float,
        vol: float,
        r: float,
        q: float = 0.0,
    ) -> float:
        """
        European Black-Scholes-Merton call or put price with continuous yield q.

        Merton, "Theory of Rational Option Pricing", Bell Journal of Economics and
        Management Science 4(1), 1973::

            d1   = [ln(S/K) + (r - q + sigma^2 / 2) t] / (sigma sqrt(t))
            d2   = d1 - sigma sqrt(t)
            Call = S e^{-qt} N(d1) - K e^{-rt} N(d2)
            Put  = K e^{-rt} N(-d2) - S e^{-qt} N(-d1)

        Invalid inputs raise. The previous implementation silently clamped
        ``tte`` to 1e-4 and ``vol`` to 0.01, which turned a caller's zero
        volatility or expired option into a plausible-looking but fictitious
        price. ``tte == 0`` and ``vol == 0`` are both well defined and return the
        discounted intrinsic value; negative inputs are caller errors.
        """
        kind = _normalize_option_type(option_type)
        s = _require_positive("spot", spot)
        k = _require_positive("strike", strike)
        t = _require_finite("tte", tte)
        sigma = _require_finite("vol", vol)
        rate = _require_finite("r", r)
        yield_ = _require_finite("q", q)

        if t < 0.0:
            raise ValueError(f"tte must be non-negative, got {tte!r}")
        if sigma < 0.0:
            raise ValueError(f"vol must be non-negative, got {vol!r}")

        discounted_strike = k * math.exp(-rate * t)
        discounted_spot = s * math.exp(-yield_ * t)

        if t == 0.0 or sigma == 0.0:
            # Zero variance: the payoff is deterministic under the forward measure.
            if kind == "CALL":
                return max(discounted_spot - discounted_strike, 0.0)
            return max(discounted_strike - discounted_spot, 0.0)

        sqrt_t = math.sqrt(t)
        d1 = (math.log(s / k) + (rate - yield_ + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t

        if kind == "CALL":
            return discounted_spot * _norm_cdf(d1) - discounted_strike * _norm_cdf(d2)
        return discounted_strike * _norm_cdf(-d2) - discounted_spot * _norm_cdf(-d1)

    def vega(
        self,
        spot: float,
        strike: float,
        tte: float,
        vol: float,
        r: float,
        q: float = 0.0,
    ) -> float:
        """
        Black-Scholes vega ``dV/dsigma`` per unit of sigma (not per vol point),
        identical for calls and puts::

            vega = S e^{-qt} phi(d1) sqrt(t)

        Exposed because it is the conditioning measure for inversion: the
        volatility a quoted price identifies is only resolved to roughly
        (price resolution) / vega. Where vega collapses -- deep in- or
        out-of-the-money, very short expiry -- that range is wide, and any
        solver's answer, this one included, is arbitrary within it.
        """
        s = _require_positive("spot", spot)
        k = _require_positive("strike", strike)
        t = _require_finite("tte", tte)
        sigma = _require_finite("vol", vol)
        rate = _require_finite("r", r)
        yield_ = _require_finite("q", q)
        # Negative inputs are caller errors and must raise here exactly as they do
        # in black_scholes_price; only the well-defined zero cases return 0.0.
        if t < 0.0:
            raise ValueError(f"tte must be non-negative, got {tte!r}")
        if sigma < 0.0:
            raise ValueError(f"vol must be non-negative, got {vol!r}")
        if t == 0.0 or sigma == 0.0:
            return 0.0

        sqrt_t = math.sqrt(t)
        d1 = (math.log(s / k) + (rate - yield_ + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
        return s * math.exp(-yield_ * t) * _norm_pdf(d1) * sqrt_t

    def no_arbitrage_price_bounds(
        self,
        option_type: str,
        spot: float,
        strike: float,
        tte: float,
        r: float,
        q: float = 0.0,
    ) -> Tuple[float, float]:
        """
        Returns ``(lower, upper)`` European price bounds implied by no arbitrage::

            call:  max(S e^{-qt} - K e^{-rt}, 0)  <=  C  <=  S e^{-qt}
            put:   max(K e^{-rt} - S e^{-qt}, 0)  <=  P  <=  K e^{-rt}

        A quote at or outside these bounds has no interior implied volatility.
        """
        kind = _normalize_option_type(option_type)
        s = _require_positive("spot", spot)
        k = _require_positive("strike", strike)
        t = _require_finite("tte", tte)
        if t < 0.0:
            raise ValueError(f"tte must be non-negative, got {tte!r}")
        rate = _require_finite("r", r)
        yield_ = _require_finite("q", q)

        discounted_spot = s * math.exp(-yield_ * t)
        discounted_strike = k * math.exp(-rate * t)
        if kind == "CALL":
            return max(discounted_spot - discounted_strike, 0.0), discounted_spot
        return max(discounted_strike - discounted_spot, 0.0), discounted_strike

    # ------------------------------------------------------------------
    # Implied volatility inversion
    # ------------------------------------------------------------------

    def implied_volatility_from_price(
        self,
        option_type: str,
        strike: float,
        tte: float,
        market_price: float,
        spot: Optional[float] = None,
        r: Optional[float] = None,
        q: Optional[float] = None,
    ) -> float:
        """
        Inverts a European Black-Scholes price to its implied volatility by
        bisection on ``[IV_SOLVER_MIN_VOL, IV_SOLVER_MAX_VOL]``.

        The BS price is strictly increasing in sigma, so the bracket is valid
        whenever the quote lies strictly inside the no-arbitrage bounds and below
        the price at ``IV_SOLVER_MAX_VOL``. Bisection converges unconditionally on
        such a bracket; Newton-Raphson does not, because its step ``residual/vega``
        is unbounded where vega collapses (deep ITM/OTM, very short expiry).

        Raises ValueError -- never returns a fabricated number -- when:
          - the quote is at or outside the no-arbitrage bounds (no solution exists);
          - the quote exceeds the price at ``IV_SOLVER_MAX_VOL`` (a solution may
            exist but is beyond the range this solver will report);
          - ``tte`` is zero (every price equals intrinsic; sigma is unidentified).
        """
        kind = _normalize_option_type(option_type)
        s = self.config.spot_price if spot is None else _require_positive("spot", spot)
        rate = self.config.risk_free_rate if r is None else _require_finite("r", r)
        yield_ = self.config.dividend_yield if q is None else _require_finite("q", q)
        k = _require_positive("strike", strike)
        t = _require_positive("tte", tte)
        price = _require_finite("market_price", market_price)

        lower_bound, upper_bound = self.no_arbitrage_price_bounds(kind, s, k, t, rate, yield_)
        if price <= lower_bound or price >= upper_bound:
            raise ValueError(
                f"{kind} price {price!r} is outside the no-arbitrage bounds "
                f"({lower_bound:.10g}, {upper_bound:.10g}) for spot={s!r}, strike={k!r}, "
                f"tte={t!r}, r={rate!r}, q={yield_!r}: no implied volatility exists. "
                "Check the quote, the spot, and whether the option is American."
            )

        price_at_max = self.black_scholes_price(kind, s, k, t, IV_SOLVER_MAX_VOL, rate, yield_)
        if price > price_at_max:
            raise ValueError(
                f"{kind} price {price!r} exceeds the model price {price_at_max:.10g} at the "
                f"solver ceiling sigma={IV_SOLVER_MAX_VOL}: implied volatility is above the "
                "supported range."
            )

        low, high = IV_SOLVER_MIN_VOL, IV_SOLVER_MAX_VOL
        for _ in range(IV_SOLVER_MAX_ITERATIONS):
            if high - low < IV_SOLVER_TOLERANCE:
                break
            mid = 0.5 * (low + high)
            if self.black_scholes_price(kind, s, k, t, mid, rate, yield_) < price:
                low = mid
            else:
                high = mid
        else:
            # Bisection halves the bracket every pass, so exhausting the loop
            # without meeting the tolerance is a numerical anomaly worth surfacing.
            logger.warning(
                "IV bisection hit the %d-iteration cap for %s K=%.6g t=%.6g: bracket [%.12g, %.12g]",
                IV_SOLVER_MAX_ITERATIONS, kind, k, t, low, high,
            )

        solution = 0.5 * (low + high)

        # The bracket can be exhausted to float64 while the *price* is flat in
        # sigma, which makes the answer precise-looking and arbitrary. Report it.
        solution_vega = self.vega(s, k, t, solution, rate, yield_)
        price_resolution = max(abs(price), 1.0) * 1e-15
        sigma_resolution = (
            price_resolution / solution_vega if solution_vega > 0.0 else float("inf")
        )
        if sigma_resolution > IV_CONDITIONING_WARN_SIGMA_RESOLUTION:
            logger.warning(
                "Implied volatility is poorly identified for %s K=%.6g t=%.6g: vega=%.3e means "
                "this price resolves sigma only to about +/-%.3g. Reported %.6f, but treat it as "
                "unreliable and consider excluding this quote from the smile fit.",
                kind, k, t, solution_vega, sigma_resolution, solution,
            )

        return solution

    # ------------------------------------------------------------------
    # Smile calibration
    # ------------------------------------------------------------------

    def calibrate_smile_from_quotes(
        self,
        quotes: Sequence[OptionMarketQuote],
        spot: Optional[float] = None,
    ) -> SmileCalibrationResult:
        """
        Inverts every quote for one expiration and least-squares fits the
        quadratic smile ``sigma(x) = sigma_atm + alpha x + beta x^2``, ``x = K/S - 1``.

        All quotes must share the same ``tte_years`` -- a smile is one expiration.
        Mixing expirations silently fits a surface cross-section as if it were a
        slice, so it raises.

        Fitting slices independently gives **no** guarantee that the resulting
        surface is calendar-arbitrage-free. Run ``construct_surface_grid`` on the
        fitted parameters and check the report before using them.
        """
        if not quotes:
            raise ValueError("quotes must be a non-empty sequence of OptionMarketQuote.")

        s = self.config.spot_price if spot is None else _require_positive("spot", spot)
        tenors = {_require_positive("tte_years", quote.tte_years) for quote in quotes}
        if len(tenors) != 1:
            raise ValueError(
                f"calibrate_smile_from_quotes fits one expiration at a time; got "
                f"{len(tenors)} distinct tte_years values: {sorted(tenors)}."
            )
        tte = tenors.pop()

        if len(quotes) < MIN_QUOTES_FOR_SMILE_FIT:
            raise ValueError(
                f"at least {MIN_QUOTES_FOR_SMILE_FIT} quotes are needed to identify "
                f"sigma_atm / alpha / beta, got {len(quotes)}."
            )

        xs: List[float] = []
        ivs: List[float] = []
        strike_iv_pairs: List[Tuple[float, float]] = []
        for quote in quotes:
            iv = self.implied_volatility_from_price(
                quote.option_type, quote.strike, quote.tte_years, quote.market_price, spot=s
            )
            xs.append(_require_positive("strike", quote.strike) / s - 1.0)
            ivs.append(iv)
            strike_iv_pairs.append((float(quote.strike), iv))

        # Normal equations for the design matrix [1, x, x^2].
        power_sums = [sum(x ** p for x in xs) for p in range(5)]
        matrix = [[power_sums[row + col] for col in range(3)] for row in range(3)]
        rhs = [sum(iv * (x ** p) for x, iv in zip(xs, ivs)) for p in range(3)]
        atm_vol, alpha, beta = _solve_3x3(matrix, rhs)

        residuals = [iv - (atm_vol + alpha * x + beta * x * x) for x, iv in zip(xs, ivs)]
        rms_error = math.sqrt(sum(res * res for res in residuals) / len(residuals))

        if atm_vol <= 0.0:
            logger.warning(
                "Calibrated ATM volatility is non-positive (%.6g) at tte=%.6g: the quotes do not "
                "support this parameterization over their moneyness range.", atm_vol, tte,
            )

        logger.info(
            "Smile calibrated at tte=%.6g from %d quotes: atm_vol=%.6g alpha=%.6g beta=%.6g "
            "rms=%.3e", tte, len(quotes), atm_vol, alpha, beta, rms_error,
        )
        return SmileCalibrationResult(
            tte_years=tte,
            atm_vol=atm_vol,
            skew_alpha=alpha,
            smile_beta=beta,
            quotes_used=len(quotes),
            rms_error=rms_error,
            implied_vols=strike_iv_pairs,
        )

    # ------------------------------------------------------------------
    # Surface evaluation
    # ------------------------------------------------------------------

    def evaluate_strike_iv(
        self,
        strike: float,
        tte_years: float,
        atm_vol: Optional[float] = None,
    ) -> float:
        """
        Evaluates the parametric quadratic smile at spot moneyness ``m = K / S``::

            IV(m) = atm_vol + alpha * (m - 1) + beta * (m - 1)^2

        This is exactly the formula documented in ``SKILL.md`` and
        ``references/workflows.md``. The previous implementation scaled the
        skew/smile offset by an undocumented factor of 0.5, so the surface it
        produced was half the skew the documentation described and disagreed with
        the sibling skill using the same ``skew_alpha`` / ``smile_beta`` names.

        ``atm_vol`` overrides the config level for this evaluation, which is how
        ``construct_surface_grid`` injects an ATM term structure. ``tte_years`` is
        validated but does not enter the formula: there is no term decay of the
        offset here (see the module docstring).

        The result is clamped to ``[MIN_STRIKE_IV, MAX_STRIKE_IV]`` and every
        binding clamp is logged, because a clamped wing is no longer the
        parametric surface and can mask an arbitrage the audit would have caught.
        """
        k = _require_positive("strike", strike)
        t = _require_positive("tte_years", tte_years)
        level = self.config.atm_vol if atm_vol is None else _require_finite("atm_vol", atm_vol)

        m = k / self.config.spot_price
        x = m - 1.0
        raw_iv = level + self.config.skew_alpha * x + self.config.smile_beta * x * x

        clamped = max(MIN_STRIKE_IV, min(MAX_STRIKE_IV, raw_iv))
        if clamped != raw_iv:
            logger.warning(
                "Strike IV clamped at K=%.6g (m=%.4f, tte=%.6g): raw %.6g -> %.6g. The wing is no "
                "longer the parametric surface and the arbitrage audit there is inconclusive.",
                k, m, t, raw_iv, clamped,
            )
        return clamped

    def construct_surface_grid(
        self,
        strikes: Sequence[float],
        expirations_tte: Sequence[float],
        atm_vol_by_tte: Optional[Mapping[float, float]] = None,
    ) -> IVSurfaceConstructionReport:
        """
        Evaluates the surface on the strike x expiration grid and audits both
        static no-arbitrage conditions.

        ``atm_vol_by_tte`` supplies an ATM volatility term structure, keyed by
        time to expiration. Every expiration in ``expirations_tte`` must be
        present when the mapping is given. Without it the ATM level is flat across
        expirations, in which case total variance at fixed moneyness rises
        mechanically with ``t`` and the calendar audit can only ever fire from the
        forward drift -- a flat-ATM surface is close to arbitrage-free by
        construction, and a clean report on one says little.

        Audits (see the module docstring for sources):

        - **Calendar**: for each log-forward moneyness ``k`` implied by the input
          strikes at the front expiration, evaluate ``w(k, t) = sigma(F_t e^k)^2 t``
          across all expirations and require it to be non-decreasing.
        - **Butterfly**: at each expiration, require every consecutive strike
          triple to price to a non-negative spacing-weighted butterfly.

        Both audits sample a finite grid. An empty or single-point grid cannot be
        audited; the report says so via ``calendar_audit_performed`` /
        ``butterfly_audit_performed`` and ``is_arbitrage_free`` is then False.
        """
        spot = self.config.spot_price

        if not strikes:
            raise ValueError("strikes must be a non-empty sequence.")
        if not expirations_tte:
            raise ValueError("expirations_tte must be a non-empty sequence.")

        sorted_strikes = sorted({_require_positive("strike", k) for k in strikes})
        sorted_expirations = sorted({_require_positive("tte_years", t) for t in expirations_tte})

        if len(sorted_strikes) != len(strikes):
            logger.info(
                "Deduplicated strikes: %d supplied, %d distinct.", len(strikes), len(sorted_strikes)
            )
        if len(sorted_expirations) != len(expirations_tte):
            logger.info(
                "Deduplicated expirations: %d supplied, %d distinct.",
                len(expirations_tte), len(sorted_expirations),
            )

        atm_levels = self._resolve_atm_levels(sorted_expirations, atm_vol_by_tte)

        grid: List[IVSurfacePoint] = []
        forwards = {t: self.forward_price(t) for t in sorted_expirations}
        for k in sorted_strikes:
            for tte in sorted_expirations:
                iv = self.evaluate_strike_iv(k, tte, atm_vol=atm_levels[tte])
                forward = forwards[tte]
                grid.append(IVSurfacePoint(
                    strike=k,
                    tte_years=tte,
                    moneyness=k / spot,
                    implied_volatility=iv,
                    total_variance=iv * iv * tte,
                    forward_price=forward,
                    log_forward_moneyness=math.log(k / forward),
                ))

        calendar_violations = self._audit_calendar_spread(sorted_expirations, sorted_strikes, atm_levels)
        butterfly_violations = self._audit_butterfly(sorted_expirations, sorted_strikes, atm_levels)

        calendar_performed = len(sorted_expirations) >= 2
        butterfly_performed = len(sorted_strikes) >= 3

        if calendar_violations and butterfly_violations:
            status = STATUS_STATIC_VIOLATION
        elif calendar_violations:
            status = STATUS_CALENDAR_VIOLATION
        elif butterfly_violations:
            status = STATUS_BUTTERFLY_VIOLATION
        elif calendar_performed and butterfly_performed:
            status = STATUS_ARBITRAGE_FREE
        else:
            status = STATUS_UNAUDITED

        unaudited: List[str] = []
        if not calendar_performed:
            unaudited.append("calendar audit skipped (needs >= 2 expirations)")
        if not butterfly_performed:
            unaudited.append("butterfly audit skipped (needs >= 3 strikes)")

        notes = (
            f"IV SURFACE CONSTRUCTED [{status}]: Spot = ${spot:,.2f}, Total Grid Points = {len(grid)} "
            f"across {len(sorted_strikes)} strikes and {len(sorted_expirations)} expirations. "
            f"Calendar violations = {len(calendar_violations)}, "
            f"butterfly violations = {len(butterfly_violations)}."
        )
        if unaudited:
            notes += " " + "; ".join(unaudited) + "."

        if calendar_violations or butterfly_violations:
            logger.error(notes)
        elif unaudited:
            logger.warning(notes)
        else:
            logger.info(notes)

        return IVSurfaceConstructionReport(
            spot_price=spot,
            risk_free_rate=self.config.risk_free_rate,
            total_surface_points=len(grid),
            grid_points=grid,
            status=status,
            audit_notes=notes,
            calendar_violations=calendar_violations,
            butterfly_violations=butterfly_violations,
            dividend_yield=self.config.dividend_yield,
            calendar_audit_performed=calendar_performed,
            butterfly_audit_performed=butterfly_performed,
        )

    # ------------------------------------------------------------------
    # Audits
    # ------------------------------------------------------------------

    def _resolve_atm_levels(
        self,
        expirations: Sequence[float],
        atm_vol_by_tte: Optional[Mapping[float, float]],
    ) -> dict:
        """Maps each expiration to its ATM volatility, defaulting to the flat config level."""
        if atm_vol_by_tte is None:
            return {t: self.config.atm_vol for t in expirations}

        levels = {}
        for t in expirations:
            if t not in atm_vol_by_tte:
                raise ValueError(
                    f"atm_vol_by_tte is missing expiration {t!r}. Supply every expiration or pass "
                    "None to use the flat config level -- silently substituting the flat level for "
                    "a missing tenor would fabricate a term structure."
                )
            levels[t] = _require_positive(f"atm_vol_by_tte[{t!r}]", atm_vol_by_tte[t])
        return levels

    def _audit_calendar_spread(
        self,
        expirations: Sequence[float],
        strikes: Sequence[float],
        atm_levels: Mapping[float, float],
    ) -> List[CalendarArbitrageViolation]:
        """
        Audits ``d/dt w(k, t) >= 0`` at fixed log-forward moneyness (Gatheral &
        Jacquier Lemma 2.1 / Definition 2.2).

        The ``k`` levels are taken from the input strikes at the **front**
        expiration -- ``k_i = ln(K_i / F_{t_0})`` -- and each is then re-struck at
        every later expiration as ``K = F_t e^k``. Auditing at fixed *strike*
        instead compares different ``k`` whenever ``r != q`` and is not the
        no-arbitrage condition.
        """
        violations: List[CalendarArbitrageViolation] = []
        if len(expirations) < 2:
            logger.info("Calendar audit skipped: %d expiration(s) supplied, need 2.", len(expirations))
            return violations

        front_forward = self.forward_price(expirations[0])
        k_levels = sorted({math.log(k / front_forward) for k in strikes})

        for k_level in k_levels:
            prev_tte: Optional[float] = None
            prev_w: Optional[float] = None
            for tte in expirations:
                strike = self.forward_price(tte) * math.exp(k_level)
                iv = self.evaluate_strike_iv(strike, tte, atm_vol=atm_levels[tte])
                w = iv * iv * tte

                if prev_w is not None and w < prev_w - CALENDAR_VARIANCE_TOLERANCE:
                    violation = CalendarArbitrageViolation(
                        log_forward_moneyness=k_level,
                        tte_short=prev_tte,
                        tte_long=tte,
                        total_variance_short=prev_w,
                        total_variance_long=w,
                    )
                    violations.append(violation)
                    logger.warning(
                        "Calendar spread arbitrage at k=%.6f: w(t=%.6g)=%.8f < w(t=%.6g)=%.8f "
                        "(shortfall %.3e).",
                        k_level, tte, w, prev_tte, prev_w, violation.shortfall,
                    )

                prev_tte, prev_w = tte, w

        return violations

    def _audit_butterfly(
        self,
        expirations: Sequence[float],
        strikes: Sequence[float],
        atm_levels: Mapping[float, float],
    ) -> List[ButterflyArbitrageViolation]:
        """
        Audits convexity of the call price in strike -- the discrete, tradeable
        form of ``d^2 C / dK^2 >= 0`` (Breeden & Litzenberger 1978), which is
        equivalent to a non-negative risk-neutral density (Gatheral & Jacquier
        Definition 2.3).

        For each consecutive strike triple, the spacing-weighted butterfly
        ``w1 C(K1) + w3 C(K3) - C(K2)`` must be non-negative. A negative value is a
        position with negative cost and non-negative payoff at expiry.

        Calls are used, but the condition is not call-specific: put-call parity is
        linear in K, so put convexity in strike is the same statement.
        """
        violations: List[ButterflyArbitrageViolation] = []
        if len(strikes) < 3:
            logger.info("Butterfly audit skipped: %d strike(s) supplied, need 3.", len(strikes))
            return violations

        spot = self.config.spot_price
        rate = self.config.risk_free_rate
        yield_ = self.config.dividend_yield
        tolerance = BUTTERFLY_TOLERANCE_FRACTION_OF_SPOT * spot

        for tte in expirations:
            prices = [
                self.black_scholes_price(
                    "CALL", spot, k, tte,
                    self.evaluate_strike_iv(k, tte, atm_vol=atm_levels[tte]),
                    rate, yield_,
                )
                for k in strikes
            ]
            for i in range(len(strikes) - 2):
                k1, k2, k3 = strikes[i], strikes[i + 1], strikes[i + 2]
                span = k3 - k1
                weight_low = (k3 - k2) / span
                weight_high = (k2 - k1) / span
                value = weight_low * prices[i] + weight_high * prices[i + 2] - prices[i + 1]

                if value < -tolerance:
                    violations.append(ButterflyArbitrageViolation(
                        tte_years=tte,
                        strike_low=k1,
                        strike_mid=k2,
                        strike_high=k3,
                        butterfly_value=value,
                    ))
                    logger.warning(
                        "Butterfly arbitrage at tte=%.6g, strikes (%.6g, %.6g, %.6g): "
                        "weighted butterfly = %.8f < 0 (negative risk-neutral density).",
                        tte, k1, k2, k3, value,
                    )

        return violations
