"""
synthetic-data-generation-for-backtest-augmentation: parametric and
non-parametric generators for synthetic return/price paths, plus a moment-parity
report for auditing a synthetic sample against its empirical baseline.

Purpose
-------
A backtest run on one short history measures one path. These generators produce
additional paths -- a diffusion baseline (GBM), a volatility-clustering baseline
(GARCH(1,1)), and an empirical resample that keeps short-range dependence
(circular block bootstrap) -- so a strategy can be evaluated over a distribution
of paths rather than the single one that happened to occur.

Return convention
-----------------
Every return series produced or consumed here is a **log return**:
``r_t = ln(P_t / P_{t-1})``. ``generate_garch`` builds prices as
``P_t = P_{t-1} * exp(r_t)``, and the log increments of ``generate_gbm`` are log
returns by construction. When bootstrapping or validating, pass **log** returns;
mixing simple and log returns silently biases every moment in the report.

Moment convention
-----------------
``validate_synthetic_path`` reports the standard sample moment ratios computed
with the **population** divisor (``ddof = 0``), applied identically to both
series so the comparison is like-for-like:

    volatility = sqrt( mean( (x - mean x)^2 ) )
    skewness   = mean( (x - mean x)^3 ) / volatility^3
    kurtosis   = mean( (x - mean x)^4 ) / volatility^4

``kurtosis`` is **Pearson (raw) kurtosis**, which is 3.0 for a normal
distribution. Excess kurtosis is ``kurtosis - 3.0``. Volatility is **per bar**,
not annualized -- annualize with ``sqrt(bars_per_year)`` at the reporting layer
if needed.

Method notes and limitations (deliberate, documented)
-----------------------------------------------------
- **GBM produces no fat tails and no volatility clustering.** Its log returns are
  IID normal by construction. It is a baseline for comparison, not a realistic
  model of asset returns. Do not use a GBM path to estimate tail risk.
- **GARCH(1,1) here uses Gaussian innovations**, so it generates clustering and
  unconditional excess kurtosis but not the conditional fat tails of a
  Student-t GARCH. Parameters are caller-supplied; this module does **not**
  estimate them from data (no QMLE fitting) -- estimate them with a dedicated
  package and pass them in.
- **Covariance stationarity is enforced**: ``alpha + beta < 1`` is required
  (Bollerslev 1986). Nelson (1990) shows GARCH(1,1) can be *strictly* stationary
  with ``alpha + beta >= 1`` (IGARCH), but such a process has **no finite
  unconditional variance**, which makes the moment-parity report meaningless.
  IGARCH simulation is therefore out of scope and rejected rather than clamped.
- **The bootstrap is circular, with a fixed block length** (Politis & Romano
  1992), not the stationary bootstrap (Politis & Romano 1994), which draws
  geometrically distributed block lengths. The fixed-length resample is not
  itself a stationary series; the circular wrap is what makes every observation
  equally likely, so the resampled mean is unbiased for the sample mean.
- **The bootstrap cannot produce a loss larger than the worst observed bar.**
  Resampling an empirical sample re-orders history; it does not extrapolate
  beyond it. For shocks outside the sample use
  ``scenario-based-stress-testing-custom-shocks``.
- **No block-length selection is performed.** ``DEFAULT_BLOCK_SIZE`` is a
  placeholder, not a standard -- see ``references/standards.md``.
- **Moment parity is a necessary, not sufficient, condition.** Matching the first
  four moments says nothing about autocorrelation, the tail index, or the
  cross-sectional dependence a multi-asset backtest needs.

References
----------
Bollerslev, T. (1986), "Generalized autoregressive conditional
heteroskedasticity", *Journal of Econometrics* 31(3), 307-327.
Nelson, D. B. (1990), "Stationarity and Persistence in the GARCH(1,1) Model",
*Econometric Theory* 6(3), 318-334.
Kuensch, H. R. (1989), "The Jackknife and the Bootstrap for General Stationary
Observations", *The Annals of Statistics* 17(3), 1217-1241.
Politis, D. N. & Romano, J. P. (1992), "A circular block-resampling procedure for
stationary data", in LePage & Billard (eds.), *Exploring the Limits of
Bootstrap*, Wiley, 263-270.
Politis, D. N. & White, H. (2004), "Automatic Block-Length Selection for the
Dependent Bootstrap", *Econometric Reviews* 23(1), 53-70; corrected by Patton,
Politis & White (2009), *Econometric Reviews* 28(4), 372-375.
"""
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: Trading days per year for daily bars. Used only by callers annualizing the
#: per-bar volatility this module reports -- nothing here annualizes for you.
DAILY_BARS_PER_YEAR = 252

#: Placeholder block length for
#: :meth:`SyntheticDataGenerator.block_bootstrap_returns`. **Not a standard.**
#: The bias/variance-optimal block length depends on the sample size and the
#: autocorrelation structure of the series (Hall, Horowitz & Jing 1995; Politis
#: & White 2004, corrected 2009). Select it per series.
DEFAULT_BLOCK_SIZE = 5

#: Default relative tolerance for the volatility-parity gate. A house heuristic
#: chosen so a 252-bar Gaussian resample passes routinely, **not** a published or
#: regulatory threshold. Override per use case.
DEFAULT_VOL_TOLERANCE = 0.35

#: Dispersion below this fraction of the series scale is floating-point noise,
#: not signal: the volatility is reported as exactly 0.0 and the standardized
#: third and fourth moments as ``None``. This is a *relative* guard; it replaces
#: the previous ``+ 1e-9`` additive guard, which biased kurtosis downward by ~9%
#: at a daily return scale (sigma = 0.01) and destroyed it entirely intraday.
DISPERSION_RELATIVE_EPSILON = 1e-12

#: Minimum observations needed for a standardized fourth moment to exist at all.
MIN_MOMENT_OBSERVATIONS = 4

#: Below this many observations the reported moments are dominated by sampling
#: error; the report is still produced but a warning is logged.
MOMENT_SAMPLE_WARN_THRESHOLD = 30


def _as_finite_returns(values: np.ndarray, name: str, min_length: int = 2) -> np.ndarray:
    """
    Coerce ``values`` to a 1-D float array and reject anything that would
    silently poison a moment calculation.

    A single ``NaN`` anywhere makes every downstream mean, standard deviation and
    comparison ``NaN``; ``NaN <= tolerance`` is ``False``, so a corrupt series
    would quietly report "not statistically consistent" for the wrong reason.
    Reject it instead.
    """
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be a 1-D sequence, got shape {arr.shape}")
    if arr.size < min_length:
        raise ValueError(
            f"{name} must contain at least {min_length} observations, got {arr.size}"
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains NaN or infinite values")
    return arr


def _standardized_moments(
    sample: np.ndarray,
) -> Tuple[float, float, Optional[float], Optional[float]]:
    """
    Return ``(mean, population_stdev, skewness, kurtosis)`` for ``sample``.

    Skewness and kurtosis are ``None`` when they are not defined: fewer than
    :data:`MIN_MOMENT_OBSERVATIONS` observations, or dispersion indistinguishable
    from floating-point noise (a constant series). ``None`` means "not
    measurable" and must be rendered as such -- never coerced to 0.0.
    """
    mean = float(np.mean(sample))
    centered = sample - mean
    stdev = float(np.sqrt(np.mean(centered ** 2)))

    # A genuinely constant series does not produce an exactly zero standard
    # deviation: the mean of 100 copies of 0.001 is off by an ulp or two, leaving
    # a residual dispersion around 1e-19. Snapping it to exactly 0.0 is what
    # makes "no measurable dispersion" testable downstream with `<= 0.0` -- and
    # keeps a 1e-19 float from being divided into as if it were a volatility.
    # The scale is taken from the series itself (its level and its largest
    # deviation), never from an absolute constant, so the test is scale-invariant:
    # a series and the same series scaled by 1e-12 get the same verdict.
    max_abs_dev = float(np.max(np.abs(centered)))
    if stdev <= DISPERSION_RELATIVE_EPSILON * max(abs(mean), max_abs_dev):
        return mean, 0.0, None, None

    if sample.size < MIN_MOMENT_OBSERVATIONS:
        return mean, stdev, None, None

    skewness = float(np.mean(centered ** 3) / stdev ** 3)
    kurtosis = float(np.mean(centered ** 4) / stdev ** 4)
    return mean, stdev, skewness, kurtosis


@dataclass
class GBMConfig:
    """
    Geometric Brownian Motion parameters.

    ``mu`` and ``sigma`` are expressed **per unit of time**, in the same time
    unit as ``dt``. With the default ``dt = 1/252`` they are annualized: for a
    daily path with 20% annualized volatility pass ``sigma=0.20``, not the
    per-day 0.0126.

    ``mu`` is the drift of the price process, so ``E[S_t] = S0 * exp(mu * t)``;
    the ``-0.5 * sigma^2`` Ito correction is applied by the generator. Passing an
    estimated mean *log* return as ``mu`` therefore understates the drift by
    ``sigma^2 / 2``.
    """
    mu: float
    sigma: float
    S0: float
    dt: float = 1.0 / DAILY_BARS_PER_YEAR
    steps: int = DAILY_BARS_PER_YEAR


@dataclass
class GARCHConfig:
    """
    GARCH(1,1) parameters, all expressed **per bar** (not annualized).

    ``omega`` is the variance intercept, so its units are squared per-bar
    returns: the default ``1e-5`` implies a long-run per-bar volatility of
    ``sqrt(1e-5 / (1 - 0.10 - 0.85)) = 1.41%``, roughly a daily equity level.
    ``mu`` is the constant mean **log** return per bar.

    ``alpha + beta < 1`` is required (covariance stationarity, Bollerslev 1986)
    and enforced by :meth:`SyntheticDataGenerator.generate_garch`.
    """
    omega: float = 1e-5
    alpha: float = 0.10
    beta: float = 0.85
    mu: float = 0.0002
    S0: float = 100.0
    steps: int = DAILY_BARS_PER_YEAR


@dataclass
class GARCHPath:
    """
    Output of :meth:`SyntheticDataGenerator.generate_garch`.

    ``prices`` has ``steps + 1`` entries (``prices[0] == S0``), ``sigmas`` has
    ``steps + 1`` entries (``sigmas[0]`` is the stationary starting conditional
    volatility), and ``returns`` has ``steps`` entries, where ``returns[t - 1]``
    is the log return realized over ``prices[t - 1] -> prices[t]``.

    Iterating yields ``(prices, sigmas)`` so that the pre-2.0 two-tuple
    unpacking ``prices, sigmas = generate_garch(cfg)`` keeps working unchanged.
    The return series -- which the workflow feeds to
    :meth:`SyntheticDataGenerator.validate_synthetic_path` -- is available as
    ``.returns`` and was previously computed and then discarded.
    """
    prices: np.ndarray
    sigmas: np.ndarray
    returns: np.ndarray

    def __iter__(self):
        return iter((self.prices, self.sigmas))


@dataclass
class SyntheticValidationReport:
    """
    Moment-parity comparison of a synthetic return sample against an empirical
    baseline. All volatilities are **per bar**; ``kurtosis`` fields are Pearson
    (raw) kurtosis, 3.0 for a normal sample.

    ``is_statistically_consistent`` is a **volatility-parity gate only** --
    ``|sigma_synth - sigma_hist| / sigma_hist <= vol_tolerance``. It deliberately
    does not gate on the mean (the mean of a return series is not estimable to
    useful precision over a backtest-length sample) nor on skewness/kurtosis
    (whose sampling error at a few hundred observations is large enough that a
    gate would reject correct generators). Those moments are reported for **both**
    series so the caller can judge them; passing this gate is necessary, not
    sufficient.

    Skewness and kurtosis fields are ``None`` when undefined -- fewer than
    :data:`MIN_MOMENT_OBSERVATIONS` observations, or a constant series. Render
    ``None`` as "not measurable"; do not coerce it to 0.0.

    Values are **not rounded**. Round at the presentation layer -- rounding here
    destroyed intraday-scale mean returns, which are legitimately ~1e-6.
    """
    mean_return_historical: float
    mean_return_synthetic: float
    volatility_historical: float
    volatility_synthetic: float
    skewness_synthetic: Optional[float]
    kurtosis_synthetic: Optional[float]
    is_statistically_consistent: bool
    # Fields below are appended (all defaulted) so the original 7-argument
    # positional construction remains valid.
    skewness_historical: Optional[float] = None
    kurtosis_historical: Optional[float] = None
    volatility_relative_error: float = 0.0
    vol_tolerance: float = DEFAULT_VOL_TOLERANCE
    observations_historical: int = 0
    observations_synthetic: int = 0


class SyntheticDataGenerator:
    """
    Synthetic path generator for backtest augmentation: GBM diffusion,
    GARCH(1,1) volatility clustering, IID and circular block bootstrap
    resampling, and a moment-parity report.

    All draws come from a single seeded ``numpy.random.Generator``, so a given
    seed plus a given sequence of calls reproduces exactly. Changing the order or
    number of calls changes the stream -- construct one generator per
    reproducible experiment rather than sharing one across varying call orders.

    This class is **not thread-safe**: ``numpy.random.Generator`` is not, and two
    threads drawing from one instance interleave the stream and destroy
    reproducibility. Use one instance per thread, each with its own seed.
    """

    def __init__(self, seed: Optional[int] = 42):
        """
        ``seed=None`` draws from OS entropy and makes the run **irreproducible**;
        a warning is logged because an unreproducible augmentation run cannot be
        re-audited. Pass an explicit integer for anything feeding a decision.
        """
        if seed is None:
            logger.warning(
                "SyntheticDataGenerator constructed without a seed; this run is not reproducible"
            )
        elif isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
            raise ValueError(f"seed must be an int or None, got {type(seed).__name__}")
        self.seed = seed
        self.rng = np.random.default_rng(seed)

    # ------------------------------------------------------------------ GBM --

    def generate_gbm(self, config: GBMConfig) -> np.ndarray:
        """
        Generate a Geometric Brownian Motion price path of ``config.steps + 1``
        points, starting at ``config.S0``.

            S_t = S_{t-1} * exp( (mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z_t )

        Log returns are IID normal by construction: **no volatility clustering
        and no fat tails**. Use it as a null baseline, not as a tail-risk model.

        Raises:
            ValueError: on a non-positive ``S0``, negative ``sigma``,
                non-positive ``dt``, ``steps < 1``, or any non-finite parameter.
        """
        self._validate_gbm_config(config)

        # Drawn as one vectorized block. numpy's Generator consumes the bit
        # stream sequentially, so this yields the identical normals that the
        # previous per-step scalar loop did. The path is then formed by
        # exponentiating a cumulative sum rather than by repeated multiplication,
        # which reassociates the floating-point arithmetic: paths generated under
        # a given seed before 2.0 reproduce to round-off (~1e-15 relative), not
        # bit-for-bit.
        z = self.rng.standard_normal(int(config.steps))
        drift = (config.mu - 0.5 * config.sigma ** 2) * config.dt
        diffusion = config.sigma * np.sqrt(config.dt) * z

        paths = np.empty(int(config.steps) + 1, dtype=float)
        paths[0] = config.S0
        paths[1:] = config.S0 * np.exp(np.cumsum(drift + diffusion))

        if not np.all(np.isfinite(paths)):
            raise ValueError(
                "GBM price path overflowed to non-finite values; reduce mu, sigma, dt or steps"
            )
        return paths

    @staticmethod
    def _validate_gbm_config(config: GBMConfig) -> None:
        for name in ("mu", "sigma", "S0", "dt"):
            value = float(getattr(config, name))
            if not np.isfinite(value):
                raise ValueError(f"GBMConfig.{name} must be finite, got {value!r}")
        if config.S0 <= 0.0:
            raise ValueError(f"GBMConfig.S0 must be > 0, got {config.S0!r}")
        if config.sigma < 0.0:
            raise ValueError(f"GBMConfig.sigma must be >= 0, got {config.sigma!r}")
        if config.dt <= 0.0:
            raise ValueError(f"GBMConfig.dt must be > 0, got {config.dt!r}")
        if int(config.steps) < 1:
            raise ValueError(f"GBMConfig.steps must be >= 1, got {config.steps!r}")

    # ---------------------------------------------------------------- GARCH --

    def generate_garch(self, config: GARCHConfig) -> GARCHPath:
        """
        Simulate a GARCH(1,1) path with Gaussian innovations.

            sigma_t^2 = omega + alpha * eps_{t-1}^2 + beta * sigma_{t-1}^2
            r_t       = mu + eps_t,   eps_t = sigma_t * Z_t,   Z_t ~ N(0, 1)
            P_t       = P_{t-1} * exp(r_t)

        The recursion is started **at the stationary point**: both ``sigma_0^2``
        and ``eps_0^2`` are set to the unconditional variance
        ``omega / (1 - alpha - beta)``, so ``sigma_1^2`` equals it exactly and no
        burn-in is required. (Starting from ``eps_0 = 0``, as the pre-2.0
        implementation did, gave ``sigma_1^2 = omega + beta * sigma_bar^2``,
        strictly below the stationary level, so early bars were systematically
        under-volatile.)

        Returns:
            GARCHPath: unpacks as ``(prices, sigmas)`` for backward compatibility
            and additionally exposes ``.returns``.

        Raises:
            ValueError: if ``omega <= 0``, ``alpha < 0``, ``beta < 0``,
                ``alpha + beta >= 1`` (no finite unconditional variance; see the
                module docstring on IGARCH), ``S0 <= 0``, ``steps < 1``, or any
                parameter is non-finite.
        """
        self._validate_garch_config(config)

        steps = int(config.steps)
        prices = np.empty(steps + 1, dtype=float)
        sigmas = np.empty(steps + 1, dtype=float)
        returns = np.empty(steps, dtype=float)

        uncond_var = config.omega / (1.0 - config.alpha - config.beta)
        prices[0] = config.S0
        sigmas[0] = float(np.sqrt(uncond_var))
        prev_eps_sq = uncond_var

        # The sigma recursion is sequential, but the innovations are not, so they
        # are drawn in one block.
        innovations = self.rng.standard_normal(steps)

        for t in range(1, steps + 1):
            sigma_t2 = (
                config.omega
                + config.alpha * prev_eps_sq
                + config.beta * sigmas[t - 1] ** 2
            )
            sigma_t = float(np.sqrt(sigma_t2))
            sigmas[t] = sigma_t

            eps_t = sigma_t * innovations[t - 1]
            ret_t = config.mu + eps_t
            returns[t - 1] = ret_t
            prices[t] = prices[t - 1] * np.exp(ret_t)
            prev_eps_sq = eps_t * eps_t

        if not np.all(np.isfinite(prices)):
            # Reachable with an extreme but individually valid parameter set: a
            # large mu or omega can overflow exp() over a long path.
            raise ValueError(
                "GARCH price path overflowed to non-finite values; reduce omega, mu, or steps"
            )

        return GARCHPath(prices=prices, sigmas=sigmas, returns=returns)

    @staticmethod
    def _validate_garch_config(config: GARCHConfig) -> None:
        for name in ("omega", "alpha", "beta", "mu", "S0"):
            value = float(getattr(config, name))
            if not np.isfinite(value):
                raise ValueError(f"GARCHConfig.{name} must be finite, got {value!r}")
        if config.omega <= 0.0:
            raise ValueError(f"GARCHConfig.omega must be > 0, got {config.omega!r}")
        if config.alpha < 0.0 or config.beta < 0.0:
            raise ValueError(
                f"GARCHConfig.alpha and .beta must be >= 0, got alpha={config.alpha!r}, "
                f"beta={config.beta!r}"
            )
        persistence = config.alpha + config.beta
        if persistence >= 1.0:
            raise ValueError(
                f"GARCHConfig requires alpha + beta < 1 for covariance stationarity "
                f"(Bollerslev 1986); got alpha + beta = {persistence!r}. At or above 1 the "
                f"process has no finite unconditional variance, so moment validation against "
                f"an empirical baseline is meaningless. IGARCH simulation is out of scope."
            )
        if config.S0 <= 0.0:
            raise ValueError(f"GARCHConfig.S0 must be > 0, got {config.S0!r}")
        if int(config.steps) < 1:
            raise ValueError(f"GARCHConfig.steps must be >= 1, got {config.steps!r}")

    # ------------------------------------------------------------ Bootstrap --

    def bootstrap_returns(self, historical_returns: np.ndarray, steps: int) -> np.ndarray:
        """
        IID bootstrap: sample individual returns with replacement (Efron 1979).

        **This destroys every serial dependence in the series** -- volatility
        clustering, autocorrelation, and the loss runs that generate the deepest
        drawdowns. A drawdown measured on an IID resample of a dependent return
        series is **optimistically biased**. Use it only as a deliberate null
        against which to measure how much dependence matters; for augmentation of
        a real series use :meth:`block_bootstrap_returns`.

        Raises:
            ValueError: if ``historical_returns`` is empty or non-finite, or
                ``steps < 1``.
        """
        history = _as_finite_returns(historical_returns, "historical_returns", min_length=1)
        steps = self._validate_steps(steps)
        logger.debug(
            "IID bootstrap of %d returns from %d observations; serial dependence is destroyed",
            steps, history.size,
        )
        return self.rng.choice(history, size=steps, replace=True)

    def block_bootstrap_returns(
        self,
        historical_returns: np.ndarray,
        steps: int,
        block_size: int = DEFAULT_BLOCK_SIZE,
    ) -> np.ndarray:
        """
        Circular block bootstrap (Politis & Romano 1992): resample contiguous
        blocks of ``block_size`` returns, wrapping around the end of the series,
        and concatenate them to ``steps`` observations.

        The **wrap is the point**. A non-circular moving-block bootstrap
        (Kuensch 1989) can only start a block at index ``0 .. n - block_size``, so
        observation ``i`` appears in ``min(i + 1, block_size, n - i)`` of the
        possible blocks: with ``n = 20`` and ``block_size = 5`` the first and last
        observations are drawn roughly **a quarter as often** as interior ones.
        In a return series that systematically under-samples whichever end of the
        window holds the crash, and it biases the resampled mean. Wrapping makes
        every observation appear in exactly ``block_size`` blocks, so each is
        equally likely and the resampled mean is unbiased for the sample mean.

        Blocks are of fixed length, so this is **not** the stationary bootstrap of
        Politis & Romano (1994), which randomizes block length geometrically.

        Args:
            historical_returns: empirical log-return series to resample from.
            steps: length of the output series.
            block_size: contiguous block length, ``1 <= block_size <= len(series)``.
                ``block_size = 1`` degenerates to the IID bootstrap. The default
                is a placeholder, not a standard -- see the module docstring.

        Raises:
            ValueError: if the series is empty or non-finite, ``steps < 1``, or
                ``block_size`` is outside ``[1, len(historical_returns)]``. A
                ``block_size`` of 0 previously produced empty blocks and spun in
                an unbounded loop that exhausted memory.
        """
        history = _as_finite_returns(historical_returns, "historical_returns", min_length=1)
        steps = self._validate_steps(steps)

        n = history.size
        block_size = int(block_size)
        if block_size < 1:
            raise ValueError(f"block_size must be >= 1, got {block_size}")
        if block_size > n:
            raise ValueError(
                f"block_size ({block_size}) exceeds the {n} available observations"
            )
        if block_size == 1:
            logger.warning(
                "block_size=1 degenerates the circular block bootstrap to an IID bootstrap; "
                "serial dependence in the resampled series is destroyed"
            )

        n_blocks = -(-steps // block_size)  # ceiling division
        starts = self.rng.integers(0, n, size=n_blocks)
        # (n_blocks, block_size) index matrix taken modulo n -- the circular wrap.
        indices = (starts[:, None] + np.arange(block_size)[None, :]) % n
        return history[indices.ravel()[:steps]]

    @staticmethod
    def _validate_steps(steps: int) -> int:
        steps_int = int(steps)
        if steps_int < 1:
            raise ValueError(f"steps must be >= 1, got {steps!r}")
        return steps_int

    # ----------------------------------------------------------- Validation --

    def validate_synthetic_path(
        self,
        historical_returns: np.ndarray,
        synthetic_returns: np.ndarray,
        vol_tolerance: float = DEFAULT_VOL_TOLERANCE,
    ) -> SyntheticValidationReport:
        """
        Compare the first four moments of a synthetic return sample against an
        empirical baseline.

        Both series must be in the **same return convention** (log returns, per
        the module docstring) and at the same bar frequency; comparing a daily
        synthetic series against an hourly baseline produces a meaningless
        volatility ratio that this function cannot detect.

        ``vol_tolerance`` is the relative volatility band for
        ``is_statistically_consistent``. The default is a house heuristic, not a
        standard -- set it from what the downstream backtest can actually
        tolerate.

        Raises:
            ValueError: if either series has fewer than 2 observations or
                contains NaN/Inf; if the historical volatility is zero (no
                baseline to compare against); or if ``vol_tolerance`` is negative
                or non-finite.
        """
        history = _as_finite_returns(historical_returns, "historical_returns")
        synthetic = _as_finite_returns(synthetic_returns, "synthetic_returns")

        tolerance = float(vol_tolerance)
        if not np.isfinite(tolerance) or tolerance < 0.0:
            raise ValueError(f"vol_tolerance must be finite and >= 0, got {vol_tolerance!r}")

        for label, sample in (("historical", history), ("synthetic", synthetic)):
            if sample.size < MOMENT_SAMPLE_WARN_THRESHOLD:
                logger.warning(
                    "%s sample has only %d observations; reported skewness and kurtosis are "
                    "dominated by sampling error", label, sample.size,
                )

        h_mean, h_vol, h_skew, h_kurt = _standardized_moments(history)
        s_mean, s_vol, s_skew, s_kurt = _standardized_moments(synthetic)

        if h_vol <= 0.0:
            # A constant baseline gives no scale to measure relative error
            # against. The pre-2.0 code divided by max(h_vol, 1e-6): two
            # different constant series (0.001 vs 0.002) both had ~1e-19 residual
            # dispersion, so the relative error came out near zero and the report
            # certified them "statistically consistent" -- a passing verdict
            # produced by the absence of data.
            raise ValueError(
                "historical_returns has zero volatility; there is no baseline to compare against"
            )

        vol_relative_error = abs(s_vol - h_vol) / h_vol
        is_consistent = bool(vol_relative_error <= tolerance)
        if not is_consistent:
            logger.info(
                "synthetic volatility %.6g deviates %.1f%% from historical %.6g (tolerance %.1f%%)",
                s_vol, vol_relative_error * 100.0, h_vol, tolerance * 100.0,
            )

        return SyntheticValidationReport(
            mean_return_historical=h_mean,
            mean_return_synthetic=s_mean,
            volatility_historical=h_vol,
            volatility_synthetic=s_vol,
            skewness_synthetic=s_skew,
            kurtosis_synthetic=s_kurt,
            is_statistically_consistent=is_consistent,
            skewness_historical=h_skew,
            kurtosis_historical=h_kurt,
            volatility_relative_error=vol_relative_error,
            vol_tolerance=tolerance,
            observations_historical=int(history.size),
            observations_synthetic=int(synthetic.size),
        )
