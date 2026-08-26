"""
monte-carlo-strategy-robustness-testing: Monte Carlo trade sequence shuffler,
IID bootstrap resampler, execution-noise perturbator, and drawdown-breach
(colloquially "Risk of Ruin") evaluator.

Design notes
------------
* **Units.** Every input is a *fractional* per-trade return on account equity
  (`0.02` = +2%, `-0.01` = -1%). Absolute P&L in currency units must be
  converted before it reaches this engine: feeding `-1500.0` into an
  `equity *= (1 + r)` recursion produces meaningless negative equity, so
  `_validate_returns` rejects anything at or below `-1.0`.

* **Non-finite inputs are rejected, not tolerated.** `float('nan')` compares
  False against every bound, so a NaN silently skipped both the peak update and
  the drawdown update in the previous implementation, and a corrupted trade log
  reported `is_robust=True`. This is a sign-off gate for deploying capital; it
  fails closed.

* **Permutation invariance.** `run_sequence_shuffling` reorders a fixed multiset
  of returns and equity compounds multiplicatively, so every permutation ends at
  the identical terminal equity `C * prod(1 + r_i)`. Shuffling measures *path*
  risk (drawdown), never terminal-wealth dispersion. The result carries
  `final_equity_is_path_invariant=True` so callers cannot mistake that constant
  for a distribution statistic. Use `run_bootstrap_resampling` for terminal
  wealth dispersion.

* **Exchangeability.** Both shuffling and IID bootstrap assume trade returns are
  exchangeable. Strategies with autocorrelated outcomes (trend-following, or
  anything sized off recent performance) lose their loss clustering under
  resampling, which *understates* realized drawdown risk. For dependent return
  streams a block bootstrap that preserves runs is the appropriate method
  (Kuensch 1989; Politis & Romano 1994) - see `references/standards.md`.

* **Determinism.** The RNG is a private `random.Random` instance re-seeded at the
  start of every run. The previous implementation called the module-level
  `random.seed()` in `__init__`, which hijacked the caller's global stream and
  left repeat calls on one engine non-reproducible.

* **"Risk of Ruin"** here is `P(max drawdown >= max_drawdown_limit)` across
  simulated paths - a *drawdown-limit breach* probability, not the classical
  gambler's-ruin probability of depleting capital. The field name is retained
  for API compatibility; the distinction is documented because a 25% drawdown
  limit is not capital depletion.
"""
from dataclasses import dataclass
import logging
import math
import random
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

MIN_TRADES = 5                     # Below this, quantiles of the path distribution are meaningless.
MIN_RECOMMENDED_SIMULATIONS = 500  # See `_warn_if_under_sampled` for the sampling-error basis.


class MonteCarloError(ValueError):
    """Raised on malformed trade logs or infeasible engine configuration."""


@dataclass
class MonteCarloResult:
    num_simulations: int
    median_final_equity: float
    p95_max_drawdown: float
    p99_max_drawdown: float
    risk_of_ruin_pct: float
    is_robust: bool  # risk_of_ruin_pct <= max_risk_of_ruin_pct and p95_max_drawdown <= limit
    mode: str = "UNSPECIFIED"
    # True when the simulation mode cannot produce terminal-wealth dispersion
    # (sequence shuffling), so `median_final_equity` is a constant, not a quantile.
    final_equity_is_path_invariant: bool = False


def _is_finite_number(value: object) -> bool:
    """True for a real, finite int/float. Rejects bool, NaN, +/-Inf, and non-numerics."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


class MonteCarloRobustnessEngine:
    """
    Evaluates trading strategy robustness against trade sequence permutation,
    IID bootstrap resampling with replacement, and execution-noise perturbation.

    All three modes consume fractional per-trade returns and return a
    `MonteCarloResult` holding drawdown quantiles and the drawdown-limit breach
    probability used for deployment sign-off.
    """

    def __init__(
        self,
        initial_capital: float = 100000.0,
        max_drawdown_limit: float = 0.25,
        num_simulations: int = 1000,
        seed: Optional[int] = 42,
        max_risk_of_ruin_pct: float = 1.0,
    ):
        """
        Args:
            initial_capital: Starting account equity. Must be finite and > 0;
                drawdown is a ratio against the running peak, which is undefined
                at zero capital.
            max_drawdown_limit: Fractional drawdown that counts as a breach, in
                (0, 1]. `0.25` means a 25% peak-to-trough decline.
            num_simulations: Simulated paths per run. Must be >= 1; a warning is
                logged below `MIN_RECOMMENDED_SIMULATIONS` because tail quantile
                estimates are then too noisy to gate a deployment decision.
            seed: Seed for the engine's private RNG. `None` draws from system
                entropy, which makes runs non-reproducible - acceptable for
                exploration, not for a recorded sign-off.
            max_risk_of_ruin_pct: Breach-probability ceiling for `is_robust`, in
                [0, 100]. Defaults to the 1.0% house threshold documented in
                `SKILL.md`; it is a risk-appetite parameter, not a regulatory limit.
        """
        if not _is_finite_number(initial_capital) or initial_capital <= 0.0:
            raise MonteCarloError(
                f"initial_capital must be finite and > 0, got {initial_capital!r}.")
        if not _is_finite_number(max_drawdown_limit) or not (0.0 < max_drawdown_limit <= 1.0):
            raise MonteCarloError(
                f"max_drawdown_limit must be in (0, 1], got {max_drawdown_limit!r}.")
        if (isinstance(num_simulations, bool) or not isinstance(num_simulations, int)
                or num_simulations < 1):
            raise MonteCarloError(f"num_simulations must be an int >= 1, got {num_simulations!r}.")
        if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
            raise MonteCarloError(f"seed must be None or an int, got {seed!r}.")
        if not _is_finite_number(max_risk_of_ruin_pct) or not (0.0 <= max_risk_of_ruin_pct <= 100.0):
            raise MonteCarloError(
                f"max_risk_of_ruin_pct must be in [0, 100], got {max_risk_of_ruin_pct!r}.")

        self.initial_capital = float(initial_capital)
        self.max_drawdown_limit = float(max_drawdown_limit)
        self.num_simulations = num_simulations
        self.seed = seed
        self.max_risk_of_ruin_pct = float(max_risk_of_ruin_pct)
        self._warn_if_under_sampled()

    # ------------------------------------------------------------------ helpers

    def _warn_if_under_sampled(self) -> None:
        """
        The empirical q-quantile of `n` paths is an order statistic whose rank has
        standard error `sqrt(n * q * (1 - q))`. At q=0.95 that is +/- 2.2 ranks
        out of 100 paths but +/- 6.9 ranks out of 1000, so the reported DD_95
        wanders across neighbouring paths at small `n`. Hence the floor.
        """
        if self.num_simulations < MIN_RECOMMENDED_SIMULATIONS:
            logger.warning(
                "num_simulations=%d is below the recommended %d; tail drawdown quantiles "
                "will be noisy and should not gate a deployment decision.",
                self.num_simulations, MIN_RECOMMENDED_SIMULATIONS,
            )

    def _new_rng(self) -> random.Random:
        """
        Private RNG re-seeded per run: repeat calls on one engine reproduce, and
        the caller's module-level `random` stream is never touched.
        """
        return random.Random(self.seed)

    @staticmethod
    def _validate_returns(trade_returns: Sequence[float], label: str) -> List[float]:
        """
        Rejects trade logs the compounding model cannot represent.

        A return at or below `-1.0` means the account was wiped on a single
        trade. Far more often it means absolute P&L (e.g. `-1500.0`) was passed
        where a fraction was expected - which drives equity negative and reports
        a >100% "drawdown". Both warrant a hard stop, not a simulation.
        """
        materialized = list(trade_returns)  # Accept any sequence or iterable, not just list.
        if len(materialized) < MIN_TRADES:
            raise MonteCarloError(
                f"{label}: insufficient trade history ({len(materialized)} trades). "
                f"Min {MIN_TRADES} required.")

        validated: List[float] = []
        for idx, r in enumerate(materialized):
            if not _is_finite_number(r):
                raise MonteCarloError(
                    f"{label}: non-finite or non-numeric trade return {r!r} at index {idx}. "
                    "NaN/Inf must be resolved in the trade log, not simulated.")
            if r <= -1.0:
                raise MonteCarloError(
                    f"{label}: trade return {r!r} at index {idx} is <= -100%. Returns must be "
                    "fractional (0.02 = +2%) and strictly greater than -1.0; a trade log in "
                    "absolute currency P&L must be divided by account equity first.")
            validated.append(float(r))
        return validated

    def _compute_equity_curve_metrics(self, trade_returns: Sequence[float]) -> Tuple[float, float]:
        """
        Computes terminal equity and maximum peak-to-trough drawdown for one path.

        `trade_returns` are fractional (0.02 = +2%). A return of exactly -1.0 or
        below - reachable only from noise perturbation, never from validated
        input - is treated as the absorbing barrier: equity 0, drawdown 100%.

        Raises:
            MonteCarloError: if compounding overflows to infinity. Validation
                bounds returns from below but not from above, so a trade log of
                large positive values - absolute currency P&L in a mostly-winning
                log, for instance - can compound past the float range. Once
                equity is `inf`, `(peak - equity) / peak` is NaN, every drawdown
                comparison silently fails, and the path would report a passing
                verdict. Fail loudly instead.
        """
        equity = self.initial_capital
        peak = equity
        max_dd = 0.0

        for r in trade_returns:
            if r <= -1.0:
                return 0.0, 1.0  # Total loss; 1.0 is the maximum representable drawdown.
            equity *= (1.0 + r)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak  # peak >= initial_capital > 0, so never divides by zero.
            if dd > max_dd:
                max_dd = dd

        # Once equity reaches +inf it can never return (1 + r > 0 for every
        # validated r), so a single check per path is sufficient.
        if not math.isfinite(equity):
            raise MonteCarloError(
                f"Equity compounded to {equity!r}: the trade returns are too large to "
                "represent. Confirm the log holds fractional returns (0.02 = +2%) rather "
                "than absolute currency P&L.")

        return equity, max_dd

    @staticmethod
    def _nearest_rank_quantile(sorted_values: Sequence[float], q: float) -> float:
        """
        Nearest-rank empirical quantile (inverse-CDF, Hyndman-Fan type 1):
        `x_(ceil(q*n))`, no interpolation. Chosen over an interpolating estimator
        because a risk gate should report a drawdown that some simulated path
        actually realized, never an average of two paths.
        """
        n = len(sorted_values)
        idx = math.ceil(q * n) - 1
        return sorted_values[min(max(idx, 0), n - 1)]

    def _summarize(
        self,
        final_equities: List[float],
        max_drawdowns: List[float],
        ruin_count: int,
        mode: str,
        final_equity_is_path_invariant: bool = False,
    ) -> MonteCarloResult:
        """Builds the result from one run's path outcomes."""
        final_equities.sort()
        max_drawdowns.sort()

        median_eq = self._nearest_rank_quantile(final_equities, 0.50)
        p95_dd = self._nearest_rank_quantile(max_drawdowns, 0.95)
        p99_dd = self._nearest_rank_quantile(max_drawdowns, 0.99)
        ruin_pct = (ruin_count / self.num_simulations) * 100.0

        is_robust = (ruin_pct <= self.max_risk_of_ruin_pct) and (p95_dd <= self.max_drawdown_limit)

        logger.info(
            "Monte Carlo %s (%d runs): median equity %s, DD_95 %.2f%%, DD_99 %.2f%%, "
            "drawdown-breach probability %.2f%% (robust: %s)",
            mode, self.num_simulations, f"{median_eq:,.2f}", p95_dd * 100.0,
            p99_dd * 100.0, ruin_pct, is_robust,
        )

        return MonteCarloResult(
            num_simulations=self.num_simulations,
            median_final_equity=median_eq,
            p95_max_drawdown=p95_dd,
            p99_max_drawdown=p99_dd,
            risk_of_ruin_pct=ruin_pct,
            is_robust=is_robust,
            mode=mode,
            final_equity_is_path_invariant=final_equity_is_path_invariant,
        )

    # -------------------------------------------------------------- simulations

    def run_sequence_shuffling(self, trade_returns: Sequence[float]) -> MonteCarloResult:
        """
        Monte Carlo trade sequence permutation (resampling WITHOUT replacement).

        Answers: "had the same trades arrived in a different order, how deep
        would the drawdown have been?" Terminal equity is invariant across
        permutations, so the returned `median_final_equity` is a constant and
        `final_equity_is_path_invariant` is True.

        Assumes trade returns are exchangeable. If the strategy's wins and losses
        cluster (trend-following), shuffling breaks that dependence and the
        reported drawdown is optimistic.
        """
        returns = self._validate_returns(trade_returns, "run_sequence_shuffling")
        rng = self._new_rng()

        final_equities: List[float] = []
        max_drawdowns: List[float] = []
        ruin_count = 0

        for _ in range(self.num_simulations):
            shuffled_returns = list(returns)
            rng.shuffle(shuffled_returns)

            eq, max_dd = self._compute_equity_curve_metrics(shuffled_returns)
            final_equities.append(eq)
            max_drawdowns.append(max_dd)
            if max_dd >= self.max_drawdown_limit:
                ruin_count += 1

        return self._summarize(
            final_equities, max_drawdowns, ruin_count,
            mode="SEQUENCE_SHUFFLING", final_equity_is_path_invariant=True,
        )

    def run_bootstrap_resampling(self, trade_returns: Sequence[float]) -> MonteCarloResult:
        """
        Monte Carlo IID bootstrap (resampling WITH replacement, Efron 1979).

        Answers: "if the trade population is resampled, how do terminal equity
        and drawdown vary?" Unlike shuffling this does vary terminal wealth,
        because each path draws a different multiset of trades.

        Assumes trades are IID draws from a stable distribution. It neither
        preserves serial dependence nor extrapolates beyond the observed return
        multiset - a loss larger than the worst historical trade can never appear.
        """
        returns = self._validate_returns(trade_returns, "run_bootstrap_resampling")
        rng = self._new_rng()
        n_trades = len(returns)

        final_equities: List[float] = []
        max_drawdowns: List[float] = []
        ruin_count = 0

        for _ in range(self.num_simulations):
            bootstrapped = [returns[rng.randrange(n_trades)] for _ in range(n_trades)]
            eq, max_dd = self._compute_equity_curve_metrics(bootstrapped)
            final_equities.append(eq)
            max_drawdowns.append(max_dd)
            if max_dd >= self.max_drawdown_limit:
                ruin_count += 1

        return self._summarize(
            final_equities, max_drawdowns, ruin_count, mode="BOOTSTRAP_RESAMPLING",
        )

    def run_noise_injection(
        self,
        trade_returns: Sequence[float],
        noise_std: float,
        mean_shift: float = 0.0,
    ) -> MonteCarloResult:
        """
        Monte Carlo execution-noise perturbation.

        Perturbs every trade return by an independent draw
        `eps ~ N(mean_shift, noise_std)` while preserving the original trade
        order, isolating execution sensitivity from sequence effects. Answers:
        "does the edge survive if each fill is worse than the backtest assumed?"

        Args:
            noise_std: Standard deviation of the per-trade perturbation, in
                fractional return units (`0.0005` = 5 bps). Required rather than
                defaulted: there is no universal slippage magnitude, so it must
                be calibrated from the caller's own execution data (see
                `transaction-cost-analysis-tca-integration`). Must be finite, >= 0.
            mean_shift: Deterministic per-trade drift added alongside the noise,
                in the same units. Zero-mean Gaussian noise is a *symmetric
                sensitivity* test, not a slippage model - real slippage is
                one-sided and always costs. Pass a negative `mean_shift`
                (e.g. `-0.0005`) to model an expected per-trade execution cost.
                Must be finite and > -1.

        A perturbed return can fall at or below -100%; such a path is floored at
        the absorbing barrier (equity 0, drawdown 100%) rather than compounding
        into negative equity.
        """
        returns = self._validate_returns(trade_returns, "run_noise_injection")
        if not _is_finite_number(noise_std) or noise_std < 0.0:
            raise MonteCarloError(f"noise_std must be finite and >= 0, got {noise_std!r}.")
        if not _is_finite_number(mean_shift) or mean_shift <= -1.0:
            raise MonteCarloError(f"mean_shift must be finite and > -1.0, got {mean_shift!r}.")

        rng = self._new_rng()
        final_equities: List[float] = []
        max_drawdowns: List[float] = []
        ruin_count = 0

        for _ in range(self.num_simulations):
            perturbed = [r + mean_shift + rng.gauss(0.0, noise_std) for r in returns]
            eq, max_dd = self._compute_equity_curve_metrics(perturbed)
            final_equities.append(eq)
            max_drawdowns.append(max_dd)
            if max_dd >= self.max_drawdown_limit:
                ruin_count += 1

        return self._summarize(
            final_equities, max_drawdowns, ruin_count, mode="NOISE_INJECTION",
        )
