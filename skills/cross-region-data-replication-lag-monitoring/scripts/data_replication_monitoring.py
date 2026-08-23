"""Cross-region replication lag monitoring for multi-region trading architectures.

Measures replication lag from heartbeat records (a write stamped on the primary,
observed on the replica), computes P95/P99 tail latency, and classifies whether a
secondary replica is safe to serve reads to trading logic.

The measurement is the standard replicated-heartbeat pattern (see
`references/standards.md`): lag = replica_receive_timestamp - primary_write_timestamp,
where the two timestamps come from **two different host clocks**. The measurement is
therefore only as trustworthy as the NTP/PTP synchronisation between those hosts, and
this module refuses to certify a replica as healthy when the samples show evidence of
clock skew.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Health / trust statuses returned in ReplicationLagHealthReport.status.
STATUS_HEALTHY = "HEALTHY"
STATUS_DEGRADED_WARNING = "DEGRADED_WARNING"
STATUS_UNSAFE_STALE = "UNSAFE_STALE"
STATUS_CLOCK_SKEW_SUSPECT = "CLOCK_SKEW_SUSPECT"
STATUS_UNKNOWN_NO_DATA = "UNKNOWN_NO_DATA"
STATUS_UNKNOWN_INSUFFICIENT_SAMPLES = "UNKNOWN_INSUFFICIENT_SAMPLES"

# A P99 is only an observed order statistic once the window holds 1/(1-0.99) = 100
# samples. Below that, numpy interpolates between the top two samples and the reported
# "P99" is effectively the observed maximum, which understates the true tail.
MIN_SAMPLES_FOR_P99 = 100


def _spike_note(
    over_unsafe_count: int, sample_count: int, max_lag_ms: float, unsafe_threshold_ms: float
) -> str:
    """Note appended when individual samples breached the unsafe threshold but the P99 did not."""
    if over_unsafe_count <= 0:
        return ""
    return (
        f" NOTE: {over_unsafe_count}/{sample_count} individual heartbeats were at or above "
        f"the {unsafe_threshold_ms}ms unsafe threshold (max={max_lag_ms:.1f}ms) without moving "
        "the P99 — a P99 ignores the worst 1% of samples by construction. Those were real "
        "stale-read windows; decide separately whether your strategy can tolerate them."
    )


@dataclass
class ReplicationHeartbeat:
    """One heartbeat write observed on a replica.

    `primary_write_timestamp_ms` is stamped by the primary region's clock;
    `replica_receive_timestamp_ms` is stamped by the replica region's clock. Both are
    epoch milliseconds.
    """

    heartbeat_id: str
    origin_region: str                 # e.g. 'us-east-1'
    replica_region: str                # e.g. 'eu-west-1'
    primary_write_timestamp_ms: float
    replica_receive_timestamp_ms: float


@dataclass
class ReplicationLagHealthReport:
    """Health verdict for one (origin_region -> replica_region) pair."""

    origin_region: str
    replica_region: str
    sample_count: int
    mean_lag_ms: float
    p95_lag_ms: float
    p99_lag_ms: float
    status: str                         # one of the STATUS_* constants
    is_read_failover_recommended: bool
    recommendation_message: str
    negative_lag_sample_count: int = 0
    max_lag_ms: float = 0.0
    samples_over_unsafe_threshold: int = 0


class CrossRegionReplicationLagMonitor:
    """Computes cross-region replication lag percentiles and stale-read verdicts.

    Failure modes are deliberately fail-safe: absent data, too few samples for a
    meaningful P99, and clock-skew-contaminated samples all recommend read failover to
    the primary rather than reporting a clean bill of health. Only a window that is
    large enough, skew-free, and inside the SLA reports ``HEALTHY``.
    """

    def __init__(
        self,
        p99_warning_threshold_ms: float = 100.0,
        p99_unsafe_threshold_ms: float = 500.0,
        min_sample_count: int = MIN_SAMPLES_FOR_P99,
        clock_skew_tolerance_ms: float = 0.0,
    ) -> None:
        """
        Args:
            p99_warning_threshold_ms: P99 lag at or above which the replica is
                ``DEGRADED_WARNING``. Comparisons are ``>=`` (fail-safe at the boundary).
            p99_unsafe_threshold_ms: P99 lag at or above which the replica is
                ``UNSAFE_STALE`` and reads must fail over to the primary.
            min_sample_count: Minimum heartbeats in the window before a P99 is treated
                as meaningful. Defaults to 100 (see ``MIN_SAMPLES_FOR_P99``). Lower it
                only if you accept that the reported P99 is really the observed maximum.
            clock_skew_tolerance_ms: How negative a measured lag may be before the
                window is treated as clock-skew-contaminated. Default 0.0 treats any
                negative lag as suspect; raise it to your measured NTP/PTP sync bound
                (this module cannot know that bound for you).

        Raises:
            ValueError: if thresholds are non-finite, negative, mis-ordered, or if
                ``min_sample_count`` < 1 or ``clock_skew_tolerance_ms`` < 0.
        """
        for name, value in (
            ("p99_warning_threshold_ms", p99_warning_threshold_ms),
            ("p99_unsafe_threshold_ms", p99_unsafe_threshold_ms),
            ("clock_skew_tolerance_ms", clock_skew_tolerance_ms),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"{name} must be a finite, non-negative number, got {value!r}"
                )
        if p99_warning_threshold_ms > p99_unsafe_threshold_ms:
            raise ValueError(
                "p99_warning_threshold_ms must not exceed p99_unsafe_threshold_ms "
                f"({p99_warning_threshold_ms} > {p99_unsafe_threshold_ms})"
            )
        if min_sample_count < 1:
            raise ValueError(f"min_sample_count must be >= 1, got {min_sample_count}")

        self.p99_warning_threshold_ms = float(p99_warning_threshold_ms)
        self.p99_unsafe_threshold_ms = float(p99_unsafe_threshold_ms)
        self.min_sample_count = int(min_sample_count)
        self.clock_skew_tolerance_ms = float(clock_skew_tolerance_ms)

    def compute_replication_lags(
        self, heartbeats: Sequence[ReplicationHeartbeat]
    ) -> List[float]:
        """Return signed replication lag (ms) per heartbeat: t_replica - t_primary.

        Lags are **not** clamped at zero. A negative value is not a "zero lag" replica —
        it is proof that the replica clock reads earlier than the primary clock, which
        means every lag measured against that clock pair is biased by the same unknown
        offset. Clamping would hide exactly the failure this module is meant to catch.

        Raises:
            ValueError: if any timestamp is non-finite (NaN/Inf). A NaN would propagate
                into the percentiles and make every threshold comparison False, silently
                reporting a stale replica as HEALTHY.
        """
        lags: List[float] = []
        for hb in heartbeats:
            write_ts = float(hb.primary_write_timestamp_ms)
            recv_ts = float(hb.replica_receive_timestamp_ms)
            if not math.isfinite(write_ts) or not math.isfinite(recv_ts):
                raise ValueError(
                    f"Heartbeat {hb.heartbeat_id!r} has non-finite timestamps "
                    f"(primary={write_ts!r}, replica={recv_ts!r}); refusing to compute lag."
                )
            lags.append(recv_ts - write_ts)
        return lags

    def evaluate_replica_health(
        self,
        origin_region: str,
        replica_region: str,
        heartbeats: Sequence[ReplicationHeartbeat],
    ) -> ReplicationLagHealthReport:
        """Evaluate P95/P99 replication lag for one region pair and classify the replica.

        The caller owns windowing: this method filters by region pair but does not sort,
        deduplicate, or truncate. Pass exactly the heartbeats belonging in the rolling
        window.

        Status precedence (every status except ``DEGRADED_WARNING`` and ``HEALTHY``
        recommends read failover): ``UNKNOWN_NO_DATA`` -> ``UNSAFE_STALE`` ->
        ``CLOCK_SKEW_SUSPECT`` -> ``UNKNOWN_INSUFFICIENT_SAMPLES`` ->
        ``DEGRADED_WARNING`` -> ``HEALTHY``.
        """
        filtered = [
            hb for hb in heartbeats
            if hb.origin_region == origin_region and hb.replica_region == replica_region
        ]

        if not filtered:
            msg = (
                f"NO REPLICATION HEARTBEATS [{origin_region} -> {replica_region}]: replica "
                "health is UNKNOWN. Absence of heartbeats is itself a fault signal (dead "
                "probe or broken link), not evidence of health. Failing reads over to the "
                "primary."
            )
            logger.error(msg)
            return ReplicationLagHealthReport(
                origin_region=origin_region, replica_region=replica_region, sample_count=0,
                mean_lag_ms=0.0, p95_lag_ms=0.0, p99_lag_ms=0.0,
                status=STATUS_UNKNOWN_NO_DATA,
                is_read_failover_recommended=True, recommendation_message=msg,
            )

        lags = np.asarray(self.compute_replication_lags(filtered), dtype=float)
        sample_count = int(lags.size)
        negative_count = int(np.count_nonzero(lags < -self.clock_skew_tolerance_ms))
        # A P99 tolerates 1% of samples by construction: a handful of multi-second
        # stalls in a large window leaves it untouched. Count them separately so the
        # caller can gate on "any observed stale window", not only on the tail statistic.
        over_unsafe_count = int(np.count_nonzero(lags >= self.p99_unsafe_threshold_ms))

        mean_lag = float(np.mean(lags))
        max_lag = float(np.max(lags))
        # Explicit linear interpolation between order statistics (numpy's default).
        p95_lag = float(np.percentile(lags, 95, method="linear"))
        p99_lag = float(np.percentile(lags, 99, method="linear"))

        pair = f"[{origin_region} -> {replica_region}]"

        if p99_lag >= self.p99_unsafe_threshold_ms:
            status = STATUS_UNSAFE_STALE
            failover = True
            msg = (
                f"UNSAFE REPLICATION LAG {pair}: P99={p99_lag:.1f}ms >= "
                f"{self.p99_unsafe_threshold_ms}ms threshold. Secondary replica is STALE! "
                "Disabling local reads & triggering failover to primary."
            )
            if negative_count:
                msg += (
                    f" NOTE: {negative_count}/{sample_count} samples are negative "
                    "(clock skew) — the true lag may be larger still."
                )
            logger.critical(msg)
        elif negative_count:
            status = STATUS_CLOCK_SKEW_SUSPECT
            failover = True
            msg = (
                f"CLOCK SKEW SUSPECTED {pair}: {negative_count}/{sample_count} heartbeats "
                f"measured a negative lag beyond the {self.clock_skew_tolerance_ms}ms "
                f"tolerance (min={float(np.min(lags)):.1f}ms). The replica clock reads "
                "earlier than the primary clock, so every lag in this window is biased by "
                f"an unknown offset and the reported P99={p99_lag:.1f}ms cannot be trusted. "
                "Fix NTP/PTP synchronisation; failing reads over to the primary until the "
                "measurement is trustworthy."
            )
            logger.critical(msg)
        elif sample_count < self.min_sample_count:
            status = STATUS_UNKNOWN_INSUFFICIENT_SAMPLES
            failover = True
            msg = (
                f"INSUFFICIENT HEARTBEATS {pair}: {sample_count} samples < "
                f"{self.min_sample_count} required to resolve a P99 as an observed order "
                f"statistic. The reported P99={p99_lag:.1f}ms is effectively the window "
                "maximum and understates the tail. Treating replica trust as UNKNOWN and "
                "failing reads over to the primary."
            )
            logger.warning(msg)
        elif p99_lag >= self.p99_warning_threshold_ms:
            status = STATUS_DEGRADED_WARNING
            failover = False
            msg = (
                f"DEGRADED REPLICATION LAG {pair}: P99={p99_lag:.1f}ms >= "
                f"{self.p99_warning_threshold_ms}ms warning threshold."
            )
            msg += _spike_note(over_unsafe_count, sample_count, max_lag,
                               self.p99_unsafe_threshold_ms)
            logger.warning(msg)
        else:
            status = STATUS_HEALTHY
            failover = False
            msg = (
                f"Replication healthy {pair}: P99={p99_lag:.1f}ms over "
                f"{sample_count} samples."
            )
            spike_note = _spike_note(over_unsafe_count, sample_count, max_lag,
                                     self.p99_unsafe_threshold_ms)
            if spike_note:
                msg += spike_note
                logger.warning(msg)
            else:
                logger.info(msg)

        return ReplicationLagHealthReport(
            origin_region=origin_region,
            replica_region=replica_region,
            sample_count=sample_count,
            mean_lag_ms=round(mean_lag, 2),
            p95_lag_ms=round(p95_lag, 2),
            p99_lag_ms=round(p99_lag, 2),
            status=status,
            is_read_failover_recommended=failover,
            recommendation_message=msg,
            negative_lag_sample_count=negative_count,
            max_lag_ms=round(max_lag, 2),
            samples_over_unsafe_threshold=over_unsafe_count,
        )
