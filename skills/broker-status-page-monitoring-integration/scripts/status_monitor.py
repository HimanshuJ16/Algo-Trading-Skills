"""
broker-status-page-monitoring-integration: Atlassian Statuspage v2 ingestion, platform
health classification, and external-outage vs internal-bug diagnosis.

This module answers one question on the order-failure path: *is this the broker or is
it us?* Both wrong answers are expensive, and they are expensive in opposite
directions:

  - Wrongly answering **"broker"** suppresses the ticket for a live bug in your own
    code and, in most deployments, trips a circuit breaker that halts trading. The bug
    stays in production, unexamined, because the pager never fired.
  - Wrongly answering **"us"** sends an engineer to debug code that is working, during
    a broker incident, which is when their attention is worth the most.

Every classification rule below follows from one principle: **each of the two
confident verdicts requires positive evidence.** ``EXTERNAL_BROKER_OUTAGE`` requires
fresh evidence that the broker is impaired; ``INTERNAL_APPLICATION_BUG`` requires fresh
evidence that it is healthy. Absent, stale, unparseable or merely *ambiguous* evidence
yields ``UNKNOWN_FAILURE`` — the verdict that routes a human to the incident instead of
resolving it automatically in either direction. The failure mode this module is written
against is a diagnoser that treats "I could not reach the status page" as "the broker
is fine".

Scope limits — read these before trusting a verdict:

  - **A status page is a human-published artifact, not telemetry.** It is updated by
    the broker's ops team after they notice, triage and decide to disclose. Lag of
    minutes is normal, and small incidents are frequently never posted at all.
    ``OPERATIONAL`` means "nothing has been published", never "nothing is wrong". This
    module must be paired with a first-party signal (order reject rates, heartbeat
    latency, WebSocket disconnect counts); it is a corroborating input, not a detector.
  - **A cached 200 is not a live 200.** Both status endpoints verified for this skill
    serve ``Cache-Control: max-age=10, public, s-maxage=10, stale-while-revalidate=20,
    stale-if-error=3600`` from a CDN. ``stale-if-error=3600`` means that when
    Statuspage's own origin fails, the edge may keep serving the last good body — a
    green "All Systems Operational" — for up to an hour.
  - **``page.updated_at`` is a last-*changed* timestamp, not a heartbeat.** It is
    captured for forensics and deliberately never used for freshness: a healthy page
    that has not changed in days carries a days-old ``updated_at``. Freshness here is
    measured from the local fetch instead.
  - **The component enum is not closed.** The public Status API documents four page
    indicators and four component statuses, but ``under_maintenance`` is settable
    through the Manage API and appears in live payloads. Unrecognised values map to
    ``UNKNOWN``, never to a guess.
  - **No network I/O.** Transport is injected. The caller's ``http_fn`` MUST enforce a
    connect and read timeout: this module is called from an order-failure handler, and
    an untimed socket there converts a failed order into a hung strategy.

References (verified 2026-08-20):
  - Atlassian, "Status API" — per-page ``/api/v2/`` endpoints, indicator and component
    status enums: https://metastatuspage.com/api
  - Atlassian Support, "What are the different APIs under Statuspage?" — "The Manage
    API is limited to 60 requests per minute. The Status API is not rate limited."
  - Statuspage Manage API — component status values, 1 request/second per token,
    429 + ``Retry-After``: https://developer.statuspage.io/
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Age beyond which a cached summary stops being evidence and the platform state is
#: reported as ``UNKNOWN``. This is an operational convention, **not** anything
#: Atlassian specifies — it is set to a small multiple of a typical 60s poll interval
#: so that a stalled poller degrades to "I don't know" rather than silently asserting
#: whatever it last saw. Override per deployment.
DEFAULT_MAX_STATUS_AGE_S = 300.0

#: Minimum spacing between fetches for the same broker. Grounded in the ``max-age=10``
#: the Statuspage CDN serves on these endpoints: a refetch inside that window returns
#: the same cached body, so it buys no fresher evidence. It also stops a burst of
#: failing orders from issuing one status fetch per failure. The public Status API is
#: documented as *not* rate limited, so this is a self-imposed bound on pointless work
#: and on latency added to the order-failure path — not a vendor requirement.
DEFAULT_MIN_REFETCH_INTERVAL_S = 10.0


class BrokerPlatformState(Enum):
    """Health of a broker platform as *published on its status page*."""

    #: Nothing impaired has been published. Not a positive assertion of health.
    OPERATIONAL = "OPERATIONAL"
    #: Impairment published, but consistent with the platform still serving traffic.
    DEGRADED = "DEGRADED"
    #: Planned, announced work. External and expected, rather than a failure.
    MAINTENANCE = "MAINTENANCE"
    #: Published outage severe enough that order flow should not be trusted.
    MAJOR_OUTAGE = "MAJOR_OUTAGE"
    #: No usable evidence: unreachable, unparseable, stale, or an unrecognised value.
    UNKNOWN = "UNKNOWN"


class IncidentDiagnosis(Enum):
    """Verdict on an execution failure."""

    #: Broker-side. Suppress the code-bug escalation; trip the circuit breaker.
    EXTERNAL_BROKER_OUTAGE = "EXTERNAL_BROKER_OUTAGE"
    #: Broker published as healthy. Escalate to the owning engineer.
    INTERNAL_APPLICATION_BUG = "INTERNAL_APPLICATION_BUG"
    #: Evidence absent, stale or ambiguous. Route to a human; suppress nothing.
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


#: Page-level ``status.indicator``. The four documented values, plus ``maintenance``,
#: which is a documented incident/maintenance *impact* and is observed blended into the
#: page indicator. Anything absent from this table is ``UNKNOWN`` by design — defaulting
#: unrecognised strings to ``MAJOR_OUTAGE`` turns a typo or a future enum value into an
#: automatic trading halt *and* an automatic ticket suppression.
_INDICATOR_STATES: Mapping[str, BrokerPlatformState] = {
    "none": BrokerPlatformState.OPERATIONAL,
    "minor": BrokerPlatformState.DEGRADED,
    "major": BrokerPlatformState.MAJOR_OUTAGE,
    "critical": BrokerPlatformState.MAJOR_OUTAGE,
    "maintenance": BrokerPlatformState.MAINTENANCE,
}

#: Component-level ``status``. ``partial_outage`` maps to ``MAJOR_OUTAGE`` rather than
#: ``DEGRADED``: it means a subset of requests to that component are failing, which is
#: exactly the situation a failed order is evidence of. This is a deliberate judgement
#: call biased toward halting, and it only ever applies to components the caller
#: explicitly declared as dependencies.
_COMPONENT_STATES: Mapping[str, BrokerPlatformState] = {
    "operational": BrokerPlatformState.OPERATIONAL,
    "degraded_performance": BrokerPlatformState.DEGRADED,
    "partial_outage": BrokerPlatformState.MAJOR_OUTAGE,
    "major_outage": BrokerPlatformState.MAJOR_OUTAGE,
    "under_maintenance": BrokerPlatformState.MAINTENANCE,
}

#: Severity order used to reduce several component states to one. ``UNKNOWN`` is not
#: ranked; it is tracked separately, because "one component is unreadable" must not be
#: silently absorbed into an all-clear.
_SEVERITY_RANK: Mapping[BrokerPlatformState, int] = {
    BrokerPlatformState.OPERATIONAL: 0,
    BrokerPlatformState.DEGRADED: 1,
    BrokerPlatformState.MAINTENANCE: 2,
    BrokerPlatformState.MAJOR_OUTAGE: 3,
}

#: States that constitute positive evidence of broker-side impairment.
_IMPAIRED_STATES = frozenset(
    {BrokerPlatformState.MAJOR_OUTAGE, BrokerPlatformState.MAINTENANCE}
)


@dataclass
class BrokerStatusSummary:
    """One parsed reading of a broker's Statuspage ``summary.json``."""

    broker_name: str
    #: Raw ``status.indicator`` as published, lowercased; ``"unknown"`` if unreadable.
    indicator: str
    #: Effective state: the component-scoped state where dependencies were declared and
    #: matched, otherwise the page state. This is what ``diagnose_execution_failure``
    #: reasons over.
    state: BrokerPlatformState
    description: str
    #: Non-operational leaf components (``group`` is not true), by name.
    affected_components: List[str]
    #: Local wall-clock time of the fetch that produced this reading. Freshness is
    #: measured from here, never from ``page_updated_at``.
    last_updated: float
    #: State derived from ``status.indicator`` alone — the blended, page-wide roll-up.
    page_state: BrokerPlatformState = BrokerPlatformState.UNKNOWN
    #: State derived only from the caller's declared dependency components.
    #: ``UNKNOWN`` when none were declared, none matched, or a match was unreadable.
    dependency_state: BrokerPlatformState = BrokerPlatformState.UNKNOWN
    #: Non-operational component *groups*, kept out of ``affected_components`` so a
    #: single failing child is not counted twice — once as itself, once as its group.
    affected_component_groups: List[str] = field(default_factory=list)
    #: Declared dependency names that matched no component in the feed. A renamed or
    #: mistyped component silently disables component-scoped diagnosis, so this is
    #: surfaced rather than swallowed.
    unmatched_dependencies: List[str] = field(default_factory=list)
    #: ``page.updated_at`` verbatim. Forensics only — see the module docstring.
    page_updated_at: Optional[str] = None
    #: False when the fetch failed or the payload was unusable.
    fetch_ok: bool = True


@dataclass
class FailureDiagnosisResult:
    """Verdict on one execution failure, with the evidence it rests on."""

    diagnosis: IncidentDiagnosis
    platform_state: BrokerPlatformState
    explanation: str
    broker_name: str = ""
    #: Age in seconds of the status reading used, or ``None`` if there was none.
    status_age_s: Optional[float] = None
    #: True only when a reading existed and was within ``max_status_age_s``.
    evidence_is_fresh: bool = False
    affected_components: List[str] = field(default_factory=list)


class BrokerStatusPageMonitor:
    """
    Polls Atlassian Statuspage v2 feeds and classifies execution failures as
    broker-side, application-side, or undetermined.

    Thread-safety: cached readings are guarded by an internal lock, so a background
    poller calling :meth:`fetch_status` and a trading thread calling
    :meth:`diagnose_execution_failure` may run concurrently.
    """

    def __init__(
        self,
        broker_status_urls: Optional[Dict[str, str]] = None,
        http_fn: Optional[Callable[[str], Tuple[int, Any]]] = None,
        dependency_components: Optional[Dict[str, Sequence[str]]] = None,
        max_status_age_s: float = DEFAULT_MAX_STATUS_AGE_S,
        min_refetch_interval_s: float = DEFAULT_MIN_REFETCH_INTERVAL_S,
        now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        """
        :param broker_status_urls: Broker key -> full ``summary.json`` URL. Keys are
            matched case-insensitively.
        :param http_fn: ``url -> (status_code, decoded_json)``. MUST enforce a connect
            and read timeout; see the module docstring.
        :param dependency_components: Broker key -> the component names that broker
            serves *your* order flow through, e.g.
            ``{"alpaca": ["Live Trading API"]}``. Matched case-insensitively against
            ``components[].name``. Supplying these is what lets the monitor ignore an
            unrelated incident on a component you do not use, and catch an outage on
            one you do while the page-wide indicator still reads ``none``.
        :param max_status_age_s: Readings older than this stop counting as evidence.
        :param min_refetch_interval_s: Lower bound on the spacing between fetches for
            one broker, enforced on the implicit refresh inside
            :meth:`diagnose_execution_failure`.
        :param now_fn: Clock returning seconds, defaulting to ``time.time``. Injectable
            for tests. Ages are clamped at zero, so a wall clock stepped backwards by
            NTP makes a stale reading look fresh; pass ``time.monotonic`` where that
            matters more than having ``last_updated`` be a readable wall-clock stamp.
        :raises ValueError: on non-positive ``max_status_age_s`` or negative
            ``min_refetch_interval_s``.
        """
        if max_status_age_s <= 0:
            raise ValueError("max_status_age_s must be positive")
        if min_refetch_interval_s < 0:
            raise ValueError("min_refetch_interval_s must be non-negative")

        self.broker_status_urls: Dict[str, str] = {
            k.strip().lower(): v
            for k, v in (
                broker_status_urls
                if broker_status_urls is not None
                else {
                    "alpaca": "https://status.alpaca.markets/api/v2/summary.json",
                    "coinbase": "https://status.coinbase.com/api/v2/summary.json",
                }
            ).items()
        }
        self._dependency_components: Dict[str, List[str]] = {
            k.strip().lower(): [str(c).strip() for c in v]
            for k, v in (dependency_components or {}).items()
        }
        self._http_fn = http_fn
        self._max_status_age_s = max_status_age_s
        self._min_refetch_interval_s = min_refetch_interval_s
        self._now_fn = now_fn or time.time
        self._lock = threading.Lock()

        #: Last **successful** reading per broker. A failed fetch does not overwrite it
        #: — during an incident the status page is itself under load, and discarding a
        #: 20-second-old good reading because of one 503 loses the best evidence
        #: available. Staleness is enforced separately, on read.
        self.last_status: Dict[str, BrokerStatusSummary] = {}
        #: Last fetch attempt time per broker, successful or not.
        self._last_attempt: Dict[str, float] = {}
        #: Reason the most recent fetch failed, per broker. Cleared on success.
        self.last_fetch_error: Dict[str, str] = {}

    # -- internal helpers -------------------------------------------------------

    def _resolve_url(self, broker_key: str) -> str:
        url = self.broker_status_urls.get(broker_key)
        if not url:
            raise ValueError(
                f"No status feed URL configured for broker '{broker_key}'. "
                f"Configured: {sorted(self.broker_status_urls)}"
            )
        return url

    def _unusable(self, broker_key: str, reason: str, now: float) -> BrokerStatusSummary:
        """Builds the reading returned when a fetch produced nothing usable."""
        with self._lock:
            self.last_fetch_error[broker_key] = reason
        logger.warning("Status feed unusable for '%s': %s", broker_key, reason)
        return BrokerStatusSummary(
            broker_name=broker_key,
            indicator="unknown",
            state=BrokerPlatformState.UNKNOWN,
            description=reason,
            affected_components=[],
            last_updated=now,
            page_state=BrokerPlatformState.UNKNOWN,
            dependency_state=BrokerPlatformState.UNKNOWN,
            fetch_ok=False,
        )

    @staticmethod
    def _reduce(states: Sequence[BrokerPlatformState]) -> BrokerPlatformState:
        """
        Reduces several component states to the worst one. If any state is ``UNKNOWN``
        and nothing else is worse than ``OPERATIONAL``, the result is ``UNKNOWN``: an
        unreadable component cannot be counted toward an all-clear.
        """
        if not states:
            return BrokerPlatformState.UNKNOWN
        known = [s for s in states if s in _SEVERITY_RANK]
        if not known:
            return BrokerPlatformState.UNKNOWN
        worst = max(known, key=lambda s: _SEVERITY_RANK[s])
        if len(known) != len(states) and worst is BrokerPlatformState.OPERATIONAL:
            return BrokerPlatformState.UNKNOWN
        return worst

    @staticmethod
    def _component_state(comp: Mapping[str, Any]) -> BrokerPlatformState:
        return _COMPONENT_STATES.get(
            str(comp.get("status", "")).strip().lower(), BrokerPlatformState.UNKNOWN
        )

    # -- public API -------------------------------------------------------------

    def fetch_status(self, broker_name: str) -> BrokerStatusSummary:
        """
        Fetches and parses one broker's Statuspage ``summary.json``.

        Never raises on transport or payload problems — those return an ``UNKNOWN``
        reading so a polling loop keeps running. Configuration errors *do* raise,
        because they are bugs rather than incidents.

        :raises ValueError: no URL configured for ``broker_name``.
        :raises RuntimeError: no ``http_fn`` transport configured.
        """
        broker_key = broker_name.strip().lower()
        url = self._resolve_url(broker_key)
        if self._http_fn is None:
            raise RuntimeError(
                "HTTP transport function not configured; pass http_fn=... . It must "
                "enforce a connect and read timeout."
            )

        now = self._now_fn()
        with self._lock:
            self._last_attempt[broker_key] = now

        try:
            status_code, data = self._http_fn(url)
        except Exception as exc:  # noqa: BLE001 - a poller must survive any transport error
            logger.error(
                "Status feed fetch raised for '%s' (%s)", broker_key, url, exc_info=True
            )
            return self._unusable(broker_key, f"transport error: {exc!r}", now)

        if status_code != 200:
            return self._unusable(
                broker_key, f"status feed returned HTTP {status_code}", now
            )
        if not isinstance(data, dict) or not isinstance(data.get("status"), dict):
            return self._unusable(
                broker_key, "status feed payload missing a 'status' object", now
            )

        # A JSON ``null`` indicator must not become the string "none": ``str(None)``
        # is literally ``"None"``, which lowercases to the *documented operational
        # value*. A missing indicator would then read as "All Systems Operational".
        raw_indicator = data["status"].get("indicator")
        indicator = (
            str(raw_indicator).strip().lower() if raw_indicator is not None else ""
        )
        raw_description = data["status"].get("description")
        description = str(raw_description) if raw_description is not None else ""
        page_state = _INDICATOR_STATES.get(indicator, BrokerPlatformState.UNKNOWN)
        if page_state is BrokerPlatformState.UNKNOWN:
            logger.warning(
                "Unrecognised status.indicator %r for '%s'; treating as UNKNOWN rather "
                "than guessing a severity.",
                indicator,
                broker_key,
            )

        raw_components = data.get("components")
        components: List[Dict[str, Any]] = (
            [c for c in raw_components if isinstance(c, dict)]
            if isinstance(raw_components, list)
            else []
        )

        affected: List[str] = []
        affected_groups: List[str] = []
        for comp in components:
            if self._component_state(comp) is BrokerPlatformState.OPERATIONAL:
                continue
            name = str(comp.get("name") or "Unnamed Component")
            # Groups carry a rolled-up status. Listing a failing child *and* its parent
            # would report two impaired components where the feed describes one.
            (affected_groups if comp.get("group") is True else affected).append(name)

        declared = self._dependency_components.get(broker_key, [])
        matched_states: List[BrokerPlatformState] = []
        matched_names = set()
        if declared:
            wanted = {d.strip().lower() for d in declared}
            for comp in components:
                name = str(comp.get("name") or "").strip().lower()
                if name in wanted:
                    matched_names.add(name)
                    matched_states.append(self._component_state(comp))
        unmatched = [d for d in declared if d.strip().lower() not in matched_names]
        if unmatched:
            logger.warning(
                "Declared dependency components absent from the '%s' status feed: %s. "
                "Component-scoped diagnosis is degraded until these names are fixed.",
                broker_key,
                unmatched,
            )

        dependency_state = self._reduce(matched_states)
        effective = (
            dependency_state
            if dependency_state is not BrokerPlatformState.UNKNOWN
            else page_state
        )

        page_obj = data.get("page")
        page_updated_at = (
            str(page_obj.get("updated_at"))
            if isinstance(page_obj, dict) and page_obj.get("updated_at") is not None
            else None
        )

        summary = BrokerStatusSummary(
            broker_name=broker_key,
            indicator=indicator or "unknown",
            state=effective,
            description=description,
            affected_components=affected,
            last_updated=now,
            page_state=page_state,
            dependency_state=dependency_state,
            affected_component_groups=affected_groups,
            unmatched_dependencies=unmatched,
            page_updated_at=page_updated_at,
            fetch_ok=True,
        )
        with self._lock:
            self.last_status[broker_key] = summary
            self.last_fetch_error.pop(broker_key, None)
        logger.info(
            "Broker status '%s': effective=%s page=%s dependency=%s indicator=%r "
            "affected=%s",
            broker_key,
            effective.value,
            page_state.value,
            dependency_state.value,
            indicator,
            affected,
        )
        return summary

    def get_cached_status(
        self, broker_name: str
    ) -> Tuple[Optional[BrokerStatusSummary], Optional[float]]:
        """
        Returns ``(summary, age_seconds)`` for the last successful reading, or
        ``(None, None)``. The summary is returned regardless of age; the caller decides
        what to do with a stale one.
        """
        broker_key = broker_name.strip().lower()
        with self._lock:
            summary = self.last_status.get(broker_key)
        if summary is None:
            return None, None
        return summary, max(0.0, self._now_fn() - summary.last_updated)

    def diagnose_execution_failure(
        self, broker_name: str, error_message: str
    ) -> FailureDiagnosisResult:
        """
        Classifies one execution failure as broker-side, application-side, or
        undetermined.

        Called from an order-failure handler, so it **never raises** — an exception
        here would displace the original trading exception. Configuration errors are
        logged and reported as ``UNKNOWN_FAILURE``.

        Decision table, applied in order:

        ==========================================  ===========================
        Evidence                                    Verdict
        ==========================================  ===========================
        No reading, or older than max_status_age_s  ``UNKNOWN_FAILURE``
        A declared dependency is impaired           ``EXTERNAL_BROKER_OUTAGE``
        Page impaired, dependencies read healthy    ``UNKNOWN_FAILURE``
        Page impaired, no dependency evidence       ``EXTERNAL_BROKER_OUTAGE``
        Anything merely degraded, or unrecognised   ``UNKNOWN_FAILURE``
        Page operational                            ``INTERNAL_APPLICATION_BUG``
        ==========================================  ===========================
        """
        broker_key = broker_name.strip().lower()
        summary, age = self.get_cached_status(broker_key)

        if summary is None or age is None or age > self._max_status_age_s:
            summary, age = self._refresh_for_diagnosis(broker_key, summary, age)

        if summary is None or age is None or age > self._max_status_age_s:
            reason = (
                "no status reading available"
                if summary is None or age is None
                else (
                    f"the last status reading is {age:.0f}s old, beyond the "
                    f"{self._max_status_age_s:.0f}s freshness bound"
                )
            )
            return FailureDiagnosisResult(
                diagnosis=IncidentDiagnosis.UNKNOWN_FAILURE,
                platform_state=BrokerPlatformState.UNKNOWN,
                explanation=(
                    f"{broker_name}: cannot classify this failure — {reason}. Absence "
                    f"of evidence is not evidence of health, so escalate to a human "
                    f"rather than suppressing or filing automatically. Original "
                    f"error: '{error_message}'."
                ),
                broker_name=broker_key,
                status_age_s=age,
                evidence_is_fresh=False,
                affected_components=(
                    list(summary.affected_components) if summary else []
                ),
            )

        return self._classify(broker_name, broker_key, summary, age, error_message)

    # -- diagnosis internals ----------------------------------------------------

    def _refresh_for_diagnosis(
        self,
        broker_key: str,
        summary: Optional[BrokerStatusSummary],
        age: Optional[float],
    ) -> Tuple[Optional[BrokerStatusSummary], Optional[float]]:
        """
        Attempts one refresh, subject to ``min_refetch_interval_s`` so that a burst of
        failing orders does not issue one status fetch per failure.
        """
        now = self._now_fn()
        with self._lock:
            last_attempt = self._last_attempt.get(broker_key)
        if last_attempt is not None and (now - last_attempt) < self._min_refetch_interval_s:
            logger.debug(
                "Skipping status refresh for '%s': last attempt %.1fs ago, below the "
                "%.1fs minimum spacing.",
                broker_key,
                now - last_attempt,
                self._min_refetch_interval_s,
            )
            return summary, age

        try:
            fresh = self.fetch_status(broker_key)
        except (ValueError, RuntimeError):
            logger.error(
                "Status monitor misconfigured for '%s'; cannot diagnose.",
                broker_key,
                exc_info=True,
            )
            return summary, age

        if fresh.fetch_ok:
            return self.get_cached_status(broker_key)
        return summary, age

    def _classify(
        self,
        broker_name: str,
        broker_key: str,
        summary: BrokerStatusSummary,
        age: float,
        error_message: str,
    ) -> FailureDiagnosisResult:
        page = summary.page_state
        dep = summary.dependency_state
        affected = list(summary.affected_components)

        def result(
            diagnosis: IncidentDiagnosis, state: BrokerPlatformState, detail: str
        ) -> FailureDiagnosisResult:
            return FailureDiagnosisResult(
                diagnosis=diagnosis,
                platform_state=state,
                explanation=(
                    f"{broker_name}: {detail} (page={page.value}, "
                    f"dependency={dep.value}, indicator='{summary.indicator}', "
                    f"evidence age {age:.0f}s, affected={affected}). Original error: "
                    f"'{error_message}'."
                ),
                broker_name=broker_key,
                status_age_s=age,
                evidence_is_fresh=True,
                affected_components=affected,
            )

        if dep in _IMPAIRED_STATES:
            return result(
                IncidentDiagnosis.EXTERNAL_BROKER_OUTAGE,
                dep,
                "a component this system depends on is published as impaired, so the "
                "code-bug escalation is suppressed and this is treated as broker-side",
            )

        if page in _IMPAIRED_STATES:
            if dep is BrokerPlatformState.OPERATIONAL:
                return result(
                    IncidentDiagnosis.UNKNOWN_FAILURE,
                    page,
                    "the broker is in a published incident but every component this "
                    "system depends on reads operational, so neither verdict is "
                    "supported and a human must decide",
                )
            return result(
                IncidentDiagnosis.EXTERNAL_BROKER_OUTAGE,
                page,
                "the broker publishes a platform-wide outage or maintenance, so the "
                "code-bug escalation is suppressed and this is treated as broker-side",
            )

        if BrokerPlatformState.DEGRADED in (page, dep):
            return result(
                IncidentDiagnosis.UNKNOWN_FAILURE,
                BrokerPlatformState.DEGRADED,
                "the broker publishes degradation, which is consistent with both a "
                "broker-side failure and an application bug, so the ticket is escalated "
                "to a human rather than suppressed",
            )

        if page is BrokerPlatformState.UNKNOWN:
            return result(
                IncidentDiagnosis.UNKNOWN_FAILURE,
                BrokerPlatformState.UNKNOWN,
                "the status feed carries no state this monitor recognises",
            )

        return result(
            IncidentDiagnosis.INTERNAL_APPLICATION_BUG,
            BrokerPlatformState.OPERATIONAL,
            "the broker publishes no impairment on any component this system depends "
            "on, so this escalates to the owning engineer. A status page lags reality "
            "— corroborate against first-party reject and latency metrics",
        )
