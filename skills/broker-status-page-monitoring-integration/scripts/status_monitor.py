"""
broker-status-page-monitoring-integration: Programmatic status page ingestion,
incident severity classification, and external outage vs internal bug diagnosis.
"""
from dataclasses import dataclass, field
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class BrokerPlatformState(Enum):
    OPERATIONAL = "OPERATIONAL"
    DEGRADED = "DEGRADED"
    MAJOR_OUTAGE = "MAJOR_OUTAGE"
    UNKNOWN = "UNKNOWN"


class IncidentDiagnosis(Enum):
    EXTERNAL_BROKER_OUTAGE = "EXTERNAL_BROKER_OUTAGE"
    INTERNAL_APPLICATION_BUG = "INTERNAL_APPLICATION_BUG"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


@dataclass
class BrokerStatusSummary:
    broker_name: str
    indicator: str  # "none", "minor", "major", "critical"
    state: BrokerPlatformState
    description: str
    affected_components: List[str]
    last_updated: float


@dataclass
class FailureDiagnosisResult:
    diagnosis: IncidentDiagnosis
    platform_state: BrokerPlatformState
    explanation: str


class BrokerStatusPageMonitor:
    """
    Monitors public broker status feeds (e.g. Statuspage.io JSON API) to classify platform health
    and diagnose whether execution failures stem from external broker outages or internal bugs.
    """

    def __init__(
        self,
        broker_status_urls: Optional[Dict[str, str]] = None,
        http_fn: Optional[Callable[[str], Tuple[int, Dict[str, Any]]]] = None,
    ):
        self.broker_status_urls = broker_status_urls or {
            "alpaca": "https://status.alpaca.markets/api/v2/summary.json",
            "coinbase": "https://status.coinbase.com/api/v2/summary.json",
        }
        self._http_fn = http_fn
        self.last_status: Dict[str, BrokerStatusSummary] = {}

    def fetch_status(self, broker_name: str) -> BrokerStatusSummary:
        """Fetches and parses the Statuspage.io JSON summary for a broker."""
        b_key = broker_name.lower()
        url = self.broker_status_urls.get(b_key)
        if not url:
            raise ValueError(f"No status feed URL configured for broker '{broker_name}'")

        if self._http_fn:
            status_code, data = self._http_fn(url)
        else:
            raise RuntimeError("HTTP transport function not configured.")

        if status_code != 200 or "status" not in data:
            summary = BrokerStatusSummary(
                broker_name=b_key,
                indicator="unknown",
                state=BrokerPlatformState.UNKNOWN,
                description=f"Failed to fetch status feed (HTTP {status_code})",
                affected_components=[],
                last_updated=time.time(),
            )
            self.last_status[b_key] = summary
            return summary

        indicator = str(data.get("status", {}).get("indicator", "none")).lower()
        description = str(data.get("status", {}).get("description", ""))

        if indicator == "none":
            state = BrokerPlatformState.OPERATIONAL
        elif indicator in ("minor", "degraded"):
            state = BrokerPlatformState.DEGRADED
        else:
            state = BrokerPlatformState.MAJOR_OUTAGE

        affected = []
        for comp in data.get("components", []):
            if comp.get("status") not in ("operational", None):
                affected.append(str(comp.get("name", "Unknown Component")))

        summary = BrokerStatusSummary(
            broker_name=b_key,
            indicator=indicator,
            state=state,
            description=description,
            affected_components=affected,
            last_updated=time.time(),
        )
        self.last_status[b_key] = summary
        logger.info(f"Broker Status '{broker_name}': {state.value} (Indicator: {indicator})")
        return summary

    def diagnose_execution_failure(self, broker_name: str, error_message: str) -> FailureDiagnosisResult:
        """
        Diagnoses an order execution failure: checks if external broker outage is reported.
        """
        b_key = broker_name.lower()
        current_status = self.last_status.get(b_key)

        if not current_status:
            try:
                current_status = self.fetch_status(b_key)
            except Exception:
                current_status = None

        if current_status and current_status.state in (BrokerPlatformState.MAJOR_OUTAGE, BrokerPlatformState.DEGRADED):
            expl = (
                f"Confirmed external broker outage on {broker_name} (State: {current_status.state.value}, "
                f"Indicator: {current_status.indicator}). Affected components: {current_status.affected_components}. "
                f"Suppressing internal bug ticket."
            )
            return FailureDiagnosisResult(
                diagnosis=IncidentDiagnosis.EXTERNAL_BROKER_OUTAGE,
                platform_state=current_status.state,
                explanation=expl,
            )

        expl = f"No broker outage reported on {broker_name}. Likely internal application bug: '{error_message}'."
        return FailureDiagnosisResult(
            diagnosis=IncidentDiagnosis.INTERNAL_APPLICATION_BUG,
            platform_state=current_status.state if current_status else BrokerPlatformState.OPERATIONAL,
            explanation=expl,
        )
