"""
disaster-recovery-runbook-for-full-region-outage: gated executor for a
cross-region trading-stack failover.

What this module is and is not
------------------------------
It is a **runbook executor with safety interlocks**. It sequences the steps of a
region failover, refuses to advance past a step whose safety precondition is not
evidenced, and produces an auditable report of what ran, what was blocked, and
why. It is used both to drive an automated failover and to rehearse one.

It is **not** an AWS client: it calls no APIs and moves no traffic. The caller
performs each action and reports back what actually happened. Consequently the
engine can only be as honest as its inputs -- but unlike a simulator that
assumes success, it *forces* the caller to assert the two facts that make a
failover safe rather than catastrophic:

* ``primary_write_fenced`` -- writes in the dead region are confirmed stopped.
* ``cancel_all_confirmed`` -- open orders are confirmed cancelled venue-side.

Both default to ``False``. A failover run that does not supply them does not
"pass with a warning"; it stops at the interlock.

Why those two interlocks
------------------------
**Split-brain.** Aurora Global Database's write fencing is explicitly
best-effort: "Because fencing writes is a best-effort attempt, it's possible
that writes might be momentarily accepted in the old primary Region, causing
split-brain issues", and "Failovers are also susceptible to *split-brain*
issues". AWS's own pre-failover guidance is to "take applications offline" to
prevent writes reaching the old primary. Promotion is therefore gated on
positive evidence of fencing, not on the assumption that it worked.

**Orders outliving the region.** A DNS switchover does not disconnect anyone:
"clients with pre-existing open connections might continue to make requests
against the impaired location until the clients reconnect" (the ALB HTTP client
keepalive default is 3600 seconds). Venue-side Cancel on Disconnect is a
backstop with documented holes -- CME's COD "does not include GTC (Good Till
Cancel) and GTD (Good Till Date) orders" and "is not invoked for a graceful
disconnect". So resting orders can and do survive the loss of the region that
placed them. Resuming trading in the secondary region before cancellation is
confirmed is how one flat book becomes two live ones.

RTO accounting
--------------
Total RTO includes DNS TTL, not just the sum of step durations. Traffic has not
moved when the record is updated; resolvers serve the cached answer until it
expires. AWS recommends "Setting a TTL of 60 or 120 seconds" for failover
records -- so a TTL at or above the RTO budget makes the budget unattainable no
matter how fast the steps run.

See ``references/standards.md`` for sources.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

STEP_OUTAGE_VERIFICATION = "OUTAGE_VERIFICATION"
STEP_CANCEL_OPEN_ORDERS = "CANCEL_OPEN_ORDERS"
STEP_PROMOTE_SECONDARY_DB = "PROMOTE_SECONDARY_DB"
STEP_DNS_SWITCHOVER = "DNS_SWITCHOVER"
STEP_COMPUTE_BOOTSTRAP_RECONCILE = "COMPUTE_BOOTSTRAP_RECONCILE"
STEP_RESUME_TRADING = "RESUME_TRADING"

#: Mandated execution order. Cancellation precedes promotion and DNS movement:
#: the book must be flat before a second region can accept traffic.
FAILOVER_STEP_ORDER: Tuple[str, ...] = (
    STEP_OUTAGE_VERIFICATION,
    STEP_CANCEL_OPEN_ORDERS,
    STEP_PROMOTE_SECONDARY_DB,
    STEP_DNS_SWITCHOVER,
    STEP_COMPUTE_BOOTSTRAP_RECONCILE,
    STEP_RESUME_TRADING,
)

DEFAULT_STEP_LATENCIES: Dict[str, float] = {
    STEP_OUTAGE_VERIFICATION: 5.0,
    STEP_CANCEL_OPEN_ORDERS: 10.0,
    STEP_PROMOTE_SECONDARY_DB: 45.0,
    STEP_DNS_SWITCHOVER: 15.0,
    STEP_COMPUTE_BOOTSTRAP_RECONCILE: 30.0,
    STEP_RESUME_TRADING: 5.0,
}

# Outcomes. `is_failover_successful` is True only for the two SUCCESSFUL_*
# outcomes; everything else leaves the desk halted and needing a human.
OUTCOME_SUCCESSFUL = "FAILOVER_SUCCESSFUL"
OUTCOME_SUCCESSFUL_WITH_DATA_LOSS = "FAILOVER_SUCCESSFUL_WITH_ACCEPTED_DATA_LOSS"
#: Secondary region is up and reconciled, but trading was NOT resumed.
OUTCOME_DEGRADED_TRADING_HALTED = "FAILOVER_DEGRADED_TRADING_HALTED"
#: Stopped at a safety interlock before the secondary took over.
OUTCOME_ABORTED = "FAILOVER_ABORTED_AT_INTERLOCK"
OUTCOME_FAILED = "FAILOVER_FAILED"

STATUS_BLOCKED = "BLOCKED"


class DrFailoverError(ValueError):
    """Raised when the engine is configured or driven with unusable inputs.

    A DR executor that accepts nonsense (failing over to the region that just
    died, a negative step duration) and still emits a confident report is worse
    than no executor at all -- it manufactures the evidence a post-incident
    review will rely on.
    """


@dataclass
class FailoverStepResult:
    step_number: int
    step_name: str
    is_success: bool
    elapsed_seconds: float
    message: str
    #: True when the step never ran because a precondition was not met. A
    #: blocked step is not a failed step: nothing was attempted.
    is_blocked: bool = False


@dataclass
class RegionDrFailoverReport:
    primary_region: str
    secondary_region: str
    total_elapsed_seconds: float        # RTO achieved, including DNS TTL
    rto_sla_seconds: float
    rpo_replication_lag_seconds: float  # RPO achieved (replication lag at promotion)
    is_failover_successful: bool
    is_rto_compliant: bool
    executed_steps: List[FailoverStepResult]
    rpo_sla_seconds: float = 15.0
    is_rpo_compliant: bool = False
    #: One of the OUTCOME_* constants -- the field to branch on. A boolean
    #: cannot distinguish "trading resumed" from "secondary up, desk halted".
    outcome: str = OUTCOME_FAILED
    #: True only when the whole sequence ran and trading was resumed.
    is_trading_resumed: bool = False
    dns_ttl_seconds: float = 0.0
    findings: List[str] = field(default_factory=list)


class RegionDrFailoverExecutorEngine:
    """Executes a region failover runbook with safety interlocks between steps.

    Deterministic and side-effect free: the same reported facts produce the same
    report, so a rehearsal and a real incident are directly comparable.
    """

    def __init__(
        self,
        primary_region: str = "us-east-1",
        secondary_region: str = "us-west-2",
        rto_sla_sec: float = 300.0,
        max_rpo_sec: float = 15.0,
    ) -> None:
        if not primary_region or not secondary_region:
            raise DrFailoverError("primary_region and secondary_region must both be non-empty")
        if primary_region == secondary_region:
            raise DrFailoverError(
                f"primary_region and secondary_region are both {primary_region!r}; "
                "a failover target inside the failed region is not a failover"
            )
        if not _is_positive(rto_sla_sec):
            raise DrFailoverError(f"rto_sla_sec must be a positive number, got {rto_sla_sec!r}")
        if not _is_finite(max_rpo_sec) or max_rpo_sec < 0:
            raise DrFailoverError(f"max_rpo_sec must be a non-negative number, got {max_rpo_sec!r}")
        self.primary_region = primary_region
        self.secondary_region = secondary_region
        self.rto_sla_sec = rto_sla_sec
        self.max_rpo_sec = max_rpo_sec

    def execute_region_failover(
        self,
        replication_lag_sec: float = 2.5,
        simulated_step_latencies: Optional[Dict[str, float]] = None,
        primary_write_fenced: bool = False,
        cancel_all_confirmed: bool = False,
        accept_data_loss: bool = False,
        dns_ttl_sec: float = 60.0,
        failed_steps: Optional[Iterable[str]] = None,
    ) -> RegionDrFailoverReport:
        """Run the failover sequence, stopping at the first unmet interlock.

        Args:
            replication_lag_sec: Replication lag to the secondary at the moment
                of promotion -- the data loss the promotion would accept. For
                Aurora Global Database this is the ``AuroraGlobalDBRPOLag``
                metric read from the secondary.
            simulated_step_latencies: Per-step durations in seconds. Keys must
                come from :data:`FAILOVER_STEP_ORDER`; unknown keys raise rather
                than being silently dropped, since a typo would otherwise leave
                a step timed by its default.
            primary_write_fenced: Positive evidence that writes in the primary
                region have stopped (the Aurora write-fencing event observed, or
                applications confirmed offline). Gates promotion. Defaults to
                ``False`` -- absence of evidence is not evidence of fencing.
            cancel_all_confirmed: Positive evidence that resting orders are
                cancelled venue-side, checked against a venue query rather than
                inferred from a dispatched cancel request. Gates resuming
                trading. Defaults to ``False``.
            accept_data_loss: Explicitly accept promoting with replication lag
                beyond the RPO objective, mirroring the ``--allow-data-loss``
                flag that AWS requires to turn a switchover into a failover.
                Without it, an over-RPO promotion is blocked.
            dns_ttl_sec: TTL on the failover DNS records. Added to the RTO,
                because traffic has not moved until cached answers expire.
            failed_steps: Steps the operator reports as having failed. Their
                dependents are blocked rather than executed.

        Returns:
            The populated :class:`RegionDrFailoverReport`.

        Raises:
            DrFailoverError: On unusable inputs -- non-finite or negative
                durations, an unknown step name, or a negative replication lag.
        """
        latencies = self._resolve_latencies(simulated_step_latencies)
        failures = self._resolve_failed_steps(failed_steps)
        if not _is_finite(replication_lag_sec) or replication_lag_sec < 0:
            raise DrFailoverError(
                f"replication_lag_sec must be a non-negative number, got {replication_lag_sec!r}"
            )
        if not _is_finite(dns_ttl_sec) or dns_ttl_sec < 0:
            raise DrFailoverError(f"dns_ttl_sec must be a non-negative number, got {dns_ttl_sec!r}")

        steps: List[FailoverStepResult] = []
        findings: List[str] = []
        total_time = 0.0
        blocked = False  # once an interlock trips, nothing downstream runs

        is_rpo_ok = replication_lag_sec <= self.max_rpo_sec
        if not is_rpo_ok:
            findings.append(
                f"RPO BREACH: replication lag {replication_lag_sec}s exceeds the "
                f"{self.max_rpo_sec}s objective; promoting accepts that much data loss."
            )

        for number, name in enumerate(FAILOVER_STEP_ORDER, start=1):
            if blocked:
                steps.append(self._blocked(number, name, "Upstream step did not complete."))
                continue

            block_reason = self._interlock_reason(
                name,
                primary_write_fenced=primary_write_fenced,
                cancel_all_confirmed=cancel_all_confirmed,
                is_rpo_ok=is_rpo_ok,
                accept_data_loss=accept_data_loss,
                replication_lag_sec=replication_lag_sec,
            )
            if block_reason is not None:
                steps.append(self._blocked(number, name, block_reason))
                findings.append(f"INTERLOCK [{name}]: {block_reason}")
                blocked = True
                continue

            elapsed = latencies[name]
            if name in failures:
                total_time += elapsed
                steps.append(
                    FailoverStepResult(
                        number, name, False, elapsed,
                        f"Step reported FAILED by the operator after {elapsed}s.",
                    )
                )
                findings.append(f"STEP FAILED [{name}]: downstream steps blocked.")
                blocked = True
                continue

            total_time += elapsed
            steps.append(
                FailoverStepResult(number, name, True, elapsed, self._success_message(name))
            )

        dns_done = self._step_succeeded(steps, STEP_DNS_SWITCHOVER)
        if dns_done:
            # Traffic has not actually moved until cached answers expire.
            total_time += dns_ttl_sec
            if dns_ttl_sec >= self.rto_sla_sec:
                findings.append(
                    f"DNS TTL {dns_ttl_sec}s is at or above the {self.rto_sla_sec}s RTO budget; "
                    "the objective is unreachable regardless of step speed. AWS suggests 60-120s "
                    "for failover records."
                )
            findings.append(
                "DNS switchover moves NEW connections only; pre-existing client connections keep "
                "reaching the impaired region until they reconnect (ALB client keepalive defaults "
                "to 3600s). Do not treat the DNS step as having drained the primary."
            )

        resumed = self._step_succeeded(steps, STEP_RESUME_TRADING)
        is_rto_ok = total_time <= self.rto_sla_sec
        if not is_rto_ok:
            findings.append(
                f"RTO BREACH: {round(total_time, 2)}s elapsed against a {self.rto_sla_sec}s objective."
            )

        outcome = self._classify(steps, resumed, is_rto_ok, is_rpo_ok, accept_data_loss)
        is_success = outcome in (OUTCOME_SUCCESSFUL, OUTCOME_SUCCESSFUL_WITH_DATA_LOSS)

        logger.critical(
            "REGIONAL DR FAILOVER [%s -> %s]: outcome=%s rto=%.1fs(sla %.1fs, incl dns_ttl=%.1fs) "
            "rpo_lag=%.1fs(max %.1fs) trading_resumed=%s",
            self.primary_region, self.secondary_region, outcome, total_time,
            self.rto_sla_sec, dns_ttl_sec if dns_done else 0.0, replication_lag_sec,
            self.max_rpo_sec, resumed,
        )

        return RegionDrFailoverReport(
            primary_region=self.primary_region,
            secondary_region=self.secondary_region,
            total_elapsed_seconds=round(total_time, 2),
            rto_sla_seconds=self.rto_sla_sec,
            rpo_replication_lag_seconds=replication_lag_sec,
            is_failover_successful=is_success,
            is_rto_compliant=is_rto_ok,
            executed_steps=steps,
            rpo_sla_seconds=self.max_rpo_sec,
            is_rpo_compliant=is_rpo_ok,
            outcome=outcome,
            is_trading_resumed=resumed,
            dns_ttl_seconds=dns_ttl_sec if dns_done else 0.0,
            findings=findings,
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _resolve_latencies(overrides: Optional[Dict[str, float]]) -> Dict[str, float]:
        latencies = dict(DEFAULT_STEP_LATENCIES)
        for name, value in (overrides or {}).items():
            if name not in DEFAULT_STEP_LATENCIES:
                raise DrFailoverError(
                    f"Unknown failover step {name!r}; expected one of {list(FAILOVER_STEP_ORDER)}"
                )
            if not _is_finite(value) or value < 0:
                raise DrFailoverError(
                    f"Latency for {name!r} must be a non-negative number, got {value!r}"
                )
            latencies[name] = float(value)
        return latencies

    @staticmethod
    def _resolve_failed_steps(failed_steps: Optional[Iterable[str]]) -> Set[str]:
        failures = set(failed_steps or ())
        unknown = failures - set(FAILOVER_STEP_ORDER)
        if unknown:
            raise DrFailoverError(
                f"Unknown failover step(s) in failed_steps: {sorted(unknown)}; "
                f"expected names from {list(FAILOVER_STEP_ORDER)}"
            )
        return failures

    @staticmethod
    def _interlock_reason(
        step_name: str,
        *,
        primary_write_fenced: bool,
        cancel_all_confirmed: bool,
        is_rpo_ok: bool,
        accept_data_loss: bool,
        replication_lag_sec: float,
    ) -> Optional[str]:
        """Return why this step must not run, or ``None`` to proceed."""
        if step_name == STEP_PROMOTE_SECONDARY_DB:
            if not primary_write_fenced:
                return (
                    "Primary-region writes are not confirmed fenced. Aurora write fencing is "
                    "best-effort and may momentarily accept writes in the old primary; promoting "
                    "now risks split-brain. Take applications offline or confirm the fencing event."
                )
            if not is_rpo_ok and not accept_data_loss:
                return (
                    f"Replication lag {replication_lag_sec}s exceeds the RPO objective and "
                    "accept_data_loss was not set. Promotion discards unreplicated writes, so it "
                    "must be an explicit decision (as AWS requires --allow-data-loss)."
                )
        if step_name == STEP_RESUME_TRADING and not cancel_all_confirmed:
            return (
                "Open-order cancellation is not confirmed venue-side. Resting orders can outlive "
                "the region that placed them -- Cancel on Disconnect excludes GTC/GTD orders and "
                "does not fire on a graceful disconnect -- so resuming here risks trading against "
                "a book that is not flat. Reconcile with the venue, then resume manually."
            )
        return None

    def _success_message(self, step_name: str) -> str:
        messages = {
            STEP_OUTAGE_VERIFICATION:
                f"Primary region {self.primary_region!r} outage corroborated by independent health signals.",
            STEP_CANCEL_OPEN_ORDERS:
                "Cancel-all dispatched to broker/venue adapters (dispatch only -- confirmation is separate).",
            STEP_PROMOTE_SECONDARY_DB:
                f"Secondary cluster in {self.secondary_region!r} promoted to read-write primary.",
            STEP_DNS_SWITCHOVER:
                f"Routing control state updated toward {self.secondary_region!r} via the ARC data plane API.",
            STEP_COMPUTE_BOOTSTRAP_RECONCILE:
                f"Execution nodes bootstrapped in {self.secondary_region!r}; broker positions reconciled.",
            STEP_RESUME_TRADING:
                "Trading resumed against a confirmed-flat book.",
        }
        return messages[step_name]

    @staticmethod
    def _blocked(number: int, step_name: str, reason: str) -> FailoverStepResult:
        return FailoverStepResult(
            step_number=number,
            step_name=step_name,
            is_success=False,
            elapsed_seconds=0.0,
            message=f"{STATUS_BLOCKED}: {reason}",
            is_blocked=True,
        )

    @staticmethod
    def _step_succeeded(steps: Sequence[FailoverStepResult], step_name: str) -> bool:
        return any(s.step_name == step_name and s.is_success for s in steps)

    @classmethod
    def _classify(
        cls,
        steps: Sequence[FailoverStepResult],
        resumed: bool,
        is_rto_ok: bool,
        is_rpo_ok: bool,
        accept_data_loss: bool,
    ) -> str:
        if any(not s.is_success and not s.is_blocked for s in steps):
            return OUTCOME_FAILED
        if not cls._step_succeeded(steps, STEP_PROMOTE_SECONDARY_DB):
            return OUTCOME_ABORTED
        if not resumed:
            return OUTCOME_DEGRADED_TRADING_HALTED
        if not is_rto_ok:
            return OUTCOME_FAILED
        if not is_rpo_ok and accept_data_loss:
            return OUTCOME_SUCCESSFUL_WITH_DATA_LOSS
        return OUTCOME_SUCCESSFUL


def _is_finite(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_positive(value: float) -> bool:
    return _is_finite(value) and value > 0
