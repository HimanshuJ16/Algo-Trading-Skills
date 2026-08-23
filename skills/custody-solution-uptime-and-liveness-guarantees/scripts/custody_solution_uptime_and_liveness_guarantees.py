"""Custody API uptime, MPC quorum, and signing-latency SLA monitoring.

This module answers one question: *is the primary custody provider live enough
to sign right now, and if not, should we fail over?*

Design stance - a liveness monitor must fail CLOSED
---------------------------------------------------
Silence is not health. Every path where the engine cannot actually establish
liveness returns a non-healthy status and recommends failover:

* no probes at all              -> ``UNKNOWN_NO_TELEMETRY``
* newest probe older than the
  configured freshness bound    -> ``STALE_TELEMETRY``
* malformed / non-finite probe  -> ``ValueError`` at ingestion

The previous revision returned ``HEALTHY`` with ``rolling_uptime_pct=100.0`` for
an empty probe list, so a total collector outage was indistinguishable from a
perfectly healthy custodian.

What this module does NOT do
----------------------------
* It does not execute failover. It sets ``is_failover_recommended``; routing and
  key-material questions belong to the caller.
* Uptime is a **probe-success ratio**, not time-weighted availability. It equals
  contractual availability only when probes are evenly spaced. ``probe_count``
  and the window fields are reported so the caller can judge that.
* Availability figures and latency ceilings are **contractual**, not standards.
  No numeric SLA is hard-coded here; see ``references/standards.md``.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

# A p99 can only resolve the top 1% of a sample once there are at least 100
# observations; below that the "99th percentile" is simply the maximum, and
# gating an SLA on it reads one unlucky request as a breach.
DEFAULT_MIN_LATENCY_SAMPLES = 100

STATUS_HEALTHY = "HEALTHY"
STATUS_QUORUM_AT_RISK = "QUORUM_AT_RISK"
STATUS_DEGRADED_SLA_BREACH = "DEGRADED_SLA_BREACH"
STATUS_STALE_TELEMETRY = "STALE_TELEMETRY"
STATUS_UNKNOWN_NO_TELEMETRY = "UNKNOWN_NO_TELEMETRY"
STATUS_QUORUM_LOST = "QUORUM_LOST_LIVENESS_HALT"

# Ordered worst-last. A single audit can raise several conditions at once; the
# reported ``status`` is the most severe, while ``recommendations`` carries all.
_STATUS_SEVERITY = {
    STATUS_HEALTHY: 0,
    STATUS_QUORUM_AT_RISK: 1,
    STATUS_DEGRADED_SLA_BREACH: 2,
    STATUS_STALE_TELEMETRY: 3,
    STATUS_UNKNOWN_NO_TELEMETRY: 4,
    STATUS_QUORUM_LOST: 5,
}


def _require_finite(value: float, field_name: str) -> float:
    """Rejects NaN and +/-Inf.

    NaN is the dangerous case: every ``>`` comparison against NaN is False, so a
    single NaN latency silently propagates through ``p99`` and passes the SLA
    gate rather than breaching it.
    """
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    return float(value)


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile of an already-sorted sequence.

    ``q`` is a fraction in [0, 1]. Matches the common "linear" / type-7
    definition: the rank is ``q * (n - 1)`` and the result interpolates between
    the two neighbouring order statistics. Stated explicitly because an SLA gate
    is only reproducible if the estimator is pinned - other definitions
    (nearest-rank, type-6) put the same sample at a different p99.
    """
    if not sorted_values:
        raise ValueError("cannot take a percentile of an empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1], got {q}")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = q * (len(sorted_values) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted_values[int(rank)])
    weight = rank - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


@dataclass
class CustodyHealthProbe:
    """One health observation of a custody provider."""

    probe_id: str
    timestamp_ms: float
    is_api_healthy: bool
    signing_latency_ms: float
    active_mpc_nodes: int


@dataclass
class ProviderSlaConfig:
    """Contractual SLA thresholds for one custody provider.

    Every threshold comes from the executed agreement with the provider. Nothing
    here is a published standard - SOC 2 attests that a provider meets its *own*
    stated commitments, it does not set a numeric uptime figure.
    """

    provider_id: str
    provider_name: str
    target_uptime_pct: float             # contractual, e.g. 99.9
    max_signing_latency_ms: float        # contractual, e.g. 2000.0
    mpc_threshold_k: int                 # shares required to sign, e.g. 2
    mpc_total_n: int                     # shares provisioned, e.g. 3
    max_probe_age_ms: Optional[float] = None
    min_latency_samples: int = DEFAULT_MIN_LATENCY_SAMPLES
    latency_rolling_window: Optional[int] = None
    failover_on_latency_breach: bool = False


@dataclass
class CustodyLivenessAuditReport:
    provider_id: str
    rolling_uptime_pct: float
    p99_signing_latency_ms: float
    current_active_mpc_nodes: int
    is_mpc_quorum_maintained: bool
    status: str
    is_failover_recommended: bool
    recommendations: List[str]
    percentiles_reliable: bool = False
    latency_sample_count: int = 0
    redundant_nodes: Optional[int] = None
    probe_count: int = 0
    window_start_ms: Optional[float] = None
    window_end_ms: Optional[float] = None
    newest_probe_age_ms: Optional[float] = None
    breached_conditions: List[str] = field(default_factory=list)


class CustodyLivenessMonitorEngine:
    """Audits custody API uptime, MPC signing quorum, and signing latency.

    In a k-of-n threshold scheme at least ``k`` shares must participate to
    produce a signature, so the cluster tolerates exactly ``n - k`` unavailable
    nodes. ``active == k`` is therefore not healthy - it is zero remaining
    redundancy, one node away from a signing halt, and is reported as
    ``QUORUM_AT_RISK`` so operators act before the halt rather than during it.
    """

    def __init__(self, config: ProviderSlaConfig):
        self._validate_config(config)
        self.config = config

    @staticmethod
    def _validate_config(config: ProviderSlaConfig) -> None:
        _require_finite(config.target_uptime_pct, "target_uptime_pct")
        _require_finite(config.max_signing_latency_ms, "max_signing_latency_ms")
        if not 0.0 <= config.target_uptime_pct <= 100.0:
            raise ValueError(
                f"target_uptime_pct must be in [0, 100], got {config.target_uptime_pct}"
            )
        if config.max_signing_latency_ms <= 0:
            raise ValueError(
                f"max_signing_latency_ms must be > 0, got {config.max_signing_latency_ms}"
            )
        if config.mpc_total_n <= 0:
            raise ValueError(f"mpc_total_n must be > 0, got {config.mpc_total_n}")
        if config.mpc_threshold_k <= 0:
            raise ValueError(f"mpc_threshold_k must be > 0, got {config.mpc_threshold_k}")
        if config.mpc_threshold_k > config.mpc_total_n:
            raise ValueError(
                f"mpc_threshold_k ({config.mpc_threshold_k}) cannot exceed "
                f"mpc_total_n ({config.mpc_total_n}) - such a quorum can never be met"
            )
        if config.min_latency_samples < 1:
            raise ValueError(
                f"min_latency_samples must be >= 1, got {config.min_latency_samples}"
            )
        if config.latency_rolling_window is not None and config.latency_rolling_window < 1:
            raise ValueError(
                f"latency_rolling_window must be >= 1 when set, "
                f"got {config.latency_rolling_window}"
            )
        if config.max_probe_age_ms is not None:
            _require_finite(config.max_probe_age_ms, "max_probe_age_ms")
            if config.max_probe_age_ms <= 0:
                raise ValueError(
                    f"max_probe_age_ms must be > 0 when set, got {config.max_probe_age_ms}"
                )

    def _validate_probe(self, probe: CustodyHealthProbe) -> None:
        _require_finite(probe.timestamp_ms, f"Probe {probe.probe_id}: timestamp_ms")
        _require_finite(probe.signing_latency_ms, f"Probe {probe.probe_id}: signing_latency_ms")
        if probe.signing_latency_ms < 0:
            raise ValueError(
                f"Probe {probe.probe_id}: signing_latency_ms must be >= 0, "
                f"got {probe.signing_latency_ms}"
            )
        if probe.active_mpc_nodes < 0:
            raise ValueError(
                f"Probe {probe.probe_id}: active_mpc_nodes must be >= 0, "
                f"got {probe.active_mpc_nodes}"
            )
        if probe.active_mpc_nodes > self.config.mpc_total_n:
            raise ValueError(
                f"Probe {probe.probe_id}: active_mpc_nodes ({probe.active_mpc_nodes}) "
                f"exceeds mpc_total_n ({self.config.mpc_total_n}) - the telemetry feed "
                f"disagrees with the provisioned cluster size"
            )

    def audit_liveness(
        self,
        probes: List[CustodyHealthProbe],
        as_of_timestamp_ms: Optional[float] = None,
    ) -> CustodyLivenessAuditReport:
        """Audits uptime, MPC quorum, and P99 signing latency over ``probes``.

        Args:
            probes: Health observations. Order does not matter - probes are
                sorted by ``timestamp_ms``, so an out-of-order arrival cannot
                mask a quorum loss by landing last in the list.
            as_of_timestamp_ms: Wall-clock reference for the freshness check.
                Required for staleness to be evaluated at all; when omitted the
                report says so rather than implying the data is current. Passed
                in rather than read from the clock so audits are deterministic
                and replayable.

        Returns:
            A `CustodyLivenessAuditReport`. ``status`` is the most severe
            condition found; ``breached_conditions`` lists every condition, since
            a quorum loss and an uptime breach routinely occur together.

        Raises:
            ValueError: on malformed telemetry (non-finite values, negative
                latency, or an ``active_mpc_nodes`` count above ``mpc_total_n``).
        """
        cfg = self.config

        if not probes:
            msg = (
                f"NO TELEMETRY [{cfg.provider_name}]: zero health probes in the audit "
                f"window. Liveness is UNKNOWN, not healthy - recommending failover "
                f"until the probe pipeline is restored."
            )
            logger.critical(msg)
            return CustodyLivenessAuditReport(
                provider_id=cfg.provider_id,
                rolling_uptime_pct=0.0,
                p99_signing_latency_ms=0.0,
                current_active_mpc_nodes=0,
                is_mpc_quorum_maintained=False,
                status=STATUS_UNKNOWN_NO_TELEMETRY,
                is_failover_recommended=True,
                recommendations=[msg],
                percentiles_reliable=False,
                latency_sample_count=0,
                redundant_nodes=None,
                probe_count=0,
                breached_conditions=[STATUS_UNKNOWN_NO_TELEMETRY],
            )

        for probe in probes:
            self._validate_probe(probe)

        # Chronological order is established here, not assumed from list order.
        # Ties on timestamp are broken toward the LOWEST node count, so that when
        # two collectors report the same instant the conservative reading wins -
        # otherwise merely reordering the input could erase a quorum loss.
        ordered = sorted(probes, key=lambda p: (p.timestamp_ms, -p.active_mpc_nodes))
        total_probes = len(ordered)
        healthy_probes = sum(1 for p in ordered if p.is_api_healthy)

        # Rounded once, then both reported and compared on that same value. The
        # previous revision rounded to 2dp before comparing, so a true 99.899%
        # rounded to 99.90 and cleared a 99.9% target it had actually missed.
        uptime_pct = round((healthy_probes / total_probes) * 100.0, 6)

        healthy_latencies = [p.signing_latency_ms for p in ordered if p.is_api_healthy]
        if cfg.latency_rolling_window is not None:
            healthy_latencies = healthy_latencies[-cfg.latency_rolling_window:]
        latency_sample_count = len(healthy_latencies)
        percentiles_reliable = latency_sample_count >= cfg.min_latency_samples
        p99_lat = (
            _percentile(sorted(healthy_latencies), 0.99) if healthy_latencies else 0.0
        )

        latest_probe = ordered[-1]
        active_nodes = latest_probe.active_mpc_nodes
        is_quorum_ok = active_nodes >= cfg.mpc_threshold_k
        redundant_nodes = active_nodes - cfg.mpc_threshold_k

        newest_probe_age_ms: Optional[float] = None
        if as_of_timestamp_ms is not None:
            _require_finite(as_of_timestamp_ms, "as_of_timestamp_ms")
            newest_probe_age_ms = as_of_timestamp_ms - latest_probe.timestamp_ms

        recommendations: List[str] = []
        breached: List[str] = []
        failover = False

        # Every condition is evaluated independently. The previous revision used
        # an if/elif chain, so a quorum loss hid a simultaneous uptime breach.
        if not is_quorum_ok:
            breached.append(STATUS_QUORUM_LOST)
            failover = True
            msg = (
                f"MPC QUORUM LOST [{cfg.provider_name}]: Active nodes = {active_nodes} < "
                f"Threshold k={cfg.mpc_threshold_k}! Automated signing HALTED. "
                f"Triggering failover."
            )
            recommendations.append(msg)
            logger.critical(msg)
        elif redundant_nodes == 0:
            breached.append(STATUS_QUORUM_AT_RISK)
            msg = (
                f"MPC QUORUM AT RISK [{cfg.provider_name}]: Active nodes = {active_nodes} "
                f"equals threshold k={cfg.mpc_threshold_k} (of n={cfg.mpc_total_n}). Zero "
                f"remaining redundancy - the next node loss halts signing. Restore a node "
                f"before the next maintenance window."
            )
            recommendations.append(msg)
            logger.warning(msg)

        if uptime_pct < cfg.target_uptime_pct:
            breached.append(STATUS_DEGRADED_SLA_BREACH)
            failover = True
            msg = (
                f"UPTIME SLA BREACH [{cfg.provider_name}]: Uptime {uptime_pct:.4f}% < "
                f"Target {cfg.target_uptime_pct}% over {total_probes} probes. "
                f"Triggering failover to secondary provider."
            )
            recommendations.append(msg)
            logger.error(msg)

        if p99_lat > cfg.max_signing_latency_ms:
            if percentiles_reliable:
                breached.append(STATUS_DEGRADED_SLA_BREACH)
                if cfg.failover_on_latency_breach:
                    failover = True
                msg = (
                    f"SIGNING LATENCY SLA BREACH [{cfg.provider_name}]: P99 Latency "
                    f"{p99_lat:.1f}ms > Max SLA {cfg.max_signing_latency_ms}ms over "
                    f"{latency_sample_count} samples."
                )
                recommendations.append(msg)
                logger.warning(msg)
            else:
                # Not a breach: a p99 from too few samples is the maximum wearing
                # a percentile's name. Surfaced, but it must not gate failover.
                msg = (
                    f"LATENCY p99 NOT GATED [{cfg.provider_name}]: p99 {p99_lat:.1f}ms "
                    f"exceeds the {cfg.max_signing_latency_ms}ms SLA but rests on only "
                    f"{latency_sample_count} samples (< {cfg.min_latency_samples}); "
                    f"treated as indeterminate, not a breach."
                )
                recommendations.append(msg)
                logger.info(msg)

        if newest_probe_age_ms is None:
            recommendations.append(
                "Freshness NOT evaluated: no as_of_timestamp_ms supplied. This report "
                "describes the probes given, which may be arbitrarily old."
            )
        elif newest_probe_age_ms < 0:
            # A probe stamped after the audit instant means collector clock skew.
            # Age checks are meaningless until that is fixed, so say so rather
            # than silently treating a future-dated probe as fresh.
            msg = (
                f"CLOCK SKEW [{cfg.provider_name}]: newest probe is stamped "
                f"{abs(newest_probe_age_ms):.0f}ms in the FUTURE relative to "
                f"as_of_timestamp_ms. Freshness cannot be trusted until collector "
                f"clocks are reconciled."
            )
            recommendations.append(msg)
            logger.error(msg)
        elif cfg.max_probe_age_ms is not None and newest_probe_age_ms > cfg.max_probe_age_ms:
            breached.append(STATUS_STALE_TELEMETRY)
            failover = True
            msg = (
                f"STALE TELEMETRY [{cfg.provider_name}]: newest probe is "
                f"{newest_probe_age_ms:.0f}ms old > max {cfg.max_probe_age_ms:.0f}ms. "
                f"Current liveness is UNKNOWN - recommending failover."
            )
            recommendations.append(msg)
            logger.critical(msg)

        if not breached:
            status = STATUS_HEALTHY
            recommendations.append("Custody provider SLA compliant and MPC quorum healthy.")
        else:
            status = max(breached, key=lambda s: _STATUS_SEVERITY[s])

        return CustodyLivenessAuditReport(
            provider_id=cfg.provider_id,
            rolling_uptime_pct=uptime_pct,
            p99_signing_latency_ms=round(p99_lat, 2),
            current_active_mpc_nodes=active_nodes,
            is_mpc_quorum_maintained=is_quorum_ok,
            status=status,
            is_failover_recommended=failover,
            recommendations=recommendations,
            percentiles_reliable=percentiles_reliable,
            latency_sample_count=latency_sample_count,
            redundant_nodes=redundant_nodes,
            probe_count=total_probes,
            window_start_ms=ordered[0].timestamp_ms,
            window_end_ms=latest_probe.timestamp_ms,
            newest_probe_age_ms=newest_probe_age_ms,
            breached_conditions=breached,
        )
