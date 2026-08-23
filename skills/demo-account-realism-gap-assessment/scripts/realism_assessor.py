"""
demo-account-realism-gap-assessment: Execution realism score calculator and demo
Sharpe ratio discount estimator comparing demo/paper trade logs with live executions.

Design principle: every guard in this module fails **closed** (toward a lower realism
score, or an outright error). A realism score gates how much live capital a
paper-tested strategy receives, so degenerate or missing data must never be scored as
"realistic parity" -- that would silently greenlight a full allocation.

The Sharpe discount applied here is a documented heuristic, not a validated estimator.
See ``references/standards.md`` for its basis and limitations.
"""
from dataclasses import dataclass
import logging
import math
import statistics
from typing import List, Sequence, Tuple

logger = logging.getLogger(__name__)

VALID_SIDES = ("BUY", "SELL")
DEMO_ENVIRONMENT = "DEMO"
LIVE_ENVIRONMENT = "LIVE"


@dataclass
class ExecutionLog:
    """A single matched execution record from either the demo or the live environment.

    Attributes:
        environment: ``"DEMO"`` or ``"LIVE"``. Validated against the list the log is
            passed in as, so demo/live logs cannot be silently transposed.
        arrival_price: Decision/arrival mid-price at order submission (Perold benchmark).
        fill_price: Achieved execution price.
        requested_qty: Quantity submitted; must be strictly positive.
        filled_qty: Quantity executed; must be within ``[0, requested_qty]``.
        submission_time: Order submission time, seconds (epoch or monotonic).
        fill_time: Fill time in the same clock as ``submission_time``; must not precede it.
        side: ``"BUY"`` or ``"SELL"``. Required, because execution slippage is only
            meaningful when signed relative to trade direction.
    """
    environment: str
    symbol: str
    arrival_price: float
    fill_price: float
    requested_qty: float
    filled_qty: float
    submission_time: float
    fill_time: float
    side: str

    @property
    def latency_ms(self) -> float:
        return (self.fill_time - self.submission_time) * 1000.0

    @property
    def slippage_bps(self) -> float:
        """Signed execution slippage in basis points, positive = adverse (cost).

        Follows the Perold implementation-shortfall sign convention: for a BUY,
        filling above arrival is a cost; for a SELL, filling below arrival is a cost.
        A negative value is genuine price improvement. Taking an absolute value here
        would let demo price improvement cancel live adverse cost and inflate the
        realism score -- exactly the distortion this skill exists to detect.
        """
        signed_diff = (
            self.fill_price - self.arrival_price
            if self.side == "BUY"
            else self.arrival_price - self.fill_price
        )
        return (signed_diff / self.arrival_price) * 10000.0

    @property
    def fill_rate(self) -> float:
        return self.filled_qty / self.requested_qty


@dataclass
class RealismAssessmentResult:
    mean_demo_latency_ms: float
    mean_live_latency_ms: float
    mean_demo_slippage_bps: float
    mean_live_slippage_bps: float
    demo_fill_rate: float
    live_fill_rate: float
    realism_score: float  # 0.0 (unrealistic) to 1.0 (perfect parity)
    adjusted_sharpe_ratio: float
    summary: str
    n_demo: int = 0
    n_live: int = 0
    is_sample_sufficient: bool = True
    meets_promotion_threshold: bool = False
    warnings: Tuple[str, ...] = ()


class DemoRealismAssessor:
    """
    Compares demo vs live execution metrics to compute a Realism Score R in [0, 1]
    and apply a heuristic Sharpe ratio discount.
    """

    def __init__(
        self,
        demo_logs: List[ExecutionLog],
        live_logs: List[ExecutionLog],
        slippage_decay_bps: float = 10.0,
        promotion_threshold: float = 0.75,
        min_samples: int = 30,
    ) -> None:
        """
        Args:
            slippage_decay_bps: Scale of the exponential slippage penalty. A live-vs-demo
                adverse slippage gap of this many bps scores ``exp(-1) ~= 0.368`` on the
                slippage component. This is a tuning parameter with no external basis --
                calibrate it to the strategy's own edge per trade.
            promotion_threshold: Realism score at or above which promotion is considered
                acceptable. Repo convention (see ``references/standards.md``), not a
                regulatory or vendor-mandated figure.
            min_samples: Below this many matched executions in either environment, the
                result is flagged as statistically insufficient. 30 is the usual
                central-limit rule of thumb, not a derived requirement.
        """
        if not demo_logs or not live_logs:
            raise ValueError("Both demo_logs and live_logs must contain execution entries.")
        if not math.isfinite(slippage_decay_bps) or slippage_decay_bps <= 0:
            raise ValueError("slippage_decay_bps must be a positive finite number.")
        if not 0.0 <= promotion_threshold <= 1.0:
            raise ValueError("promotion_threshold must lie in [0, 1].")
        if min_samples < 1:
            raise ValueError("min_samples must be at least 1.")

        self._validate_logs(demo_logs, DEMO_ENVIRONMENT)
        self._validate_logs(live_logs, LIVE_ENVIRONMENT)

        self.demo_logs = demo_logs
        self.live_logs = live_logs
        self.slippage_decay_bps = slippage_decay_bps
        self.promotion_threshold = promotion_threshold
        self.min_samples = min_samples

    @staticmethod
    def _validate_logs(logs: Sequence[ExecutionLog], expected_environment: str) -> None:
        """Rejects degenerate execution records instead of scoring them as parity.

        Every check here guards a path that previously produced a *higher* realism
        score from worse data (NaN slippage, zero arrival price, zero live latency).
        """
        for i, log in enumerate(logs):
            where = f"{expected_environment} log #{i} ({log.symbol})"

            if log.environment != expected_environment:
                raise ValueError(
                    f"{where}: environment is '{log.environment}' but was supplied as "
                    f"{expected_environment}. Demo and live logs must not be transposed."
                )
            if log.side not in VALID_SIDES:
                raise ValueError(f"{where}: side must be one of {VALID_SIDES}, got '{log.side}'.")

            for name in ("arrival_price", "fill_price", "requested_qty",
                         "filled_qty", "submission_time", "fill_time"):
                value = getattr(log, name)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"{where}: {name} must be numeric, got {type(value).__name__}.")
                if not math.isfinite(value):
                    raise ValueError(f"{where}: {name} must be finite, got {value}.")

            if log.arrival_price <= 0:
                raise ValueError(f"{where}: arrival_price must be positive, got {log.arrival_price}.")
            if log.fill_price <= 0:
                raise ValueError(f"{where}: fill_price must be positive, got {log.fill_price}.")
            if log.requested_qty <= 0:
                raise ValueError(f"{where}: requested_qty must be positive, got {log.requested_qty}.")
            if log.filled_qty < 0 or log.filled_qty > log.requested_qty:
                raise ValueError(
                    f"{where}: filled_qty {log.filled_qty} must lie within "
                    f"[0, requested_qty={log.requested_qty}]."
                )
            if log.fill_time < log.submission_time:
                raise ValueError(
                    f"{where}: fill_time {log.fill_time} precedes submission_time "
                    f"{log.submission_time}."
                )

    def assess_realism(self, unadjusted_demo_sharpe: float) -> RealismAssessmentResult:
        """Calculates latency gap, slippage discrepancy, realism score, and adjusted Sharpe.

        Args:
            unadjusted_demo_sharpe: Sharpe ratio observed in the demo/paper environment.
                Required -- there is no defensible default, since a fabricated value
                produces a fabricated allocation recommendation.
        """
        if not isinstance(unadjusted_demo_sharpe, (int, float)) or isinstance(unadjusted_demo_sharpe, bool):
            raise ValueError("unadjusted_demo_sharpe must be numeric.")
        if not math.isfinite(unadjusted_demo_sharpe):
            raise ValueError("unadjusted_demo_sharpe must be finite.")

        warnings: List[str] = []

        demo_lat = statistics.mean([l.latency_ms for l in self.demo_logs])
        live_lat = statistics.mean([l.latency_ms for l in self.live_logs])

        demo_slip = statistics.mean([l.slippage_bps for l in self.demo_logs])
        live_slip = statistics.mean([l.slippage_bps for l in self.live_logs])

        demo_fr = statistics.mean([l.fill_rate for l in self.demo_logs])
        live_fr = statistics.mean([l.fill_rate for l in self.live_logs])

        # 1. Latency Score (0 to 1). Demo latency at or above live latency is
        #    conservative and scores 1.0; instant demo fills score ~0.
        if live_lat <= 0:
            raise ValueError(
                f"Mean live latency is {live_lat:.3f}ms; live executions cannot be "
                "instantaneous. Check fill/submission timestamps before scoring."
            )
        lat_score = min(1.0, max(0.0, demo_lat / live_lat))

        # 2. Slippage Score (0 to 1). Uses SIGNED slippage, so demo price improvement
        #    widens rather than cancels the gap against live adverse cost.
        slip_diff_bps = max(0.0, live_slip - demo_slip)
        slip_score = math.exp(-slip_diff_bps / self.slippage_decay_bps)

        # 3. Fill Rate Score (0 to 1).
        if demo_fr <= 0:
            raise ValueError(
                "Mean demo fill rate is 0; there are no demo fills to compare against."
            )
        fr_score = min(1.0, max(0.0, live_fr / demo_fr))

        # Composite Realism Score: 30% Latency + 40% Slippage + 30% Fill Rate
        realism_score = max(0.0, min(1.0, 0.30 * lat_score + 0.40 * slip_score + 0.30 * fr_score))

        # A discount can only reduce a positive Sharpe. Scaling a NEGATIVE Sharpe by
        # R < 1 would move it toward zero, making a losing demo strategy look better
        # the less realistic its execution was.
        if unadjusted_demo_sharpe > 0:
            adjusted_sharpe = unadjusted_demo_sharpe * realism_score
        else:
            adjusted_sharpe = unadjusted_demo_sharpe
            warnings.append(
                f"Demo Sharpe {unadjusted_demo_sharpe:.2f} is not positive; the realism "
                "discount was not applied. An execution-fidelity discount cannot "
                "rehabilitate a strategy that already loses money on paper."
            )

        # The comparison assumes matched instruments; differing symbol sets mean the
        # score partly measures instrument differences rather than environment ones.
        demo_symbols = {l.symbol for l in self.demo_logs}
        live_symbols = {l.symbol for l in self.live_logs}
        if demo_symbols != live_symbols:
            warnings.append(
                f"Symbol sets differ between environments (demo={sorted(demo_symbols)}, "
                f"live={sorted(live_symbols)}); the score partly reflects instrument "
                "differences rather than execution-environment differences."
            )

        n_demo, n_live = len(self.demo_logs), len(self.live_logs)
        is_sample_sufficient = n_demo >= self.min_samples and n_live >= self.min_samples
        if not is_sample_sufficient:
            warnings.append(
                f"Sample size below min_samples={self.min_samples} "
                f"(demo n={n_demo}, live n={n_live}); the realism score is indicative "
                "only and should not gate a capital allocation on its own."
            )

        meets_threshold = realism_score >= self.promotion_threshold

        summary = (
            f"Execution Realism Score: {realism_score:.2f}/1.00 | "
            f"Live Latency: {live_lat:.1f}ms (Demo: {demo_lat:.1f}ms) | "
            f"Live Slippage: {live_slip:.1f}bps (Demo: {demo_slip:.1f}bps) | "
            f"Adjusted Sharpe: {adjusted_sharpe:.2f} (Demo: {unadjusted_demo_sharpe:.2f})"
        )
        logger.info(summary)
        for message in warnings:
            logger.warning(message)

        return RealismAssessmentResult(
            mean_demo_latency_ms=demo_lat,
            mean_live_latency_ms=live_lat,
            mean_demo_slippage_bps=demo_slip,
            mean_live_slippage_bps=live_slip,
            demo_fill_rate=demo_fr,
            live_fill_rate=live_fr,
            realism_score=realism_score,
            adjusted_sharpe_ratio=adjusted_sharpe,
            summary=summary,
            n_demo=n_demo,
            n_live=n_live,
            is_sample_sufficient=is_sample_sufficient,
            meets_promotion_threshold=meets_threshold,
            warnings=tuple(warnings),
        )
