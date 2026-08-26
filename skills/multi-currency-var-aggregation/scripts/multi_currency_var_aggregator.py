"""
multi-currency-var-aggregation: Value at Risk (VaR) and Expected Shortfall (ES/CVaR)
for a portfolio whose positions are denominated in several currencies.

The point of the module is that a foreign-currency position carries *two* risk
factors, not one. Converting a position to the base currency and then applying the
asset's own volatility measures only half of it. The base-currency return of
position i held in currency c is the compounded asset and FX return

    R_base(i,t) = (1 + R_native(i,t)) * (1 + R_fx(c,t)) - 1

which follows directly from V_base = Q * P_native * E(c -> base): the value is a
product, so the return is a product of gross returns. Asset-FX correlation is
therefore captured automatically inside the synthesised series -- there is no
separate correlation input to get wrong.

FX quoting convention (get this wrong and every FX sign flips)
--------------------------------------------------------------
``fx_rate_to_base`` is **units of base currency per one unit of the native
currency** (base per native). With base USD and a EUR position, EUR/USD = 1.10 means
one EUR buys 1.10 USD, so ``fx_rate_to_base = 1.10``.

``fx_returns_to_base[c]`` must be the period return of **that same rate**, i.e.
E_t(c -> base) / E_{t-1}(c -> base) - 1. Supplying the inverse quote (native per
base, e.g. USD/JPY quoted as JPY per USD while the base is USD) negates every FX
return. Nothing in the data can detect this: the run succeeds and reports a number
in which FX diversifies the book when it actually amplifies it, or vice versa. The
module can only enforce the one case it can see -- that the base currency's own FX
return is zero -- so the caller must verify the direction of every other series.

Aggregation is done on position *values*, not weights
-----------------------------------------------------
Portfolio P&L per period is ``sum_i V_i * R_base(i,t)`` in base-currency units. This
is algebraically identical to ``V_total * sum_i w_i * R_base(i,t)`` for an ordinary
long-only book, but it does not divide by net portfolio value, so it stays well
defined for long/short books whose net value is near zero while gross exposure is
large -- exactly the shape of a currency-hedged or market-neutral cross-border
portfolio.

Estimator conventions
---------------------
- **Parametric (variance-covariance) VaR**: ``z_c * sigma_P * sqrt(T)``, with
  ``sigma_P`` the sample (n-1) standard deviation of the base-currency P&L series
  and ``z_c`` the standard normal quantile from ``statistics.NormalDist.inv_cdf``.
  Drift is excluded by default (the standard short-horizon convention, and the
  conservative one for a positive-drift book); set ``VarConfig.subtract_mean_drift``
  to use ``z_c * sigma_P - mu_P`` instead, and then the historical and parametric
  measures are on the same footing.
- **Historical simulation VaR**: with the n loss observations sorted worst-first and
  ``k = ceil(n * (1 - c))``, VaR is the k-th worst loss. At n = 100 and c = 0.95 that
  is the 5th worst loss. Other conventions exist (some texts use
  ``floor(n*(1-c)) + 1``); they differ by one observation only when ``n*(1-c)`` is an
  integer, which is precisely the common round-n case, so the convention is stated
  here rather than left implicit.
- **Expected Shortfall (CVaR)**: the mean of those same k worst losses. ES >= VaR
  holds by construction.

Regulatory context (verified sources -- read the limitation, not just the number)
--------------------------------------------------------------------------------
- BCBS "Minimum capital requirements for market risk" (Jan 2019, d457), MAR33.3:
  "In calculating ES, a bank must use a 97.5th percentile, one-tailed confidence
  level." MAR32.18 requires desk-level backtesting of the one-day VaR measure at
  both the 97.5th and 99th percentiles.
- BCBS MAR33.4(5): the base-horizon ES "must be calculated for changes in the risk
  factors ... over the time interval T **without scaling from a shorter horizon**".
  So ``holding_period_days > 1`` here -- which applies sqrt(T) -- produces an
  internal management measure, **not** an FRTB-compliant liquidity-adjusted ES.
- BCBS MAR33.12 Table 2 assigns FX its own liquidity horizons (10 days for the
  Committee's specified currency pairs, 20 days for other pairs, 40 days for FX
  volatility), i.e. FX is a distinct risk-factor category whose horizon need not
  match the asset's.
- 12 CFR 217.205(b)(1)-(2) (US market risk rule) does permit conversion: the
  VaR-based measure uses "a one-tail, 99.0 percent confidence level, and a holding
  period equivalent to a 10-business-day movement", and an institution "may convert
  VaR-based measures using holding periods other than 10 business days to the
  equivalent of a 10-business-day holding period", on "a historical observation
  period of at least one year".

Nothing in this module is a regulatory capital calculation. Jurisdictions differ on
whether horizon scaling is even permitted; treat the output as an internal risk
measure unless your own supervisor's rule has been checked.

Limitations (deliberate, documented)
------------------------------------
- **Linear / delta-normal only.** Position value is assumed proportional to price.
  Options, convertibles and other convex payoffs are mis-measured by both the
  parametric and the historical branch here, because the historical branch also
  revalues linearly rather than repricing the instrument.
- **Sampling frequency is the caller's.** VaR comes out at the frequency of the
  supplied returns. Daily returns give a 1-day VaR. There is no annualisation and
  no way to infer frequency from a list of floats.
- **sqrt(T) scaling assumes serially independent, identically distributed returns.**
  Autocorrelated or volatility-clustered returns break it in both directions.
- **No look-ahead protection.** Return series must end at the last completed period
  before the valuation date; the module cannot check this.
- **Component VaR is the parametric (Euler) decomposition** and inherits the
  elliptical-distribution assumption. It decomposes the parametric VaR, not the
  historical VaR and not the ES.
- **Cost is O(m * n)** in positions and periods, pure Python, no third-party
  dependency. Fine for the hundreds-of-positions, thousands-of-periods range; not a
  tick-level path.
"""
import logging
import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Dict, List, Mapping, Sequence

logger = logging.getLogger(__name__)

_STANDARD_NORMAL = NormalDist()

#: BCBS MAR33.3 -- ES confidence level under the FRTB internal models approach.
BASEL_ES_CONFIDENCE_LEVEL = 0.975

#: 12 CFR 217.205(b)(1) / BCBS MAR32.18 -- VaR confidence level.
BASEL_VAR_CONFIDENCE_LEVEL = 0.99

#: Trading days in a year. Used only to warn when the sample is shorter than the
#: one-year minimum observation period of 12 CFR 217.205(b)(2).
TRADING_DAYS_PER_YEAR = 252


@dataclass
class MultiCurrencyPosition:
    """
    One position, priced in its native currency.

    ``fx_rate_to_base`` is base currency units per one unit of ``native_currency``
    (1.0 when the position is already in the base currency). A negative ``quantity``
    denotes a short and is supported.
    """
    symbol: str
    native_currency: str
    quantity: float
    current_price_native: float
    fx_rate_to_base: float = 1.0        # 1.0 if native_currency == base_currency

    @property
    def value_base(self) -> float:
        return self.quantity * self.current_price_native * self.fx_rate_to_base

    def validate(self, base_currency: str) -> None:
        """Rejects inputs that would otherwise produce a silently wrong VaR."""
        if not self.symbol:
            raise ValueError("Position symbol must be a non-empty string.")
        if not self.native_currency:
            raise ValueError(f"Position '{self.symbol}' has an empty native_currency.")
        for name, value in (
            ("quantity", self.quantity),
            ("current_price_native", self.current_price_native),
            ("fx_rate_to_base", self.fx_rate_to_base),
        ):
            if not math.isfinite(value):
                raise ValueError(
                    f"Position '{self.symbol}' has non-finite {name}={value!r}.")
        if self.current_price_native <= 0.0:
            raise ValueError(
                f"Position '{self.symbol}' has non-positive price "
                f"{self.current_price_native}; short exposure is expressed by a "
                f"negative quantity, not a negative price.")
        if self.fx_rate_to_base <= 0.0:
            raise ValueError(
                f"Position '{self.symbol}' has non-positive fx_rate_to_base "
                f"{self.fx_rate_to_base}. The rate is base units per native unit "
                f"and must be > 0.")
        if self.native_currency == base_currency and not math.isclose(
                self.fx_rate_to_base, 1.0, rel_tol=1e-12):
            raise ValueError(
                f"Position '{self.symbol}' is denominated in the base currency "
                f"'{base_currency}' but has fx_rate_to_base="
                f"{self.fx_rate_to_base}; it must be exactly 1.0.")


@dataclass
class VarConfig:
    """
    Risk-measure configuration.

    ``holding_period_days`` > 1 applies sqrt(T) scaling to a measure estimated at the
    frequency of the supplied returns. That is permitted by 12 CFR 217.205(b)(1) but
    explicitly *not* by BCBS MAR33.4(5) for the FRTB base horizon -- see the module
    docstring.

    ``min_observations`` defaults to ``max(2, ceil(1 / (1 - confidence_level)))``,
    the smallest sample in which the historical tail bucket contains at least one
    observation (20 at 95%, 100 at 99%). Below it a "historical VaR" is just the
    single worst observation in a sample too short to locate a quantile.
    """
    confidence_level: float = 0.95       # 0.95 or 0.99
    holding_period_days: int = 1
    base_currency: str = "USD"
    subtract_mean_drift: bool = False
    min_observations: int = 0            # 0 -> derive from confidence_level

    def validate(self) -> None:
        if not 0.0 < self.confidence_level < 1.0:
            raise ValueError(
                f"confidence_level must be in the open interval (0, 1), got "
                f"{self.confidence_level}.")
        if self.confidence_level < 0.5:
            raise ValueError(
                f"confidence_level {self.confidence_level} is below 0.5; VaR is a "
                f"tail measure and this is almost always an inverted input "
                f"(0.05 supplied where 0.95 was meant).")
        if isinstance(self.holding_period_days, bool) or not isinstance(
                self.holding_period_days, int):
            raise ValueError(
                f"holding_period_days must be an int, got "
                f"{type(self.holding_period_days).__name__}.")
        if self.holding_period_days < 1:
            raise ValueError(
                f"holding_period_days must be >= 1, got {self.holding_period_days}.")
        if not self.base_currency:
            raise ValueError("base_currency must be a non-empty ISO 4217 code.")
        if self.min_observations < 0:
            raise ValueError(
                f"min_observations must be >= 0 (0 means derive), got "
                f"{self.min_observations}.")

    def required_observations(self) -> int:
        """
        Smallest sample in which the tail bucket holds at least one observation.

        Never returns fewer than 2 even when ``min_observations`` is set lower: the
        (n-1) sample variance is undefined at n = 1.
        """
        if self.min_observations > 0:
            return max(2, self.min_observations)
        return max(2, math.ceil(1.0 / (1.0 - self.confidence_level)))


@dataclass
class MultiCurrencyVarReport:
    base_currency: str
    total_portfolio_value_base: float           # NET value; may be ~0 for a hedged book
    confidence_level: float
    parametric_var_base: float
    historical_var_base: float
    expected_shortfall_cvar_base: float
    currency_risk_breakdown: Dict[str, float]   # net EXPOSURE per currency, not risk
    status: str                                 # 'VAR_CALCULATION_SUCCESS'
    audit_notes: str
    holding_period_days: int = 1
    holding_period_scaled: bool = False         # True when sqrt(T) was applied
    observations_used: int = 0
    tail_observations_used: int = 0             # k = ceil(n * (1 - c))
    gross_exposure_base: float = 0.0            # sum |V_i|
    portfolio_volatility_base: float = 0.0      # sigma of the 1-period P&L series
    portfolio_mean_pnl_base: float = 0.0        # mu of the 1-period P&L series
    #: Euler decomposition of ``parametric_var_base``; sums to it up to rounding.
    currency_component_var_base: Dict[str, float] = field(default_factory=dict)
    symbol_component_var_base: Dict[str, float] = field(default_factory=dict)


class MultiCurrencyVarAggregatorEngine:
    """
    Multi-currency Value at Risk (VaR) and Expected Shortfall (CVaR) aggregation
    engine, accounting for joint asset-FX return covariance and per-currency
    Euler (component VaR) risk decomposition.
    """

    @staticmethod
    def _get_z_score(confidence: float) -> float:
        """
        Standard normal quantile z such that P(Z <= z) = ``confidence``.

        Uses ``statistics.NormalDist.inv_cdf`` (Wichura AS241, stdlib since 3.8) for
        every level. A previous revision hard-coded 0.90/0.95/0.99 and fell back to
        ``math.erfinv`` -- which does not exist in the Python ``math`` module -- so
        any other confidence level raised ``AttributeError`` at call time. Those
        hard-coded values were themselves correct (1.2816 / 1.6449 / 2.3263) and are
        reproduced by ``inv_cdf`` to full double precision.
        """
        if not 0.0 < confidence < 1.0:
            raise ValueError(
                f"confidence must be in the open interval (0, 1), got {confidence}.")
        return _STANDARD_NORMAL.inv_cdf(confidence)

    @staticmethod
    def _tail_count(num_periods: int, confidence: float) -> int:
        """
        Number of worst-loss observations in the tail bucket, ``ceil(n * (1 - c))``,
        clamped to ``[1, n]``.

        VaR is the k-th worst loss and ES the mean of the k worst. At n = 100 and
        c = 0.95 this is 5, not 6: a previous revision used ``floor(n * (1 - c))`` as
        a 0-based index, which selected the 6th worst loss and averaged 6 losses,
        systematically understating VaR whenever ``n * (1 - c)`` is an integer.

        The epsilon is not cosmetic. ``1 - 0.95`` is 0.05000000000000004 in binary
        floating point, so a bare ``ceil(100 * (1 - 0.95))`` returns 6 and silently
        reintroduces the very off-by-one this convention exists to remove.
        """
        tail = num_periods * (1.0 - confidence)
        k = math.ceil(tail - 1e-9 * max(1.0, tail))
        return max(1, min(k, num_periods))

    @staticmethod
    def _validate_series(
        series: Sequence[float],
        expected_length: int,
        context: str,
    ) -> List[float]:
        """Rejects empty, wrong-length and non-finite return series."""
        values = list(series)
        if not values:
            raise ValueError(f"Empty return series for {context}.")
        if expected_length and len(values) != expected_length:
            raise ValueError(
                f"Mismatched return series length for {context}. Expected "
                f"{expected_length}, got {len(values)}. All series must be aligned "
                f"to the same observation dates before aggregation.")
        for idx, value in enumerate(values):
            if not math.isfinite(value):
                raise ValueError(
                    f"Non-finite return {value!r} at index {idx} for {context}. "
                    f"A NaN propagates silently to a NaN VaR that still reports "
                    f"success; clean or drop the observation instead.")
        return values

    def _resolve_fx_series(
        self,
        position: MultiCurrencyPosition,
        fx_returns_to_base: Mapping[str, Sequence[float]],
        expected_length: int,
        base_currency: str,
    ) -> List[float]:
        """
        Returns the FX return series for ``position``'s currency.

        Only the base currency may be absent (its rate is identically 1.0, so its
        return is identically 0). A missing series for any *other* currency is an
        error, not a zero vector: silently defaulting to zeros deletes exactly the
        FX risk this module exists to measure and understates VaR without warning.
        """
        currency = position.native_currency
        series = fx_returns_to_base.get(currency)

        if currency == base_currency:
            if series is None:
                return [0.0] * expected_length
            values = self._validate_series(
                series, expected_length, f"base currency '{currency}' FX returns")
            if any(v != 0.0 for v in values):
                raise ValueError(
                    f"FX return series for the base currency '{currency}' must be "
                    f"identically zero (the base-to-base rate is 1.0 by "
                    f"definition). A non-zero series here means the quoting "
                    f"direction or the base currency is wrong.")
            return values

        if series is None:
            raise ValueError(
                f"Missing FX return series for currency '{currency}' (position "
                f"'{position.symbol}'). Every non-base currency requires one; "
                f"assuming a constant FX rate would silently drop the currency "
                f"risk this calculation exists to measure.")
        return self._validate_series(
            series, expected_length, f"currency '{currency}' FX returns")

    @staticmethod
    def _sample_covariance(
        series_a: Sequence[float],
        mean_a: float,
        series_b: Sequence[float],
        mean_b: float,
    ) -> float:
        """Sample (n-1) covariance of two aligned series with known means."""
        n = len(series_a)
        return sum(
            (series_a[t] - mean_a) * (series_b[t] - mean_b) for t in range(n)
        ) / (n - 1)

    def calculate_multi_currency_var(
        self,
        config: VarConfig,
        positions: List[MultiCurrencyPosition],
        native_symbol_returns: Mapping[str, Sequence[float]],
        fx_returns_to_base: Mapping[str, Sequence[float]],
    ) -> MultiCurrencyVarReport:
        """
        Synthesises joint base-currency returns (compounding asset and FX returns),
        then computes parametric VaR, historical simulation VaR, Expected Shortfall
        and the per-currency Euler decomposition of the parametric VaR.

        Raises ``ValueError`` on any input that would produce a silently wrong
        number: an empty book, a missing or misaligned return series, a missing FX
        series for a non-base currency, non-finite data, or a sample too short to
        locate the requested quantile.
        """
        config.validate()
        if not positions:
            raise ValueError("Position list cannot be empty.")

        base_currency = config.base_currency

        # 1. Validate positions and synthesise the joint base-currency return series.
        #    R_base = (1 + R_native) * (1 + R_fx) - 1
        #    Series are indexed by POSITION, not by symbol: two lots of the same
        #    symbol are distinct exposures. Keying by symbol previously let a second
        #    lot overwrite the first, so the portfolio silently lost a position.
        num_periods = 0
        joint_returns: List[List[float]] = []
        values_base: List[float] = []

        for position in positions:
            position.validate(base_currency)

            native = native_symbol_returns.get(position.symbol)
            if native is None:
                raise ValueError(
                    f"Missing historical return series for symbol "
                    f"'{position.symbol}'.")
            native_values = self._validate_series(
                native, num_periods, f"symbol '{position.symbol}'")
            if num_periods == 0:
                num_periods = len(native_values)

            fx_values = self._resolve_fx_series(
                position, fx_returns_to_base, num_periods, base_currency)

            # Written as rn + rfx + rn*rfx rather than (1+rn)(1+rfx)-1: algebraically
            # identical, but it does not add 1.0 and subtract it again, so small
            # returns keep their significant digits.
            joint_returns.append([
                rn + rfx + rn * rfx
                for rn, rfx in zip(native_values, fx_values)
            ])
            values_base.append(position.value_base)

        required = config.required_observations()
        if num_periods < required:
            raise ValueError(
                f"Insufficient history: {num_periods} observations for a "
                f"{config.confidence_level:.1%} measure, which needs at least "
                f"{required} for the tail bucket to contain one observation. "
                f"Supply a longer aligned series or lower the confidence level.")
        if num_periods < TRADING_DAYS_PER_YEAR:
            logger.warning(
                "VaR estimated from %d observations. 12 CFR 217.205(b)(2) requires "
                "a historical observation period of at least one year (~%d daily "
                "observations) for a regulatory VaR-based measure.",
                num_periods, TRADING_DAYS_PER_YEAR)

        gross_exposure = sum(abs(v) for v in values_base)
        if gross_exposure <= 0.0:
            raise ValueError(
                "Gross exposure is zero; there is no position to measure risk on.")
        total_value_base = sum(values_base)

        # 2. Base-currency P&L series. Value-weighted, not weight-weighted: this
        #    stays well defined when net portfolio value is near zero (hedged or
        #    market-neutral cross-border books) instead of dividing by it.
        num_positions = len(positions)
        portfolio_pnl = [
            sum(values_base[i] * joint_returns[i][t] for i in range(num_positions))
            for t in range(num_periods)
        ]

        mean_pnl = sum(portfolio_pnl) / num_periods
        variance_pnl = sum(
            (x - mean_pnl) ** 2 for x in portfolio_pnl) / (num_periods - 1)
        sigma_pnl = math.sqrt(max(0.0, variance_pnl))
        if sigma_pnl <= 0.0:
            logger.warning(
                "Portfolio P&L series has zero variance across %d observations. "
                "Parametric VaR will be reported as zero; verify the input series "
                "is not stale, constant, or a repeated single observation.",
                num_periods)

        z_score = self._get_z_score(config.confidence_level)
        holding_factor = math.sqrt(config.holding_period_days)
        holding_scaled = config.holding_period_days > 1
        if holding_scaled:
            logger.warning(
                "Applying sqrt(%d) holding-period scaling. This is an internal "
                "management measure: BCBS MAR33.4(5) requires the FRTB base-horizon "
                "ES to be computed over the horizon without scaling from a shorter "
                "one, though 12 CFR 217.205(b)(1) does permit conversion.",
                config.holding_period_days)

        # 3. Parametric (variance-covariance) VaR.
        drift_term = mean_pnl if config.subtract_mean_drift else 0.0
        parametric_var_base = (z_score * sigma_pnl - drift_term) * holding_factor

        # 4. Historical simulation VaR and Expected Shortfall.
        #    Loss series in base currency; a positive number is a loss.
        losses_base = sorted(
            (-pnl * holding_factor for pnl in portfolio_pnl), reverse=True)
        tail_count = self._tail_count(num_periods, config.confidence_level)
        historical_var_base = losses_base[tail_count - 1]
        expected_shortfall_base = sum(losses_base[:tail_count]) / tail_count

        # 5. Euler decomposition of the parametric VaR.
        #    ComponentVaR_i = sqrt(T) * (z * V_i * (Sigma V)_i / sigma_P - V_i * mu_i)
        #    sums to parametric_var_base exactly: VaR is homogeneous of degree 1 in
        #    position values, so Euler's theorem applies with no residual.
        #
        #    (Sigma V)_i = sum_j cov(r_i, r_j) * V_j = cov(r_i, portfolio P&L), so
        #    the marginal term is one covariance against the P&L series already in
        #    hand. Forming the full m x m covariance matrix would give the same
        #    numbers at O(m^2 * n) instead of O(m * n).
        #
        #    This decomposes the PARAMETRIC VaR only -- not the historical VaR and
        #    not the ES.
        symbol_components: Dict[str, float] = {}
        currency_components: Dict[str, float] = {}
        for i, position in enumerate(positions):
            position_mean = sum(joint_returns[i]) / num_periods
            drift_i = (values_base[i] * position_mean
                       if config.subtract_mean_drift else 0.0)
            if sigma_pnl > 0.0:
                marginal = self._sample_covariance(
                    joint_returns[i], position_mean, portfolio_pnl, mean_pnl)
                risk_i = z_score * values_base[i] * marginal / sigma_pnl
            else:
                risk_i = 0.0
            component = holding_factor * (risk_i - drift_i)
            symbol_components[position.symbol] = (
                symbol_components.get(position.symbol, 0.0) + component)
            currency_components[position.native_currency] = (
                currency_components.get(position.native_currency, 0.0) + component)

        # 6. Net exposure by currency. This is EXPOSURE, not risk -- the risk
        #    decomposition is currency_component_var_base above.
        currency_exposure: Dict[str, float] = {}
        for i, position in enumerate(positions):
            currency_exposure[position.native_currency] = (
                currency_exposure.get(position.native_currency, 0.0) + values_base[i])
        currency_exposure = {k: round(v, 2) for k, v in currency_exposure.items()}

        notes = (
            f"MULTI-CURRENCY VAR CALCULATED [{base_currency}]: "
            f"Net Value = {total_value_base:,.2f} {base_currency}, "
            f"Gross Exposure = {gross_exposure:,.2f} {base_currency}, "
            f"{config.confidence_level * 100:.1f}% / {config.holding_period_days}d "
            f"Parametric VaR = {parametric_var_base:,.2f}, "
            f"Historical VaR = {historical_var_base:,.2f}, "
            f"ES = {expected_shortfall_base:,.2f} "
            f"(n={num_periods}, tail k={tail_count}"
            f"{', sqrt-T scaled' if holding_scaled else ''})."
        )
        logger.info(notes)

        return MultiCurrencyVarReport(
            base_currency=base_currency,
            total_portfolio_value_base=round(total_value_base, 2),
            confidence_level=config.confidence_level,
            parametric_var_base=round(parametric_var_base, 2),
            historical_var_base=round(historical_var_base, 2),
            expected_shortfall_cvar_base=round(expected_shortfall_base, 2),
            currency_risk_breakdown=currency_exposure,
            status="VAR_CALCULATION_SUCCESS",
            audit_notes=notes,
            holding_period_days=config.holding_period_days,
            holding_period_scaled=holding_scaled,
            observations_used=num_periods,
            tail_observations_used=tail_count,
            gross_exposure_base=round(gross_exposure, 2),
            portfolio_volatility_base=round(sigma_pnl, 6),
            portfolio_mean_pnl_base=round(mean_pnl, 6),
            currency_component_var_base={
                k: round(v, 2) for k, v in currency_components.items()},
            symbol_component_var_base={
                k: round(v, 2) for k, v in symbol_components.items()},
        )
