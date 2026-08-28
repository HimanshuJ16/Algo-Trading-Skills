"""
strategy-performance-attribution-vs-market-beta: decomposes a realized strategy
return series into a risk-free component, priced factor exposures (market beta and,
optionally, the Fama-French size and value tilts), and the residual intercept --
Jensen's alpha -- together with the inference needed to decide whether that alpha is
distinguishable from zero.

Definitions as implemented (each verified against the primary source cited in
`references/standards.md`):

- **Jensen's alpha** (Jensen, "The Performance of Mutual Funds in the Period
  1945-1964", *Journal of Finance* 23(2), 1968, eq. 8): the intercept of

      R_j,t - R_F,t = alpha_j + beta_j * (R_M,t - R_F,t) + u_j,t

  Jensen: the intercept "represents the average incremental rate of return on the
  portfolio per unit time which is due solely to the manager's ability to forecast
  future security prices."

- **Fama-French three-factor alpha** (Fama & French, "Common Risk Factors in the
  Returns on Stocks and Bonds", *Journal of Financial Economics* 33, 1993): the same
  intercept with SMB and HML added as regressors. SMB and HML are zero-investment
  long-short spreads, so they enter **raw** -- the risk-free rate is subtracted from
  the strategy and market legs only, never from SMB/HML.

- **Inference on alpha.** Jensen states that "the sampling distribution of the
  estimate, alpha-hat_j, is a student t distribution with n-2 degrees of freedom".
  This engine therefore compares |t| against the exact two-sided Student-t critical
  value at `significance_level` with n-k residual degrees of freedom, not against the
  fixed normal value 1.96. At n = 252 daily observations the exact threshold is 1.9695,
  so the distinction rarely changes a verdict; at n = 60 monthly observations it is
  2.0017, and 1.96 overstates significance.

- **Robust standard errors.** Jensen's footnote 12 assumes u_j,t is serially
  independent. Realized strategy residuals frequently are not (stale marks, overlapping
  holding periods, momentum), which biases OLS standard errors downward and inflates
  the alpha t-statistic. `standard_errors="hac"` selects Newey-West standard errors
  with a Bartlett kernel; the default lag is the Newey & West (1994) rule of thumb
  floor(4 * (n/100)^(2/9)).

- **Return decomposition.** OLS with an intercept forces the residuals to sum to zero,
  so the sample means satisfy exactly

      mean(R_strat) = rf_period + alpha + sum_i beta_i * mean(x_i)

  Annualizing every term by `periods_per_year` gives the additive attribution reported
  in `factor_breakdown`. `unexplained_residual_pct` is the closure error of that
  identity and must be zero up to floating-point noise; it exists so a caller can
  assert the decomposition rather than trust it.

Input conventions -- getting these wrong is the dominant source of wrong answers:

- **Decimal fractions, not percent.** A 0.53% day is `0.0053`. Ken French's data
  library distributes Mkt-RF, SMB, HML and RF in **percent** (a daily file row reads
  `20260630, 0.73, 0.10, -0.62, 0.01`), so those columns must be divided by 100 before
  they are passed here. Mixing the units scales every beta by 100.
- **`market_returns` is a total return by default**, from which the risk-free rate is
  subtracted internally. French's `Mkt-RF` column is **already** an excess return; pass
  it with `market_returns_are_excess=True` or the risk-free rate is deducted twice.
- **Simple periodic returns**, aligned on a unique, sorted index.

Limitations (deliberate, documented):

- **Simple returns, not log returns.** Jensen notes the CAPM linearity holds across
  time intervals "as long as the returns are measured as continuously compounded rates
  of return". This engine regresses simple periodic returns, the near-universal
  practice, and annualizes alpha arithmetically as alpha * periods_per_year. Both are
  approximations that are accurate for small per-period returns and degrade for large
  ones.
- **In-sample and backward-looking.** Alpha, betas and R-squared describe the supplied
  window. They are not forecasts, and a full-sample regression on data that was also
  used to select the strategy carries a selection bias this engine cannot detect.
- **No multiple-testing correction.** A 5% test applied to twenty strategies produces
  a "significant" alpha by chance; see `factor-research-multiple-testing-correction`.
- **Static betas.** A single regression assumes constant exposure over the window. A
  strategy that times its beta will show a misleading average.
- **R-squared measures variance explained, not alpha.** A high R-squared is fully
  compatible with a large and significant alpha; the two are separate questions.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

logger = logging.getLogger(__name__)

#: Trading periods per year assumed for daily return series.
DEFAULT_PERIODS_PER_YEAR = 252

#: Two-sided significance level for the alpha and factor-loading t-tests.
DEFAULT_SIGNIFICANCE_LEVEL = 0.05

#: Below one year of observations at the configured frequency the report is flagged
#: rather than silently presenting annualized statistics from a short sample. This is
#: a warning, never a refusal.
MIN_RECOMMENDED_OBSERVATION_YEARS = 1

#: Residual sum of squares at or below this is treated as numerically zero, which makes
#: every standard error zero and every t-statistic undefined rather than infinite.
_RSS_EPSILON = 1e-24

#: Total annual return (in percent) at or below this is treated as non-positive, making
#: "alpha as a share of total return" undefined rather than enormous or sign-inverted.
_TOTAL_RETURN_EPSILON_PCT = 1e-9

_OLS = "ols"
_HAC = "hac"


@dataclass
class StrategyPerformanceAttributionVsMarketBetaConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True


class StrategyPerformanceAttributionVsMarketBeta:
    """Legacy class retained for 100% backward compatibility."""

    def __init__(self, config: StrategyPerformanceAttributionVsMarketBetaConfig) -> None:
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled


@dataclass
class FactorAttributionBreakdown:
    """One regressor's estimated loading and its share of the annualized return.

    `factor_annual_return_pct` is the annualized mean of the series **as it entered the
    regression** -- excess of the risk-free rate for the market leg, raw for SMB/HML --
    so that `return_contribution_pct = beta_exposure * factor_annual_return_pct` is a
    term of the exact additive decomposition described in the module docstring.
    """
    factor_name: str
    beta_exposure: float                       # regression loading on this factor
    factor_annual_return_pct: float            # annualized mean of the regressor, in %
    return_contribution_pct: float             # beta * factor_annual_return_pct, in %
    t_statistic: Optional[float]               # None when the residual variance is zero
    is_statistically_significant: bool         # |t| >= two-sided Student-t critical value
    standard_error: Optional[float] = None     # SE of beta_exposure, per period
    p_value: Optional[float] = None            # two-sided, Student-t with df_residual


@dataclass
class PerformanceAttributionReport:
    """Attribution of one strategy's realized return over the aligned sample window.

    All `_pct` fields are percentages (5.0 means 5%), annualized by
    `periods_per_year`. Every figure describes the aligned observations only; see
    `observations` and `dropped_observations`.
    """
    strategy_id: str
    total_realized_annual_return_pct: float
    risk_free_rate_pct: float                  # the annual input rate, echoed back
    annualized_jensens_alpha_pct: float
    alpha_t_statistic: Optional[float]         # None when the residual variance is zero
    is_true_alpha_significant: bool
    r_squared: float
    market_beta: float
    factor_breakdown: List[FactorAttributionBreakdown]
    # Alpha as a share of the total annual return. None -- never 0.0 -- when the total
    # return is zero or negative, where the ratio is undefined or sign-inverted.
    alpha_percentage_of_total_return: Optional[float]
    audit_notes: str
    observations: int = 0                      # rows used in the regression
    dropped_observations: int = 0              # rows dropped by alignment / missing data
    degrees_of_freedom: int = 0                # n - k, residual degrees of freedom
    adjusted_r_squared: Optional[float] = None
    alpha_p_value: Optional[float] = None      # two-sided, Student-t with df_residual
    alpha_standard_error: Optional[float] = None
    significance_level: float = DEFAULT_SIGNIFICANCE_LEVEL
    t_critical_value: Optional[float] = None   # two-sided Student-t threshold actually used
    standard_error_type: str = _OLS            # 'ols' or 'hac'
    hac_lags: Optional[int] = None             # Newey-West Bartlett lags, when HAC
    market_factor_is_excess: bool = False      # was market_returns already excess of rf?
    # Annualized contribution of the risk-free rate; the first term of the decomposition.
    risk_free_contribution_pct: float = 0.0
    # Closure error of rf + alpha + sum(contributions) against the realized total.
    # Zero up to floating-point noise; a non-zero value indicates a bug, not a finding.
    unexplained_residual_pct: float = 0.0
    # Sample is shorter than one year at the configured frequency.
    insufficient_history_warning: bool = False
    # Human-readable reasons any Optional metric above is None.
    undefined_metrics: Tuple[str, ...] = ()


class StrategyPerformanceAttributionEngine:
    """Runs CAPM / Fama-French time-series regressions and reports the attribution.

    Args:
        periods_per_year: Periods per year used to annualize alpha and factor means
            (252 daily, 52 weekly, 12 monthly). Must be a positive integer.
        significance_level: Two-sided level for the alpha and factor t-tests.
    """

    def __init__(
        self,
        periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
        significance_level: float = DEFAULT_SIGNIFICANCE_LEVEL,
    ) -> None:
        if isinstance(periods_per_year, bool) or not isinstance(
                periods_per_year, (int, np.integer)) or periods_per_year <= 0:
            raise ValueError(
                f"periods_per_year must be a positive integer, got {periods_per_year!r}")
        if not 0.0 < significance_level < 1.0:
            raise ValueError(
                f"significance_level must lie in (0, 1), got {significance_level!r}")
        self.periods_per_year = int(periods_per_year)
        self.significance_level = float(significance_level)

    # ------------------------------------------------------------------ regression

    @staticmethod
    def _newey_west_meat(design: np.ndarray, residuals: np.ndarray, lags: int) -> np.ndarray:
        """Newey-West Bartlett-kernel estimate of sum_t sum_s E[u_t u_s x_t x_s'].

        S = G_0 + sum_{l=1..L} (1 - l/(L+1)) (G_l + G_l'),
        G_l = sum_t u_t u_{t-l} x_t x_{t-l}'

        Requires `design` and `residuals` in chronological order; the caller enforces a
        monotonically increasing index before selecting HAC.
        """
        scaled = design * residuals[:, None]
        meat = scaled.T @ scaled
        for lag in range(1, lags + 1):
            weight = 1.0 - lag / (lags + 1.0)
            gamma = scaled[lag:].T @ scaled[:-lag]
            meat = meat + weight * (gamma + gamma.T)
        return meat

    @staticmethod
    def _default_hac_lags(n_observations: int) -> int:
        """Newey & West (1994) rule of thumb: floor(4 * (n/100)^(2/9))."""
        lags = int(math.floor(4.0 * (n_observations / 100.0) ** (2.0 / 9.0)))
        return max(0, min(lags, n_observations - 1))

    def _run_regression(
        self,
        y: np.ndarray,
        design: np.ndarray,
        standard_errors: str,
        hac_lags: Optional[int],
    ) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray], float,
               Optional[float], int, Optional[int]]:
        """Fit y = design @ beta + u and return inference for every coefficient.

        Returns (beta, standard_errors, t_statistics, r_squared, adjusted_r_squared,
        df_residual, hac_lags_used). `t_statistics` is None when the residual sum of
        squares is numerically zero, which makes every standard error zero and every
        t-statistic undefined rather than infinite.
        """
        n_obs, n_cols = design.shape
        beta, _, rank, _ = np.linalg.lstsq(design, y, rcond=None)
        if rank < n_cols:
            raise ValueError(
                f"regressor matrix is rank deficient (rank {rank} < {n_cols} columns): "
                "the factors supplied are collinear, so the loadings are not "
                "identified. Drop or orthogonalize a factor.")

        residuals = y - design @ beta
        ss_res = float(residuals @ residuals)
        centred = y - float(np.mean(y))
        ss_tot = float(centred @ centred)

        df_residual = n_obs - n_cols
        adjusted_r_squared: Optional[float] = None
        if ss_tot > _RSS_EPSILON:
            r_squared = 1.0 - ss_res / ss_tot
            adjusted_r_squared = 1.0 - (1.0 - r_squared) * (n_obs - 1) / float(df_residual)
        else:
            # A constant excess-return series has no variance for the factors to
            # explain, so R-squared is 0/0. Guarding on ss_tot > 0.0 alone is not
            # enough: floating-point summation leaves a residue around 1e-38, and
            # dividing by it produced values such as -119.55.
            r_squared = 0.0

        if ss_res <= _RSS_EPSILON:
            # Perfect in-sample fit: every standard error is zero, so no t-statistic is
            # defined. Reporting 0.0 here would read as "not significant", the exact
            # inversion of the truth.
            return (beta, np.zeros(n_cols), None, r_squared,
                    adjusted_r_squared, df_residual, None)

        xtx_inv = np.linalg.inv(design.T @ design)
        lags_used: Optional[int] = None
        if standard_errors == _HAC:
            lags_used = hac_lags if hac_lags is not None else self._default_hac_lags(n_obs)
            meat = self._newey_west_meat(design, residuals, lags_used)
            # Stata `newey`-style small-sample correction n / (n - k).
            cov = xtx_inv @ meat @ xtx_inv * (n_obs / float(df_residual))
        else:
            sigma_squared = ss_res / float(df_residual)
            cov = sigma_squared * xtx_inv

        variances = np.diag(cov)
        if np.any(variances < 0.0):
            raise ValueError(
                "estimated coefficient covariance matrix has a negative diagonal "
                "entry; the design matrix is numerically ill-conditioned.")
        se = np.sqrt(variances)
        t_stats = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0.0)
        return beta, se, t_stats, r_squared, adjusted_r_squared, df_residual, lags_used

    # ------------------------------------------------------------------ validation

    @staticmethod
    def _validate_series(name: str, series: pd.Series) -> pd.Series:
        """Coerce one input series to float, rejecting the failure modes alignment hides."""
        if not isinstance(series, pd.Series):
            raise TypeError(f"{name} must be a pandas Series, got {type(series).__name__}")
        if series.index.has_duplicates:
            raise ValueError(
                f"{name} has duplicate index labels; de-duplicate upstream (pandas "
                "would otherwise join the duplicates combinatorially).")
        numeric = pd.to_numeric(series, errors="coerce")
        if int(numeric.isna().sum()) > int(series.isna().sum()):
            raise ValueError(f"{name} contains non-numeric values.")
        values = numeric.to_numpy(dtype=float, na_value=np.nan)
        if bool(np.isinf(values).any()):
            raise ValueError(
                f"{name} contains infinite values. NaN is dropped by alignment; an "
                "infinity is a data error and is not silently discarded.")
        return numeric.astype(float)

    # ------------------------------------------------------------------ public API

    def analyze_attribution(
        self,
        strategy_id: str,
        strategy_returns: pd.Series,
        market_returns: pd.Series,
        smb_returns: Optional[pd.Series] = None,
        hml_returns: Optional[pd.Series] = None,
        risk_free_rate_annual_pct: float = 2.0,
        market_returns_are_excess: bool = False,
        standard_errors: str = _OLS,
        hac_lags: Optional[int] = None,
    ) -> PerformanceAttributionReport:
        """Decompose a strategy return series with a CAPM or Fama-French regression.

        Args:
            strategy_id: Identifier echoed into the report and the audit note.
            strategy_returns: Simple periodic returns as decimal fractions (0.0053 =
                0.53%), net of fees and financing.
            market_returns: Market benchmark returns, same units and index. A **total**
                return by default, from which the risk-free rate is subtracted
                internally; set `market_returns_are_excess=True` when passing an
                already-excess series such as Ken French's `Mkt-RF`.
            smb_returns: Optional Fama-French size factor. A zero-investment spread, so
                it is used raw -- the risk-free rate is not subtracted.
            hml_returns: Optional Fama-French value factor, likewise used raw.
            risk_free_rate_annual_pct: Annual risk-free rate in percent (2.0 = 2%),
                de-annualized geometrically to a periodic simple rate.
            market_returns_are_excess: True when `market_returns` is already excess of
                the risk-free rate. Passing French's `Mkt-RF` with this left False
                deducts the risk-free rate twice.
            standard_errors: `"ols"` (default; assumes iid homoskedastic residuals) or
                `"hac"` for Newey-West standard errors robust to heteroskedasticity and
                serial correlation. `"hac"` requires a chronologically sorted index.
            hac_lags: Bartlett kernel lag truncation for `"hac"`. Defaults to the
                Newey & West (1994) rule of thumb floor(4 * (n/100)^(2/9)).

        Returns:
            A `PerformanceAttributionReport` describing the aligned sample only.

        Raises:
            TypeError: an input is not a pandas Series.
            ValueError: invalid arguments, non-numeric or infinite inputs, duplicate
                index labels, an unsorted index under `"hac"`, fewer than k+2 aligned
                observations, or perfectly collinear factors.
        """
        standard_errors = str(standard_errors).lower()
        if standard_errors not in (_OLS, _HAC):
            raise ValueError(
                f"standard_errors must be '{_OLS}' or '{_HAC}', got {standard_errors!r}")
        if hac_lags is not None:
            if standard_errors != _HAC:
                raise ValueError("hac_lags is only meaningful when standard_errors='hac'")
            if isinstance(hac_lags, bool) or not isinstance(
                    hac_lags, (int, np.integer)) or hac_lags < 0:
                raise ValueError(f"hac_lags must be a non-negative integer, got {hac_lags!r}")
            hac_lags = int(hac_lags)
        if not np.isfinite(risk_free_rate_annual_pct):
            raise ValueError("risk_free_rate_annual_pct must be finite")
        if risk_free_rate_annual_pct <= -100.0:
            raise ValueError(
                "risk_free_rate_annual_pct must exceed -100 (a rate of -100% or worse "
                "cannot be de-annualized geometrically).")

        columns = {
            "strat": self._validate_series("strategy_returns", strategy_returns),
            "mkt": self._validate_series("market_returns", market_returns),
        }
        # (report label, frame column, series) in the order they enter the design.
        optional_factors: List[Tuple[str, str, Optional[pd.Series]]] = [
            ("Size (SMB)", "smb", smb_returns),
            ("Value (HML)", "hml", hml_returns),
        ]
        for label, key, series in optional_factors:
            if series is not None:
                columns[key] = self._validate_series(f"{label} returns", series)

        aligned = pd.DataFrame(columns)
        frame = aligned.dropna()
        raw_rows = len(aligned)
        n_obs = len(frame)
        n_cols = 2 + sum(1 for _, _, s in optional_factors if s is not None)

        if n_obs < n_cols + 2:
            raise ValueError(
                f"{n_obs} aligned observation(s) for {n_cols} regressors (intercept "
                f"included): at least {n_cols + 2} are required to leave 2 residual "
                "degrees of freedom. Check that the input series actually overlap.")

        if standard_errors == _HAC:
            if not frame.index.is_monotonic_increasing:
                raise ValueError(
                    "standard_errors='hac' requires a chronologically sorted index: the "
                    "Newey-West kernel weights observations by their lag distance, "
                    "which is meaningless under an arbitrary row order. Sort the "
                    "inputs first.")
            if hac_lags is not None and hac_lags >= n_obs:
                # Lags at or beyond the sample length contribute only empty
                # autocovariance terms, so an out-of-range value would be silently
                # absorbed instead of flagged.
                raise ValueError(
                    f"hac_lags={hac_lags} must be smaller than the {n_obs} aligned "
                    "observations; a lag beyond the sample contributes nothing.")

        # Geometric de-annualization, matching how a periodic simple risk-free rate
        # compounds to the quoted annual rate (and how Ken French's RF column is built).
        # rf/periods_per_year overstates the periodic hurdle by ~12bp/yr at a 5% rate.
        rf_period = (1.0 + risk_free_rate_annual_pct / 100.0) ** (
            1.0 / self.periods_per_year) - 1.0

        strat = frame["strat"].to_numpy()
        y = strat - rf_period
        market_regressor = frame["mkt"].to_numpy()
        if not market_returns_are_excess:
            market_regressor = market_regressor - rf_period

        factor_names: List[str] = ["Market (MKT)"]
        regressors: List[np.ndarray] = [market_regressor]
        for label, key, series in optional_factors:
            if series is not None:
                factor_names.append(label)
                regressors.append(frame[key].to_numpy())

        design = np.column_stack([np.ones(n_obs), *regressors])
        beta, se, t_stats, r_squared, adj_r_squared, df_residual, lags_used = (
            self._run_regression(y, design, standard_errors, hac_lags))

        undefined: List[str] = []
        t_critical: Optional[float] = None
        if t_stats is None:
            undefined.append(
                "t-statistics and p-values: the residual variance is zero (perfect "
                "in-sample fit), so the sampling distribution of the estimates is "
                "degenerate")
        else:
            t_critical = float(stats.t.ppf(1.0 - self.significance_level / 2.0, df_residual))
        if adj_r_squared is None:
            undefined.append(
                "r_squared and adjusted_r_squared: the strategy excess return series "
                "has no variance, so the share of variance explained is 0/0. "
                "r_squared is reported as 0.0 for typing; do not read it as a fit")

        annual_scale = self.periods_per_year * 100.0
        annualized_alpha_pct = float(beta[0]) * annual_scale
        alpha_t = None if t_stats is None else float(t_stats[0])
        alpha_se = None if t_stats is None else float(se[0])
        alpha_p = (None if alpha_t is None
                   else float(2.0 * stats.t.sf(abs(alpha_t), df_residual)))
        alpha_significant = (alpha_t is not None and t_critical is not None
                             and abs(alpha_t) >= t_critical)

        factor_breakdown: List[FactorAttributionBreakdown] = []
        for idx, name in enumerate(factor_names, start=1):
            loading = float(beta[idx])
            factor_annual_pct = float(np.mean(regressors[idx - 1])) * annual_scale
            factor_t = None if t_stats is None else float(t_stats[idx])
            factor_p = (None if factor_t is None
                        else float(2.0 * stats.t.sf(abs(factor_t), df_residual)))
            factor_breakdown.append(FactorAttributionBreakdown(
                factor_name=name,
                beta_exposure=round(loading, 4),
                factor_annual_return_pct=round(factor_annual_pct, 4),
                return_contribution_pct=round(loading * factor_annual_pct, 4),
                t_statistic=None if factor_t is None else round(factor_t, 4),
                is_statistically_significant=(
                    factor_t is not None and t_critical is not None
                    and abs(factor_t) >= t_critical),
                standard_error=None if t_stats is None else float(se[idx]),
                p_value=None if factor_p is None else round(factor_p, 6),
            ))

        # Exact additive decomposition: OLS residuals under an intercept sum to zero, so
        # this closes to floating-point noise.
        total_annual_pct = float(np.mean(strat)) * annual_scale
        rf_contribution_pct = rf_period * annual_scale
        explained_pct = (rf_contribution_pct + annualized_alpha_pct
                         + sum(float(beta[i]) * float(np.mean(regressors[i - 1])) * annual_scale
                               for i in range(1, len(beta))))
        unexplained_pct = total_annual_pct - explained_pct

        alpha_share: Optional[float]
        if total_annual_pct <= _TOTAL_RETURN_EPSILON_PCT:
            alpha_share = None
            undefined.append(
                "alpha_percentage_of_total_return: the total realized return is zero or "
                "negative, so alpha as a share of it is undefined or sign-inverted")
        else:
            alpha_share = round(annualized_alpha_pct / total_annual_pct * 100.0, 2)

        min_observations = self.periods_per_year * MIN_RECOMMENDED_OBSERVATION_YEARS
        insufficient_history = n_obs < min_observations

        t_text = "undefined" if alpha_t is None else f"{alpha_t:.2f}"
        notes = (
            f"PERFORMANCE ATTRIBUTION [{strategy_id}]: Total Return = "
            f"{total_annual_pct:.2f}%, Jensen's Alpha = {annualized_alpha_pct:+.2f}% "
            f"(t={t_text}, {standard_errors.upper()} SE, n={n_obs}), Market Beta = "
            f"{float(beta[1]):.2f}, R2 = {r_squared:.2f}."
        )
        if insufficient_history:
            notes += (f" WARNING: {n_obs} observations is under one year at "
                      f"{self.periods_per_year} periods/year; the annualized figures "
                      "are statistically fragile.")
        logger.info(notes)

        return PerformanceAttributionReport(
            strategy_id=strategy_id,
            total_realized_annual_return_pct=round(total_annual_pct, 2),
            risk_free_rate_pct=risk_free_rate_annual_pct,
            annualized_jensens_alpha_pct=round(annualized_alpha_pct, 2),
            alpha_t_statistic=None if alpha_t is None else round(alpha_t, 2),
            is_true_alpha_significant=alpha_significant,
            r_squared=round(r_squared, 4),
            market_beta=round(float(beta[1]), 4),
            factor_breakdown=factor_breakdown,
            alpha_percentage_of_total_return=alpha_share,
            audit_notes=notes,
            observations=n_obs,
            dropped_observations=raw_rows - n_obs,
            degrees_of_freedom=df_residual,
            adjusted_r_squared=None if adj_r_squared is None else round(adj_r_squared, 4),
            alpha_p_value=None if alpha_p is None else round(alpha_p, 6),
            alpha_standard_error=alpha_se,
            significance_level=self.significance_level,
            t_critical_value=None if t_critical is None else round(t_critical, 4),
            standard_error_type=standard_errors,
            hac_lags=lags_used,
            market_factor_is_excess=bool(market_returns_are_excess),
            risk_free_contribution_pct=round(rf_contribution_pct, 4),
            unexplained_residual_pct=round(unexplained_pct, 10),
            insufficient_history_warning=insufficient_history,
            undefined_metrics=tuple(undefined),
        )
