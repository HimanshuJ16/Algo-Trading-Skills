"""
quanto-options-and-cross-currency-derivative-structures: Black-Scholes pricing and
Greeks for European *quanto* options -- an option on a foreign-currency-denominated
asset whose payoff is settled in the domestic currency at a contractually **fixed**
exchange rate.

Conventions (getting either of these backwards is the classic quanto mispricing)
--------------------------------------------------------------------------------
**Exchange rate direction.** ``X_t`` is the cost, in units of the *domestic*
currency, of one unit of the *foreign* currency -- "domestic per foreign". A
USD-domestic / EUR-foreign quote of 1.20 means one EUR costs 1.20 USD. The
"domestic" and "foreign" labels are a property of how the rate is quoted, not of
where anyone is sitting.

``correlation`` (rho) is the instantaneous correlation between the return on the
foreign asset ``S`` (in *foreign* currency) and the return on ``X`` in that
direction. If your correlation was estimated against the inverted quote
(foreign per domestic) its sign is flipped, and the drift adjustment below moves
by ``2 * rho * sigma_S * sigma_X`` -- 1.8 percentage points of annual drift at
the module defaults. There is no way for this engine to detect that; it is the
caller's responsibility.

**Strike currency.** ``strike_price`` is in the **foreign** asset's own units --
the same units as ``spot_price`` -- and the entire payoff is then multiplied by
``fixed_fx_rate``::

    payoff = fixed_fx_rate * max(S_T - K, 0)   [domestic currency]

This is the ``X_bar * BS(strike = K_domestic / X_bar)`` form. If your term sheet
quotes the strike in domestic currency, divide by ``fixed_fx_rate`` before
passing it in.

Model
-----
Under the *domestic* risk-neutral measure the foreign asset drifts at::

    mu_quanto = r_f - q - rho * sigma_S * sigma_X

(Haugh, IEOR E4707 lecture notes, "Foreign Exchange, ADRs and Quanto-Securities",
Section 4, equation (17); equivalently the ``exp(-rho * sigma_S * sigma_chi * t)``
quanto correction of arXiv:2501.07200, equation (7).) The option is then
Black-Scholes on that drift, **discounted at the domestic rate** ``r_d``, and
scaled by the fixed conversion multiplier::

    V_call = F_X * e^{-r_d T} * [ S e^{mu_quanto T} N(d1) - K N(d2) ]
    V_put  = F_X * e^{-r_d T} * [ K N(-d2) - S e^{mu_quanto T} N(-d1) ]
    d1     = [ln(S/K) + (mu_quanto + sigma_S^2 / 2) T] / (sigma_S sqrt(T))
    d2     = d1 - sigma_S sqrt(T)

Vega has two channels, not one
------------------------------
``sigma_S`` enters this model **twice**: through ``d1``/``d2`` as in plain
Black-Scholes, and through the drift ``mu_quanto``, because the quanto adjustment
is ``rho * sigma_S * sigma_X``. The total derivative is therefore::

    dV/dsigma_S = [spot channel]  F_X e^{-r_d T} S e^{mu T} n(d1) sqrt(T)
                + [drift channel] (dV/dmu) * (-rho * sigma_X)

Only the spot channel is the familiar Black-Scholes vega. Reporting it alone
overstates call vega and understates put vega -- 6.9% and 4.8% respectively at
the module defaults -- and produces the tell-tale symptom of an identical vega
for the call and the put, which is true in plain Black-Scholes but **false** for
a quanto. Both components are reported separately so the correction is auditable.

Scope
-----
European exercise, cash settlement, constant volatilities, constant correlation,
a single flat ``sigma_S`` (no smile, no term structure). See SKILL.md
"When NOT to Use" -- in particular this engine does **not** price *composite*
("compo") options, which convert at the prevailing spot FX rather than a fixed
rate and whose strike is fixed in domestic currency. Those are a different
contract with different risk, not a parameterization of this one.
"""
import logging
import math
from dataclasses import dataclass
from typing import Tuple

logger = logging.getLogger(__name__)

#: Payoff sides this engine prices. Anything else raises rather than defaulting.
_VALID_OPTION_TYPES: Tuple[str, ...] = ("CALL", "PUT")

STATUS_SUCCESS = "QUANTO_PRICING_SUCCESSFUL"


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return (1.0 / math.sqrt(2.0 * math.pi)) * math.exp(-0.5 * x * x)


def _require_finite(name: str, value: float) -> float:
    """
    Rejects NaN/Inf before it reaches the model.

    Comparisons against NaN are always False, so a plain ``if value <= 0`` guard
    lets NaN straight through and the caller receives a report whose every field
    is NaN and whose status still reads SUCCESSFUL.
    """
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    return numeric


def _require_positive(name: str, value: float) -> float:
    """Rejects non-positive values where the model takes a log of or divides by them."""
    numeric = _require_finite(name, value)
    if numeric <= 0.0:
        raise ValueError(f"{name} must be strictly positive, got {value!r}")
    return numeric


def _require_non_negative(name: str, value: float) -> float:
    """Rejects negative values for quantities that are magnitudes (a volatility)."""
    numeric = _require_finite(name, value)
    if numeric < 0.0:
        raise ValueError(f"{name} must be non-negative, got {value!r}")
    return numeric


def _normalize_option_type(option_type: str) -> str:
    """
    Maps caller input to 'CALL' or 'PUT', raising on anything else.

    Branching on ``if opt == "CALL": ... else: <put>`` means a caller passing
    'C', 'Call ' with a stray space, or a typo such as 'CAL' silently receives a
    *put* price -- and, if the raw string is echoed into the report, an audit
    trail that does not even reveal which side was priced.
    """
    if not isinstance(option_type, str):
        raise ValueError(f"option_type must be a string, got {type(option_type).__name__}")
    normalized = option_type.strip().upper()
    if normalized not in _VALID_OPTION_TYPES:
        raise ValueError(f"option_type must be one of {_VALID_OPTION_TYPES}, got {option_type!r}")
    return normalized


@dataclass
class InputData:
    """
    Quanto option contract terms and market data.

    All rates and volatilities are continuously compounded, annualized decimals
    (0.05 = 5%). See the module docstring for the two conventions that matter:
    ``correlation`` is measured against the FX rate quoted **domestic per
    foreign**, and ``strike_price`` is in the **foreign** asset's own units.
    """
    spot_price: float = 100.0            # S: Foreign asset spot, in foreign currency
    strike_price: float = 100.0          # K: Strike, in FOREIGN currency (same units as S)
    time_to_expiry_years: float = 1.0    # T: Time to expiry in years, > 0
    domestic_rate: float = 0.05          # r_d: Domestic risk-free rate (discounting only)
    foreign_rate: float = 0.02           # r_f: Foreign risk-free rate (asset drift)
    dividend_yield: float = 0.0          # q: Continuous dividend yield of the foreign asset
    asset_volatility: float = 0.20       # sigma_S: Foreign asset volatility, > 0
    fx_volatility: float = 0.15          # sigma_X: FX rate volatility, >= 0
    correlation: float = 0.30            # rho: corr(asset, FX) with FX = domestic per foreign
    fixed_fx_rate: float = 1.0           # F_X: Contractually FIXED conversion multiplier, > 0
    option_type: str = 'CALL'            # 'CALL' or 'PUT'; anything else raises


@dataclass
class QuantoOptionPricingReport:
    """
    Pricing and risk output. **Nothing here is rounded.**

    Version 1.0.0 rounded every field before returning it; ``quanto_gamma`` was
    rounded to 6 decimal places, which on an index-level underlying such as the
    Nikkei 225 (spot ~38,000, gamma ~5e-05) discards all but the first
    significant figure. Quantize at the presentation layer, where the notional
    is known.
    """
    spot_price: float
    strike_price: float
    option_type: str
    quanto_drift: float                    # mu_quanto = r_f - q - rho * sigma_S * sigma_X
    quanto_forward_foreign: float          # S * e^{mu_quanto * T}, in foreign currency units
    d1: float
    d2: float
    quanto_option_price_domestic: float
    quanto_delta: float                    # dV / dS
    quanto_gamma: float                    # d2V / dS2
    quanto_vega: float                     # TOTAL dV / dsigma_S (both channels below)
    quanto_vega_spot_component: float      # Black-Scholes vega at fixed drift
    quanto_vega_drift_component: float     # Contribution via dmu_quanto/dsigma_S = -rho * sigma_X
    fx_correlation_sensitivity: float      # dV / drho
    status: str
    audit_notes: str


class QuantoOptionsAndCrossCurrencyDerivativeStructuresEngine:
    """
    European quanto option pricer: Black-Scholes with the cross-currency drift
    adjustment ``mu = r_f - q - rho * sigma_S * sigma_X``, domestic-rate
    discounting, and quanto Delta / Gamma / Vega / correlation sensitivity.

    Stateless and deterministic: the same ``InputData`` always yields the same
    report, and instances share nothing.
    """

    def price_quanto_option(self, data: InputData) -> QuantoOptionPricingReport:
        """
        Prices a European quanto option and returns its risk report.

        Raises:
            ValueError: on non-finite input, non-positive ``spot_price`` /
                ``strike_price`` / ``time_to_expiry_years`` / ``asset_volatility``
                / ``fixed_fx_rate``, negative ``fx_volatility``, ``correlation``
                outside [-1, 1], or an unrecognized ``option_type``.
        """
        S = _require_positive("spot_price", data.spot_price)
        K = _require_positive("strike_price", data.strike_price)
        T = _require_positive("time_to_expiry_years", data.time_to_expiry_years)
        r_d = _require_finite("domestic_rate", data.domestic_rate)
        r_f = _require_finite("foreign_rate", data.foreign_rate)
        q = _require_finite("dividend_yield", data.dividend_yield)
        sigma_S = _require_positive("asset_volatility", data.asset_volatility)
        sigma_X = _require_non_negative("fx_volatility", data.fx_volatility)
        rho = _require_finite("correlation", data.correlation)
        # F_X is a contractual conversion multiplier, not a market variable. A
        # non-positive value flips the sign of every price and Greek.
        F_X = _require_positive("fixed_fx_rate", data.fixed_fx_rate)
        opt_type = _normalize_option_type(data.option_type)

        if not -1.0 <= rho <= 1.0:
            raise ValueError(f"correlation must lie in [-1, 1], got {rho!r}")

        # Domestic risk-neutral drift of the foreign asset (Haugh eq. 17).
        r_quanto = r_f - q - (rho * sigma_S * sigma_X)

        sqrt_T = math.sqrt(T)
        d1 = (math.log(S / K) + (r_quanto + 0.5 * sigma_S ** 2) * T) / (sigma_S * sqrt_T)
        d2 = d1 - sigma_S * sqrt_T

        disc_dom = math.exp(-r_d * T)
        drift_exp = math.exp(r_quanto * T)
        forward = S * drift_exp

        if opt_type == "CALL":
            price = F_X * disc_dom * (forward * norm_cdf(d1) - K * norm_cdf(d2))
            delta = F_X * disc_dom * drift_exp * norm_cdf(d1)
            # dV/dmu. The n(d1)/n(d2) terms cancel via forward * n(d1) = K * n(d2).
            dV_dmu = F_X * disc_dom * forward * T * norm_cdf(d1)
        else:  # PUT
            price = F_X * disc_dom * (K * norm_cdf(-d2) - forward * norm_cdf(-d1))
            delta = -F_X * disc_dom * drift_exp * norm_cdf(-d1)
            # Negative: raising the drift raises E[S_T] and so *lowers* the put.
            dV_dmu = -F_X * disc_dom * forward * T * norm_cdf(-d1)

        gamma = (F_X * disc_dom * drift_exp * norm_pdf(d1)) / (S * sigma_S * sqrt_T)

        # Vega: sigma_S enters d1/d2 AND the drift (via the rho*sigma_S*sigma_X
        # adjustment). Both channels belong in the total derivative.
        vega_spot = F_X * disc_dom * forward * norm_pdf(d1) * sqrt_T
        vega_drift = dV_dmu * (-rho * sigma_X)
        vega = vega_spot + vega_drift

        # dV/drho = dV/dmu * dmu/drho, with dmu/drho = -sigma_S * sigma_X.
        # Signed by dV_dmu, so it is negative for a call and POSITIVE for a put:
        # more correlation lowers the drift, which lifts the put.
        correlation_sens = dV_dmu * (-sigma_S * sigma_X)

        notes = (
            f"QUANTO OPTION PRICING [{opt_type} - {S}/{K} (T={T}y)]: "
            f"Price = {price:.6f} (domestic, at fixed FX {F_X}), "
            f"Quanto Drift = {r_quanto:.6f} (r_f = {r_f:.6f}, "
            f"adj = {-rho * sigma_S * sigma_X:+.6f}), "
            f"d1 = {d1:.6f}, d2 = {d2:.6f}, Delta = {delta:.6f}, "
            f"Vega = {vega:.6f} (spot {vega_spot:.6f} + drift {vega_drift:+.6f}), "
            f"dV/drho = {correlation_sens:+.6f}."
        )
        logger.info(notes)

        return QuantoOptionPricingReport(
            spot_price=S,
            strike_price=K,
            option_type=opt_type,
            quanto_drift=r_quanto,
            quanto_forward_foreign=forward,
            d1=d1,
            d2=d2,
            quanto_option_price_domestic=price,
            quanto_delta=delta,
            quanto_gamma=gamma,
            quanto_vega=vega,
            quanto_vega_spot_component=vega_spot,
            quanto_vega_drift_component=vega_drift,
            fx_correlation_sensitivity=correlation_sens,
            status=STATUS_SUCCESS,
            audit_notes=notes,
        )
