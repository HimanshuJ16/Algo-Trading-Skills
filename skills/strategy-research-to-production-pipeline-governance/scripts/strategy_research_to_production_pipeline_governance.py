"""
strategy-research-to-production-pipeline-governance: promotion gatekeeper and
tamper-evident audit ledger for moving a trading strategy from research to live
capital.

Purpose
-------
Answer one question, once per stage transition, and leave behind a record a
supervisor can verify: *is this strategy allowed to advance one step closer to
live capital, and who authorised it?* The engine evaluates a fixed set of gates,
refuses the transition if any gate fails, and appends the decision to a
hash-chained ledger so a later reader can prove the record was not edited after
the fact.

Pipeline stages (strictly ordered)
----------------------------------
    RESEARCH_BACKTEST -> INDEPENDENT_VALIDATION -> PAPER_TRADING_SHADOW
                      -> STAGING_CANARY -> LIVE_PRODUCTION

A promotion must advance **exactly one** stage. Skipping a stage is the failure
this engine exists to prevent, so ``RESEARCH_BACKTEST -> LIVE_PRODUCTION`` is
rejected on sequencing alone, however good the metrics are. Backward and
same-stage transitions are not promotions and are also rejected; rolling a
strategy back is a different workflow (see
``strategy-decommissioning-and-position-unwind-procedure``).

Which gates apply to which target stage
---------------------------------------
==========================  ==========================================
Target stage                Gates evaluated (in addition to sequencing)
==========================  ==========================================
INDEPENDENT_VALIDATION      reproducibility, Sharpe, drawdown
PAPER_TRADING_SHADOW        + independent validator recorded
STAGING_CANARY              + paper-trading duration, shadow divergence
LIVE_PRODUCTION             + designated-person sign-off
==========================  ==========================================

Thresholds are house defaults, not standards
--------------------------------------------
``min_backtest_sharpe``, ``max_backtest_drawdown_pct``,
``max_shadow_tracking_error_pct`` and ``min_paper_trading_days`` are defaults
chosen for this engine. **No regulator publishes a minimum backtest Sharpe, a
maximum shadow divergence, or a minimum paper-trading duration.** ESMA's
supervisory briefing on algorithmic trading states that the "scope, frequency,
and intensity of testing vary significantly across the industry" and recognises
"the need for proportionality in applying the testing provisions"
(ESMA74-1505669079-10311, Feb 2026, para. 26) -- which argues directly against
presenting any fixed number as an industry standard. Calibrate per strategy and
record what you used; ``references/standards.md`` carries the full evidence
table.

What the regulation does require (verified)
-------------------------------------------
- **RTS 6 Art. 5(2)** (Commission Delegated Regulation (EU) 2017/589): "A person
  designated by the senior management of the investment firm shall authorise the
  deployment or substantial update of an algorithmic trading system, trading
  algorithm or algorithmic trading strategy." The obligation names a
  *senior-management-designated person*, not a risk committee. This engine's
  sign-off gate records **who** authorised the transition; mapping that person to
  your firm's committee structure is your governance decision, not a regulatory
  one.
- **RTS 6 Art. 5(7)**: firms must keep records of software changes documenting
  timing, the person making the change, approvals, and the nature of the change.
- **RTS 6 Art. 11(1)**: a material production change must be "preceded by a
  review of that change by a person designated by senior management".
- **ESMA supervisory briefing, para. 31**: "Investment firms are required to
  timestamp, approve, and record all material changes." Hence the decision record
  carries an explicit ``decided_at_utc`` rather than an implicit clock read, and
  the audit hash binds that timestamp together with the decision content.

Audit hash and ledger
---------------------
``audit_trail_hash`` is the SHA-256 of a canonical JSON serialisation of the
**entire** decision: strategy, both stages, every artifact value, every
configured threshold, the gate outcomes, the recorded timestamp, and the previous
entry's hash. Two consequences that the previous implementation did not provide:

1. **It is reproducible.** ``verify_audit_hash(decision)`` recomputes it. A hash
   seeded with an unrecorded ``time.time()`` could never be recomputed by anyone,
   which makes it decoration rather than an audit trail.
2. **It binds the content.** Editing a recorded Sharpe ratio, a validator id, or
   an approval flag changes the hash. The previous hash covered only the
   strategy id, the two stage names, the git hash and the boolean outcome, so the
   quantitative evidence behind the decision was not protected at all.

Chaining each entry to its predecessor makes the ledger tamper-*evident*:
``verify_ledger()`` fails if any earlier entry was altered or removed. Note the
precise claim, and its limit:

- The chain detects an **edit or deletion** made without recomputing the chain.
- It does **not** defend against someone who can rewrite the whole ledger: with
  write access and this module, every downstream digest can simply be
  recalculated. A hash chain is only as strong as its anchor. To get a claim
  worth making to a supervisor, persist each ``audit_trail_hash`` somewhere the
  strategy owner cannot rewrite -- append-only WORM storage, an audit database
  with no UPDATE grant, or a periodic digest countersigned by a separate
  function.
- It says nothing about durability. The in-memory list is as deletable as any
  other Python object. This class provides the integrity check, not the storage.

Limitations (deliberate, documented)
------------------------------------
- **Stateless across calls except for the ledger.** The engine does not verify
  that a strategy actually *completed* the earlier stages -- it checks that the
  transition being requested is a single forward step and that the artifacts
  presented satisfy the gates. Pair it with your pipeline's stage store.
- **Artifacts are asserted, not measured.** ``backtest_sharpe`` and
  ``shadow_tracking_error_pct`` are numbers the caller supplies. The engine
  cannot tell a genuine out-of-sample Sharpe from an in-sample one, and cannot
  detect look-ahead bias -- see ``lookahead-bias-elimination`` and
  ``walk-forward-validation-setup`` for the controls that can.
- **RTS 6 Art. 8 deployment limits are not modelled.** Art. 8 requires
  predefined limits on the number of instruments traded, the price/value/number
  of orders, strategy positions, and the number of venues before an algorithm is
  deployed. This engine gates the *decision*; it does not hold or enforce those
  limits. Declare and enforce them in the execution layer.
- **Identity is a string.** ``author_id`` / ``validator_id`` are opaque labels.
  The engine enforces that they differ; it cannot authenticate that either
  person exists or holds the designated authority.
"""
import hashlib
import json
import logging
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Minimum length of an abbreviated Git commit hash accepted by the
#: reproducibility gate. Git's own default abbreviation is 7 hex characters; a
#: full SHA-1 object name is 40 and a SHA-256 object name is 64.
MIN_GIT_HASH_LEN = 7
MAX_GIT_HASH_LEN = 64

#: A commit hash must be hexadecimal. The previous implementation accepted any
#: string of length >= 7, so the placeholder ``"notahash"`` passed the
#: reproducibility gate.
_HEX_RE = re.compile(r"\A[0-9a-fA-F]+\Z")

#: Sentinel value for "no previous entry" at the head of the audit chain.
GENESIS_HASH = "0" * 64

#: Upper bound for a percentage drawdown. Losing more than 100% of capital is not
#: expressible as a drawdown percentage on a long-only equity curve, and a value
#: above it signals a unit error (a fraction/percent mix-up) rather than a real
#: figure.
MAX_DRAWDOWN_PCT = 100.0


@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "strategy-research-to-production-pipeline-governance"


class Engine:
    """Legacy Engine class for backward compatibility."""

    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return True


class PipelineStage(str, Enum):
    RESEARCH_BACKTEST = "RESEARCH_BACKTEST"
    INDEPENDENT_VALIDATION = "INDEPENDENT_VALIDATION"
    PAPER_TRADING_SHADOW = "PAPER_TRADING_SHADOW"
    STAGING_CANARY = "STAGING_CANARY"
    LIVE_PRODUCTION = "LIVE_PRODUCTION"


#: The pipeline order. A promotion must advance exactly one position in this
#: tuple. Defined once so the ordering cannot drift from the documentation.
PIPELINE_ORDER: Tuple[PipelineStage, ...] = (
    PipelineStage.RESEARCH_BACKTEST,
    PipelineStage.INDEPENDENT_VALIDATION,
    PipelineStage.PAPER_TRADING_SHADOW,
    PipelineStage.STAGING_CANARY,
    PipelineStage.LIVE_PRODUCTION,
)

#: Stages at or beyond which an independent validator must be named. Promoting
#: *out of* INDEPENDENT_VALIDATION without recording an independent validator
#: makes that stage a no-op, which is how "independent validation" becomes a
#: rubber stamp.
_VALIDATOR_REQUIRED_FROM = PipelineStage.PAPER_TRADING_SHADOW

#: Stages whose entry requires evidence from a completed shadow run.
_SHADOW_EVIDENCE_STAGES = (PipelineStage.STAGING_CANARY, PipelineStage.LIVE_PRODUCTION)


class PromotionStatus(str, Enum):
    """
    Stable machine-readable outcome codes.

    The previous implementation documented ``REJECTED_LOW_SHARPE`` /
    ``REJECTED_SHADOW_ERROR`` / ``REJECTED_MISSING_SIGNOFF`` but emitted
    ``"REJECTED_GATES_FAILED (3)"`` -- a string with the failure count
    interpolated into it, so no caller could match on it and none of the
    documented codes was ever produced. Branch on this enum and read
    ``failed_gates`` for the detail.
    """
    APPROVED_FOR_PROMOTION = "APPROVED_FOR_PROMOTION"
    REJECTED_GATES_FAILED = "REJECTED_GATES_FAILED"


@dataclass
class StagePromotionArtifacts:
    """
    Evidence submitted in support of one promotion request.

    All values are **asserted by the caller**; the engine validates their shape
    and range, not their truthfulness.
    """
    #: Git commit hash of the strategy code under review. Hexadecimal, 7-64
    #: characters (abbreviated or full SHA-1/SHA-256 object name).
    git_commit_hash: str
    #: Checksum of the exact dataset the backtest consumed. Any non-blank
    #: identifier; the engine cannot recompute it.
    dataset_checksum: str
    #: Out-of-sample backtest Sharpe ratio. Must be finite.
    backtest_sharpe: float
    #: Worst peak-to-trough decline of the backtest equity curve, as a
    #: **positive percentage magnitude**: a 12% drawdown is ``12.0``, never
    #: ``-12.0``. The previous implementation compared ``value <= cap`` without
    #: enforcing the sign, so a catastrophic ``-40.0`` passed a 15% cap.
    backtest_max_drawdown_pct: float
    #: Divergence between shadow paper-trading fills and the backtest simulation
    #: over the same period, as a **non-negative percentage**.
    #:
    #: Note the naming: canonically, "tracking error" is the standard deviation
    #: of active (difference) returns, annualized. What is gated here is a
    #: fill-level divergence between two runs of the same strategy. The field
    #: name is retained for API compatibility, but **the caller must fix and
    #: document the definition** -- an annualized standard deviation of return
    #: differences and a mean absolute per-fill price divergence are different
    #: quantities, and 5.0 means something very different under each. Whichever
    #: you choose, compute it the same way for every strategy you compare.
    shadow_tracking_error_pct: float
    #: Consecutive calendar days of shadow paper trading completed. Non-negative.
    paper_trading_days: int
    #: Whether the designated approver (RTS 6 Art. 5(2)) has signed off.
    has_risk_committee_signoff: bool
    #: Identifier of the person who developed the strategy.
    author_id: str
    #: Identifier of the independent validator / designated approver. Must differ
    #: from ``author_id``: an author who validates their own work is not
    #: independent validation.
    validator_id: str


@dataclass
class StagePromotionDecision:
    strategy_id: str
    current_stage: PipelineStage
    target_stage: PipelineStage
    is_approved: bool
    #: Value of :class:`PromotionStatus`. Stable and matchable -- it no longer
    #: has the failure count interpolated into it.
    status_code: str
    passed_gates: List[str]
    failed_gates: List[str]
    #: Full 64-character SHA-256 of the canonical decision payload (see
    #: ``_canonical_payload``). Recompute with :func:`verify_audit_hash`.
    audit_trail_hash: str
    audit_notes: str
    #: ISO-8601 UTC timestamp of the decision. Recorded explicitly because ESMA
    #: requires material changes to be timestamped, approved and recorded, and
    #: because a hash over an unrecorded clock read is not reproducible.
    decided_at_utc: str = ""
    #: Hash of the preceding ledger entry, or ``GENESIS_HASH`` for the first.
    #: This is what makes the ledger tamper-evident.
    previous_audit_hash: str = GENESIS_HASH
    #: Position of this decision in the engine's ledger, starting at 0.
    ledger_index: int = 0


def _require_finite(value: float, name: str) -> float:
    """Rejects NaN and infinity, which silently defeat every threshold check."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(
            f"{name} must be finite, got {value!r}. NaN compares False against "
            f"every threshold, so a corrupt metric would silently fail or pass a "
            f"gate depending on which direction the comparison runs."
        )
    return numeric


def _validate_artifacts(artifacts: StagePromotionArtifacts) -> None:
    """
    Rejects artifact bundles that cannot support a governance decision.

    Fails closed by raising rather than by returning a rejection: a malformed
    submission is a process error to be fixed, not a strategy to be rejected on
    its merits, and the two must not be conflated in the audit record.
    """
    if not isinstance(artifacts, StagePromotionArtifacts):
        raise ValueError(
            f"artifacts must be a StagePromotionArtifacts, got "
            f"{type(artifacts).__name__}"
        )

    _require_finite(artifacts.backtest_sharpe, "backtest_sharpe")

    drawdown = _require_finite(
        artifacts.backtest_max_drawdown_pct, "backtest_max_drawdown_pct")
    if not 0.0 <= drawdown <= MAX_DRAWDOWN_PCT:
        raise ValueError(
            f"backtest_max_drawdown_pct must be a positive percentage magnitude "
            f"in [0, {MAX_DRAWDOWN_PCT}], got {drawdown}. A 12% drawdown is 12.0, "
            f"not -12.0 -- a negative value passes any `<= cap` comparison and "
            f"silently certifies the worst backtests."
        )

    tracking_error = _require_finite(
        artifacts.shadow_tracking_error_pct, "shadow_tracking_error_pct")
    if tracking_error < 0.0:
        raise ValueError(
            f"shadow_tracking_error_pct must be non-negative, got {tracking_error}. "
            f"It measures a magnitude of divergence; a negative value has no "
            f"meaning and passes any `<= cap` comparison."
        )

    if isinstance(artifacts.paper_trading_days, bool) or not isinstance(
            artifacts.paper_trading_days, int):
        raise ValueError(
            f"paper_trading_days must be an int, got "
            f"{type(artifacts.paper_trading_days).__name__}")
    if artifacts.paper_trading_days < 0:
        raise ValueError(
            f"paper_trading_days must be non-negative, got "
            f"{artifacts.paper_trading_days}")

    if not isinstance(artifacts.has_risk_committee_signoff, bool):
        raise ValueError(
            f"has_risk_committee_signoff must be a bool, got "
            f"{type(artifacts.has_risk_committee_signoff).__name__}. A truthy "
            f"non-bool (a non-empty string, say) would grant approval by accident."
        )

    for name in ("git_commit_hash", "dataset_checksum", "author_id", "validator_id"):
        value = getattr(artifacts, name)
        if not isinstance(value, str):
            raise ValueError(
                f"{name} must be a str, got {type(value).__name__}")

    if not artifacts.author_id.strip():
        raise ValueError(
            "author_id must be a non-blank identifier: an unattributed strategy "
            "cannot satisfy the RTS 6 Art. 5(7) obligation to record who made a "
            "change."
        )


def _normalise_decision_timestamp(value: str) -> str:
    """
    Requires an unambiguous, timezone-aware ISO-8601 instant.

    A governance record that says "approved at 09:30" without an offset is not
    an audit trail in a firm that operates across time zones -- the reader
    cannot tell which 09:30, and the ambiguity is worst around session
    boundaries and DST transitions, exactly when it matters. The field is named
    ``_utc``; this enforces that the value actually says so.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("decided_at_utc must be a non-blank ISO-8601 string")
    candidate = value.strip()
    # datetime.fromisoformat only accepts a trailing 'Z' from Python 3.11.
    normalised = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError(
            f"decided_at_utc must be an ISO-8601 timestamp, got {value!r} ({exc})"
        ) from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(
            f"decided_at_utc must carry a UTC offset, got {value!r}. A naive "
            f"timestamp in a promotion record cannot be reconciled against "
            f"exchange session times or another jurisdiction's records."
        )
    return candidate


def _is_valid_commit_hash(candidate: str) -> bool:
    """
    A commit hash is hexadecimal, of plausible length, and not all zeros.

    ``len(candidate) >= 7`` -- the previous check -- accepted ``"notahash"`` and
    ``"0000000"``, both of which are exactly the placeholders a stalled pipeline
    emits when it cannot resolve a real revision.
    """
    trimmed = candidate.strip()
    if not MIN_GIT_HASH_LEN <= len(trimmed) <= MAX_GIT_HASH_LEN:
        return False
    if not _HEX_RE.match(trimmed):
        return False
    return set(trimmed) != {"0"}


def _canonical_payload(
    strategy_id: str,
    current_stage: PipelineStage,
    target_stage: PipelineStage,
    artifacts: StagePromotionArtifacts,
    thresholds: Dict[str, float],
    is_approved: bool,
    status_code: str,
    passed_gates: Sequence[str],
    failed_gates: Sequence[str],
    decided_at_utc: str,
    previous_audit_hash: str,
    ledger_index: int,
) -> str:
    """
    Serialises everything the audit hash must protect, deterministically.

    ``sort_keys`` plus a fixed separator makes the encoding independent of dict
    insertion order, so the same decision hashes identically on any interpreter
    run. Every quantitative input and every configured threshold is included:
    without the thresholds, the same artifacts evaluated against a quietly
    loosened Sharpe floor would produce an indistinguishable record.
    """
    payload = {
        "strategy_id": strategy_id,
        "current_stage": current_stage.value,
        "target_stage": target_stage.value,
        "artifacts": asdict(artifacts),
        "thresholds": thresholds,
        "is_approved": is_approved,
        "status_code": status_code,
        "passed_gates": list(passed_gates),
        "failed_gates": list(failed_gates),
        "decided_at_utc": decided_at_utc,
        "previous_audit_hash": previous_audit_hash,
        "ledger_index": ledger_index,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_audit_hash(
    strategy_id: str,
    current_stage: PipelineStage,
    target_stage: PipelineStage,
    artifacts: StagePromotionArtifacts,
    thresholds: Dict[str, float],
    is_approved: bool,
    status_code: str,
    passed_gates: Sequence[str],
    failed_gates: Sequence[str],
    decided_at_utc: str,
    previous_audit_hash: str,
    ledger_index: int,
) -> str:
    """Full 64-character SHA-256 hex digest of the canonical decision payload."""
    canonical = _canonical_payload(
        strategy_id, current_stage, target_stage, artifacts, thresholds,
        is_approved, status_code, passed_gates, failed_gates, decided_at_utc,
        previous_audit_hash, ledger_index,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_audit_hash(
    decision: StagePromotionDecision,
    artifacts: StagePromotionArtifacts,
    thresholds: Dict[str, float],
) -> bool:
    """
    Recomputes a decision's audit hash from the evidence and compares.

    Returns ``False`` if any recorded value was altered after the decision. The
    caller must supply the artifacts and thresholds the decision was made
    against; that is the point -- the hash proves those specific inputs produced
    that specific outcome.
    """
    expected = compute_audit_hash(
        decision.strategy_id, decision.current_stage, decision.target_stage,
        artifacts, thresholds, decision.is_approved, decision.status_code,
        decision.passed_gates, decision.failed_gates, decision.decided_at_utc,
        decision.previous_audit_hash, decision.ledger_index,
    )
    return expected == decision.audit_trail_hash


class StrategyResearchToProductionGovernanceEngine:
    """
    Promotion gatekeeper for the research-to-production pipeline.

    Evaluates one stage transition at a time against a fixed gate set and appends
    each decision -- approved or rejected -- to a hash-chained, tamper-evident
    ledger. Rejections are recorded too: a governance trail that only contains
    approvals cannot show that anything was ever refused.
    """

    def __init__(
        self,
        min_backtest_sharpe: float = 1.50,
        max_backtest_drawdown_pct: float = 15.0,
        max_shadow_tracking_error_pct: float = 5.0,
        min_paper_trading_days: int = 14,
    ):
        """
        All four thresholds are **house defaults, not standards**. See the module
        docstring and ``references/standards.md``: no regulator publishes a
        minimum Sharpe, a maximum shadow divergence, or a minimum paper-trading
        duration. Calibrate them and record what you used -- changing a gate
        threshold is itself a material change under the ESMA briefing's retesting
        triggers (para. 31, "Risk Controls: changing thresholds").
        """
        self.min_sharpe = _require_finite(min_backtest_sharpe, "min_backtest_sharpe")

        self.max_drawdown = _require_finite(
            max_backtest_drawdown_pct, "max_backtest_drawdown_pct")
        if not 0.0 < self.max_drawdown <= MAX_DRAWDOWN_PCT:
            raise ValueError(
                f"max_backtest_drawdown_pct must be in (0, {MAX_DRAWDOWN_PCT}], "
                f"got {self.max_drawdown}. A cap of 1000.0 is not a cap.")

        self.max_tracking_error = _require_finite(
            max_shadow_tracking_error_pct, "max_shadow_tracking_error_pct")
        if self.max_tracking_error < 0.0:
            raise ValueError(
                f"max_shadow_tracking_error_pct must be non-negative, got "
                f"{self.max_tracking_error}. A negative cap can never be met and "
                f"blocks every promotion.")

        if isinstance(min_paper_trading_days, bool) or not isinstance(
                min_paper_trading_days, int):
            raise ValueError(
                f"min_paper_trading_days must be an int, got "
                f"{type(min_paper_trading_days).__name__}")
        if min_paper_trading_days < 0:
            raise ValueError(
                f"min_paper_trading_days must be non-negative, got "
                f"{min_paper_trading_days}")
        self.min_paper_days = min_paper_trading_days

        self._ledger: List[StagePromotionDecision] = []

    @property
    def thresholds(self) -> Dict[str, float]:
        """The configured gate thresholds, as bound into every audit hash."""
        return {
            "min_backtest_sharpe": self.min_sharpe,
            "max_backtest_drawdown_pct": self.max_drawdown,
            "max_shadow_tracking_error_pct": self.max_tracking_error,
            "min_paper_trading_days": float(self.min_paper_days),
        }

    @property
    def ledger(self) -> Tuple[StagePromotionDecision, ...]:
        """Every decision recorded by this engine, oldest first."""
        return tuple(self._ledger)

    def evaluate_stage_promotion(
        self,
        strategy_id: str,
        current_stage: PipelineStage,
        target_stage: PipelineStage,
        artifacts: StagePromotionArtifacts,
        decided_at_utc: Optional[str] = None,
    ) -> StagePromotionDecision:
        """
        Evaluates one promotion request and appends the outcome to the ledger.

        ``decided_at_utc`` may be supplied (ISO-8601) to make a decision fully
        reproducible in tests or when replaying a historical approval; otherwise
        the current UTC time is recorded.

        Raises ``ValueError`` on structurally invalid input -- a malformed
        submission is a process failure, distinct from a strategy that was
        evaluated and refused, and the ledger must not blur the two.
        """
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise ValueError("strategy_id must be a non-blank string")
        if not isinstance(current_stage, PipelineStage):
            raise ValueError(
                f"current_stage must be a PipelineStage, got "
                f"{type(current_stage).__name__}")
        if not isinstance(target_stage, PipelineStage):
            raise ValueError(
                f"target_stage must be a PipelineStage, got "
                f"{type(target_stage).__name__}")
        _validate_artifacts(artifacts)

        if decided_at_utc is None:
            decided_at_utc = datetime.now(timezone.utc).isoformat()
        else:
            decided_at_utc = _normalise_decision_timestamp(decided_at_utc)

        passed: List[str] = []
        failed: List[str] = []

        # 1. Stage sequencing gate.
        #
        # This runs first and is the gate the previous implementation lacked
        # entirely: it evaluated the artifact gates and approved
        # RESEARCH_BACKTEST -> LIVE_PRODUCTION, skipping independent validation,
        # shadow trading and canary in a single hop, while the documentation
        # claimed sequential gatekeeping was enforced.
        current_index = PIPELINE_ORDER.index(current_stage)
        target_index = PIPELINE_ORDER.index(target_stage)
        step = target_index - current_index
        if step == 1:
            passed.append(
                f"STAGE_SEQUENCE_GATE: {current_stage.value} -> {target_stage.value} "
                f"advances exactly one stage.")
        elif step > 1:
            skipped = ", ".join(
                s.value for s in PIPELINE_ORDER[current_index + 1:target_index])
            failed.append(
                f"STAGE_SEQUENCE_GATE: {current_stage.value} -> {target_stage.value} "
                f"skips {step - 1} stage(s) ({skipped}). Promotion must advance "
                f"exactly one stage.")
        else:
            failed.append(
                f"STAGE_SEQUENCE_GATE: {current_stage.value} -> {target_stage.value} "
                f"is not a forward promotion. Rollback and decommissioning are a "
                f"separate workflow.")

        # 2. Reproducibility gate.
        commit_ok = _is_valid_commit_hash(artifacts.git_commit_hash)
        checksum_ok = bool(artifacts.dataset_checksum.strip())
        if commit_ok and checksum_ok:
            passed.append(
                "REPRODUCIBILITY_GATE: Valid Git commit hash and dataset checksum "
                "verified.")
        else:
            reasons = []
            if not commit_ok:
                reasons.append(
                    f"git_commit_hash {artifacts.git_commit_hash!r} is not a "
                    f"{MIN_GIT_HASH_LEN}-{MAX_GIT_HASH_LEN} character non-zero "
                    f"hexadecimal commit id")
            if not checksum_ok:
                reasons.append("dataset_checksum is blank")
            failed.append(f"REPRODUCIBILITY_GATE: {'; '.join(reasons)}.")

        # 3. Backtest quantitative gates.
        if artifacts.backtest_sharpe >= self.min_sharpe:
            passed.append(
                f"SHARPE_GATE: Backtest Sharpe ({artifacts.backtest_sharpe:.2f}) "
                f">= Min ({self.min_sharpe:.2f}).")
        else:
            failed.append(
                f"SHARPE_GATE: Backtest Sharpe ({artifacts.backtest_sharpe:.2f}) "
                f"< Min ({self.min_sharpe:.2f}).")

        if artifacts.backtest_max_drawdown_pct <= self.max_drawdown:
            passed.append(
                f"DRAWDOWN_GATE: Backtest Max DD "
                f"({artifacts.backtest_max_drawdown_pct:.1f}%) <= Cap "
                f"({self.max_drawdown:.1f}%).")
        else:
            failed.append(
                f"DRAWDOWN_GATE: Backtest Max DD "
                f"({artifacts.backtest_max_drawdown_pct:.1f}%) > Cap "
                f"({self.max_drawdown:.1f}%).")

        # 4. Independence gate.
        #
        # Applies from PAPER_TRADING_SHADOW onward, i.e. to every promotion out
        # of INDEPENDENT_VALIDATION and beyond. Without it the stage named
        # "independent validation" imposed no requirement at all, and at
        # LIVE_PRODUCTION an author could sign off on their own strategy.
        if target_index >= PIPELINE_ORDER.index(_VALIDATOR_REQUIRED_FROM):
            validator = artifacts.validator_id.strip()
            author = artifacts.author_id.strip()
            if not validator:
                failed.append(
                    "INDEPENDENCE_GATE: No validator_id recorded; the strategy has "
                    "not been independently validated.")
            elif validator == author:
                failed.append(
                    f"INDEPENDENCE_GATE: validator_id and author_id are the same "
                    f"person ({validator!r}). Self-validation is not independent "
                    f"validation.")
            else:
                passed.append(
                    f"INDEPENDENCE_GATE: Independent validator {validator!r} "
                    f"recorded, distinct from author {author!r}.")

        # 5. Shadow execution evidence gates (entry to canary or live).
        if target_stage in _SHADOW_EVIDENCE_STAGES:
            if artifacts.paper_trading_days >= self.min_paper_days:
                passed.append(
                    f"PAPER_DAYS_GATE: Shadow paper trading duration "
                    f"({artifacts.paper_trading_days}d) >= Min "
                    f"({self.min_paper_days}d).")
            else:
                failed.append(
                    f"PAPER_DAYS_GATE: Shadow paper trading duration "
                    f"({artifacts.paper_trading_days}d) < Min "
                    f"({self.min_paper_days}d).")

            if artifacts.shadow_tracking_error_pct <= self.max_tracking_error:
                passed.append(
                    f"SHADOW_TRACKING_GATE: Shadow divergence "
                    f"({artifacts.shadow_tracking_error_pct:.2f}%) <= Cap "
                    f"({self.max_tracking_error:.2f}%).")
            else:
                failed.append(
                    f"SHADOW_TRACKING_GATE: Shadow divergence "
                    f"({artifacts.shadow_tracking_error_pct:.2f}%) > Cap "
                    f"({self.max_tracking_error:.2f}%).")

        # 6. Designated-person sign-off gate (RTS 6 Art. 5(2)), live entry only.
        if target_stage == PipelineStage.LIVE_PRODUCTION:
            if artifacts.has_risk_committee_signoff and artifacts.validator_id.strip():
                passed.append(
                    f"RISK_GOVERNANCE_GATE: Deployment authorised by designated "
                    f"approver {artifacts.validator_id.strip()!r}.")
            else:
                failed.append(
                    "RISK_GOVERNANCE_GATE: Missing formal sign-off from a "
                    "designated approver.")

        is_approved = not failed
        status_code = (
            PromotionStatus.APPROVED_FOR_PROMOTION.value if is_approved
            else PromotionStatus.REJECTED_GATES_FAILED.value
        )

        ledger_index = len(self._ledger)
        previous_hash = (
            self._ledger[-1].audit_trail_hash if self._ledger else GENESIS_HASH)
        audit_hash = compute_audit_hash(
            strategy_id, current_stage, target_stage, artifacts, self.thresholds,
            is_approved, status_code, passed, failed, decided_at_utc,
            previous_hash, ledger_index,
        )

        notes = (
            f"PIPELINE GOVERNANCE [{status_code}] ({strategy_id}): Transition "
            f"{current_stage.value} -> {target_stage.value} at {decided_at_utc}. "
            f"Passed Gates = {len(passed)}, Failed Gates = {len(failed)}. "
            f"Audit Hash = {audit_hash}."
        )

        decision = StagePromotionDecision(
            strategy_id=strategy_id,
            current_stage=current_stage,
            target_stage=target_stage,
            is_approved=is_approved,
            status_code=status_code,
            passed_gates=passed,
            failed_gates=failed,
            audit_trail_hash=audit_hash,
            audit_notes=notes,
            decided_at_utc=decided_at_utc,
            previous_audit_hash=previous_hash,
            ledger_index=ledger_index,
        )
        self._ledger.append(decision)

        if is_approved:
            logger.info(notes)
        else:
            logger.warning(notes)

        return decision

    def verify_ledger(
        self,
        artifacts_by_index: Optional[Dict[int, StagePromotionArtifacts]] = None,
    ) -> bool:
        """
        Checks the ledger's hash chain for tampering.

        Verifies that each entry's ``previous_audit_hash`` matches its
        predecessor's digest and that ``ledger_index`` is contiguous, so an
        entry cannot be removed or reordered undetected. Pass
        ``artifacts_by_index`` to additionally re-derive each digest from the
        original evidence, which is what detects an edit *within* an entry.

        This proves the record is internally consistent. It is a tamper-evidence
        check, not immutability: durability is a property of where the ledger is
        persisted, not of this list.
        """
        expected_previous = GENESIS_HASH
        for position, entry in enumerate(self._ledger):
            if entry.ledger_index != position:
                logger.warning(
                    "Ledger integrity failure at position %d: ledger_index is %d.",
                    position, entry.ledger_index)
                return False
            if entry.previous_audit_hash != expected_previous:
                logger.warning(
                    "Ledger chain broken at index %d: previous_audit_hash does "
                    "not match the preceding entry.", position)
                return False
            if artifacts_by_index is not None:
                artifacts = artifacts_by_index.get(position)
                if artifacts is None:
                    logger.warning(
                        "Ledger verification incomplete: no artifacts supplied "
                        "for index %d.", position)
                    return False
                if not verify_audit_hash(entry, artifacts, self.thresholds):
                    logger.warning(
                        "Ledger entry %d fails hash verification: its recorded "
                        "content does not match its digest.", position)
                    return False
            expected_previous = entry.audit_trail_hash
        return True
