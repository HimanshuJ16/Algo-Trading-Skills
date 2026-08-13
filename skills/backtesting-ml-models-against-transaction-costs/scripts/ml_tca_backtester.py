"""
backtesting-ml-models-against-transaction-costs: turnover-aware net-of-cost
evaluation of continuous ML signals.

Cost convention
---------------
`bps_cost_half_turn` is charged per *unit of turnover*, where turnover is the
absolute change in target position measured in units of |position| = 1:

    flat -> long          = 1 unit  = 1 half-turn
    long -> flat          = 1 unit  = 1 half-turn
    long -> short (flip)  = 2 units = 2 half-turns
    round trip (in+out)   = 2 units = 2 half-turns

so the breakeven hurdle for a round trip is `2 * bps_cost_half_turn / 10_000`
expressed as a decimal return (the default config satisfies this exactly:
5 bps -> 0.001).

Alignment contract (look-ahead safety)
--------------------------------------
`actual_returns[i]` MUST be the return realised *after* `predictions[i]` was
observable — i.e. the caller aligns the forward return before calling. This
module cannot detect a mis-aligned series; passing the contemporaneous return
produces a look-ahead-biased backtest that looks excellent and is worthless.
See `lookahead-bias-elimination`.
"""
from dataclasses import dataclass
import logging
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MlTcaBacktesterConfig:
    """
    Immutable so the invariants checked in `__post_init__` cannot be bypassed by
    reassigning a field after construction (which would also leave the cost rate
    cached on the backtester silently stale).

    :param bps_cost_half_turn: Round-trip-halves cost in basis points, charged per
        unit of turnover (slippage + fees + commission). Must be >= 0.
    :param signal_threshold: Minimum |prediction| required to *enter* a position,
        in the same units as `predictions` (decimal return for a return model).
        Must be > 0 — a zero or negative entry threshold has no economically
        meaningful interpretation and would make long/short regions overlap.
    :param exit_threshold: Optional buy/hold spread ("hysteresis"). When set, an
        open position is held until |prediction| drops below this value, instead
        of being closed as soon as it drops below `signal_threshold`. Must satisfy
        0 <= exit_threshold <= signal_threshold. `None` (default) preserves the
        stateless single-threshold rule; setting it equal to `signal_threshold`
        is numerically identical to `None`.
    :param liquidate_at_end: Charge the exit half-turn on any position still open
        on the final bar. Default True — leaving it False understates cost for
        any strategy that finishes with an open position.
    """

    bps_cost_half_turn: float = 5.0
    signal_threshold: float = 0.001
    exit_threshold: Optional[float] = None
    liquidate_at_end: bool = True

    def __post_init__(self) -> None:
        if not np.isfinite(self.bps_cost_half_turn) or self.bps_cost_half_turn < 0.0:
            raise ValueError(
                f"bps_cost_half_turn must be a finite value >= 0, got {self.bps_cost_half_turn!r}. "
                "A negative cost would credit the strategy for trading."
            )
        if not np.isfinite(self.signal_threshold) or self.signal_threshold <= 0.0:
            raise ValueError(
                f"signal_threshold must be a finite value > 0, got {self.signal_threshold!r}. "
                "With a non-positive threshold the long and short regions overlap."
            )
        if self.exit_threshold is not None:
            if not np.isfinite(self.exit_threshold) or self.exit_threshold < 0.0:
                raise ValueError(
                    f"exit_threshold must be a finite value >= 0, got {self.exit_threshold!r}."
                )
            if self.exit_threshold > self.signal_threshold:
                raise ValueError(
                    f"exit_threshold ({self.exit_threshold}) must not exceed signal_threshold "
                    f"({self.signal_threshold}); a wider exit than entry band would churn positions."
                )


class MlTcaBacktester:
    """
    Evaluates Machine Learning signals against transaction costs.
    Calculates position turnover, applies cost drag, and outputs gross vs net performance.
    """

    def __init__(self, config: MlTcaBacktesterConfig):
        self.config = config
        self.cost_rate = self.config.bps_cost_half_turn / 10000.0

    @staticmethod
    def _as_clean_1d(array: Any, label: str) -> np.ndarray:
        """Coerce array-like to a finite 1-D float array, or raise ValueError."""
        try:
            values = np.asarray(array, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be numeric array-like: {exc}") from exc
        if values.ndim != 1:
            raise ValueError(f"{label} must be 1-dimensional, got shape {values.shape}.")
        if values.size and not np.all(np.isfinite(values)):
            bad = int(np.count_nonzero(~np.isfinite(values)))
            raise ValueError(
                f"{label} contains {bad} NaN/Inf value(s). Drop or impute them before "
                "backtesting — silently treating NaN predictions as flat, or compounding "
                "NaN returns, produces meaningless performance metrics."
            )
        return values

    def _target_positions(self, predictions: np.ndarray) -> np.ndarray:
        """Map predictions to target positions in {-1, 0, +1}."""
        entry = self.config.signal_threshold
        exit_level = self.config.exit_threshold

        if exit_level is None or exit_level == entry:
            # Stateless rule. Mutually exclusive because entry > 0 is enforced.
            return np.where(
                predictions >= entry, 1.0, np.where(predictions <= -entry, -1.0, 0.0)
            )

        # Buy/hold spread: hold an open position while |prediction| >= exit_level.
        # Sequential by construction; the vectorised branch above covers the
        # default (and far more common) path.
        positions = np.zeros(predictions.shape[0], dtype=float)
        state = 0.0
        for i, pred in enumerate(predictions):
            if pred >= entry:
                state = 1.0
            elif pred <= -entry:
                state = -1.0
            elif state > 0.0 and pred < exit_level:
                state = 0.0
            elif state < 0.0 and pred > -exit_level:
                state = 0.0
            positions[i] = state
        return positions

    @staticmethod
    def _compound(period_returns: np.ndarray) -> float:
        """
        Geometrically compound a series of period returns.

        Returns -1.0 (total loss) if any period wipes out capital, rather than
        letting np.prod silently sign-flip through negative wealth.
        """
        if period_returns.size == 0:
            return 0.0
        if np.any(period_returns <= -1.0):
            logger.warning("Capital fully eroded within the sample; reporting -100% total return.")
            return -1.0
        return float(np.expm1(np.sum(np.log1p(period_returns))))

    def process(self, predictions: Any, actual_returns: Any) -> Dict[str, Any]:
        """
        :param predictions: 1-D array-like of ML predictions observable at time t.
        :param actual_returns: 1-D array-like of the returns realised *after* each
            prediction (see the module docstring's alignment contract). Element i
            must be the return earned by acting on `predictions[i]`.
        :return: Dictionary containing gross and net performance metrics.
        :raises ValueError: on length mismatch, non-1-D input, or NaN/Inf values.
        """
        predictions = self._as_clean_1d(predictions, "predictions")
        actual_returns = self._as_clean_1d(actual_returns, "actual_returns")

        if predictions.shape[0] != actual_returns.shape[0]:
            raise ValueError(
                f"Predictions and actual_returns must have the same length "
                f"({predictions.shape[0]} vs {actual_returns.shape[0]})."
            )

        n_periods = predictions.shape[0]

        # 1. Thresholding: target positions (+1 Long, -1 Short, 0 Flat)
        positions = self._target_positions(predictions)

        # 2. Gross returns: position held over period i earns actual_returns[i]
        gross_returns = positions * actual_returns

        # 3. Turnover. Compare with the prior period's position; the book starts flat.
        pos_shifted = np.empty(n_periods, dtype=float)
        if n_periods:
            pos_shifted[0] = 0.0
            pos_shifted[1:] = positions[:-1]
        turnover = np.abs(positions - pos_shifted)

        # Close any position still open on the final bar; without this the exit
        # half-turn of the last trade is never paid and net returns are overstated.
        if self.config.liquidate_at_end and n_periods:
            turnover[-1] += abs(positions[-1])

        costs = turnover * self.cost_rate

        # 4. Net returns
        net_returns = gross_returns - costs

        cum_gross = self._compound(gross_returns)
        cum_net = self._compound(net_returns)
        total_turnover = float(np.sum(turnover))
        total_trades = int(np.count_nonzero(turnover))

        logger.info(
            "ML TCA backtest: %d periods | turnover=%.2f units | gross=%.4f -> net=%.4f "
            "(cost paid=%.4f at %.1f bps/half-turn)",
            n_periods, total_turnover, cum_gross, cum_net,
            float(np.sum(costs)), self.config.bps_cost_half_turn,
        )

        return {
            "Total Gross Return": cum_gross,
            "Total Net Return": cum_net,
            "Total Turnover (Units)": total_turnover,
            "Total Trade Count": total_trades,
            "Total Cost Paid": float(np.sum(costs)),
            "Cost Drag (%)": float((cum_gross - cum_net) * 100),
            "Net Returns Series": net_returns.tolist(),
            "Gross Returns Series": gross_returns.tolist(),
            "Positions Series": positions.tolist(),
        }
