"""
options-backtesting-with-realistic-iv-surface: parametric implied-volatility surface
(moneyness smile/skew + term-structure decay), Black-Scholes-Merton option pricer,
and analytic Greeks for options backtesting.

Surface model
-------------
The strike volatility is a quadratic-in-moneyness smile whose *offset from ATM* is
scaled by a power law in time to expiration:

    m       = K / S                                   (strike moneyness)
    offset  = alpha * (m - 1) + beta * (m - 1)^2      (skew slope + smile curvature)
    s(T)    = min(MAX_SKEW_TERM_SCALE, (T_ref / T) ** gamma)
    sigma(m, T) = sigma_atm + offset * s(T)

`alpha` and `beta` are therefore calibrated *at the reference tenor* T_ref
(``REFERENCE_TENOR_YEARS``, 30 days), where s(T_ref) = 1 by construction. Quoting the
calibration tenor matters: the same alpha describes a very different surface at one
week than at one year.

The `s(T)` term is what makes this a genuine surface rather than a single smile
repeated at every expiration. A power-law decay of the at-the-money skew is a
standard stylized fact of equity index surfaces: the SSVI parameterization of
Gatheral & Jacquier ("Arbitrage-free SVI volatility surfaces", Quantitative Finance
18(6), 2014; arXiv:1204.0646) produces an ATM skew proportional to T^(-1/2), and the
rough-volatility literature (Gatheral, Jaisson & Rosenbaum, "Volatility is rough",
Quantitative Finance 18(6):933-949, 2018; arXiv:1410.3394) reports a short-maturity
skew decaying as T^(H - 1/2) with H of order 0.1, i.e. roughly T^(-0.4).

``gamma`` (``skew_term_decay``) is therefore a *calibration input*, not a constant of
nature: 0.5 is the SSVI baseline default, published empirical estimates cluster in
the 0.4-0.5 range, and the exponent should be fitted per underlying. Setting
``skew_term_decay=0.0`` disables term scaling entirely and reproduces a single smile
at every expiration.

Pricing model
-------------
European Black-Scholes-Merton with a continuous dividend yield q (Merton, "Theory of
Rational Option Pricing", Bell Journal of Economics and Management Science 4(1),
1973):

    d1 = [ln(S/K) + (r - q + sigma^2 / 2) T] / (sigma sqrt(T))
    d2 = d1 - sigma sqrt(T)
    Call = S e^(-qT) N(d1) - K e^(-rT) N(d2)
    Put  = K e^(-rT) N(-d2) - S e^(-qT) N(-d1)

Limitations (documented, deliberate)
------------------------------------
- **European exercise only.** No early-exercise premium is computed, so American
  options - every listed US single-stock option, and index options on some venues -
  are systematically underpriced, most visibly for in-the-money puts and for calls
  on a stock about to go ex-dividend. Do not use this engine to decide whether to
  exercise early.
- **Continuous dividend yield only.** Discrete cash dividends before expiry are not
  modelled. The escrowed-dividend convention (replace S with S minus the present
  value of dividends paid before expiry) must be applied by the caller *before*
  calling ``price_option``; passing an unadjusted spot overprices calls and
  underprices puts across the ex-dividend date.
- **The surface is parametric, not fitted.** ``get_strike_iv`` evaluates a closed
  form; it does not interpolate market quotes and is not checked for static
  arbitrage. For calibration to live quotes and calendar/butterfly arbitrage audits
  see the `options-implied-volatility-surface-construction` skill.
- **The power law is extrapolated below one month.** Empirically the equity-index ATM
  skew power law is documented over maturities beyond roughly one month; sub-month
  behaviour deviates from it. ``MAX_SKEW_TERM_SCALE`` bounds that extrapolation - it
  is a guard against an unbounded offset as T -> 0, not a market observation.
- **Nothing is rounded.** Prices, IVs and Greeks are returned at full float
  precision. Rounding a theoretical price to the tick breaks put-call parity, so
  quantize at the presentation or fill-simulation layer, not here.
"""
import logging
import math
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

#: Tenor at which ``skew_alpha`` / ``smile_beta`` are calibrated: s(T_ref) = 1.
#: 30 calendar days, the standard front-month quoting tenor.
REFERENCE_TENOR_YEARS = 30.0 / 365.0

#: SSVI-baseline power-law exponent for the decay of the skew/smile offset in T.
#: See module docstring: 0.5 (SSVI) vs ~0.4 (rough volatility) - calibrate per name.
DEFAULT_SKEW_TERM_DECAY = 0.5

#: Upper bound on the term-structure scale factor s(T). With the defaults
#: (T_ref = 30d, gamma = 0.5) this binds only below 30 / 4^2 = 1.875 days to expiry,
#: i.e. it caps extrapolation into the region where the power law is not empirically
#: supported. It is a numerical guard, not a calibrated market quantity.
MAX_SKEW_TERM_SCALE = 4.0

#: Hard bounds on the strike IV the surface may return. The quadratic smile is
#: unbounded in both directions for extreme moneyness; without these the wings can
#: produce a negative or absurd volatility. Binding is logged, never silent.
MIN_STRIKE_IV = 0.05
MAX_STRIKE_IV = 3.0

#: Vega is reported per 1 volatility *point* (a move from 20% to 21%), the desk
#: convention, rather than per unit of sigma.
VEGA_POINTS_PER_UNIT_VOL = 100.0

#: Theta is reported per calendar day.
CALENDAR_DAYS_PER_YEAR = 365.0

_VALID_OPTION_TYPES = ("CALL", "PUT")


@dataclass
class OptionGreeks:
    delta: float                        # dV/dS, per $1 of underlying
    gamma: float                        # d2V/dS2
    theta: float                        # dV/dt, per CALENDAR day
    vega: float                         # dV/dsigma, per 1 volatility POINT (1%)


@dataclass
class OptionPricingResult:
    option_type: str                    # 'CALL' or 'PUT'
    underlying_price: float
    strike_price: float
    tte_years: float
    atm_volatility: float
    strike_iv: float                    # sigma(m, T) actually used to price
    option_price: float                 # Unrounded. Intrinsic when tte_years == 0.
    greeks: OptionGreeks
    risk_free_rate: float = 0.0         # Recorded for backtest auditability
    dividend_yield: float = 0.0         # Continuous q applied to this valuation
    term_skew_scale: float = 1.0        # s(T) applied to the smile offset
    is_expired: bool = False            # True when tte_years == 0 (intrinsic payoff)


def _require_finite(name: str, value: float) -> float:
    """Rejects NaN/Inf before it can propagate silently into a price."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return numeric


def _normalize_option_type(option_type: str) -> str:
    """
    Maps caller input to 'CALL' or 'PUT', raising on anything else.

    Treating *any* non-'CALL' string as a put means a caller passing 'C' silently
    receives a put price. Unknown types must raise.
    """
    if not isinstance(option_type, str):
        raise ValueError(f"option_type must be a string, got {type(option_type).__name__}")
    normalized = option_type.strip().upper()
    if normalized not in _VALID_OPTION_TYPES:
        raise ValueError(
            f"option_type must be one of {_VALID_OPTION_TYPES}, got {option_type!r}"
        )
    return normalized


class OptionsIVSurfaceEngine:
    """
    Evaluates a parametric implied-volatility surface sigma(K/S, T) and prices
    European options on it with Black-Scholes-Merton, returning analytic Greeks.

    Args:
        risk_free_rate: Continuously compounded risk-free rate r.
        skew_alpha: Linear moneyness coefficient, calibrated at REFERENCE_TENOR_YEARS.
            Negative reproduces the equity put skew.
        smile_beta: Quadratic moneyness coefficient (smile convexity), same tenor.
        dividend_yield: Continuous dividend yield q used when no per-call override is
            given. Discrete cash dividends are NOT handled - see module docstring.
        skew_term_decay: Power-law exponent gamma for the term decay of the smile
            offset. 0.0 disables term structure (single smile at every expiration).
    """

    def __init__(
        self,
        risk_free_rate: float = 0.05,
        skew_alpha: float = -0.30,      # Negative slope (put skew)
        smile_beta: float = 0.50,       # Convexity (smile)
        dividend_yield: float = 0.0,
        skew_term_decay: float = DEFAULT_SKEW_TERM_DECAY,
    ) -> None:
        self.risk_free_rate = _require_finite("risk_free_rate", risk_free_rate)
        self.skew_alpha = _require_finite("skew_alpha", skew_alpha)
        self.smile_beta = _require_finite("smile_beta", smile_beta)
        self.dividend_yield = _require_finite("dividend_yield", dividend_yield)
        self.skew_term_decay = _require_finite("skew_term_decay", skew_term_decay)
        if self.skew_term_decay < 0.0:
            raise ValueError(
                f"skew_term_decay must be >= 0 (0 disables term scaling), "
                f"got {skew_term_decay!r}"
            )

    def term_skew_scale(self, tte_years: float) -> float:
        """
        Power-law scale s(T) applied to the smile offset, normalised so that
        s(REFERENCE_TENOR_YEARS) == 1.0.

            s(T) = min(MAX_SKEW_TERM_SCALE, (T_ref / T) ** gamma)

        Short expirations scale the skew up, long expirations flatten it. Returns
        1.0 when ``skew_term_decay`` is 0, and also when the surface is flat
        (``skew_alpha`` and ``smile_beta`` both 0) and so has no offset to scale -
        the flat-IV comparison leg of a skew-drag audit must not emit extrapolation
        warnings for a scale factor that multiplies zero.

        Raises:
            ValueError: if ``tte_years`` is not finite or is <= 0.
        """
        tte = _require_finite("tte_years", tte_years)
        if tte <= 0.0:
            raise ValueError(f"tte_years must be > 0 to scale skew, got {tte_years!r}")
        if self.skew_term_decay == 0.0 or (self.skew_alpha == 0.0 and self.smile_beta == 0.0):
            return 1.0

        scale = (REFERENCE_TENOR_YEARS / tte) ** self.skew_term_decay
        if scale > MAX_SKEW_TERM_SCALE:
            logger.warning(
                "Term skew scale %.4f capped at %.2f for tte_years=%.6f; the skew "
                "power law is extrapolated, not calibrated, at this tenor.",
                scale, MAX_SKEW_TERM_SCALE, tte,
            )
            return MAX_SKEW_TERM_SCALE
        return scale

    def _evaluate_surface(
        self, spot: float, strike: float, tte_years: float, atm_vol: float
    ) -> Tuple[float, float]:
        """
        Returns ``(strike_iv, term_skew_scale)`` in one pass.

        ``price_option`` needs both, and ``term_skew_scale`` can emit an
        extrapolation warning, so evaluating it twice would log the same warning
        twice for a single valuation.
        """
        spot = _require_finite("spot", spot)
        strike = _require_finite("strike", strike)
        atm_vol = _require_finite("atm_vol", atm_vol)
        if spot <= 0.0:
            raise ValueError(f"spot must be > 0, got {spot!r}")
        if strike <= 0.0:
            raise ValueError(f"strike must be > 0, got {strike!r}")
        if atm_vol <= 0.0:
            raise ValueError(f"atm_vol must be > 0, got {atm_vol!r}")

        moneyness = strike / spot
        offset = (
            self.skew_alpha * (moneyness - 1.0)
            + self.smile_beta * ((moneyness - 1.0) ** 2)
        )
        term_scale = self.term_skew_scale(tte_years)
        strike_iv = atm_vol + offset * term_scale

        clamped = max(MIN_STRIKE_IV, min(MAX_STRIKE_IV, strike_iv))
        if clamped != strike_iv:
            logger.warning(
                "Strike IV %.4f clamped to %.4f at moneyness %.4f, tte_years %.6f; "
                "the quadratic smile is being extrapolated beyond its valid range.",
                strike_iv, clamped, moneyness, tte_years,
            )
        return clamped, term_scale

    def get_strike_iv(self, spot: float, strike: float, tte_years: float, atm_vol: float) -> float:
        """
        Evaluates the surface at (K/S, T):

            sigma(m, T) = atm_vol + [alpha (m - 1) + beta (m - 1)^2] * s(T)

        The result is clamped to [MIN_STRIKE_IV, MAX_STRIKE_IV]; a binding clamp is
        logged, because a clamped IV is a model limit being hit rather than a
        market-consistent volatility.

        Raises:
            ValueError: on non-finite inputs, spot <= 0, strike <= 0, tte_years <= 0,
                or atm_vol <= 0.
        """
        strike_iv, _ = self._evaluate_surface(spot, strike, tte_years, atm_vol)
        return strike_iv

    @staticmethod
    def _norm_cdf(x: float) -> float:
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    @staticmethod
    def _norm_pdf(x: float) -> float:
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    def _expired_result(
        self, option_type: str, spot: float, strike: float, atm_vol: float, q: float
    ) -> OptionPricingResult:
        """
        Terminal payoff at T = 0. A backtest that holds to expiry must settle on
        intrinsic value, not on a Black-Scholes price evaluated at an epsilon tenor -
        the latter reports residual time value and an unbounded gamma on a contract
        that no longer has either.
        """
        if option_type == "CALL":
            price = max(spot - strike, 0.0)
            delta = 1.0 if spot > strike else 0.0
        else:
            price = max(strike - spot, 0.0)
            delta = -1.0 if spot < strike else 0.0

        return OptionPricingResult(
            option_type=option_type,
            underlying_price=spot,
            strike_price=strike,
            tte_years=0.0,
            atm_volatility=atm_vol,
            strike_iv=atm_vol,          # Surface is degenerate at T = 0; unused.
            option_price=price,
            greeks=OptionGreeks(delta=delta, gamma=0.0, theta=0.0, vega=0.0),
            risk_free_rate=self.risk_free_rate,
            dividend_yield=q,
            term_skew_scale=1.0,
            is_expired=True,
        )

    def price_option(
        self,
        option_type: str,
        spot: float,
        strike: float,
        tte_years: float,
        atm_vol: float,
        dividend_yield: Optional[float] = None,
    ) -> OptionPricingResult:
        """
        Prices a European call or put at the surface volatility for its strike and
        tenor, with analytic Black-Scholes-Merton Greeks.

        Greek conventions: theta is per calendar day, vega is per 1 volatility point
        (20% -> 21%), delta and gamma are per $1 of underlying. Nothing is rounded.

        Args:
            option_type: 'CALL' or 'PUT' (case-insensitive). Anything else raises.
            spot: Underlying price S, already adjusted for any discrete dividends
                paid before expiry (see module docstring).
            strike: Strike K.
            tte_years: Time to expiration in years. 0 settles at intrinsic value;
                negative raises.
            atm_vol: At-the-money implied volatility for this tenor.
            dividend_yield: Continuous q for this valuation; falls back to the
                engine-level ``dividend_yield`` when omitted.

        Raises:
            ValueError: on an unknown option type, non-finite inputs, spot <= 0,
                strike <= 0, atm_vol <= 0, or tte_years < 0.
        """
        normalized_type = _normalize_option_type(option_type)
        spot = _require_finite("spot", spot)
        strike = _require_finite("strike", strike)
        tte = _require_finite("tte_years", tte_years)
        atm_vol = _require_finite("atm_vol", atm_vol)
        q = self.dividend_yield if dividend_yield is None else _require_finite(
            "dividend_yield", dividend_yield
        )

        if spot <= 0.0:
            raise ValueError(f"spot must be > 0, got {spot!r}")
        if strike <= 0.0:
            raise ValueError(f"strike must be > 0, got {strike!r}")
        if atm_vol <= 0.0:
            raise ValueError(f"atm_vol must be > 0, got {atm_vol!r}")
        if tte < 0.0:
            raise ValueError(
                f"tte_years must be >= 0, got {tte_years!r}; a negative tenor means "
                "the backtest clock has passed expiry without settling the contract"
            )

        if tte == 0.0:
            return self._expired_result(normalized_type, spot, strike, atm_vol, q)

        strike_iv, term_scale = self._evaluate_surface(spot, strike, tte, atm_vol)

        r = self.risk_free_rate
        sigma = strike_iv
        sqrt_t = math.sqrt(tte)

        d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * tte) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t

        pdf_d1 = self._norm_pdf(d1)
        discount_r = math.exp(-r * tte)
        discount_q = math.exp(-q * tte)

        if normalized_type == "CALL":
            n_d1 = self._norm_cdf(d1)
            n_d2 = self._norm_cdf(d2)
            price = spot * discount_q * n_d1 - strike * discount_r * n_d2
            delta = discount_q * n_d1
            theta_annual = (
                -discount_q * spot * pdf_d1 * sigma / (2.0 * sqrt_t)
                - r * strike * discount_r * n_d2
                + q * spot * discount_q * n_d1
            )
        else:
            n_minus_d1 = self._norm_cdf(-d1)
            n_minus_d2 = self._norm_cdf(-d2)
            price = strike * discount_r * n_minus_d2 - spot * discount_q * n_minus_d1
            delta = -discount_q * n_minus_d1
            theta_annual = (
                -discount_q * spot * pdf_d1 * sigma / (2.0 * sqrt_t)
                + r * strike * discount_r * n_minus_d2
                - q * spot * discount_q * n_minus_d1
            )

        gamma = discount_q * pdf_d1 / (spot * sigma * sqrt_t)
        vega = spot * discount_q * pdf_d1 * sqrt_t / VEGA_POINTS_PER_UNIT_VOL
        theta = theta_annual / CALENDAR_DAYS_PER_YEAR

        greeks = OptionGreeks(
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
        )

        return OptionPricingResult(
            option_type=normalized_type,
            underlying_price=spot,
            strike_price=strike,
            tte_years=tte,
            atm_volatility=atm_vol,
            strike_iv=strike_iv,
            # Clamp only the floating-point residue that can make a deep-OTM price
            # marginally negative; never floor a genuinely near-worthless option to a
            # synthetic minimum tick, which would break put-call parity and invent
            # premium on wings a backtest should be letting expire worthless.
            option_price=max(0.0, price),
            greeks=greeks,
            risk_free_rate=r,
            dividend_yield=q,
            term_skew_scale=term_scale,
            is_expired=False,
        )
