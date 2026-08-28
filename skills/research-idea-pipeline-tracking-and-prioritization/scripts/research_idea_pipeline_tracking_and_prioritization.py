"""
research-idea-pipeline-tracking-and-prioritization: a research idea register,
lifecycle state machine, and multi-factor triage score for a quantitative
research backlog.

Purpose
-------
Answer one question for a research team with more ideas than researcher-months:
*which candidate hypothesis do we work on next, and what is already stuck?*
The engine registers each idea once, scores it on four declared inputs, ranks
the active backlog deterministically, enforces legal lifecycle transitions, and
keeps an append-only transition log so a rejected idea cannot quietly reappear
as new work.

Priority score
--------------
For an idea with expected Sharpe ``S``, estimated capacity ``C`` (US dollars),
implementation complexity ``k`` (1-5) and data cost tier ``d`` (1-5)::

    priority = S * log10(C) / (k * d)

**This is a house heuristic defined by this engine.** It is not published, not
standardised, and not derived from any regulator, exchange, or paper. Its only
job is to impose a consistent, inspectable ordering on a backlog that would
otherwise be ranked by whoever argued loudest. Four properties of the formula
matter and are enforced or documented rather than hidden:

1. **The capacity term is the logarithm of a dimensional quantity**, i.e. it is
   really ``log10(C / $1)`` with an implicit $1 anchor. Expressing capacity in
   any other unit shifts each score by ``S/(k*d)`` times the change of decade,
   which is a different amount for every idea and therefore **reorders the
   ranking**. Capacity must be passed in whole US dollars, and two reports are
   comparable only if both used the same unit and the same formula.
2. **The score is ordinal, not cardinal.** A score of 8 is not "twice as good"
   as 4. Use it to order a backlog, never as an expected-value estimate.
3. **Negative expected Sharpe breaks monotonicity.** With ``S < 0`` the
   denominator inverts the intended meaning: the same losing idea scores -14.0
   at complexity 1 and -2.8 at complexity 5, so *harder to build* would rank
   *better*. Negative ``expected_sharpe`` is rejected at registration; an idea
   whose research came back negative belongs in ``REJECTED``, not in the
   ranking.
4. **Capacity below $1 is outside the domain of the score** (``log10`` turns
   negative and flips the sign of the whole expression). It is rejected rather
   than silently floored.

The dominant error source is the input, not the arithmetic
----------------------------------------------------------
``expected_sharpe`` is supplied by the proposer. A Sharpe ratio taken from a
preliminary or exploratory backtest is biased upward by however many
specifications were tried before that one was reported, and this bias is
routinely larger than any difference this score resolves:

- Harvey, C.R., Liu, Y. and Zhu, H. (2016), "... and the Cross-Section of
  Expected Returns", *Review of Financial Studies* 29(1), 5-68: after
  accounting for the multiple testing behind hundreds of published factors, a
  newly discovered factor needs a t-ratio above roughly 3.0, not the
  conventional 2.0.
- Bailey, D.H. and Lopez de Prado, M. (2014), "The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting and Non-Normality",
  *Journal of Portfolio Management* 40(5), 94-107: gives the correction for a
  Sharpe ratio selected as the best of ``N`` trials.

Feed this engine a deflated or otherwise multiple-testing-adjusted Sharpe where
one is available, and record which convention was used. Ranking a deflated
Sharpe against an unadjusted one is meaningless. The same applies to the
annualisation: every ``expected_sharpe`` in one register must be stated at the
same horizon (annualised is the usual choice).

Limitations (documented, deliberate)
------------------------------------
- **The engine validates ranges, not truthfulness.** ``expected_sharpe`` and
  ``estimated_capacity_usd`` are unverifiable assertions by the proposer. The
  score inherits their optimism exactly.
- **No dependency or correlation modelling.** Two ideas that are the same trade
  in different clothes both rank highly; the score cannot see it. Cross-idea
  overlap belongs to ``cross-strategy-correlation-monitoring``.
- **Complexity and data cost are ordinal tiers**, multiplied as if they were
  cardinal. A tier-4 idea is not literally twice the work of a tier-2 idea.
- **Staleness is measured from the last stage change**, not from the last work
  done on the idea. An idea genuinely being researched for 60 days in
  ``BACKTESTING`` is reported as stalled; that is the intended prompt for a
  review, not an accusation.
- **This is a triage aid, not an approval control.** It records no sign-off, no
  testing evidence, and no segregation of duties. Pre-deployment algorithm
  approval belongs to ``strategy-research-to-production-pipeline-governance``
  and the jurisdiction-specific compliance skills.
"""
import logging
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Mapping, Optional, Tuple, Union

logger = logging.getLogger(__name__)

#: Documented inclusive range for ``implementation_complexity`` and
#: ``data_cost_tier``. Both are ordinal tiers: 1 = low, 5 = high.
MIN_TIER = 1
MAX_TIER = 5

#: Smallest capacity the priority score is defined for. ``log10`` is negative
#: below $1, which would flip the sign of the whole score.
MIN_CAPACITY_USD = 1.0


class ResearchPipelineError(ValueError):
    """Raised for invalid pipeline input, unknown ideas, or illegal transitions."""


class PipelineStage(str, Enum):
    """Lifecycle stages an idea may occupy. String-valued for report keys."""

    PROPOSED = "PROPOSED"
    BACKTESTING = "BACKTESTING"
    PAPER_TRADING = "PAPER_TRADING"
    PRODUCTION_READY = "PRODUCTION_READY"
    REJECTED = "REJECTED"


#: Stages excluded from the active ranking.
INACTIVE_STAGES: Tuple[PipelineStage, ...] = (PipelineStage.REJECTED,)

#: Stages where elapsed time is not a signal: the pipeline's work is finished.
TERMINAL_STAGES: Tuple[PipelineStage, ...] = (
    PipelineStage.PRODUCTION_READY,
    PipelineStage.REJECTED,
)

#: Legal forward and backward transitions. Research iterates, so one-step
#: demotions are allowed; REJECTED is terminal and is left only through the
#: explicit, logged ``reopen_idea`` call.
ALLOWED_TRANSITIONS: Mapping[PipelineStage, Tuple[PipelineStage, ...]] = {
    PipelineStage.PROPOSED: (PipelineStage.BACKTESTING, PipelineStage.REJECTED),
    PipelineStage.BACKTESTING: (
        PipelineStage.PAPER_TRADING,
        PipelineStage.PROPOSED,
        PipelineStage.REJECTED,
    ),
    PipelineStage.PAPER_TRADING: (
        PipelineStage.PRODUCTION_READY,
        PipelineStage.BACKTESTING,
        PipelineStage.REJECTED,
    ),
    PipelineStage.PRODUCTION_READY: (PipelineStage.REJECTED,),
    PipelineStage.REJECTED: (),
}


def _utc_now() -> datetime:
    """Default clock. Injectable so reports are deterministic under test."""
    return datetime.now(timezone.utc)


def _coerce_stage(value: Union[str, PipelineStage], context: str) -> PipelineStage:
    """Accepts a PipelineStage or a case-insensitive stage name."""
    if isinstance(value, PipelineStage):
        return value
    if not isinstance(value, str):
        raise ResearchPipelineError(
            f"{context}: stage must be a PipelineStage or str, got {type(value).__name__}"
        )
    try:
        return PipelineStage(value.strip().upper())
    except ValueError:
        legal = ", ".join(s.value for s in PipelineStage)
        raise ResearchPipelineError(
            f"{context}: unknown stage {value!r}. A misspelt stage would leave a "
            f"rejected idea in the active ranking. Legal stages: {legal}"
        ) from None


def _require_finite(value: object, context: str) -> float:
    """Rejects NaN/Inf before they reach the score and poison the ranking."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchPipelineError(
            f"{context}: expected a real number, got {type(value).__name__}"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ResearchPipelineError(
            f"{context}: must be finite, got {numeric!r}. A non-finite input produces "
            f"a NaN priority score, and NaN compares False against everything, so the "
            f"idea can surface at rank 1 while looking like a computed result."
        )
    return numeric


def _require_tier(value: object, context: str) -> int:
    """Enforces the documented 1-5 ordinal tier instead of silently clamping."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchPipelineError(
            f"{context}: expected an int in [{MIN_TIER}, {MAX_TIER}], "
            f"got {type(value).__name__}"
        )
    if not MIN_TIER <= value <= MAX_TIER:
        raise ResearchPipelineError(
            f"{context}: must be an integer in [{MIN_TIER}, {MAX_TIER}], got {value}. "
            f"Clamping an out-of-range tier to 1 awards the maximum possible score to "
            f"the worst-specified idea."
        )
    return value


def _require_text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchPipelineError(
            f"{context}: must be a non-empty string, got {value!r}"
        )
    return value.strip()


def _require_aware(moment: object, context: str) -> datetime:
    if not isinstance(moment, datetime):
        raise ResearchPipelineError(
            f"{context}: expected a datetime, got {type(moment).__name__}"
        )
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ResearchPipelineError(
            f"{context}: datetime must be timezone-aware. A naive timestamp compared "
            f"against a UTC clock silently mis-measures time-in-stage by the local "
            f"UTC offset."
        )
    return moment


@dataclass(frozen=True)
class ResearchIdea:
    """
    One candidate research hypothesis. Frozen: the engine stores its own copy so
    a caller cannot mutate ``expected_sharpe`` or ``stage`` after registration
    and bypass validation.

    ``estimated_capacity_usd`` must be in whole US dollars -- see the module
    docstring on why the unit changes the ranking.
    """

    idea_id: str
    title: str
    author: str
    expected_sharpe: float
    estimated_capacity_usd: float
    implementation_complexity: int
    data_cost_tier: int
    stage: PipelineStage = PipelineStage.PROPOSED

    def __post_init__(self) -> None:
        ctx = f"idea {self.idea_id!r}"
        object.__setattr__(self, "idea_id", _require_text(self.idea_id, "idea_id"))
        object.__setattr__(self, "title", _require_text(self.title, f"{ctx} title"))
        object.__setattr__(self, "author", _require_text(self.author, f"{ctx} author"))

        sharpe = _require_finite(self.expected_sharpe, f"{ctx} expected_sharpe")
        if sharpe < 0.0:
            raise ResearchPipelineError(
                f"{ctx} expected_sharpe: must be >= 0, got {sharpe}. The score divides "
                f"by complexity, so a negative Sharpe ranks a harder idea above an "
                f"easier one. Record a losing idea as REJECTED instead of scoring it."
            )
        object.__setattr__(self, "expected_sharpe", sharpe)

        capacity = _require_finite(
            self.estimated_capacity_usd, f"{ctx} estimated_capacity_usd"
        )
        if capacity < MIN_CAPACITY_USD:
            raise ResearchPipelineError(
                f"{ctx} estimated_capacity_usd: must be >= {MIN_CAPACITY_USD} USD, got "
                f"{capacity}. log10 is negative below $1, which inverts the sign of the "
                f"priority score."
            )
        object.__setattr__(self, "estimated_capacity_usd", capacity)

        object.__setattr__(
            self,
            "implementation_complexity",
            _require_tier(
                self.implementation_complexity, f"{ctx} implementation_complexity"
            ),
        )
        object.__setattr__(
            self,
            "data_cost_tier",
            _require_tier(self.data_cost_tier, f"{ctx} data_cost_tier"),
        )
        object.__setattr__(self, "stage", _coerce_stage(self.stage, ctx))


@dataclass(frozen=True)
class StageTransition:
    """One append-only audit record of a lifecycle change."""

    idea_id: str
    from_stage: PipelineStage
    to_stage: PipelineStage
    at: datetime
    reason: str


@dataclass(frozen=True)
class PrioritizedIdea:
    """One ranked, active idea. ``priority_score`` is exact, not rounded."""

    idea_id: str
    title: str
    stage: PipelineStage
    priority_score: float
    rank: int
    below_priority_threshold: bool = False


@dataclass(frozen=True)
class StalledIdea:
    """A non-terminal idea that has not changed stage recently enough."""

    idea_id: str
    title: str
    stage: PipelineStage
    days_in_stage: float


@dataclass(frozen=True)
class ResearchPipelineReport:
    """
    Snapshot of the register.

    ``ranked_ideas`` holds every active idea in rank order; ``top_priority_ideas``
    is the first ``top_n`` of that same list -- the shortlist, not the backlog.
    ``status`` distinguishes an empty register (``NO_IDEAS``) from one where every
    idea has been rejected (``NO_ACTIVE_IDEAS``).
    """

    total_ideas: int
    active_ideas: int
    top_priority_ideas: Tuple[PrioritizedIdea, ...]
    stage_breakdown: Dict[str, int]
    status: str
    audit_notes: str
    ranked_ideas: Tuple[PrioritizedIdea, ...] = ()
    stalled_ideas: Tuple[StalledIdea, ...] = ()
    below_threshold_count: int = 0
    generated_at: Optional[datetime] = None


@dataclass
class _IdeaRecord:
    """Engine-internal state: the idea plus the timing and history it needs."""

    idea: ResearchIdea
    registered_at: datetime
    stage_entered_at: datetime
    history: List[StageTransition] = field(default_factory=list)


class ResearchIdeaPipelineTrackingAndPrioritizationEngine:
    """
    Research idea register, lifecycle state machine, and multi-factor triage
    score. See the module docstring for the score's definition and its limits.

    Parameters
    ----------
    min_priority_score:
        Ideas scoring below this are still ranked and reported, but flagged
        ``below_priority_threshold`` so a review can prune them. Nothing is ever
        silently dropped -- an idea that vanishes from the report is an idea a
        reviewer cannot decide about. House default; calibrate it.
    top_n:
        Size of the ``top_priority_ideas`` shortlist.
    max_stage_age_days:
        Days in a non-terminal stage after which an idea is reported as stalled.
        House default, no external basis.
    clock:
        Returns the current timezone-aware UTC time. Injectable for testing.
    """

    def __init__(
        self,
        min_priority_score: float = 1.0,
        top_n: int = 5,
        max_stage_age_days: float = 30.0,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        threshold = _require_finite(min_priority_score, "min_priority_score")
        if threshold < 0.0:
            raise ResearchPipelineError(
                f"min_priority_score: must be >= 0, got {threshold}"
            )
        if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
            raise ResearchPipelineError(f"top_n: must be an int >= 1, got {top_n!r}")
        max_age = _require_finite(max_stage_age_days, "max_stage_age_days")
        if max_age <= 0.0:
            raise ResearchPipelineError(f"max_stage_age_days: must be > 0, got {max_age}")
        if not callable(clock):
            raise ResearchPipelineError("clock: must be a callable returning a datetime")

        self.min_priority_score = threshold
        self.top_n = top_n
        self.max_stage_age_days = max_age
        self._clock = clock
        self._records: Dict[str, _IdeaRecord] = {}

    # ------------------------------------------------------------------- state

    @property
    def ideas(self) -> Dict[str, ResearchIdea]:
        """Registered ideas by id. A copy: mutating it does not touch the register."""
        return {idea_id: rec.idea for idea_id, rec in self._records.items()}

    def get_idea(self, idea_id: str) -> ResearchIdea:
        """Returns one registered idea, or raises if the id is unknown."""
        return self._record(idea_id).idea

    def get_history(self, idea_id: str) -> Tuple[StageTransition, ...]:
        """Append-only transition log for one idea, oldest first."""
        return tuple(self._record(idea_id).history)

    def _record(self, idea_id: str) -> _IdeaRecord:
        try:
            return self._records[idea_id]
        except KeyError:
            raise ResearchPipelineError(f"unknown idea_id {idea_id!r}") from None

    def _now(self) -> datetime:
        return _require_aware(self._clock(), "clock()")

    # ------------------------------------------------------------ registration

    def add_idea(self, idea: ResearchIdea) -> None:
        """
        Registers a research idea.

        Raises on a duplicate ``idea_id``: silently overwriting would discard the
        existing idea's stage and its entire transition history, which is exactly
        the record this register exists to keep.
        """
        if not isinstance(idea, ResearchIdea):
            raise ResearchPipelineError(
                f"add_idea: expected a ResearchIdea, got {type(idea).__name__}"
            )
        if idea.idea_id in self._records:
            existing = self._records[idea.idea_id].idea
            raise ResearchPipelineError(
                f"duplicate idea_id {idea.idea_id!r} (already registered as "
                f"{existing.title!r}, stage {existing.stage.value}). Re-registering "
                f"would erase its stage and history; use update_stage, or register the "
                f"new idea under its own id."
            )
        now = self._now()
        self._records[idea.idea_id] = _IdeaRecord(
            idea=replace(idea), registered_at=now, stage_entered_at=now
        )
        logger.info(
            "Registered idea %s (%s) at stage %s",
            idea.idea_id,
            idea.title,
            idea.stage.value,
        )

    # ----------------------------------------------------------- state machine

    def update_stage(
        self,
        idea_id: str,
        new_stage: Union[str, PipelineStage],
        reason: str = "",
    ) -> bool:
        """
        Moves an idea to ``new_stage`` if the transition is legal.

        Unknown ids, unknown stage names, and illegal transitions raise rather
        than returning False -- a silent no-op leaves the caller believing an
        idea was rejected while it is still being ranked. ``reason`` is mandatory
        for a move to ``REJECTED`` so the register records *why*, which is the
        whole point of not re-researching a dead idea a year later.

        Returns True when a transition was applied, False when the idea is
        already at ``new_stage``.
        """
        record = self._record(idea_id)
        target = _coerce_stage(new_stage, f"update_stage({idea_id!r})")
        current = record.idea.stage

        if target is current:
            logger.debug(
                "Idea %s already at stage %s; no transition", idea_id, current.value
            )
            return False

        if target is PipelineStage.REJECTED and not (reason or "").strip():
            raise ResearchPipelineError(
                f"update_stage({idea_id!r} -> REJECTED): a reason is required. An "
                f"unexplained rejection cannot stop the same idea being proposed again."
            )

        if target not in ALLOWED_TRANSITIONS[current]:
            legal = ", ".join(s.value for s in ALLOWED_TRANSITIONS[current]) or "none"
            extra = (
                " Use reopen_idea() to revive a rejected idea; it keeps the rejection "
                "in the history."
                if current is PipelineStage.REJECTED
                else ""
            )
            raise ResearchPipelineError(
                f"illegal transition for {idea_id!r}: {current.value} -> {target.value}. "
                f"Legal from {current.value}: {legal}.{extra}"
            )

        self._apply_transition(record, target, reason)
        return True

    def reopen_idea(self, idea_id: str, reason: str) -> None:
        """
        Explicitly returns a REJECTED idea to PROPOSED, keeping the rejection in
        its history. Separate from ``update_stage`` so reviving a dead idea is a
        deliberate, logged act rather than a stage string.
        """
        record = self._record(idea_id)
        if record.idea.stage is not PipelineStage.REJECTED:
            raise ResearchPipelineError(
                f"reopen_idea({idea_id!r}): only a REJECTED idea can be reopened; it is "
                f"at {record.idea.stage.value}."
            )
        if not (reason or "").strip():
            raise ResearchPipelineError(
                f"reopen_idea({idea_id!r}): a reason is required -- what changed since "
                f"the rejection?"
            )
        self._apply_transition(record, PipelineStage.PROPOSED, reason)

    def _apply_transition(
        self, record: _IdeaRecord, target: PipelineStage, reason: str
    ) -> None:
        now = self._now()
        transition = StageTransition(
            idea_id=record.idea.idea_id,
            from_stage=record.idea.stage,
            to_stage=target,
            at=now,
            reason=(reason or "").strip(),
        )
        record.idea = replace(record.idea, stage=target)
        record.stage_entered_at = now
        record.history.append(transition)
        logger.info(
            "Idea %s: %s -> %s (%s)",
            transition.idea_id,
            transition.from_stage.value,
            transition.to_stage.value,
            transition.reason or "no reason given",
        )

    # ----------------------------------------------------------------- scoring

    def calculate_priority_score(self, idea: ResearchIdea) -> float:
        """
        Priority score = expected_sharpe * log10(capacity_usd) / (complexity * data_cost).

        Returns the exact value, unrounded: the ranking sorts on it, and rounding
        before sorting manufactures ties that are then broken by registration
        order. Format for display at the point of display.
        """
        if not isinstance(idea, ResearchIdea):
            raise ResearchPipelineError(
                f"calculate_priority_score: expected a ResearchIdea, got "
                f"{type(idea).__name__}"
            )
        capacity_factor = math.log10(idea.estimated_capacity_usd)
        denominator = idea.implementation_complexity * idea.data_cost_tier
        return (idea.expected_sharpe * capacity_factor) / denominator

    # ------------------------------------------------------------------ report

    def generate_pipeline_report(self) -> ResearchPipelineReport:
        """
        Scores every active idea, ranks them deterministically, and reports stage
        counts, the stalled backlog, and how many ideas fall below
        ``min_priority_score``.

        Ties are broken by ``idea_id`` so two ideas with identical scores rank in
        the same order on every run, independent of registration order.
        """
        now = self._now()
        stage_counts: Dict[str, int] = {stage.value: 0 for stage in PipelineStage}
        scored: List[Tuple[float, str, ResearchIdea]] = []
        stalled: List[StalledIdea] = []

        for record in self._records.values():
            idea = record.idea
            stage_counts[idea.stage.value] += 1

            if idea.stage not in TERMINAL_STAGES:
                days_in_stage = (now - record.stage_entered_at).total_seconds() / 86400.0
                if days_in_stage < 0.0:
                    # A clock that moved backwards makes every idea look fresh and
                    # silently disables stall detection. Say so rather than hide it.
                    logger.warning(
                        "Idea %s entered %s at %s, which is after the current clock "
                        "reading %s; stall detection is unreliable for this report.",
                        idea.idea_id,
                        idea.stage.value,
                        record.stage_entered_at.isoformat(),
                        now.isoformat(),
                    )
                if days_in_stage > self.max_stage_age_days:
                    stalled.append(
                        StalledIdea(
                            idea_id=idea.idea_id,
                            title=idea.title,
                            stage=idea.stage,
                            days_in_stage=days_in_stage,
                        )
                    )

            if idea.stage not in INACTIVE_STAGES:
                scored.append((self.calculate_priority_score(idea), idea.idea_id, idea))

        scored.sort(key=lambda item: (-item[0], item[1]))
        ranked = tuple(
            PrioritizedIdea(
                idea_id=idea.idea_id,
                title=idea.title,
                stage=idea.stage,
                priority_score=score,
                rank=position,
                below_priority_threshold=score < self.min_priority_score,
            )
            for position, (score, _idea_id, idea) in enumerate(scored, 1)
        )
        stalled.sort(key=lambda item: (-item.days_in_stage, item.idea_id))

        total = len(self._records)
        active = len(ranked)
        below_threshold = sum(1 for item in ranked if item.below_priority_threshold)

        if total == 0:
            status = "NO_IDEAS"
        elif active == 0:
            status = "NO_ACTIVE_IDEAS"
        else:
            status = "PIPELINE_ACTIVE"

        if active > 0:
            top = ranked[0]
            notes = (
                f"RESEARCH PIPELINE [{status}]: Total Ideas = {total}, Active = {active}, "
                f"Top Idea = '{top.title}' (Score: {top.priority_score:.4f}), "
                f"Below threshold ({self.min_priority_score:g}) = {below_threshold}, "
                f"Stalled (> {self.max_stage_age_days:g}d in stage) = {len(stalled)}"
            )
        else:
            notes = (
                f"RESEARCH PIPELINE [{status}]: Total Ideas = {total}, no active ideas "
                f"to rank."
            )

        logger.info(notes)
        if stalled:
            logger.warning(
                "%d idea(s) stalled beyond %g days: %s",
                len(stalled),
                self.max_stage_age_days,
                ", ".join(f"{item.idea_id}@{item.stage.value}" for item in stalled),
            )

        return ResearchPipelineReport(
            total_ideas=total,
            active_ideas=active,
            top_priority_ideas=ranked[: self.top_n],
            stage_breakdown=stage_counts,
            status=status,
            audit_notes=notes,
            ranked_ideas=ranked,
            stalled_ideas=tuple(stalled),
            below_threshold_count=below_threshold,
            generated_at=now,
        )
