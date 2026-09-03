"""
value-at-risk-var-live-monitoring: real-time Parametric VaR, Historical Simulation
VaR and Conditional VaR (CVaR / Expected Shortfall) over a live position book, plus
a pre-trade circuit breaker that vetoes new risk-increasing orders on a limit breach.

Estimator conventions (stated because the common ones disagree by an observation)
--------------------------------------------------------------------------------
- **Parametric (variance-covariance) VaR**: ``z_c * sigma_p - mu_p`` as a fraction of
  NAV, where ``sigma_p`` is the sample (n-1) standard deviation of the reconstructed
  portfolio return series and ``z_c`` is the standard normal quantile from
  ``statistics.NormalDist.inv_cdf`` (Wichura AS241, stdlib since Python 3.8). Set
  ``subtract_mean_drift=False`` for the drift-free ``z_c * sigma_p`` convention,
  which is the more conservative one for a positive-drift book.
- **Historical simulation VaR**: with the n portfolio returns sorted worst-first and
  ``k = ceil(n * (1 - c))``, VaR is the loss of the k-th worst observation. At
  n = 100, c = 0.99 that is the single worst; at n = 250, c = 0.99 it is the 3rd
  worst. A previous revision used ``int((1 - c) * n)`` as a 0-based index, which
  collapses to index 0 for every n < 100 at 99% -- so "historical VaR" was the worst
  observation in the sample regardless of sample size, and CVaR was identical to it.
- **CVaR / Expected Shortfall**: the mean loss of those same k worst observations.
  ``CVaR >= VaR`` holds by construction.

Weights, leverage and shorts
----------------------------
``w_i = q_i * p_i / NAV``. A short is a negative ``quantity`` (never a negative
price), so ``w_i`` is signed and the portfolio return series nets longs against
shorts. Leverage is therefore already in the number: a book with
``sum |w_i| = 3.0`` produces roughly three times the VaR of the same unlevered book,
and ``VaRMetrics.gross_exposure_pct`` reports that multiple so the caller can see it.

Return-series alignment is the CALLER's job
-------------------------------------------
Every series in ``returns_dict`` must be the same length and must be indexed by the
same observation dates, oldest first. Ragged series are rejected rather than
truncated: a previous revision took ``min(len(...))`` and then read index ``0..n-1``
from the *front* of each series, which pairs an old observation of a long series with
a recent observation of a short one. Nothing in a list of floats can detect that, and
the resulting covariance -- and therefore the VaR -- is silently wrong. A 50/50 book
of one asset at +2% and one at -2% every day has a true VaR of zero; under the old
front-truncation with a 30/20 length split it reported a 1.69% parametric VaR.

Regulatory context (verified; read the scope, not just the number)
-----------------------------------------------------------------
- BCBS "Minimum capital requirements for market risk" (d457, Jan 2019), MAR33.3:
  under the FRTB internal models approach the capital measure is Expected Shortfall
  at a "97.5th percentile, one-tailed confidence level" -- not 99% VaR. MAR33.4 sets
  a base liquidity horizon of 10 days. This module produces a 1-period measure at the
  frequency of the supplied returns and is **not** a regulatory capital calculation.
- BCBS MAR32.18 requires desk-level backtesting of the one-day VaR measure
  "calibrated to the most recent 12 months' data, equally weighted" at both the
  97.5th and 99th percentiles; MAR32.5 backtests the bank-wide model at the 99th.
- 12 CFR 217.205(b)(2) (US market risk rule): "The VaR-based measure must be based on
  a historical observation period of at least one year." Both sources put the
  supervisory floor for a 99% one-day measure near 250 observations, which is why
  ``min_observations`` defaults to ``ceil(1 / (1 - c))`` (100 at 99% -- the smallest
  sample whose tail bucket holds one observation) and why a sample shorter than one
  trading year is logged as a warning.
- 17 CFR 240.15c3-5(c)(1)(i) obliges a broker-dealer *with market access* to maintain
  pre-trade controls "reasonably designed to prevent the entry of orders that exceed
  appropriate pre-set credit or capital thresholds". The rule names no metric: a VaR
  limit can be one such threshold, it is not itself the requirement, and under
  15c3-5(d)(1) the controls must be under the broker-dealer's direct and exclusive
  control. A proprietary firm trading as a *customer* of a broker-dealer is not the
  regulated party here; its own VaR breaker is an internal control.

Limitations (deliberate, documented)
------------------------------------
- **Linear / delta-normal only.** Position value is assumed proportional to price.
  Options and other convex payoffs are mis-measured by both branches, since the
  historical branch also revalues linearly rather than repricing the instrument.
- **Parametric VaR assumes normality** and understates fat-tailed books. Compare it
  against the historical and CVaR figures rather than trusting it alone.
- **Frequency is the caller's.** Daily returns give a 1-day VaR. There is no
  annualisation and no sqrt(T) scaling here.
- **No look-ahead protection.** Series must end at the last *completed* period before
  the valuation instant; the module cannot verify this.
- **The breaker cannot verify ``is_risk_reducing``.** That flag is the caller's
  assertion about the order and is logged, not validated.
- **Cost is O(m * n)** in symbols and observations, pure Python, no third-party
  dependency.
"""
from dataclasses import dataclass
import logging
import math
import statistics
from statistics import NormalDist
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_STANDARD_NORMAL = NormalDist()

#: BCBS MAR32.18 / MAR32.5 -- VaR confidence level used for supervisory backtesting.
BASEL_VAR_CONFIDENCE_LEVEL = 0.99

#: BCBS MAR33.3 -- Expected Shortfall confidence level under the FRTB IMA.
BASEL_ES_CONFIDENCE_LEVEL = 0.975

#: Trading days in a year. Used only to warn when the sample is shorter than the
#: one-year minimum observation period of 12 CFR 217.205(b)(2) / BCBS MAR32.18.
TRADING_DAYS_PER_YEAR = 252


class VaRMonitorError(ValueError):
    """
    Raised when position, price, return-history or configuration data is invalid.

    Every rejection path in this module raises this type. A live risk loop that
    guards ``except VaRMonitorError`` therefore catches all of them -- an earlier
    revision leaked ``KeyError`` for a symbol missing from ``returns_dict`` and
    ``AttributeError`` from ``statistics`` internals for a NaN return, both of which
    slipped straight past such a guard.
    """
    pass


@dataclass
class VaRMetrics:
    """
    One VaR/CVaR snapshot. Percentage fields are fractions of NAV (0.05 == 5%).

    Fields after ``is_breached`` were added in 2.0.0 and default, so existing
    positional construction of the original eight fields still works.
    """
    confidence_level: float
    parametric_var_usd: float
    parametric_var_pct: float
    historical_var_usd: float
    historical_var_pct: float
    cvar_usd: float
    cvar_pct: float
    is_breached: bool
    #: Names of the measures that breached their limit, e.g. ``("historical_var",)``.
    breaching_measures: Tuple[str, ...] = ()
    #: Largest breaching value as a fraction of NAV; 0.0 when nothing breached.
    binding_var_pct: float = 0.0
    observations_used: int = 0
    #: k = ceil(n * (1 - c)); the number of observations averaged into CVaR.
    tail_observations_used: int = 0
    #: sum |w_i| -- gross exposure as a multiple of NAV. > 1.0 means leverage.
    gross_exposure_pct: float = 0.0
    #: sum w_i -- net directional exposure as a multiple of NAV.
    net_exposure_pct: float = 0.0
    portfolio_volatility_pct: float = 0.0
    portfolio_mean_return_pct: float = 0.0


@dataclass
class LiveRiskStatus:
    """Pre-trade verdict for a proposed order."""
    approved: bool
    var_metrics: VaRMetrics
    breach_reason: Optional[str]
    #: True when a limit was breached but the order was allowed through because the
    #: caller asserted it reduces risk. ``approved`` is True and ``breach_reason``
    #: still describes the live breach.
    risk_reducing_override: bool = False


class LiveValueAtRiskMonitor:
    """
    Computes 1-period Parametric VaR, Historical Simulation VaR and Conditional VaR
    (CVaR / Expected Shortfall) across active holdings, and vetoes new
    risk-increasing orders when a limit is breached.

    Args:
        confidence_level: One-tailed confidence for all three measures, in (0.5, 1).
        var_limit_pct: Breach threshold as a fraction of NAV, applied to BOTH the
            parametric and the historical VaR. A breach is ``>= limit``.
        cvar_limit_pct: Optional separate threshold for CVaR. ``None`` (default)
            leaves CVaR reported but out of the breach decision, preserving the
            pre-2.0.0 behaviour. Set it to bring the tail-severity measure -- the one
            FRTB actually capitalises (MAR33.3) -- into the breaker.
        min_observations: Override for the required sample size. ``0`` derives
            ``max(2, ceil(1 / (1 - confidence_level)))``: 100 at 99%, 20 at 95%,
            the smallest sample in which the historical tail bucket holds one
            observation. Below that a "historical VaR" is only the worst observation
            of a sample too short to locate the quantile. Setting this lower is a
            deliberate, logged opt-out, never silent.
        subtract_mean_drift: When True (default) parametric VaR is
            ``z_c * sigma_p - mu_p``; when False it is ``z_c * sigma_p``.
    """

    def __init__(
        self,
        confidence_level: float = 0.99,
        var_limit_pct: float = 0.05,
        cvar_limit_pct: Optional[float] = None,
        min_observations: int = 0,
        subtract_mean_drift: bool = True,
    ) -> None:
        if isinstance(confidence_level, bool) or not isinstance(confidence_level, (int, float)):
            raise VaRMonitorError(
                f"confidence_level must be a number, got {type(confidence_level).__name__}.")
        if not math.isfinite(confidence_level):
            raise VaRMonitorError(f"confidence_level must be finite, got {confidence_level!r}.")
        if not 0.5 < confidence_level < 1.0:
            raise VaRMonitorError(
                f"confidence_level must be in the open interval (0.5, 1.0), got "
                f"{confidence_level}. VaR is an upper-tail loss measure; 0.01 supplied "
                f"where 0.99 was meant would read the profit tail and report no risk."
            )
        if isinstance(var_limit_pct, bool) or not isinstance(var_limit_pct, (int, float)):
            raise VaRMonitorError(
                f"var_limit_pct must be a number, got {type(var_limit_pct).__name__}.")
        if not math.isfinite(var_limit_pct):
            raise VaRMonitorError(f"var_limit_pct must be finite, got {var_limit_pct!r}.")
        if var_limit_pct <= 0.0:
            raise VaRMonitorError(
                f"var_limit_pct must be > 0, got {var_limit_pct}. A non-positive limit "
                f"blocks every order rather than bounding risk."
            )
        if cvar_limit_pct is not None:
            if isinstance(cvar_limit_pct, bool) or not isinstance(cvar_limit_pct, (int, float)):
                raise VaRMonitorError(
                    f"cvar_limit_pct must be a number or None, got "
                    f"{type(cvar_limit_pct).__name__}.")
            if not math.isfinite(cvar_limit_pct):
                raise VaRMonitorError(f"cvar_limit_pct must be finite, got {cvar_limit_pct!r}.")
            if cvar_limit_pct <= 0.0:
                raise VaRMonitorError(f"cvar_limit_pct must be > 0 when set, got {cvar_limit_pct}.")
        if isinstance(min_observations, bool) or not isinstance(min_observations, int):
            raise VaRMonitorError(
                f"min_observations must be an int, got {type(min_observations).__name__}.")
        if min_observations < 0:
            raise VaRMonitorError(
                f"min_observations must be >= 0 (0 means derive), got {min_observations}.")

        self.confidence_level = float(confidence_level)
        self.var_limit_pct = float(var_limit_pct)
        self.cvar_limit_pct = None if cvar_limit_pct is None else float(cvar_limit_pct)
        self.min_observations = min_observations
        self.subtract_mean_drift = bool(subtract_mean_drift)

    # ------------------------------------------------------------------ helpers

    def required_observations(self) -> int:
        """
        Smallest accepted sample size.

        Derived as ``max(2, ceil(1 / (1 - c)))`` unless ``min_observations`` was set.
        Never below 2: the (n-1) sample standard deviation is undefined at n = 1.
        """
        if self.min_observations > 0:
            return max(2, self.min_observations)
        return max(2, math.ceil(1.0 / (1.0 - self.confidence_level)))

    def _z_score(self) -> float:
        """
        Standard normal quantile z with P(Z <= z) = ``confidence_level``.

        A previous revision looked this up in a dict keyed on 0.90/0.95/0.99 and fell
        back to 2.326 for anything else, so a 99.9% monitor silently used the 99%
        multiplier and **understated** VaR by 25% (2.326 against a true 3.090), while
        a 97.5% monitor overstated it by 19% (2.326 against 1.960).
        """
        return _STANDARD_NORMAL.inv_cdf(self.confidence_level)

    @staticmethod
    def _tail_count(n_observations: int, confidence_level: float) -> int:
        """
        Size of the tail bucket, ``ceil(n * (1 - c))``, clamped to ``[1, n]``.

        The epsilon is not cosmetic: ``1 - 0.99`` is 0.010000000000000009 in binary
        floating point, so a bare ``ceil(100 * (1 - 0.99))`` returns 2 rather than 1
        and shifts the quantile by one observation at exactly the round sample sizes
        this convention exists to pin down.
        """
        tail = n_observations * (1.0 - confidence_level)
        k = math.ceil(tail - 1e-9 * max(1.0, tail))
        return max(1, min(k, n_observations))

    @staticmethod
    def _finite(value: object, label: str) -> float:
        """Coerces to float and rejects NaN/Inf and non-numeric input."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise VaRMonitorError(
                f"{label} must be a number, got {type(value).__name__} {value!r}.")
        out = float(value)
        if not math.isfinite(out):
            raise VaRMonitorError(
                f"{label} is non-finite ({out!r}). A NaN propagates to a NaN VaR, and "
                f"every 'NaN >= limit' comparison is False -- the breaker would report "
                f"success while approving every order. Clean or drop the observation "
                f"instead."
            )
        return out

    def _build_portfolio_returns(
        self,
        weights: Mapping[str, float],
        returns_dict: Mapping[str, Sequence[float]],
    ) -> List[float]:
        """Validates the return matrix and reconstructs the portfolio return series."""
        symbols = sorted(weights)
        lengths = {s: len(returns_dict[s]) for s in symbols}
        n_history = lengths[symbols[0]]
        if any(n != n_history for n in lengths.values()):
            raise VaRMonitorError(
                f"Return series lengths differ: {lengths}. All series must be aligned "
                f"to the same observation dates, oldest first, before VaR estimation. "
                f"Truncating to the shortest would pair an old observation of one "
                f"series with a recent observation of another and silently corrupt "
                f"the covariance."
            )

        required = self.required_observations()
        if n_history < required:
            raise VaRMonitorError(
                f"Insufficient return history for VaR estimation at "
                f"{self.confidence_level:.1%} confidence: {n_history} observations, "
                f"{required} required (ceil(1/(1-c)) -- the smallest sample whose tail "
                f"bucket holds one observation). BCBS MAR32.18 and 12 CFR 217.205(b)(2) "
                f"put the supervisory floor at one year (~{TRADING_DAYS_PER_YEAR} "
                f"observations). Pass min_observations to override deliberately."
            )
        if n_history < TRADING_DAYS_PER_YEAR:
            logger.warning(
                "VaR sample of %d observations is shorter than the one-year "
                "observation period required by 12 CFR 217.205(b)(2) and BCBS "
                "MAR32.18 (~%d); the tail estimate is correspondingly noisy.",
                n_history, TRADING_DAYS_PER_YEAR,
            )

        series: Dict[str, List[float]] = {
            s: [
                self._finite(r, f"Return for '{s}' at index {t}")
                for t, r in enumerate(returns_dict[s])
            ]
            for s in symbols
        }

        return [
            sum(weights[s] * series[s][t] for s in symbols)
            for t in range(n_history)
        ]

    # ------------------------------------------------------------------- public

    def compute_var_metrics(
        self,
        positions: Mapping[str, float],       # symbol -> signed quantity (short < 0)
        prices: Mapping[str, float],          # symbol -> price in NAV currency, > 0
        returns_dict: Mapping[str, Sequence[float]],  # symbol -> aligned return history
        portfolio_nav: float,
    ) -> VaRMetrics:
        """
        Calculates Parametric VaR, Historical VaR and CVaR for the current book.

        Every symbol holding a non-zero quantity must appear in both ``prices`` and
        ``returns_dict``; a missing entry is a rejection, not a silently dropped
        position. All return series must share one length and one date index.

        Raises:
            VaRMonitorError: on non-positive or non-finite NAV, a missing price or
                return series, a non-positive or non-finite price, a non-finite
                quantity or return, ragged series, or an insufficient sample.
        """
        nav = self._finite(portfolio_nav, "portfolio_nav")
        if nav <= 0:
            raise VaRMonitorError(
                f"Invalid portfolio NAV: {nav}. VaR is expressed as a fraction of NAV "
                f"and is undefined at or below zero equity; escalate to the margin / "
                f"liquidation path instead of computing a risk number."
            )

        held: List[str] = []
        for symbol, quantity in positions.items():
            if self._finite(quantity, f"Quantity for '{symbol}'") != 0.0:
                held.append(symbol)

        if not held:
            return VaRMetrics(
                confidence_level=self.confidence_level,
                parametric_var_usd=0.0,
                parametric_var_pct=0.0,
                historical_var_usd=0.0,
                historical_var_pct=0.0,
                cvar_usd=0.0,
                cvar_pct=0.0,
                is_breached=False,
            )

        missing_prices = sorted(s for s in held if s not in prices)
        if missing_prices:
            raise VaRMonitorError(
                f"No price for held symbol(s) {missing_prices}. An unpriced position "
                f"cannot be dropped from a risk number -- its exposure is real."
            )
        missing_returns = sorted(s for s in held if s not in returns_dict)
        if missing_returns:
            raise VaRMonitorError(
                f"No return history for held symbol(s) {missing_returns}. A newly "
                f"listed or newly onboarded instrument carries risk the model cannot "
                f"see; supply a proxy series or exclude the position explicitly."
            )

        # 1. Signed position weights. Shorts are negative quantities, never negative
        #    prices, so sum |w_i| is gross exposure and sum w_i is net exposure.
        weights: Dict[str, float] = {}
        for s in held:
            price = self._finite(prices[s], f"Price for '{s}'")
            if price <= 0.0:
                raise VaRMonitorError(
                    f"Non-positive price {price} for '{s}'. Short exposure is expressed "
                    f"by a negative quantity, not a negative price; a negative price "
                    f"flips the sign of the weight and inverts the position's risk."
                )
            # The weight is derived, so it can overflow to +/-inf even when the
            # quantity, price and NAV are each individually finite (a fat-fingered
            # size against a near-zero NAV). Left unchecked it reaches fmean as an
            # inf and surfaces as a bare ValueError ("-inf + inf in fsum") rather
            # than a VaRMonitorError.
            weights[s] = self._finite(
                (float(positions[s]) * price) / nav,
                f"Position weight for '{s}' (quantity * price / NAV)",
            )

        gross_exposure_pct = sum(abs(w) for w in weights.values())
        net_exposure_pct = sum(weights.values())

        # 2. Reconstruct the historical portfolio return distribution.
        port_returns = self._build_portfolio_returns(weights, returns_dict)
        n_history = len(port_returns)

        # 3. Parametric (variance-covariance) VaR.
        mean_ret = statistics.fmean(port_returns)
        std_ret = statistics.stdev(port_returns)
        param_raw = self._z_score() * std_ret
        if self.subtract_mean_drift:
            param_raw -= mean_ret
        param_var_pct = max(param_raw, 0.0)
        param_var_usd = param_var_pct * nav

        # 4. Historical simulation VaR: the k-th worst observation, k = ceil(n(1-c)).
        sorted_returns = sorted(port_returns)          # ascending -> worst first
        k = self._tail_count(n_history, self.confidence_level)
        hist_var_pct = max(-sorted_returns[k - 1], 0.0)
        hist_var_usd = hist_var_pct * nav

        # 5. CVaR / Expected Shortfall: mean loss of those same k worst observations.
        cvar_pct = max(-statistics.fmean(sorted_returns[:k]), 0.0)
        cvar_usd = cvar_pct * nav

        # 6. Breach attribution -- which measure tripped, and by how much.
        measured = {
            "parametric_var": (param_var_pct, self.var_limit_pct),
            "historical_var": (hist_var_pct, self.var_limit_pct),
            "cvar": (cvar_pct, self.cvar_limit_pct),
        }
        breaching = [
            name for name, (value, limit) in measured.items()
            if limit is not None and value >= limit
        ]
        binding_var_pct = max((measured[n][0] for n in breaching), default=0.0)

        logger.info(
            "Live VaR (%.1f%%, n=%d, tail k=%d): parametric %.2f%% (%.2f), "
            "historical %.2f%% (%.2f), CVaR %.2f%% (%.2f), gross exposure %.2fx NAV "
            "| breached: %s",
            self.confidence_level * 100.0, n_history, k,
            param_var_pct * 100.0, param_var_usd,
            hist_var_pct * 100.0, hist_var_usd,
            cvar_pct * 100.0, cvar_usd,
            gross_exposure_pct,
            ",".join(breaching) if breaching else "none",
        )

        return VaRMetrics(
            confidence_level=self.confidence_level,
            parametric_var_usd=param_var_usd,
            parametric_var_pct=param_var_pct,
            historical_var_usd=hist_var_usd,
            historical_var_pct=hist_var_pct,
            cvar_usd=cvar_usd,
            cvar_pct=cvar_pct,
            is_breached=bool(breaching),
            breaching_measures=tuple(breaching),
            binding_var_pct=binding_var_pct,
            observations_used=n_history,
            tail_observations_used=k,
            gross_exposure_pct=gross_exposure_pct,
            net_exposure_pct=net_exposure_pct,
            portfolio_volatility_pct=std_ret,
            portfolio_mean_return_pct=mean_ret,
        )

    def evaluate_live_risk(
        self,
        positions: Mapping[str, float],
        prices: Mapping[str, float],
        returns_dict: Mapping[str, Sequence[float]],
        portfolio_nav: float,
        is_risk_reducing: bool = False,
    ) -> LiveRiskStatus:
        """
        Pre-trade verdict for one proposed order.

        A breach vetoes **risk-increasing** orders only. Pass
        ``is_risk_reducing=True`` for a close, a partial reduction or a hedge:
        blocking those is how a VaR breach becomes unrecoverable, because the trades
        that would cure the breach are exactly the ones the breaker refuses. The
        module cannot verify the claim -- it is the caller's assertion about the
        order, and it is logged so the override stays auditable.

        This module never cancels or submits anything; the caller enforces the
        verdict. It measures the *current* book, so it answers "is the book already
        over its budget", not "would this order put it over" -- fold the prospective
        fill into ``positions`` first if you need the latter.
        """
        metrics = self.compute_var_metrics(positions, prices, returns_dict, portfolio_nav)

        if not metrics.is_breached:
            return LiveRiskStatus(approved=True, var_metrics=metrics, breach_reason=None)

        limits = f"VaR limit {self.var_limit_pct:.2%}"
        if self.cvar_limit_pct is not None:
            limits += f", CVaR limit {self.cvar_limit_pct:.2%}"
        reason = (
            f"LIVE VAR BREACH! Breaching measure(s): "
            f"{', '.join(metrics.breaching_measures)}. Binding value "
            f"{metrics.binding_var_pct:.2%} of NAV against {limits} at "
            f"{self.confidence_level:.1%} confidence "
            f"(parametric {metrics.parametric_var_pct:.2%}, "
            f"historical {metrics.historical_var_pct:.2%}, "
            f"CVaR {metrics.cvar_pct:.2%}, gross exposure "
            f"{metrics.gross_exposure_pct:.2f}x NAV)."
        )

        if is_risk_reducing:
            logger.warning("%s Allowing order: caller asserts it reduces risk.", reason)
            return LiveRiskStatus(
                approved=True,
                var_metrics=metrics,
                breach_reason=reason,
                risk_reducing_override=True,
            )

        logger.warning("%s Blocking new risk-increasing position entries.", reason)
        return LiveRiskStatus(approved=False, var_metrics=metrics, breach_reason=reason)
