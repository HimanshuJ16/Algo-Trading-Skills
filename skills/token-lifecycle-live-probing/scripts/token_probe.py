"""
token-lifecycle-live-probing: Production-grade live token probing manager,
3-outcome response classification (VALID / INVALID / AMBIGUOUS),
jittered backoff for ambiguous network timeouts, and empirical token lifespan tracking.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ProbeOutcome(str, Enum):
    VALID = "VALID"          # 2xx read response (cached token working)
    INVALID = "INVALID"      # 401/403 explicit token revocation -> trigger re-auth
    AMBIGUOUS = "AMBIGUOUS"  # 5xx / timeout / network drop -> retry backoff (do NOT treat as invalid)


# Backward compatibility constants
VALID = ProbeOutcome.VALID.value
INVALID = ProbeOutcome.INVALID.value
AMBIGUOUS = ProbeOutcome.AMBIGUOUS.value


def classify_probe_response(
    status_code: Optional[int], is_timeout: bool, invalid_codes: Tuple[int, ...] = (401, 403)
) -> str:
    """Classifies HTTP probe response into VALID, INVALID, or AMBIGUOUS."""
    if is_timeout or status_code is None:
        return AMBIGUOUS
    if status_code in invalid_codes:
        return INVALID
    if 200 <= status_code < 300:
        return VALID
    if status_code >= 500:
        return AMBIGUOUS
    return INVALID


def probe_with_backoff(
    probe_fn: Callable[[], Tuple[Optional[int], bool]],
    max_attempts: int = 3,
    base_delay: float = 0.1,
) -> str:
    """
    Executes cheap read-only probe call. Retries ONLY on AMBIGUOUS network outcomes.
    """
    for attempt in range(max_attempts):
        status_code, is_timeout = probe_fn()
        result = classify_probe_response(status_code, is_timeout)

        if result != AMBIGUOUS:
            return result

        if attempt < max_attempts - 1:
            backoff = base_delay * (2 ** attempt)
            logger.warning(f"Ambiguous token probe outcome (status={status_code}, timeout={is_timeout}). Retrying in {backoff:.2f}s...")
            time.sleep(backoff)

    return AMBIGUOUS


class LiveTokenProbeManager:
    """
    Probes cached broker tokens using side-effect-free GET calls before live trading,
    logging empirical token lifespans and triggering headless re-authentication on explicit revocation.
    """

    def __init__(self, alert_fn: Optional[Callable[[str], None]] = None):
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))
        self.empirical_lifespans: Dict[str, List[float]] = {}  # {broker: [lifespan_sec, ...]}

    def verify_and_refresh_token(
        self,
        broker_name: str,
        cached_token: Optional[str],
        probe_fn: Callable[[str], Tuple[Optional[int], bool]],
        reauth_fn: Callable[[], str],
    ) -> Tuple[str, bool]:
        """
        1. Probes cached_token if present.
        2. If VALID -> return cached_token.
        3. If INVALID -> trigger reauth_fn(), re-probe new token, and return new_token.
        4. If AMBIGUOUS -> trigger reauth_fn() after retries exhaust.
        """
        broker = broker_name.lower()

        if cached_token:
            probe_result = probe_with_backoff(lambda: probe_fn(cached_token))
            if probe_result == VALID:
                logger.info(f"Cached token for '{broker}' probed VALID.")
                return cached_token, False

            if probe_result == INVALID:
                logger.warning(f"Cached token for '{broker}' probed INVALID (401/403). Triggering headless re-auth...")

        # Trigger Re-authentication
        new_token = reauth_fn()

        # Verify new token with single probe
        status_code, is_timeout = probe_fn(new_token)
        new_res = classify_probe_response(status_code, is_timeout)

        if new_res != VALID:
            err_msg = f"Freshly issued token for '{broker}' failed validation probe (outcome={new_res})."
            self.alert_fn(err_msg)
            raise RuntimeError(err_msg)

        logger.info(f"Re-authentication successful for '{broker}'. New token verified VALID.")
        return new_token, True

    def record_lifespan(self, broker_name: str, issued_at_ts: float, invalidated_at_ts: float):
        broker = broker_name.lower()
        lifespan = max(0.0, invalidated_at_ts - issued_at_ts)
        if broker not in self.empirical_lifespans:
            self.empirical_lifespans[broker] = []
        self.empirical_lifespans[broker].append(lifespan)
        logger.info(f"Recorded empirical token lifespan for '{broker}': {lifespan/3600.0:.2f} hours.")
