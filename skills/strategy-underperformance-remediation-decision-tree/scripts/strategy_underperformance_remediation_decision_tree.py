"""
strategy-underperformance-remediation-decision-tree: quantitative triage adjudicator.

When a live strategy underperforms, four incompatible causes produce the same
symptom — a fallen Sharpe ratio — and each has a different, mutually exclusive
remedy. Recalibrating parameters when the real problem is a stale data feed fits
the model to noise; decommissioning during a market-wide regime shift destroys
capacity that cannot be rebuilt. This module applies a fixed, pre-declared triage
order to a metrics payload and returns an auditable routing decision:

    Node 1  Alpha hypothesis invalid          -> MANDATORY_STRATEGY_DECOMMISSION
    Node 2  Data feed broken or slippage      -> OPTIMIZE_EXECUTION_AND_DATA
            eating > 50% of expected alpha
    Node 2a Live history too short to judge   -> EXTEND_OBSERVATION_INSUFFICIENT_HISTORY
    Node 3  Strategy AND peer group impaired  -> TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL
    Node 4  Strategy impaired, peers healthy  -> RECALIBRATE_MODEL_PARAMETERS
    Healthy Strategy at or above mandate      -> MAINTAIN_TRADING

The engine measures nothing and executes nothing. It **adjudicates**: the caller
supplies already-computed statistics, and the engine applies the declared order and
produces a record of which nodes were evaluated, which cleared, and which fired.

Why this order, and not another
-------------------------------
Every node is terminal, so the order *is* the policy:

- **Node 1 first** because a dead economic hypothesis makes every downstream metric
  irrelevant: there is nothing to recalibrate and no execution fix that restores an
  edge that no longer exists.
- **Node 2 before Nodes 3 and 4** because a broken data feed or an execution cost that
  swallows the alpha invalidates the Sharpe ratios that Nodes 3 and 4 compare. Tuning
  signal parameters against fills that were destroyed by slippage fits the model to the
  execution defect.
- **Node 3 before Node 4** because joint impairment is the more conservative reading of
  the same evidence. When the peer group is down too, the data does not support an
  idiosyncratic verdict, and cutting capital while retaining the signal is reversible
  where a recalibration (which overwrites the parameter set) is not.

Caller conventions the engine cannot verify
-------------------------------------------
- ``is_alpha_hypothesis_valid`` is a **human research judgment about the economic
  mechanism** — has the inefficiency been arbitraged away, has the market structure
  that created it changed — and NOT a re-reading of live P&L. Deriving it from recent
  returns makes Node 1 a Sharpe threshold with an irreversible consequence, and it will
  fire during exactly the regime shifts Node 3 exists to survive.
- ``realized_slippage_bps`` is a **positive cost magnitude in basis points**. A signed
  convention (cost as a negative number) makes the ratio negative, so it never exceeds
  the limit and Node 2 is silently disarmed. Pass ``0.0`` for net price improvement.
- ``realized_slippage_bps`` and ``expected_alpha_bps`` must be measured **over the same
  horizon and the same unit of activity** — both per round trip, or both per unit time.
  Comparing per-trade slippage against annualized alpha understates the ratio by
  roughly the number of trades per year and defeats Node 2 entirely.
- ``live_sharpe``, ``backtest_sharpe`` and ``peer_benchmark_sharpe`` must share an
  annualization convention and a return frequency, or the comparisons are meaningless.
- ``peer_benchmark_sharpe`` must be a **defensible peer group** for this strategy.
  Benchmarking a market-neutral book against a long-only index manufactures a Node 4
  "parameter drift" verdict every time the index rallies. Selecting the benchmark is
  `benchmark-selection-for-strategy-evaluation`; this engine only compares against the
  number supplied.

Statistical limitation
----------------------
The routing thresholds are Sharpe ratio comparisons, and a Sharpe ratio estimated over
a short live window is dominated by estimation error. Lo (2002), "The Statistics of
Sharpe Ratios", Financial Analysts Journal 58(4), gives the asymptotic standard error
under IID returns as SE(SR) = sqrt((1 + SR^2 / 2) / T), with the IID time-aggregation
rule SR(q) = sqrt(q) * SR (Eq. 17-18). Composing them for an annualized Sharpe built
from T observations at q periods per year:

    SE(SR_annual) = sqrt((q + SR_annual^2 / 2) / T)

At T = 60 daily observations and a threshold of 1.0 that standard error is ~2.05 —
larger than the threshold being tested. A strategy measured at 0.6 against a 1.0
mandate is therefore not *shown* to be underperforming; it is merely observed below.
Supply ``live_observation_count`` and the report carries ``sharpe_standard_error`` and
``sharpe_evidence_conclusive`` so a committee reading the report can see which of the
two it is. Set ``min_live_observations`` to refuse to route on a window that is too
short at all, which returns ``EXTEND_OBSERVATION_INSUFFICIENT_HISTORY`` rather than a
confident-looking verdict.

For a hypothesis test of the strategy-versus-peer difference with a defined null
distribution, rather than the two independent threshold comparisons used here, see
`strategy-performance-decay-detection-vs-market-wide-decay`.

Scope
-----
This module produces a *recommendation*, not an action. It does not cancel orders,
liquidate positions, halt a strategy, change an allocation, or re-optimize anything;
``MANDATORY_STRATEGY_DECOMMISSION`` sets a field on a dataclass. Unwinding is
`strategy-decommissioning-and-position-unwind-procedure`; halting is
`kill-switch-and-drawdown-circuit-breakers`, which must remain structurally
independent of strategy logic.

Acting on the recommendation is a governed event. Under MiFID II RTS 6
(Commission Delegated Regulation (EU) 2017/589) Article 5, deployment or substantial
update of an algorithmic trading system, trading algorithm or algorithmic trading
strategy must be authorised by a person designated by senior management, and ESMA's
February 2026 Supervisory Briefing on Algorithmic Trading in the EU (non-binding,
issued under Article 29(2) of the ESMA Regulation) defines a material change as any
modification that may alter the behaviour, risk profile or compliance posture of a
strategy, requires firms to timestamp, approve and record all material changes, and
warns at paragraph 30 that "a series of minor or small changes due to recalibrations
could accumulate over time, when uncontrolled or unchecked, into a material change in
the model output without it being tested". A remediation engine that emits
``RECALIBRATE_MODEL_PARAMETERS`` on a monthly cadence is precisely the mechanism that
briefing describes, so each recalibration must be recorded and retested individually.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Observations per year for daily returns, used for Sharpe standard errors.
TRADING_DAYS_PER_YEAR = 252.0

#: Two-sided 95% normal critical value, used for the Sharpe conclusiveness check.
Z_95 = 1.959963984540054


class RemediationAction(str, Enum):
    """Terminal outcome of one triage evaluation."""

    MAINTAIN_TRADING = "MAINTAIN_TRADING"
    RECALIBRATE_MODEL_PARAMETERS = "RECALIBRATE_MODEL_PARAMETERS"
    OPTIMIZE_EXECUTION_AND_DATA = "OPTIMIZE_EXECUTION_AND_DATA"
    TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL = "TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL"
    MANDATORY_STRATEGY_DECOMMISSION = "MANDATORY_STRATEGY_DECOMMISSION"
    #: Live window too short for the Sharpe-based nodes to mean anything. Emitted only
    #: when ``min_live_observations`` is configured; it is an explicit refusal to route,
    #: not a verdict of health.
    EXTEND_OBSERVATION_INSUFFICIENT_HISTORY = "EXTEND_OBSERVATION_INSUFFICIENT_HISTORY"


def _require_finite(value: float, name: str) -> float:
    """
    Coerce ``value`` to ``float`` and reject non-finite input.

    Every node in this tree is a threshold comparison, and **every comparison against
    ``NaN`` is False**. A ``NaN`` ``live_sharpe`` therefore fails ``live_sharpe <
    min_sharpe`` at Nodes 3 and 4 and falls through to the healthy branch, so a totally
    corrupt payload returns ``MAINTAIN_TRADING`` — "no remediation required" — which is
    the most permissive verdict the engine can produce. Reject instead of comparing.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number, got {value!r}") from exc
    if not math.isfinite(numeric):
        raise ValueError(
            f"{name} must be a finite number, got {value!r}. Non-finite values defeat "
            f"every threshold comparison in this tree; a NaN Sharpe falls through to "
            f"MAINTAIN_TRADING instead of triggering remediation."
        )
    return numeric


def _require_strict_bool(value: bool, name: str) -> bool:
    """
    Reject anything that is not a genuine ``bool``.

    Both flags are read for truthiness, and the strings ``"False"`` and ``"false"`` are
    truthy. An agent or a JSON payload that passes ``is_alpha_hypothesis_valid="False"``
    would clear Node 1 and keep a strategy with a dead hypothesis trading.
    """
    if not isinstance(value, bool):
        raise ValueError(
            f"{name} must be a bool, got {value!r} ({type(value).__name__}). Non-bool "
            f"values are read for truthiness, and the string 'False' is truthy."
        )
    return value


def annualized_sharpe_standard_error(
    sharpe_annualized: float,
    n_observations: int,
    periods_per_year: float = TRADING_DAYS_PER_YEAR,
) -> float:
    """
    Asymptotic standard error of an *annualized* Sharpe ratio estimate under IID returns.

    Lo (2002), Financial Analysts Journal 58(4), gives the per-period result
    SE(SR) = sqrt((1 + SR^2 / 2) / T) and the IID time-aggregation rule
    SR(q) = sqrt(q) * SR (Eq. 17), whose asymptotic distribution scales by the same
    factor (Eq. 18). Substituting SR_period = SR_annual / sqrt(q) and multiplying the
    per-period standard error by sqrt(q) collapses to:

        SE(SR_annual) = sqrt((q + SR_annual^2 / 2) / T)

    Passing ``periods_per_year=1`` recovers Lo's per-period Table 1 directly.

    This is an IID result. Under serial correlation — routine in strategy returns, and
    severe for smoothed or illiquid marks — it understates the true standard error, so
    treat it as a floor on the estimation error, not a bound.

    Args:
        sharpe_annualized: Annualized Sharpe ratio estimate.
        n_observations: Number of return observations T behind the estimate.
        periods_per_year: Observations per year q (252 for daily returns).

    Returns:
        The standard error, in annualized Sharpe units.

    Raises:
        ValueError: If inputs are non-finite, or T < 1, or q <= 0.
    """
    sharpe = _require_finite(sharpe_annualized, "sharpe_annualized")
    q = _require_finite(periods_per_year, "periods_per_year")
    if q <= 0.0:
        raise ValueError(f"periods_per_year must be > 0, got {periods_per_year!r}")
    if isinstance(n_observations, bool) or not isinstance(n_observations, int):
        raise ValueError(f"n_observations must be an int, got {n_observations!r}")
    if n_observations < 1:
        raise ValueError(f"n_observations must be >= 1, got {n_observations!r}")
    return math.sqrt((q + sharpe * sharpe / 2.0) / float(n_observations))


@dataclass
class UnderperformanceTriageMetrics:
    """
    One strategy's already-computed triage inputs at one evaluation point.

    All three Sharpe ratios must share an annualization convention and return
    frequency. ``realized_slippage_bps`` and ``expected_alpha_bps`` must share a
    horizon and a unit of activity. See the module docstring for the full set of
    caller conventions — the engine can validate ranges, never conventions.
    """

    strategy_id: str
    live_sharpe: float
    backtest_sharpe: float                # context only; no node reads it
    peer_benchmark_sharpe: float
    realized_slippage_bps: float          # POSITIVE cost magnitude in bps, e.g. 15.0
    expected_alpha_bps: float             # strictly positive; same horizon as slippage
    is_data_feed_healthy: bool
    is_alpha_hypothesis_valid: bool       # human judgment on the economic mechanism
    #: Number of live return observations behind ``live_sharpe``. Optional, but without
    #: it no sample-size gate exists and the report cannot say whether the Sharpe
    #: evidence is conclusive.
    live_observation_count: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError(
                f"strategy_id must be a non-empty string, got {self.strategy_id!r}. "
                f"An unattributable remediation record cannot be audited."
            )

        self.live_sharpe = _require_finite(self.live_sharpe, "live_sharpe")
        self.backtest_sharpe = _require_finite(self.backtest_sharpe, "backtest_sharpe")
        self.peer_benchmark_sharpe = _require_finite(
            self.peer_benchmark_sharpe, "peer_benchmark_sharpe")

        self.realized_slippage_bps = _require_finite(
            self.realized_slippage_bps, "realized_slippage_bps")
        if self.realized_slippage_bps < 0.0:
            raise ValueError(
                f"realized_slippage_bps must be a positive cost magnitude in basis "
                f"points, got {self.realized_slippage_bps!r}. A signed cost convention "
                f"makes the slippage-to-alpha ratio negative, so it never exceeds the "
                f"limit and Node 2 is silently disarmed. Pass 0.0 for net price "
                f"improvement."
            )

        self.expected_alpha_bps = _require_finite(
            self.expected_alpha_bps, "expected_alpha_bps")
        if self.expected_alpha_bps <= 0.0:
            raise ValueError(
                f"expected_alpha_bps must be > 0, got {self.expected_alpha_bps!r}. The "
                f"slippage-to-alpha ratio is undefined at zero and sign-inverted below "
                f"it, so Node 2 would clear regardless of how large the slippage is. A "
                f"strategy with no expected alpha has no edge to protect: that is a "
                f"Node 1 hypothesis question, not a Node 2 execution question."
            )

        self.is_data_feed_healthy = _require_strict_bool(
            self.is_data_feed_healthy, "is_data_feed_healthy")
        self.is_alpha_hypothesis_valid = _require_strict_bool(
            self.is_alpha_hypothesis_valid, "is_alpha_hypothesis_valid")

        if self.live_observation_count is not None:
            if (isinstance(self.live_observation_count, bool)
                    or not isinstance(self.live_observation_count, int)):
                raise ValueError(
                    f"live_observation_count must be an int or None, got "
                    f"{self.live_observation_count!r}"
                )
            if self.live_observation_count < 1:
                raise ValueError(
                    f"live_observation_count must be >= 1, got "
                    f"{self.live_observation_count!r}"
                )


@dataclass
class StrategyRemediationReport:
    """
    Auditable outcome of one triage evaluation.

    ``is_capital_reduced`` and ``is_decommissioned`` describe what the *recommendation*
    calls for. Nothing in this module reduces capital or decommissions anything.
    """

    strategy_id: str
    recommended_action: RemediationAction
    #: Ordered audit trail of every node evaluated, cleared nodes included, with the
    #: decisive node last. A cleared node recorded here is evidence it was tested;
    #: absence means the node was never reached.
    triage_path: List[str]
    is_capital_reduced: bool
    is_decommissioned: bool
    remediation_instructions: str
    audit_notes: str
    #: Identifier of the node that determined the outcome, e.g. "NODE_2_EXECUTION".
    decisive_node: str = ""
    #: ``realized_slippage_bps / expected_alpha_bps``. Always defined: the constructor
    #: rejects a non-positive denominator.
    slippage_to_alpha_ratio: float = 0.0
    #: ``live_sharpe - peer_benchmark_sharpe``. A plain difference, not a ratio, so it
    #: stays defined when a benchmark Sharpe is near zero or negative.
    sharpe_gap_vs_peer: float = 0.0
    #: ``live_sharpe - backtest_sharpe``. Context for the committee; no node reads it.
    sharpe_gap_vs_backtest: float = 0.0
    #: Standard error of ``live_sharpe`` (Lo 2002). ``None`` when
    #: ``live_observation_count`` was not supplied.
    sharpe_standard_error: Optional[float] = None
    #: True only when ``live_sharpe`` sits more than 1.96 standard errors away from
    #: ``min_healthy_sharpe``, i.e. the routing between "impaired" and "healthy" is
    #: supported at 95%. ``False`` whenever the sample size is unknown.
    sharpe_evidence_conclusive: bool = False
    #: Caveats that do not change the routing but change how it should be read.
    #: An empty tuple means no caveats applied.
    warnings: Tuple[str, ...] = field(default_factory=tuple)


class StrategyUnderperformanceRemediationEngine:
    """
    Quantitative triage adjudicator separating execution failure, market-wide regime
    shift, parameter drift, and structural alpha decay as causes of underperformance.

    Stateless, deterministic, and side-effect free beyond logging: the same metrics
    always yield the same report, and the engine holds policy rather than strategy
    state, so one instance may serve a whole book.

    Args:
        min_healthy_sharpe: Live Sharpe at or above which a strategy meets its mandate.
            House default, not an external standard.
        max_slippage_alpha_ratio: Fraction of expected alpha that realized slippage may
            consume before Node 2 fires. Compared strictly (``>``), so a ratio exactly
            at the limit clears.
        min_peer_sharpe: Peer benchmark Sharpe at or above which the peer group counts
            as healthy, discriminating Node 3 (joint impairment) from Node 4
            (idiosyncratic). House default, not an external standard.
        min_live_observations: If set, a live window shorter than this returns
            ``EXTEND_OBSERVATION_INSUFFICIENT_HISTORY`` rather than a Sharpe-based
            verdict, and a payload with no ``live_observation_count`` at all is
            rejected. ``None`` (the default) disables the gate entirely.
        periods_per_year: Return frequency behind the Sharpe ratios, used only for the
            standard-error annotation (252 for daily returns).

    Raises:
        ValueError: On a non-finite or out-of-range policy threshold.
    """

    def __init__(
        self,
        min_healthy_sharpe: float = 1.0,
        max_slippage_alpha_ratio: float = 0.50,  # slippage eating > 50% of alpha
        min_peer_sharpe: float = 0.50,
        *,
        min_live_observations: Optional[int] = None,
        periods_per_year: float = TRADING_DAYS_PER_YEAR,
    ) -> None:
        self.min_sharpe = _require_finite(min_healthy_sharpe, "min_healthy_sharpe")

        self.max_slippage_ratio = _require_finite(
            max_slippage_alpha_ratio, "max_slippage_alpha_ratio")
        if self.max_slippage_ratio <= 0.0:
            raise ValueError(
                f"max_slippage_alpha_ratio must be > 0, got {max_slippage_alpha_ratio!r}"
            )

        self.min_peer_sharpe = _require_finite(min_peer_sharpe, "min_peer_sharpe")
        if self.min_peer_sharpe > self.min_sharpe:
            raise ValueError(
                f"min_peer_sharpe ({self.min_peer_sharpe}) must not exceed "
                f"min_healthy_sharpe ({self.min_sharpe}); otherwise a strategy can be "
                f"routed to Node 4 for 'underperforming healthy peers' whose benchmark "
                f"bar is stricter than its own mandate."
            )

        if min_live_observations is not None:
            if isinstance(min_live_observations, bool) or not isinstance(
                    min_live_observations, int):
                raise ValueError(
                    f"min_live_observations must be an int or None, got "
                    f"{min_live_observations!r}"
                )
            if min_live_observations < 1:
                raise ValueError(
                    f"min_live_observations must be >= 1, got {min_live_observations!r}"
                )
        self.min_live_observations = min_live_observations

        self.periods_per_year = _require_finite(periods_per_year, "periods_per_year")
        if self.periods_per_year <= 0.0:
            raise ValueError(f"periods_per_year must be > 0, got {periods_per_year!r}")

    def evaluate_remediation(
        self, metrics: UnderperformanceTriageMetrics
    ) -> StrategyRemediationReport:
        """
        Route one strategy through the triage tree and return an auditable report.

        Nodes resolve in the fixed order documented on the module, and every branch is
        terminal. See the module docstring for why that order is the one that is safe.

        Args:
            metrics: Validated triage inputs. Not mutated.

        Returns:
            The remediation report, including the cleared nodes that were tested on the
            way to the decisive one.

        Raises:
            ValueError: If ``metrics`` is not an ``UnderperformanceTriageMetrics``, or
                if the sample-size gate is configured and the payload carries no
                ``live_observation_count``.
        """
        if not isinstance(metrics, UnderperformanceTriageMetrics):
            raise ValueError(
                f"metrics must be an UnderperformanceTriageMetrics, got "
                f"{type(metrics).__name__}"
            )

        path: List[str] = []
        warnings: List[str] = []

        slippage_ratio = metrics.realized_slippage_bps / metrics.expected_alpha_bps
        gap_vs_peer = metrics.live_sharpe - metrics.peer_benchmark_sharpe
        gap_vs_backtest = metrics.live_sharpe - metrics.backtest_sharpe

        sharpe_se: Optional[float] = None
        sharpe_conclusive = False
        if metrics.live_observation_count is not None:
            sharpe_se = annualized_sharpe_standard_error(
                metrics.live_sharpe,
                metrics.live_observation_count,
                periods_per_year=self.periods_per_year,
            )
            sharpe_conclusive = (
                abs(metrics.live_sharpe - self.min_sharpe) > Z_95 * sharpe_se
            )

        # Node 1: fundamental hypothesis failure. Irreversible, so it outranks
        # everything: there is nothing to recalibrate and no execution fix for an edge
        # that no longer exists.
        if not metrics.is_alpha_hypothesis_valid:
            node = "NODE_1_HYPOTHESIS_FAILURE"
            path.append(
                f"{node}: economic edge declared no longer valid by research review.")
            action = RemediationAction.MANDATORY_STRATEGY_DECOMMISSION
            instructions = (
                "DECOMMISSION_IMMEDIATELY: cancel open orders, liquidate positions, and "
                "retire the strategy codebase.")
            cap_reduced = True
            decommissioned = True
            if not metrics.is_data_feed_healthy:
                warnings.append(
                    "Hypothesis declared invalid while the data feed is unhealthy. "
                    "Confirm the judgment rests on the economic mechanism and not on "
                    "metrics derived from a broken feed: decommissioning is "
                    "irreversible, a feed repair is not."
                )
            if metrics.peer_benchmark_sharpe < self.min_peer_sharpe:
                warnings.append(
                    f"Hypothesis declared invalid while the peer group is also impaired "
                    f"(peer Sharpe {metrics.peer_benchmark_sharpe:.4f} < "
                    f"{self.min_peer_sharpe:.4f}). Node 3 exists because a regime shift "
                    f"looks like alpha decay; verify the judgment is not a restatement "
                    f"of recent returns."
                )
        else:
            path.append("NODE_1_HYPOTHESIS_VALID: economic edge affirmed; continuing.")

            # Node 2: data or execution dysfunction. Must precede the Sharpe nodes —
            # a broken feed or an alpha-consuming cost invalidates the very ratios
            # Nodes 3 and 4 compare.
            if not metrics.is_data_feed_healthy or slippage_ratio > self.max_slippage_ratio:
                node = "NODE_2_EXECUTION_DYSFUNCTION"
                reason = (
                    "data feed unhealthy" if not metrics.is_data_feed_healthy
                    else f"slippage consuming {slippage_ratio:.2%} of expected alpha "
                         f"(> {self.max_slippage_ratio:.2%})"
                )
                path.append(f"{node}: {reason}.")
                action = RemediationAction.OPTIMIZE_EXECUTION_AND_DATA
                instructions = (
                    "PAUSE_OR_REPAIR_EXECUTION: fix data pipeline SLAs, tune execution "
                    "algorithm order slicing, or switch venue/broker.")
                cap_reduced = True
                decommissioned = False
                if not metrics.is_data_feed_healthy:
                    warnings.append(
                        "Data feed unhealthy: live_sharpe, peer_benchmark_sharpe and "
                        "the slippage ratio in this report were computed from a feed "
                        "the caller flagged as unreliable. Re-run the triage after the "
                        "feed is repaired before acting on any Sharpe comparison."
                    )
            else:
                path.append(
                    f"NODE_2_EXECUTION_CLEARED: feed healthy and slippage at "
                    f"{slippage_ratio:.2%} of expected alpha "
                    f"(<= {self.max_slippage_ratio:.2%}).")

                # Node 2a: sample-size gate. Everything below is a Sharpe comparison,
                # so refuse to route a window too short to distinguish the branches.
                insufficient = False
                if self.min_live_observations is not None:
                    if metrics.live_observation_count is None:
                        raise ValueError(
                            "min_live_observations is configured but metrics carry no "
                            "live_observation_count; the sample-size gate cannot be "
                            "evaluated and an ungated Sharpe verdict would be silently "
                            "unsupported."
                        )
                    insufficient = (
                        metrics.live_observation_count < self.min_live_observations)

                if insufficient:
                    node = "NODE_2A_INSUFFICIENT_HISTORY"
                    path.append(
                        f"{node}: {metrics.live_observation_count} live observations "
                        f"< {self.min_live_observations} required.")
                    action = RemediationAction.EXTEND_OBSERVATION_INSUFFICIENT_HISTORY
                    instructions = (
                        "EXTEND_OBSERVATION: continue at reduced size and re-triage once "
                        "the live window is long enough. A short record is not evidence "
                        "of decay and not evidence of health.")
                    cap_reduced = False
                    decommissioned = False
                else:
                    if self.min_live_observations is not None:
                        path.append(
                            f"NODE_2A_HISTORY_SUFFICIENT: "
                            f"{metrics.live_observation_count} live observations "
                            f">= {self.min_live_observations}.")

                    # Node 3: joint impairment. Resolves before Node 4 because a
                    # capital cut is reversible where overwriting a parameter set is
                    # not, and joint impairment is the conservative reading.
                    if (metrics.live_sharpe < self.min_sharpe
                            and metrics.peer_benchmark_sharpe < self.min_peer_sharpe):
                        node = "NODE_3_REGIME_SHIFT"
                        path.append(
                            f"{node}: strategy below mandate "
                            f"({metrics.live_sharpe:.4f} < {self.min_sharpe:.4f}) and "
                            f"peer group jointly impaired "
                            f"({metrics.peer_benchmark_sharpe:.4f} < "
                            f"{self.min_peer_sharpe:.4f}).")
                        action = RemediationAction.TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL
                        instructions = (
                            "REDUCE_ALLOCATION_50%: reduce strategy allocation by 50% "
                            "while retaining signal execution until the regime turns.")
                        cap_reduced = True
                        decommissioned = False

                    # Node 4: idiosyncratic underperformance against a healthy peer
                    # group.
                    elif metrics.live_sharpe < self.min_sharpe:
                        node = "NODE_4_PARAMETER_DRIFT"
                        path.append(
                            f"{node}: strategy below mandate "
                            f"({metrics.live_sharpe:.4f} < {self.min_sharpe:.4f}) while "
                            f"the peer group is healthy "
                            f"({metrics.peer_benchmark_sharpe:.4f} >= "
                            f"{self.min_peer_sharpe:.4f}).")
                        action = RemediationAction.RECALIBRATE_MODEL_PARAMETERS
                        instructions = (
                            "RECALIBRATE_PARAMETERS: run walk-forward parameter "
                            "re-optimization with out-of-sample validation, then retest "
                            "and re-authorise before redeployment.")
                        cap_reduced = False
                        decommissioned = False
                        if gap_vs_peer >= 0.0:
                            warnings.append(
                                f"Strategy Sharpe {metrics.live_sharpe:.4f} is at or "
                                f"above its peer benchmark "
                                f"({metrics.peer_benchmark_sharpe:.4f}), so it is "
                                f"missing its own mandate without underperforming its "
                                f"cohort. That is weak evidence of parameter drift; "
                                f"review the mandate and the benchmark choice before "
                                f"re-optimizing."
                            )

                    # Healthy.
                    else:
                        node = "HEALTHY_NODE"
                        path.append(
                            f"{node}: strategy at or above mandate "
                            f"({metrics.live_sharpe:.4f} >= {self.min_sharpe:.4f}).")
                        action = RemediationAction.MAINTAIN_TRADING
                        instructions = "MAINTAIN_TRADING: no remediation required."
                        cap_reduced = False
                        decommissioned = False
                        if metrics.peer_benchmark_sharpe < self.min_peer_sharpe:
                            warnings.append(
                                f"Strategy meets its mandate while the peer group is "
                                f"impaired (peer Sharpe "
                                f"{metrics.peer_benchmark_sharpe:.4f} < "
                                f"{self.min_peer_sharpe:.4f}). Confirm the benchmark is "
                                f"still a valid cohort for this strategy."
                            )

        # Caveats that apply to any Sharpe-driven outcome.
        if action in (
            RemediationAction.TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL,
            RemediationAction.RECALIBRATE_MODEL_PARAMETERS,
            RemediationAction.MAINTAIN_TRADING,
        ):
            if sharpe_se is None:
                warnings.append(
                    "live_observation_count not supplied: the sample size behind "
                    "live_sharpe is unknown, so this routing cannot be distinguished "
                    "from estimation noise."
                )
            elif not sharpe_conclusive:
                warnings.append(
                    f"Sharpe evidence inconclusive: live_sharpe "
                    f"{metrics.live_sharpe:.4f} sits within 1.96 standard errors "
                    f"(SE = {sharpe_se:.2f} over "
                    f"{metrics.live_observation_count} observations, Lo 2002) of the "
                    f"{self.min_sharpe:.4f} mandate threshold. The routing is a "
                    f"policy decision on a noisy estimate, not a demonstrated finding."
                )

        if action is RemediationAction.RECALIBRATE_MODEL_PARAMETERS:
            warnings.append(
                "A parameter recalibration is a material change: timestamp, approve, "
                "record and retest it individually. ESMA's February 2026 supervisory "
                "briefing (paragraph 30) warns that a series of small recalibrations "
                "can accumulate into an untested material change."
            )

        notes = (
            f"REMEDIATION REPORT [{action.value}] ({metrics.strategy_id}): "
            f"Path = {' -> '.join(path)} Action = {instructions}"
        )

        if decommissioned:
            logger.error(notes)
        elif cap_reduced:
            logger.warning(notes)
        else:
            logger.info(notes)

        for warning in warnings:
            logger.warning("REMEDIATION CAVEAT (%s): %s", metrics.strategy_id, warning)

        return StrategyRemediationReport(
            strategy_id=metrics.strategy_id,
            recommended_action=action,
            triage_path=path,
            is_capital_reduced=cap_reduced,
            is_decommissioned=decommissioned,
            remediation_instructions=instructions,
            audit_notes=notes,
            decisive_node=node,
            slippage_to_alpha_ratio=slippage_ratio,
            sharpe_gap_vs_peer=gap_vs_peer,
            sharpe_gap_vs_backtest=gap_vs_backtest,
            sharpe_standard_error=sharpe_se,
            sharpe_evidence_conclusive=sharpe_conclusive,
            warnings=tuple(warnings),
        )
