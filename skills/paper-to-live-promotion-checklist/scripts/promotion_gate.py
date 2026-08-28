"""
paper-to-live-promotion-checklist: the gate that decides whether a strategy which has
completed a paper-trading period may begin routing real orders with real capital.

Purpose
-------
Replace "it's been running fine, let's flip the switch" with a conjunctive, recorded,
reproducible decision across six criteria, followed by a *discrete* human sign-off:

    min_paper_duration       the paper period ran at least N days
    min_trades_count         the paper period produced at least N trades
    slippage_alignment       realised paper slippage tracks modelled backtest slippage
    accuracy_alignment       paper signal accuracy tracks walk-forward accuracy
    risk_controls_exercised  at least one risk control actually fired during the period
    auth_reauth_survived     at least one broker token expiry/reauth cycle was survived

All six must pass. There is no weighting, no partial credit and no override flag.
``PromotionDecisionReport.failed_checks`` exists to diagnose *why* a strategy was
refused; 5/6 is exactly as rejected as 0/6.

What this engine actually checks
--------------------------------
**Recorded observations, not verified facts.** ``risk_controls_triggered=2`` means the
caller asserted that two risk controls fired. This module does not read the paper-trading
logs, re-run the backtest, query the broker, or inspect whether each control's *response*
matched its design. Its value is that the six claims are captured together, against
thresholds recorded in the report itself, in an artefact a reviewer can reproduce six
months later -- not that any individual claim has been verified.

Four consequences that must be designed around rather than assumed away:

- **Paper fills are simulated, so ``slippage_alignment`` may be vacuous.** A paper-trading
  run does not transact. Its "realised" slippage is whatever the fill simulator produced.
  If that simulator shares its slippage model with the backtest -- the common case when
  both live in the same codebase -- this check compares a model to itself and passes
  tautologically while proving nothing about live execution. The check is informative only
  when the paper fill path is driven by *observed* market data (touched quotes, queue
  position, real venue latency) independently of the backtest's cost model. If it is not,
  record that fact in the sign-off rather than treating the green tick as evidence. See
  ``demo-account-realism-gap-assessment`` and ``execution-realistic-simulation``.
- **The 10pp accuracy band is narrower than the sampling noise at 30 trades.** Under a
  binomial model, the standard error of a hit rate around 0.56 over 30 observations is
  about 9.1pp, so a two-sided 95% band is roughly +/-17.8pp. A perfectly aligned strategy
  therefore fails ``accuracy_alignment`` a substantial fraction of the time at the gate's
  own minimum trade count, and a genuine 10pp degradation is barely distinguishable from
  noise. ``evaluate_gate`` computes this half-width from the supplied sample size and
  emits an advisory whenever the configured tolerance sits inside it. Widen the tolerance,
  raise ``min_trades_count``, or treat the check as diagnostic -- but do not read a pass
  at n=30 as evidence of parity.
- **This is not a pre-trade control and not a kill switch.** ``approved=True`` cancels no
  order and caps no position. ``check_rollback_trigger`` is a *review* trigger for the
  initial live window, evaluated on whatever cadence the caller chooses; the control that
  must stop trading in real time is ``kill-switch-and-drawdown-circuit-breakers``, which
  is required to stay structurally independent of this module.
- **Sizing the initial live allocation is a different decision.** This gate answers "may
  this begin?". How much capital, and on what ramp, is
  ``incremental-capital-deployment-for-new-strategies``. Earlier versions of this module
  hard-coded a 25% initial sizing that nothing here applied and that contradicts that
  skill's 10% seed tier; ``initial_live_sizing_pct`` is now a value the reviewer supplies
  at sign-off and this module records, never one it recommends.

Threshold provenance
--------------------
Every default below is a **house heuristic**, not a published, regulatory or industry
standard. No regulator prescribes a paper-trading duration, a trade count, a slippage
tolerance or an accuracy band:

- MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589) requires *that* firms
  test before deployment and prescribes what the testing must establish (Article 5,
  "General methodology"), conformance testing (Article 6), a testing environment separated
  from production (Article 7), controlled deployment with predefined limits (Article 8)
  and stress testing (Article 10). It names no minimum duration and no performance metric.
  ESMA's Supervisory Briefing on Algorithmic Trading in the EU (ESMA74-1505669079-10311,
  26 February 2026) expressly recognises "the need for proportionality in applying the
  testing provisions", and is itself non-binding.
- SEBI mandates testing in the exchange's simulated environment before new or changed
  software is put in use, and monthly participation in that environment for approved
  algorithms -- an *exchange conformance* obligation, not an internal strategy-performance
  gate like this one.

Calibrate the numbers to your asset class, holding period and signal frequency, and record
the ones you used -- which is why ``policy_applied`` is embedded in every report and
``policy_weakened`` names any threshold set below the shipped default. See
``references/standards.md`` for the full citation table.

Limitations (documented, deliberate)
------------------------------------
- **Duration is not coverage.** Twenty quiet days demonstrate that the plumbing works, not
  that the strategy survives stress. Nothing here measures which regimes the paper window
  actually contained -- see ``multi-year-regime-coverage-requirement``.
- **A trigger count is not a proven control.** ``risk_controls_exercised`` counts firings.
  Whether each control's observed response matched its design is a human review question
  against ``risk-control-unit-testing-framework``.
- **The engine defines no metric.** ``days_run`` may be calendar or trading days,
  ``signal_accuracy`` may be a per-trade hit rate or a per-bar directional accuracy -- pick
  one convention and make sure it is the convention that set the threshold, and supply
  ``accuracy_sample_size`` when accuracy is not measured over ``trades_count``.
- **Stateless and single-strategy.** No history, no re-promotion tracking. A material
  change to the strategy is a new deployment requiring a new gate, not a continuation of
  this one.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: House default: minimum paper-trading days. Calendar-vs-trading days is the caller's
#: convention. No regulator mandates a paper-trading duration. Not a standard.
DEFAULT_MIN_PAPER_DAYS = 20

#: House default: relative tolerance on |paper - modelled| / modelled slippage.
#: Note the asymmetry with the accuracy tolerance below: this one is RELATIVE.
DEFAULT_SLIPPAGE_TOLERANCE_PCT = 0.15

#: House default: ABSOLUTE tolerance on |paper accuracy - walk-forward accuracy|,
#: expressed in accuracy units (0.10 == 10 percentage points), NOT a relative fraction.
#: The ``_pct`` suffix is retained for backwards compatibility with the 1.x parameter
#: name; the semantics differ from ``slippage_tolerance_pct`` and always have.
DEFAULT_ACCURACY_TOLERANCE_PCT = 0.10

#: House default: minimum trades in the paper period. See the module docstring -- at 30
#: trades the accuracy check sits inside its own sampling noise.
DEFAULT_MIN_TRADES_COUNT = 30

#: House defaults for the post-promotion review trigger. The multiples implement the
#: documented "2x the paper baseline" rule; the floors stop a flatteringly quiet paper
#: period from producing a hair-trigger threshold. Both floors are house heuristics and
#: are instrument-dependent -- 50 bps of slippage is noise in a small cap and enormous in
#: a front-month index future. Override them per strategy.
DEFAULT_ROLLBACK_DRAWDOWN_MULTIPLE = 2.0
DEFAULT_ROLLBACK_DRAWDOWN_FLOOR = 0.05
DEFAULT_ROLLBACK_SLIPPAGE_MULTIPLE = 2.0
DEFAULT_ROLLBACK_SLIPPAGE_FLOOR = 0.005

#: Two-sided 95% normal quantile, used only to describe the sampling noise around the
#: accuracy comparison. It gates nothing; it annotates.
_Z_95 = 1.959963984540054

REQUIRED_PAPER_KEYS: Tuple[str, ...] = (
    "days_run",
    "trades_count",
    "avg_slippage",
    "signal_accuracy",
    "risk_controls_triggered",
    "reauth_cycles_survived",
)
REQUIRED_BACKTEST_KEYS: Tuple[str, ...] = (
    "modeled_slippage",
    "walk_forward_accuracy",
)
REQUIRED_LIVE_KEYS: Tuple[str, ...] = ("max_drawdown_pct", "avg_slippage")
REQUIRED_BASELINE_KEYS: Tuple[str, ...] = ("max_drawdown_pct", "avg_slippage")

SUMMARY_APPROVED = "PROMOTION APPROVED: All paper-to-live gate checks passed successfully."
SUMMARY_REJECTED = "PROMOTION REJECTED: One or more gate criteria failed. Review details."


def _require_keys(payload: Any, keys: Sequence[str], name: str) -> None:
    """
    Fail loudly on an absent key instead of substituting a default.

    The 1.x implementation read every field with ``dict.get(key, default)``. A
    ``paper_stats`` dict that simply omitted ``reauth_cycles_survived`` produced the audit
    line "Survived 0 natural/forced token expiry cycles" -- a fabricated observation
    indistinguishable from a real one -- and an absent ``walk_forward_accuracy`` invented a
    0.50 baseline for the accuracy comparison. In a gate that authorises real capital, an
    unsupplied input is a data failure, never an observed value.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a dict, got {type(payload).__name__}")
    missing = [k for k in keys if k not in payload]
    if missing:
        raise ValueError(
            f"{name} is missing required key(s): {', '.join(missing)}. Supply every "
            f"field explicitly -- this gate never defaults an unobserved metric."
        )


def _require_finite(value: Any, name: str) -> float:
    """
    Reject NaN, Inf and non-numeric input on any field a check compares.

    ``NaN`` fails every comparison, so a corrupt metric surfaced as a *strategy* failure
    rather than a *data* failure -- the two demand different responses from different
    people. ``bool`` is excluded because it is a subclass of ``int`` and ``True`` must
    never silently become a slippage of 1.0.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(
            f"{name} must be finite, got {value!r}. A non-finite input is corrupt "
            f"upstream data, not a strategy that failed the gate."
        )
    return numeric


def _require_positive_finite(value: Any, name: str) -> float:
    """
    Require a strictly positive finite number.

    Applied to ``modeled_slippage``, which is the denominator of the relative slippage
    comparison. The 1.x code guarded that division with ``if bt_slip > 0 else 0.0``, which
    made a zero, negative or NaN modelled slippage *pass* ``slippage_alignment``
    unconditionally, whatever the paper period observed. A backtest that modelled zero
    execution cost is exactly the backtest this gate exists to catch, and it was the one
    input guaranteed to clear the check.
    """
    numeric = _require_finite(value, name)
    if numeric <= 0.0:
        raise ValueError(
            f"{name} must be > 0, got {numeric!r}. A non-positive modelled slippage means "
            f"the backtest priced execution as free; that is a backtest defect to fix, "
            f"not a promotion criterion to divide by."
        )
    return numeric


def _require_unit_interval(value: Any, name: str) -> float:
    """
    Require a proportion in [0, 1].

    Guards the percent-vs-fraction confusion that an agent or a spreadsheet import
    reliably produces: ``signal_accuracy=58`` meaning 58% is not a 58x divergence from a
    0.56 walk-forward accuracy, it is a unit error.
    """
    numeric = _require_finite(value, name)
    if not 0.0 <= numeric <= 1.0:
        raise ValueError(
            f"{name} must be a proportion in [0, 1], got {numeric!r}. Use 0.58 for 58%, "
            f"not 58."
        )
    return numeric


def _require_non_negative_int(value: Any, name: str) -> int:
    """``bool`` is a subclass of ``int``; ``True`` must not silently become 1 trade."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return value


def _require_non_empty_str(value: Any, name: str) -> str:
    """A sign-off attributed to a blank reviewer is not a sign-off."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _require_drawdown(value: Any, name: str) -> float:
    """
    Require a drawdown expressed as a non-negative magnitude in [0, 1].

    The sign convention is the trap: a caller reporting an 8% drawdown as ``-0.08``
    silently cleared every ``live_dd >= threshold`` test in the 1.x rollback trigger, so
    the worse the drawdown the more certainly the trigger stayed quiet. Rejecting the
    negative is the only safe reading -- this module cannot tell a sign convention from a
    gain.
    """
    numeric = _require_finite(value, name)
    if not 0.0 <= numeric <= 1.0:
        raise ValueError(
            f"{name} must be a non-negative drawdown magnitude in [0, 1], got {numeric!r}. "
            f"Report an 8% drawdown as 0.08, never as -0.08 or 8."
        )
    return numeric


@dataclass
class GateCheckResult:
    """One criterion's outcome. ``passed`` is always a real ``bool``."""
    check_name: str
    passed: bool
    observed_value: Any
    expected_value: Any
    details: str


@dataclass
class PromotionDecisionReport:
    """
    The machine verdict plus, once ``record_sign_off`` has been called, the human decision
    that actually authorises promotion.

    ``approved`` is the six-check verdict alone. ``is_authorised`` additionally requires a
    recorded reviewer and initial live sizing, because the workflow this skill encodes
    treats sign-off as a discrete step rather than an implicit consequence of a green
    dashboard.
    """
    approved: bool
    checks: List[GateCheckResult]
    summary: str
    reviewer_id: Optional[str] = None
    # 1.x defaulted these to 0.25 / 0.05 and then never read them. They are now recorded
    # decisions, supplied at sign-off, or ``None`` if no sign-off has happened.
    initial_live_sizing_pct: Optional[float] = None
    rollback_drawdown_pct: Optional[float] = None
    decided_at: Optional[str] = None
    policy_applied: Dict[str, Any] = field(default_factory=dict)
    failed_checks: List[str] = field(default_factory=list)
    policy_weakened: List[str] = field(default_factory=list)
    advisories: List[str] = field(default_factory=list)

    @property
    def is_authorised(self) -> bool:
        """True only when the checks passed *and* a human sign-off has been recorded."""
        return (
            self.approved
            and self.reviewer_id is not None
            and self.initial_live_sizing_pct is not None
        )

    def record_sign_off(
        self,
        reviewer_id: str,
        initial_live_sizing_pct: float,
        rollback_drawdown_pct: float,
        decided_at: str,
    ) -> "PromotionDecisionReport":
        """
        Attach the human decision to the verdict.

        This records a sign-off; it never obtains one, and it deliberately refuses to stamp
        a rejected report. Under MiFID II RTS 6 Article 5, deployment of an algorithmic
        trading system must be authorised by a person designated by the investment firm's
        senior management -- this field is evidence that such a person decided, not a
        substitute for their decision.

        ``decided_at`` is supplied by the caller rather than read from the system clock so
        that a report regenerated from stored inputs reproduces byte-for-byte.

        Raises:
            ValueError: if the report was rejected, the reviewer id is blank,
                ``initial_live_sizing_pct`` is outside (0, 1], or the rollback drawdown is
                not a magnitude in [0, 1].
        """
        if not self.approved:
            raise ValueError(
                "Cannot record a promotion sign-off on a rejected report: "
                f"failed checks {self.failed_checks}. Remediate and re-run the gate."
            )
        self.reviewer_id = _require_non_empty_str(reviewer_id, "reviewer_id")
        sizing = _require_finite(initial_live_sizing_pct, "initial_live_sizing_pct")
        if not 0.0 < sizing <= 1.0:
            raise ValueError(
                f"initial_live_sizing_pct must be in (0, 1], got {sizing!r}. Use 0.10 for "
                f"a 10% seed allocation."
            )
        self.initial_live_sizing_pct = sizing
        self.rollback_drawdown_pct = _require_drawdown(
            rollback_drawdown_pct, "rollback_drawdown_pct"
        )
        self.decided_at = _require_non_empty_str(decided_at, "decided_at")
        if sizing >= 1.0:
            self.advisories.append(
                "initial_live_sizing_pct is 1.0: the strategy goes live at full target "
                "size with no reduced-size window. See "
                "incremental-capital-deployment-for-new-strategies."
            )
        logger.info(
            "Promotion signed off by %s at %s: initial sizing %.2f, rollback drawdown %.2f",
            self.reviewer_id, self.decided_at, sizing, self.rollback_drawdown_pct,
        )
        return self


class PaperToLivePromotionGate:
    """
    Automated promotion gate evaluating a paper-trading period against backtest
    expectations before authorising promotion to live capital.

    The six checks are conjunctive. Passing certifies that six observations were recorded
    against thresholds that are themselves recorded -- it does not verify them, allocate
    capital, or authorise deployment on its own.
    """

    def __init__(
        self,
        min_days: int = DEFAULT_MIN_PAPER_DAYS,
        slippage_tolerance_pct: float = DEFAULT_SLIPPAGE_TOLERANCE_PCT,
        accuracy_tolerance_pct: float = DEFAULT_ACCURACY_TOLERANCE_PCT,
        min_trades_count: int = DEFAULT_MIN_TRADES_COUNT,
        rollback_drawdown_multiple: float = DEFAULT_ROLLBACK_DRAWDOWN_MULTIPLE,
        rollback_drawdown_floor: float = DEFAULT_ROLLBACK_DRAWDOWN_FLOOR,
        rollback_slippage_multiple: float = DEFAULT_ROLLBACK_SLIPPAGE_MULTIPLE,
        rollback_slippage_floor: float = DEFAULT_ROLLBACK_SLIPPAGE_FLOOR,
    ) -> None:
        """
        Args:
            min_days: Minimum paper-trading duration. Calendar or trading days is the
                caller's convention; it must match the convention that set the number.
            slippage_tolerance_pct: **Relative** tolerance on
                ``|paper - modelled| / modelled`` slippage. 0.15 == within 15% of model.
            accuracy_tolerance_pct: **Absolute** tolerance on
                ``|paper accuracy - walk-forward accuracy|``, in accuracy units.
                0.10 == 10 percentage points, not 10% relative. The two tolerance
                parameters share a suffix and not a meaning; this is preserved from 1.x.
            min_trades_count: Minimum trades in the paper period.
            rollback_drawdown_multiple: Multiple of the paper drawdown baseline at which
                the initial-live review trigger fires.
            rollback_drawdown_floor: Absolute drawdown below which the trigger never fires,
                whatever the baseline. A house heuristic.
            rollback_slippage_multiple: As above, for slippage.
            rollback_slippage_floor: As above, for slippage. Instrument-dependent.

        Raises:
            ValueError: on any non-finite, negative or wrongly-typed threshold, or a
                rollback multiple below 1.0.
        """
        self.min_days = _require_non_negative_int(min_days, "min_days")
        self.slippage_tolerance_pct = _require_finite(
            slippage_tolerance_pct, "slippage_tolerance_pct")
        if self.slippage_tolerance_pct < 0.0:
            raise ValueError("slippage_tolerance_pct must be >= 0")
        self.accuracy_tolerance_pct = _require_unit_interval(
            accuracy_tolerance_pct, "accuracy_tolerance_pct")
        self.min_trades_count = _require_non_negative_int(
            min_trades_count, "min_trades_count")
        self.rollback_drawdown_multiple = _require_finite(
            rollback_drawdown_multiple, "rollback_drawdown_multiple")
        self.rollback_drawdown_floor = _require_drawdown(
            rollback_drawdown_floor, "rollback_drawdown_floor")
        self.rollback_slippage_multiple = _require_finite(
            rollback_slippage_multiple, "rollback_slippage_multiple")
        self.rollback_slippage_floor = _require_finite(
            rollback_slippage_floor, "rollback_slippage_floor")
        for name in ("rollback_drawdown_multiple", "rollback_slippage_multiple"):
            if getattr(self, name) < 1.0:
                raise ValueError(
                    f"{name} must be >= 1.0; a multiple below 1 would trigger a rollback "
                    f"review on live behaviour that is better than the paper baseline."
                )
        if self.rollback_slippage_floor < 0.0:
            raise ValueError("rollback_slippage_floor must be >= 0")

    def policy_applied(self) -> Dict[str, Any]:
        """
        Threshold snapshot embedded in every report.

        A report recording ``PROMOTION APPROVED`` without the thresholds that produced it
        proves nothing: a gate configured with ``min_days=0, min_trades_count=0`` emits the
        identical string as the shipped default.
        """
        return {
            "min_days": self.min_days,
            "min_trades_count": self.min_trades_count,
            "slippage_tolerance_pct_relative": self.slippage_tolerance_pct,
            "accuracy_tolerance_absolute": self.accuracy_tolerance_pct,
            "rollback_drawdown_multiple": self.rollback_drawdown_multiple,
            "rollback_drawdown_floor": self.rollback_drawdown_floor,
            "rollback_slippage_multiple": self.rollback_slippage_multiple,
            "rollback_slippage_floor": self.rollback_slippage_floor,
        }

    def weakened_thresholds(self) -> List[str]:
        """
        Thresholds set more permissively than the shipped defaults.

        Naming the relaxations keeps a deliberately loosened gate visible in the record
        instead of indistinguishable from a strict one.
        """
        weakened: List[str] = []
        if self.min_days < DEFAULT_MIN_PAPER_DAYS:
            weakened.append(
                f"min_days: {self.min_days} < default {DEFAULT_MIN_PAPER_DAYS}")
        if self.min_trades_count < DEFAULT_MIN_TRADES_COUNT:
            weakened.append(
                f"min_trades_count: {self.min_trades_count} < default "
                f"{DEFAULT_MIN_TRADES_COUNT}")
        if self.slippage_tolerance_pct > DEFAULT_SLIPPAGE_TOLERANCE_PCT:
            weakened.append(
                f"slippage_tolerance_pct: {self.slippage_tolerance_pct} > default "
                f"{DEFAULT_SLIPPAGE_TOLERANCE_PCT}")
        if self.accuracy_tolerance_pct > DEFAULT_ACCURACY_TOLERANCE_PCT:
            weakened.append(
                f"accuracy_tolerance_pct: {self.accuracy_tolerance_pct} > default "
                f"{DEFAULT_ACCURACY_TOLERANCE_PCT}")
        return weakened

    def evaluate_gate(
        self, paper_stats: Dict[str, Any], backtest_stats: Dict[str, Any]
    ) -> PromotionDecisionReport:
        """
        Evaluate the six promotion criteria.

        Args:
            paper_stats: must contain every key in ``REQUIRED_PAPER_KEYS``. Optionally
                ``accuracy_sample_size`` -- the number of independent observations behind
                ``signal_accuracy``, when that is not ``trades_count``.
            backtest_stats: must contain every key in ``REQUIRED_BACKTEST_KEYS``.

        Returns:
            A ``PromotionDecisionReport`` with ``approved`` set, ``policy_applied``
            embedded, and no sign-off recorded. Call ``record_sign_off`` to authorise.

        Raises:
            ValueError: on a missing key, a non-finite or wrongly-typed metric, a
                non-positive ``modeled_slippage``, or an accuracy outside [0, 1]. A corrupt
                input is never reported as a failed criterion.
        """
        _require_keys(paper_stats, REQUIRED_PAPER_KEYS, "paper_stats")
        _require_keys(backtest_stats, REQUIRED_BACKTEST_KEYS, "backtest_stats")

        days_run = _require_non_negative_int(paper_stats["days_run"], "days_run")
        trades_count = _require_non_negative_int(
            paper_stats["trades_count"], "trades_count")
        paper_slip = _require_finite(paper_stats["avg_slippage"], "avg_slippage")
        paper_acc = _require_unit_interval(
            paper_stats["signal_accuracy"], "signal_accuracy")
        risk_triggers = _require_non_negative_int(
            paper_stats["risk_controls_triggered"], "risk_controls_triggered")
        reauth_cycles = _require_non_negative_int(
            paper_stats["reauth_cycles_survived"], "reauth_cycles_survived")
        bt_slip = _require_positive_finite(
            backtest_stats["modeled_slippage"], "modeled_slippage")
        bt_acc = _require_unit_interval(
            backtest_stats["walk_forward_accuracy"], "walk_forward_accuracy")

        accuracy_n = _require_non_negative_int(
            paper_stats.get("accuracy_sample_size", trades_count), "accuracy_sample_size")

        advisories: List[str] = []
        checks: List[GateCheckResult] = []

        # 1. Minimum paper duration.
        checks.append(
            GateCheckResult(
                check_name="min_paper_duration",
                passed=days_run >= self.min_days,
                observed_value=f"{days_run} days",
                expected_value=f">= {self.min_days} days",
                details=(
                    f"Paper trading ran for {days_run} days (calendar-vs-trading days is "
                    f"the caller's convention)."
                ),
            )
        )

        # 2. Minimum trades count.
        checks.append(
            GateCheckResult(
                check_name="min_trades_count",
                passed=trades_count >= self.min_trades_count,
                observed_value=trades_count,
                expected_value=f">= {self.min_trades_count}",
                details=f"Paper trading recorded {trades_count} trades.",
            )
        )

        # 3. Slippage alignment -- RELATIVE tolerance against the modelled value.
        slip_diff = abs(paper_slip - bt_slip) / bt_slip
        checks.append(
            GateCheckResult(
                check_name="slippage_alignment",
                passed=slip_diff <= self.slippage_tolerance_pct,
                observed_value=f"{paper_slip:.4f}",
                expected_value=(
                    f"{bt_slip:.4f} (+/- {self.slippage_tolerance_pct:.0%} relative)"
                ),
                details=(
                    f"Slippage differs from the backtest model by {slip_diff:.1%} "
                    f"(relative)."
                ),
            )
        )
        if paper_slip < 0.0:
            advisories.append(
                f"avg_slippage is negative ({paper_slip:.4f}): the paper fill simulator "
                f"granted net price improvement across the period. Inspect the simulator "
                f"before reading this as an execution edge."
            )

        # 4. Signal accuracy alignment -- ABSOLUTE tolerance, in accuracy units.
        acc_diff = abs(paper_acc - bt_acc)
        checks.append(
            GateCheckResult(
                check_name="accuracy_alignment",
                passed=acc_diff <= self.accuracy_tolerance_pct,
                observed_value=f"{paper_acc:.2%}",
                expected_value=(
                    f"{bt_acc:.2%} (+/- {self.accuracy_tolerance_pct * 100:.1f}pp absolute)"
                ),
                details=(
                    f"Signal accuracy differs from walk-forward accuracy by "
                    f"{acc_diff * 100:.2f}pp."
                ),
            )
        )
        advisories.extend(self._accuracy_sampling_advisories(bt_acc, accuracy_n))

        # 5. Risk controls exercised.
        checks.append(
            GateCheckResult(
                check_name="risk_controls_exercised",
                passed=risk_triggers > 0,
                observed_value=risk_triggers,
                expected_value=">= 1",
                details=(
                    f"Risk controls fired {risk_triggers} time(s) in paper mode. This is a "
                    f"count of firings, not evidence that each response matched its design."
                ),
            )
        )

        # 6. Broker auth / reauth cycle survived.
        checks.append(
            GateCheckResult(
                check_name="auth_reauth_survived",
                passed=reauth_cycles >= 1,
                observed_value=reauth_cycles,
                expected_value=">= 1",
                details=f"Survived {reauth_cycles} natural/forced token expiry cycle(s).",
            )
        )

        failed = [c.check_name for c in checks if not c.passed]
        all_passed = not failed
        report = PromotionDecisionReport(
            approved=all_passed,
            checks=checks,
            summary=SUMMARY_APPROVED if all_passed else SUMMARY_REJECTED,
            policy_applied=self.policy_applied(),
            failed_checks=failed,
            policy_weakened=self.weakened_thresholds(),
            advisories=advisories,
        )
        logger.info(
            "Paper-to-live gate: approved=%s failed=%s weakened=%s",
            all_passed, failed, report.policy_weakened,
        )
        return report

    def _accuracy_sampling_advisories(self, bt_acc: float, n: int) -> List[str]:
        """
        Describe the sampling noise the accuracy comparison sits inside.

        Under a binomial model with the walk-forward accuracy as the null, the standard
        error of an observed hit rate over ``n`` independent observations is
        ``sqrt(p(1-p)/n)``. When the configured tolerance is narrower than the resulting
        two-sided 95% half-width, the check rejects aligned strategies at a high rate and
        cannot distinguish a real degradation of that size from noise. This annotates the
        report; it never changes the verdict.
        """
        if n <= 0:
            return [
                "accuracy_alignment has no sample size (0 observations): the comparison is "
                "not evaluable and its result must not be read as parity."
            ]
        standard_error = math.sqrt(bt_acc * (1.0 - bt_acc) / n)
        half_width = _Z_95 * standard_error
        if standard_error == 0.0 or self.accuracy_tolerance_pct >= half_width:
            return []
        return [
            f"accuracy_alignment tolerance ({self.accuracy_tolerance_pct * 100:.1f}pp) is "
            f"narrower than the 95% sampling half-width at n={n} "
            f"({half_width * 100:.1f}pp, SE {standard_error * 100:.1f}pp). A strategy whose "
            f"true accuracy equals the walk-forward value will fail this check a "
            f"substantial fraction of the time, and a real degradation of this size is not "
            f"distinguishable from noise. Raise min_trades_count, widen the tolerance, or "
            f"treat the check as diagnostic."
        ]

    def check_rollback_trigger(
        self, live_stats: Dict[str, Any], paper_baseline: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        Evaluate early live-trading behaviour against the paper baseline.

        Fires when live drawdown or live slippage reaches
        ``max(floor, multiple * paper baseline)``. Both rules are evaluated, so a
        simultaneous breach reports both reasons rather than only the first, and each
        message names the threshold actually applied together with which rule produced it
        -- the 1.x message quoted "2x paper baseline" even when the absolute floor was the
        binding constraint, putting a number in the audit trail that was not the number
        used.

        This is a **review** trigger for the initial live window, not a kill switch: it
        returns a decision for a human or a supervisor process to act on and stops nothing
        by itself. Real-time position protection belongs to
        ``kill-switch-and-drawdown-circuit-breakers``.

        Args:
            live_stats: must contain ``max_drawdown_pct`` (a non-negative magnitude,
                0.08 == 8%) and ``avg_slippage``.
            paper_baseline: the same keys, from the paper period.

        Returns:
            ``(triggered, message)``. The message begins with ``ROLLBACK TRIGGERED`` when
            ``triggered`` is True.

        Raises:
            ValueError: on missing keys, non-finite values, or a drawdown reported as a
                negative number.
        """
        _require_keys(live_stats, REQUIRED_LIVE_KEYS, "live_stats")
        _require_keys(paper_baseline, REQUIRED_BASELINE_KEYS, "paper_baseline")

        live_dd = _require_drawdown(
            live_stats["max_drawdown_pct"], "live max_drawdown_pct")
        paper_dd = _require_drawdown(
            paper_baseline["max_drawdown_pct"], "paper baseline max_drawdown_pct")
        live_slip = _require_finite(live_stats["avg_slippage"], "live avg_slippage")
        paper_slip = _require_finite(
            paper_baseline["avg_slippage"], "paper baseline avg_slippage")

        reasons: List[str] = []

        dd_scaled = paper_dd * self.rollback_drawdown_multiple
        dd_threshold = max(self.rollback_drawdown_floor, dd_scaled)
        if live_dd >= dd_threshold:
            bound_by = (
                "absolute floor" if dd_threshold > dd_scaled
                else f"{self.rollback_drawdown_multiple:g}x paper baseline"
            )
            reasons.append(
                f"live drawdown {live_dd:.2%} >= applied threshold {dd_threshold:.2%} "
                f"(bound by {bound_by}; paper baseline {paper_dd:.2%})"
            )

        slip_scaled = paper_slip * self.rollback_slippage_multiple
        slip_threshold = max(self.rollback_slippage_floor, slip_scaled)
        if live_slip >= slip_threshold:
            bound_by = (
                "absolute floor" if slip_threshold > slip_scaled
                else f"{self.rollback_slippage_multiple:g}x paper baseline"
            )
            reasons.append(
                f"live slippage {live_slip:.4f} >= applied threshold {slip_threshold:.4f} "
                f"(bound by {bound_by}; paper baseline {paper_slip:.4f})"
            )

        if reasons:
            msg = "ROLLBACK TRIGGERED: " + "; ".join(reasons) + "."
            logger.critical(msg)
            return True, msg
        return False, "Live metrics operating within acceptable bounds."


def evaluate_promotion_gate(
    paper_stats: Dict[str, Any],
    backtest_stats: Dict[str, Any],
    min_days: int = DEFAULT_MIN_PAPER_DAYS,
    tolerance: float = DEFAULT_SLIPPAGE_TOLERANCE_PCT,
) -> Dict[str, bool]:
    """
    Deprecated 1.x helper retained for backwards compatibility.

    **Prefer ``PaperToLivePromotionGate.evaluate_gate``.** This function checks only four of
    the six criteria -- it ignores ``min_trades_count`` and ``auth_reauth_survived``
    entirely -- and applies the single ``tolerance`` argument with two different meanings:
    relative for slippage, absolute for accuracy. It also produces a bare dict with no
    recorded policy, so its verdict is not an audit record. It performs the same input
    validation as the class so that it cannot silently pass on corrupt data, but a caller
    who reaches for the shorter name gets a materially weaker gate.

    Returns:
        A dict of criterion name -> bool, plus ``all_pass``.

    Raises:
        ValueError: on the same conditions as ``evaluate_gate``.
    """
    logger.warning(
        "evaluate_promotion_gate is deprecated and evaluates only 4 of 6 criteria; use "
        "PaperToLivePromotionGate.evaluate_gate for a complete, recorded verdict."
    )
    _require_keys(
        paper_stats,
        ("days_run", "avg_slippage", "signal_accuracy", "risk_controls_triggered"),
        "paper_stats",
    )
    _require_keys(backtest_stats, REQUIRED_BACKTEST_KEYS, "backtest_stats")

    days_run = _require_non_negative_int(paper_stats["days_run"], "days_run")
    paper_slip = _require_finite(paper_stats["avg_slippage"], "avg_slippage")
    paper_acc = _require_unit_interval(paper_stats["signal_accuracy"], "signal_accuracy")
    risk_triggers = _require_non_negative_int(
        paper_stats["risk_controls_triggered"], "risk_controls_triggered")
    bt_slip = _require_positive_finite(
        backtest_stats["modeled_slippage"], "modeled_slippage")
    bt_acc = _require_unit_interval(
        backtest_stats["walk_forward_accuracy"], "walk_forward_accuracy")
    tolerance = _require_finite(tolerance, "tolerance")

    checks: Dict[str, bool] = {
        "min_duration_met": days_run >= _require_non_negative_int(min_days, "min_days"),
        "slippage_within_tolerance": abs(paper_slip - bt_slip) <= tolerance * bt_slip,
        "accuracy_within_tolerance": abs(paper_acc - bt_acc) <= tolerance,
        "risk_controls_exercised": risk_triggers > 0,
    }
    checks["all_pass"] = all(checks.values())
    return checks
