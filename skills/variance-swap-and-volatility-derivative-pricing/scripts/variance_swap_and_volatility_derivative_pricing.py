"""
variance-swap-and-volatility-derivative-pricing: static log-contract replication of
the fair variance strike, zero-mean realized variance, the volatility-swap convexity
correction, and seasoned mark-to-market for OTC variance swaps.

Primary source
--------------
Demeterfi, Derman, Kamal & Zou, "More Than You Ever Wanted To Know About Volatility
Swaps", Goldman Sachs Quantitative Strategies Research Notes, March 1999 ("DDKZ").
Equation numbers below refer to that paper.

Units convention (every number in this module obeys it)
-------------------------------------------------------
Variance is carried in **volatility points squared**: a 20% annualized volatility is
``K_vol = 20.0`` and ``K_var = 400.0``, not ``0.20`` / ``0.04``. Raw decimal variance
is multiplied by ``VARIANCE_SCALE = 10_000`` exactly once, at the boundary of each
public method. Vega notional is dollars per **volatility point**; variance notional is
dollars per **variance point**, and the two are linked by DDKZ Equation 43::

    sigma_R - K_vol ~= (1 / (2 * K_vol)) * (sigma_R^2 - K_vol^2)
    =>  N_var = N_vega / (2 * K_vol)

Fair variance strike (DDKZ Equation 27)
---------------------------------------
For a reference level ``S*`` separating the put and call wings::

    K_var = (2/T) * [ r*T - (S_0 * e^{rT} / S* - 1) - ln(S* / S_0) ]
            + (2/T) * e^{rT} * SUM_i (dK_i / K_i^2) * Q(K_i)

``S*`` is set to ``K_0``, the largest **available** strike at or below the forward
``F = S_0 e^{rT}`` -- the same anchor Cboe uses (``K_0`` = "first strike equal to or
otherwise immediately below F", Cboe Volatility Index Mathematics Methodology,
formula (1)). Anchoring on a *traded* strike rather than on ``F`` itself is what the
bracketed term corrects for: expanding it in ``x = F/S*`` gives
``-(x-1) + ln(x) ~= -(x-1)^2 / 2``, so the DDKZ term reduces to Cboe's
``-(1/T) * (F/K_0 - 1)^2`` to second order. It is exactly zero only when ``S* = F``.

``Q(K_i)`` is the out-of-the-money quote: a put for ``K_i < K_0``, a call for
``K_i > K_0``, and the **average of the put and the call** at ``K_i = K_0``. ``dK_i``
is half the distance between neighbouring *selected* strikes, and the one-sided
distance at each end of the grid.

Known limitations -- read before using this for settlement or margin
--------------------------------------------------------------------
- **Truncation is a one-way bias.** A finite strike range always *understates* the
  fair variance, and the shortfall grows with maturity: DDKZ Table 4 prices a
  flat-25%-vol underlying at ``(25.0)^2`` from a 50%-200% strike range but at only
  ``(23.0)^2`` from a 75%-125% range at one year (``(24.9)^2`` at three months).
  ``calculate_fair_strikes`` logs a warning when the supplied range is narrower than
  DDKZ's wide benchmark; it cannot repair the missing wings.
- **No jump adjustment.** Replication is exact only for a continuous price path.
  A single downward jump of size ``J`` leaves a residual whose leading term is cubic,
  ``(2/(3T)) * J^3`` (DDKZ Equations 40-42).
- **Realized variance here divides by the observed return count.** Term sheets
  divide by the *expected* observation count for the full accrual period and specify
  disruption-day handling; use this function for accrual-to-date monitoring, not for
  final settlement, unless the two divisors coincide.
- **The volatility-swap strike needs a vol-of-vol input.** There is no static
  replication of a volatility swap (DDKZ, "From Variance to Volatility Contracts").
  ``sqrt(K_var)`` is the *naive upper bound*; a real ``K_vol`` requires a view on the
  volatility of realized volatility. See ``calculate_fair_strikes``.
"""
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)
# Library convention: the embedding application owns handler configuration.
logger.addHandler(logging.NullHandler())

# Decimal variance (0.04) -> volatility points squared (400.0).
VARIANCE_SCALE = 10_000.0

# DDKZ Table 4 benchmark: a 50%-200% *of initial spot* strike range reproduces the
# true fair variance at both three months and one year; narrower ranges understate it.
_WIDE_RANGE_LOW = 0.50
_WIDE_RANGE_HIGH = 2.00

# Deep-OTM premiums computed in floating point (a vendor's theoretical price, or a
# model mark) can land a few ulps below zero through cancellation. Anything within
# this tolerance is clamped to zero; anything below it is bad data and is rejected.
_PRICE_NEGATIVE_TOLERANCE = 1e-9


class OptionType(Enum):
    CALL = "CALL"
    PUT = "PUT"


class SwapType(Enum):
    VARIANCE_SWAP = "VARIANCE_SWAP"
    VOLATILITY_SWAP = "VOLATILITY_SWAP"


class VariancePricingError(Exception):
    """Base exception for Variance Swap pricing errors."""
    pass


@dataclass
class OptionQuote:
    strike: float
    option_type: OptionType
    price: float
    implied_vol: float = 0.0


@dataclass
class VarianceSwapContract:
    contract_id: str
    symbol: str
    swap_type: SwapType
    strike_vol_pct: float          # Strike in volatility percentage points (e.g. 20.0 for 20%)
    vega_notional_usd: float       # Vega Notional e.g. $100,000 per vol point
    t_years: float                 # Total time to maturity in years
    spot_price: float              # Spot AT INCEPTION -- not the valuation-date spot
    risk_free_rate: float          # Annualized risk-free interest rate e.g. 0.05

    @property
    def strike_var(self) -> float:
        """Strike in variance terms (K_var = K_vol^2)."""
        return self.strike_vol_pct ** 2

    @property
    def variance_notional_usd(self) -> float:
        """Variance Notional N_var = N_vega / (2 * K_vol) (DDKZ Equation 43)."""
        if self.strike_vol_pct <= 0:
            raise VariancePricingError("Volatility strike must be positive.")
        return self.vega_notional_usd / (2.0 * self.strike_vol_pct)


@dataclass
class RealizedVarianceResult:
    annualized_realized_vol_pct: float
    annualized_realized_var: float
    num_observations: int
    daily_returns_variance: float


@dataclass
class FairStrikeResult:
    fair_variance_strike: float     # K_var (e.g. 400.0 for 20% vol)
    fair_volatility_strike: float   # K_vol (e.g. 20.0%)
    convexity_adjustment_pct: float # K_var - K_vol^2; zero only when vol_of_vol is zero
    forward_price: float
    num_options_used: int
    # Diagnostics for replication quality -- appended, so positional construction
    # of the five fields above keeps working.
    reference_strike: float = 0.0   # S* = K_0, the put/call boundary actually used
    min_strike: float = 0.0
    max_strike: float = 0.0


@dataclass
class VarianceSwapMTMResult:
    contract_id: str
    current_mtm_usd: float
    realized_vol_so_far_pct: float
    realized_var_so_far: float
    fair_remaining_var_strike: float
    unrealized_pnl_usd: float


def _require_finite(value: float, label: str) -> float:
    """Rejects NaN/Inf before it can propagate silently through the pricing chain."""
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise VariancePricingError(f"{label} must be a real number, got {value!r}.") from exc
    if not math.isfinite(v):
        raise VariancePricingError(f"{label} must be finite, got {v!r}.")
    return v


class VarianceSwapPricingEngine:
    """
    Institutional Variance Swap & Volatility Derivative Pricing Engine.

    Implements Demeterfi, Derman, Kamal & Zou (1999) static replication of variance
    swaps from out-of-the-money European option strips, zero-mean realized log-return
    variance, the volatility-swap convexity correction, and seasoned Mark-to-Market
    valuation. See the module docstring for the units convention and the limitations
    that bound every number this class returns.
    """

    def __init__(self):
        logger.info("Initialized Quantitative Variance Swap Pricing Engine")

    @staticmethod
    def calculate_realized_variance(
        price_history: List[float], annualization_factor: int = 252
    ) -> RealizedVarianceResult:
        """
        Annualized realized variance from a price history, **zero-mean convention**::

            r_i        = ln(S_i / S_{i-1})
            sigma^2    = (A / N) * SUM_i r_i^2 * 10_000

        The sample mean is deliberately *not* subtracted. DDKZ (page 2) note the
        zero-mean method "is theoretically preferable, because it corresponds most
        closely to the contract that can be replicated by options portfolios"; do not
        "fix" this into a sample variance.

        ``N`` is the number of returns actually observed, so the result is the
        variance accrued *to date*, annualized. A settlement calculation divides by
        the expected observation count fixed in the term sheet instead -- see the
        module docstring.

        Args:
            price_history: Chronologically ordered positive prices, at least two.
            annualization_factor: Observations per year (252 US business days; DDKZ
                use 260 in their example -- match the term sheet, not the default).

        Raises:
            VariancePricingError: fewer than two prices, a non-positive or
                non-finite price, or a non-positive annualization factor.
        """
        if len(price_history) < 2:
            raise VariancePricingError("At least 2 price observations required to calculate realized variance.")
        if annualization_factor <= 0:
            raise VariancePricingError(
                f"Annualization factor must be positive, got {annualization_factor!r}."
            )

        log_returns = []
        for i in range(1, len(price_history)):
            prev = _require_finite(price_history[i - 1], f"price_history[{i - 1}]")
            curr = _require_finite(price_history[i], f"price_history[{i}]")
            if prev <= 0 or curr <= 0:
                raise VariancePricingError("Price observations must be positive.")
            log_returns.append(math.log(curr / prev))

        n = len(log_returns)
        sum_sq_returns = sum(r ** 2 for r in log_returns)

        daily_variance = sum_sq_returns / n
        annualized_var = (annualization_factor / n) * sum_sq_returns * VARIANCE_SCALE
        annualized_vol = math.sqrt(annualized_var)

        return RealizedVarianceResult(
            annualized_realized_vol_pct=annualized_vol,
            annualized_realized_var=annualized_var,
            num_observations=n,
            daily_returns_variance=daily_variance,
        )

    @staticmethod
    def _select_otm_quotes(
        option_strip: List[OptionQuote], forward: float
    ) -> Tuple[float, List[Tuple[float, float]], int]:
        """
        Reduces a raw option chain to one OTM price per strike, Cboe-style.

        Returns ``(s_star, [(strike, price), ...], quotes_consumed)`` with the strike
        list sorted ascending. ``s_star`` is ``K_0``, the largest available strike at
        or below ``forward``. Puts are used strictly below ``K_0``, calls strictly
        above, and at ``K_0`` the average of the put and the call when both are
        quoted (Cboe Volatility Index Mathematics Methodology, "Q(K_0) is the average
        of the K_0 put option price and K_0 call option price").

        Collapsing to one price per strike **before** the ``dK`` grid is built is
        load-bearing: computing ``dK`` over a raw two-sided chain halves every
        interior spacing, because each strike appears twice.
        """
        by_strike: Dict[float, Dict[OptionType, float]] = {}
        for idx, quote in enumerate(option_strip):
            strike = _require_finite(quote.strike, f"option_strip[{idx}].strike")
            price = _require_finite(quote.price, f"option_strip[{idx}].price")
            if strike <= 0:
                raise VariancePricingError(f"Option strike must be positive, got {strike!r}.")
            if price < -_PRICE_NEGATIVE_TOLERANCE:
                raise VariancePricingError(f"Option price must be non-negative, got {price!r}.")
            price = max(0.0, price)     # absorb float noise from model-derived marks
            if not isinstance(quote.option_type, OptionType):
                raise VariancePricingError(f"Unknown option type {quote.option_type!r}.")
            slot = by_strike.setdefault(strike, {})
            if quote.option_type in slot:
                raise VariancePricingError(
                    f"Duplicate {quote.option_type.value} quote at strike {strike}; "
                    "the strip must carry at most one price per (strike, type)."
                )
            slot[quote.option_type] = price

        strikes_at_or_below = [k for k in by_strike if k <= forward]
        if not strikes_at_or_below:
            raise VariancePricingError(
                f"No strike at or below the forward {forward:.4f}; the put wing is "
                "missing entirely and the log contract cannot be anchored."
            )
        s_star = max(strikes_at_or_below)

        selected: List[Tuple[float, float]] = []
        consumed = 0
        for strike in sorted(by_strike):
            slot = by_strike[strike]
            if strike < s_star:
                if OptionType.PUT in slot:                     # ITM calls below K_0 are dropped
                    selected.append((strike, slot[OptionType.PUT]))
                    consumed += 1
            elif strike > s_star:
                if OptionType.CALL in slot:                    # ITM puts above K_0 are dropped
                    selected.append((strike, slot[OptionType.CALL]))
                    consumed += 1
            else:
                prices = [slot[t] for t in (OptionType.PUT, OptionType.CALL) if t in slot]
                selected.append((strike, sum(prices) / len(prices)))
                consumed += len(prices)

        below = sum(1 for k, _ in selected if k < s_star)
        above = sum(1 for k, _ in selected if k > s_star)
        if below == 0 or above == 0:
            raise VariancePricingError(
                f"One-sided option strip: {below} OTM put strike(s) below and {above} "
                f"OTM call strike(s) above the reference strike {s_star} (forward "
                f"{forward:.4f}). Static replication needs both wings; a one-sided "
                "strip understates K_var without any error signal. If the forward "
                "looks wrong, check that the rate is a decimal (0.05), not a percent."
            )
        return s_star, selected, consumed

    @staticmethod
    def calculate_fair_strikes(
        spot: float,
        r: float,
        t_years: float,
        option_strip: List[OptionQuote],
        vol_of_vol_points: float = 0.0,
    ) -> FairStrikeResult:
        """
        Fair variance strike ``K_var`` by static log-contract replication, DDKZ
        Equation 27, anchored on ``S* = K_0`` (see the module docstring for the
        formula and for why the bracketed term is not zero in general).

        The volatility strike applies DDKZ's Appendix D assumption that realized
        volatility is normally distributed with mean ``K_vol`` and standard deviation
        ``vol_of_vol_points`` (Equation D4). Under that assumption
        ``K_var = E[sigma_R^2] = K_vol^2 + Var(sigma_R)`` exactly, so::

            K_vol = sqrt(K_var - vol_of_vol_points^2)

        With the default ``vol_of_vol_points = 0`` this collapses to ``sqrt(K_var)``,
        which DDKZ call the *naive* estimate (Equation 44) and show to be an upper
        bound -- ``K_vol < sqrt(K_var)`` strictly, or the variance swap would
        dominate the volatility swap at every realized volatility. **A volatility
        swap must not be struck at the default.** Supply a vol-of-vol estimate, in
        volatility points, from a model or from the VIX-of-VIX style market.

        Args:
            spot: Current spot price of the underlying, > 0.
            r: Continuously compounded risk-free rate to maturity.
            t_years: Time to maturity in years, > 0.
            option_strip: European option quotes. May be a full two-sided chain;
                ITM quotes are discarded and each strike contributes once.
            vol_of_vol_points: Standard deviation of realized volatility in
                volatility points (e.g. ``5.0`` for 5 vol points). Must satisfy
                ``vol_of_vol_points^2 < K_var``.

        Returns:
            FairStrikeResult with ``convexity_adjustment_pct = K_var - K_vol^2``,
            which equals ``vol_of_vol_points^2`` under the Appendix D assumption.

        Raises:
            VariancePricingError: on non-positive spot/maturity, malformed quotes,
                a one-sided strip, a negative replicated variance (arbitrageable or
                stale quotes), or a vol-of-vol exceeding the variance strike.
        """
        spot = _require_finite(spot, "spot")
        r = _require_finite(r, "r")
        t_years = _require_finite(t_years, "t_years")
        vol_of_vol_points = _require_finite(vol_of_vol_points, "vol_of_vol_points")
        if spot <= 0 or t_years <= 0:
            raise VariancePricingError("Spot price and time-to-maturity must be positive.")
        if vol_of_vol_points < 0:
            raise VariancePricingError("vol_of_vol_points is a standard deviation and cannot be negative.")
        if not option_strip or len(option_strip) < 2:
            raise VariancePricingError("Option strip requires at least 2 quotes across strikes.")

        forward = spot * math.exp(r * t_years)
        s_star, selected, num_used = VarianceSwapPricingEngine._select_otm_quotes(option_strip, forward)

        strikes = [k for k, _ in selected]
        n = len(strikes)
        integral_sum = 0.0
        for i, (k_i, q_i) in enumerate(selected):
            if i == 0:
                delta_k = strikes[1] - strikes[0]
            elif i == n - 1:
                delta_k = strikes[n - 1] - strikes[n - 2]
            else:
                delta_k = (strikes[i + 1] - strikes[i - 1]) / 2.0
            integral_sum += (delta_k / (k_i ** 2)) * q_i

        # DDKZ Equation 27. The bracket vanishes only when S* == forward.
        log_contract_term = (2.0 / t_years) * (
            r * t_years - (forward / s_star - 1.0) - math.log(s_star / spot)
        )
        option_term = (2.0 / t_years) * math.exp(r * t_years) * integral_sum
        fair_var = (log_contract_term + option_term) * VARIANCE_SCALE

        if fair_var <= 0.0:
            raise VariancePricingError(
                f"Replicated variance is non-positive ({fair_var:.6f} vol points^2). "
                "This indicates arbitrageable, stale, or mis-signed option quotes "
                "rather than a zero-volatility market."
            )

        if strikes[0] > _WIDE_RANGE_LOW * spot or strikes[-1] < _WIDE_RANGE_HIGH * spot:
            logger.warning(
                "Strike range [%.2f, %.2f] is narrower than the %.0f%%-%.0f%% of spot "
                "(%.2f) benchmark; K_var=%.2f is a downward-biased estimate and the bias "
                "grows with maturity (DDKZ Table 4).",
                strikes[0], strikes[-1], _WIDE_RANGE_LOW * 100, _WIDE_RANGE_HIGH * 100,
                spot, fair_var,
            )

        convexity_adj = vol_of_vol_points ** 2
        if convexity_adj >= fair_var:
            raise VariancePricingError(
                f"vol_of_vol_points^2 ({convexity_adj:.4f}) must be strictly less than "
                f"K_var ({fair_var:.4f}); the implied volatility strike would be "
                "non-real."
            )
        fair_vol = math.sqrt(fair_var - convexity_adj)

        return FairStrikeResult(
            fair_variance_strike=fair_var,
            fair_volatility_strike=fair_vol,
            convexity_adjustment_pct=convexity_adj,
            forward_price=forward,
            num_options_used=num_used,
            reference_strike=s_star,
            min_strike=strikes[0],
            max_strike=strikes[-1],
        )

    def price_variance_swap_mtm(
        self,
        contract: VarianceSwapContract,
        elapsed_t_years: float,
        price_history: List[float],
        remaining_option_strip: List[OptionQuote],
        current_spot: Optional[float] = None,
        current_risk_free_rate: Optional[float] = None,
    ) -> VarianceSwapMTMResult:
        """
        Mark-to-Market present value of a seasoned variance swap::

            V_exp = (t/T) * realized_var_to_date + ((T-t)/T) * K_var_remaining
            MTM   = e^{-r(T-t)} * N_var * (V_exp - K_var_strike)

        Variance is additive in time, which is what makes this linear blend exact:
        the accrued leg is already realized and the forward leg is priced off today's
        strip. The result is both the mark and the unrealized P&L, because a swap
        struck at fair value has zero value at inception.

        Args:
            contract: Contract terms. ``contract.spot_price`` and
                ``contract.risk_free_rate`` are **inception** values.
            elapsed_t_years: Accrued time, in ``[0, contract.t_years]``.
            price_history: Prices observed over the accrued period.
            remaining_option_strip: Option quotes expiring at the swap's maturity,
                priced as of the valuation date.
            current_spot: Valuation-date spot. Defaults to the inception spot with a
                warning -- a stale spot moves the forward, and with it the put/call
                boundary, so pass the live spot for any mark that feeds margin.
            current_risk_free_rate: Valuation-date rate to maturity. Defaults to the
                inception rate with a warning.

        Raises:
            VariancePricingError: elapsed time outside ``[0, T]``; a volatility-swap
                contract (this payoff is variance-linear -- see
                ``calculate_fair_strikes`` for why a volatility strike needs a
                vol-of-vol model); insufficient price history for an accrued period;
                or a missing strip while material time remains.
        """
        elapsed_t_years = _require_finite(elapsed_t_years, "elapsed_t_years")
        if _require_finite(contract.t_years, "contract.t_years") <= 0:
            raise VariancePricingError(
                f"Contract {contract.contract_id!r} has t_years={contract.t_years!r}; "
                "a swap with no accrual period cannot be marked."
            )
        if elapsed_t_years < 0 or elapsed_t_years > contract.t_years:
            raise VariancePricingError("Elapsed time must be between 0 and contract maturity T.")
        if contract.swap_type is not SwapType.VARIANCE_SWAP:
            raise VariancePricingError(
                f"price_variance_swap_mtm values a variance-linear payoff, but "
                f"contract {contract.contract_id!r} is a {contract.swap_type.value}. "
                "Marking a volatility swap on this formula overstates it by the "
                "convexity bias; price the volatility leg with a vol-of-vol model."
            )

        t_total = contract.t_years
        t_remaining = t_total - elapsed_t_years

        if current_spot is None:
            logger.warning(
                "MTM [%s]: no current_spot supplied; falling back to the inception spot "
                "%.4f. The forward and the put/call boundary will be stale.",
                contract.contract_id, contract.spot_price,
            )
            spot = contract.spot_price
        else:
            spot = _require_finite(current_spot, "current_spot")
        if current_risk_free_rate is None:
            logger.warning(
                "MTM [%s]: no current_risk_free_rate supplied; falling back to the "
                "inception rate %.4f.", contract.contract_id, contract.risk_free_rate,
            )
            rate = contract.risk_free_rate
        else:
            rate = _require_finite(current_risk_free_rate, "current_risk_free_rate")

        discount_factor = math.exp(-rate * t_remaining)

        # 1. Realized variance accrued so far.
        if elapsed_t_years > 0:
            if len(price_history) < 2:
                raise VariancePricingError(
                    f"{elapsed_t_years:.4f}y has accrued on contract "
                    f"{contract.contract_id!r} but only {len(price_history)} price(s) "
                    "were supplied; the accrued variance cannot be substituted with "
                    "the contract strike."
                )
            realized_res = self.calculate_realized_variance(price_history)
            realized_var_so_far = realized_res.annualized_realized_var
            realized_vol_so_far = realized_res.annualized_realized_vol_pct
        else:
            # Zero weight on the accrued leg; report zero rather than an invented level.
            realized_var_so_far = 0.0
            realized_vol_so_far = 0.0

        # 2. Fair variance strike over the remaining accrual period.
        if t_remaining <= 1e-4:
            # Fully accrued: the forward leg carries no weight.
            fair_rem_var = realized_var_so_far
        elif not remaining_option_strip:
            raise VariancePricingError(
                f"{t_remaining:.4f}y remains on contract {contract.contract_id!r} but "
                "no option strip was supplied; the forward variance leg cannot be "
                "marked."
            )
        else:
            fair_rem_var = self.calculate_fair_strikes(
                spot, rate, t_remaining, remaining_option_strip
            ).fair_variance_strike

        # 3. Expected total variance at maturity (variance is additive in time).
        weight_elapsed = elapsed_t_years / t_total
        weight_remaining = t_remaining / t_total
        expected_total_var = (weight_elapsed * realized_var_so_far) + (weight_remaining * fair_rem_var)

        # 4. Present value.
        var_diff = expected_total_var - contract.strike_var
        mtm_usd = discount_factor * contract.variance_notional_usd * var_diff

        logger.info(
            "Variance Swap MTM [%s]: RealizedVol=%.2f%%, FairRemVar=%.2f, "
            "ExpTotalVar=%.2f, MTM=$%s",
            contract.contract_id, realized_vol_so_far, fair_rem_var,
            expected_total_var, f"{mtm_usd:,.2f}",
        )

        return VarianceSwapMTMResult(
            contract_id=contract.contract_id,
            current_mtm_usd=mtm_usd,
            realized_vol_so_far_pct=realized_vol_so_far,
            realized_var_so_far=realized_var_so_far,
            fair_remaining_var_strike=fair_rem_var,
            unrealized_pnl_usd=mtm_usd,
        )
