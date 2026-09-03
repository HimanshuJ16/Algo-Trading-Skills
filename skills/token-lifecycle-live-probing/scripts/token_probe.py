"""
token-lifecycle-live-probing: live probing of cached broker tokens before trading.

Provides 3-outcome response classification (VALID / INVALID / AMBIGUOUS), capped
equal-jitter backoff for ambiguous outcomes, and empirical token-lifespan tracking.

The load-bearing rule of this module: AMBIGUOUS is not INVALID. A timeout, a 5xx,
a 429 or an unrecognised status means the probe never reached a verdict on the
token. Re-authenticating on those is the anti-pattern this skill exists to prevent
-- it converts a broker-side outage into a login-rate-limit lockout. Only a status
the broker defines as an auth failure triggers re-authentication; everything else
raises AmbiguousProbeError and leaves the decision to the caller.
"""
import logging
import random
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


class ProbeOutcome(str, Enum):
    VALID = "VALID"          # 2xx read response (cached token working)
    INVALID = "INVALID"      # broker-defined auth failure -> trigger re-auth
    AMBIGUOUS = "AMBIGUOUS"  # no verdict reached -> retry, then escalate; never re-auth


# Backward compatibility constants
VALID = ProbeOutcome.VALID.value
INVALID = ProbeOutcome.INVALID.value
AMBIGUOUS = ProbeOutcome.AMBIGUOUS.value

_OUTCOMES = frozenset({VALID, INVALID, AMBIGUOUS})

# Only statuses the broker documents as session/token failures belong here.
# Kite Connect: 403 "Session expired or invalidate. Must relogin" (TokenException).
# Fyers API v3: HTTP 401 carrying {"s":"error","code":-16,...}.
DEFAULT_INVALID_CODES: Tuple[int, ...] = (401, 403)

# Statuses that explicitly mean "try again", not "your token is dead".
# Kite Connect documents 429 as rate limiting; Fyers returns 429 with
# {"code":429,"message":"request limit reached","s":"error"}. Breeze documents 408.
# Classifying any of these as INVALID re-authenticates during exactly the
# conditions under which a login is most likely to be throttled.
DEFAULT_RETRYABLE_CODES: Tuple[int, ...] = (408, 425, 429)


class TokenProbeError(RuntimeError):
    """Base class for probe failures that leave the bot not ready to trade.

    `token` may hold a live credential. Never log, `repr`, or serialise it.
    """

    def __init__(self, message: str, token: Optional[str] = None, outcome: Optional[str] = None):
        super().__init__(message)
        self.token = token
        self.outcome = outcome


class AmbiguousProbeError(TokenProbeError):
    """The probe never reached a verdict on the token.

    The cached token is NOT known to be dead. The correct responses are to hold
    off trading and retry later, or to escalate to an operator -- not to
    re-authenticate, which risks tripping the broker's login rate limit while the
    broker is already degraded.
    """


class TokenVerificationError(TokenProbeError):
    """A freshly issued token failed its post-authentication verification probe.

    `token` carries the new token so the caller can persist it rather than
    discard it and burn another login on the next start.
    """


def _unpack_probe_result(result: Sequence[Any]) -> Tuple[Optional[int], bool, Any]:
    """Accepts a 2-tuple (status_code, is_timeout) or 3-tuple with a response body."""
    if not isinstance(result, (tuple, list)) or len(result) not in (2, 3):
        raise ValueError(
            "probe_fn must return (status_code, is_timeout) or "
            f"(status_code, is_timeout, body); got {type(result).__name__}"
        )
    if len(result) == 2:
        return result[0], bool(result[1]), None
    return result[0], bool(result[1]), result[2]


def classify_probe_response(
    status_code: Optional[int],
    is_timeout: bool,
    invalid_codes: Tuple[int, ...] = DEFAULT_INVALID_CODES,
    retryable_codes: Tuple[int, ...] = DEFAULT_RETRYABLE_CODES,
    body: Any = None,
    body_classifier: Optional[Callable[[Any], Optional[str]]] = None,
) -> str:
    """Classifies a probe response into VALID, INVALID, or AMBIGUOUS.

    Precedence: timeout/no-status -> broker auth codes -> retryable codes -> 5xx
    -> 2xx (optionally refined by `body_classifier`) -> anything else.

    Anything unrecognised is AMBIGUOUS, deliberately. A 400, 404, 405 or 410 is a
    client/config defect that re-authentication cannot fix, and a redirect to a
    login page is a guess; none of them justify spending a login attempt.

    `body_classifier(body) -> VALID | INVALID | AMBIGUOUS | None` is consulted
    only for 2xx responses, so it can downgrade an apparent success but never
    upgrade a transport-level failure. That is the hook for brokers that report
    errors inside a 2xx envelope -- ICICI Breeze returns
    {"Success": ..., "Status": <http-style code>, "Error": ...}, so a dead Breeze
    session can arrive under HTTP 200 and a status-only classifier calls it VALID.
    Returning None means "no opinion"; returning anything outside the three
    outcomes is a wiring bug and raises ValueError.
    """
    if is_timeout or status_code is None:
        return AMBIGUOUS
    if status_code in invalid_codes:
        return INVALID
    if status_code in retryable_codes:
        return AMBIGUOUS
    if status_code >= 500:
        return AMBIGUOUS
    if 200 <= status_code < 300:
        if body_classifier is not None:
            verdict = body_classifier(body)
            if verdict is not None:
                if verdict not in _OUTCOMES:
                    raise ValueError(
                        f"body_classifier returned {verdict!r}; expected one of "
                        f"{sorted(_OUTCOMES)} or None"
                    )
                return verdict
        return VALID
    logger.warning(
        f"Probe returned unrecognised status {status_code}; treating as AMBIGUOUS "
        f"rather than spending a re-authentication on it."
    )
    return AMBIGUOUS


def probe_with_backoff(
    probe_fn: Callable[[], Sequence[Any]],
    max_attempts: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 8.0,
    invalid_codes: Tuple[int, ...] = DEFAULT_INVALID_CODES,
    retryable_codes: Tuple[int, ...] = DEFAULT_RETRYABLE_CODES,
    body_classifier: Optional[Callable[[Any], Optional[str]]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    rng: Optional[Callable[[], float]] = None,
) -> str:
    """Runs the cheap read-only probe, retrying ONLY on AMBIGUOUS outcomes.

    `probe_fn` must translate transport failures into `(None, True)` itself. An
    exception raised out of `probe_fn` -- a bare `requests.get` will raise
    `ConnectionError` or `Timeout` -- propagates uncaught and skips the retry
    logic entirely, turning a transient blip into a hard startup failure. Wrap the
    HTTP call.

    Delay is equal jitter -- half the capped exponential delay, plus a random
    share of the other half. The floor keeps the backoff meaningful for a single
    client; the random half stops a fleet of bots restarting after the same broker
    outage from retrying in lockstep. `sleep_fn` and `rng` are injected so tests
    stay deterministic and fast.

    Returns AMBIGUOUS if every attempt was ambiguous. Callers must not read that
    as INVALID.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    if base_delay < 0 or max_delay < 0:
        raise ValueError("base_delay and max_delay must be non-negative")
    random_fn = rng if rng is not None else random.random

    result = AMBIGUOUS
    for attempt in range(max_attempts):
        status_code, is_timeout, body = _unpack_probe_result(probe_fn())
        result = classify_probe_response(
            status_code,
            is_timeout,
            invalid_codes=invalid_codes,
            retryable_codes=retryable_codes,
            body=body,
            body_classifier=body_classifier,
        )

        if result != AMBIGUOUS:
            return result

        if attempt < max_attempts - 1:
            capped = min(max_delay, base_delay * (2 ** attempt))
            backoff = capped / 2.0 + random_fn() * capped / 2.0
            logger.warning(
                f"Ambiguous token probe outcome (status={status_code}, timeout={is_timeout}); "
                f"attempt {attempt + 1}/{max_attempts}, retrying in {backoff:.2f}s..."
            )
            sleep_fn(backoff)

    return result


class LiveTokenProbeManager:
    """Probes cached broker tokens with side-effect-free GET calls before trading.

    Re-authenticates only on a broker-defined auth failure. An ambiguous probe
    raises AmbiguousProbeError instead of re-authenticating, so a broker outage
    cannot turn into a login-rate-limit lockout.

    Not synchronised. Two concurrent `verify_and_refresh_token` calls for the same
    broker can both observe INVALID and both call `reauth_fn`, spending two logins
    and leaving one caller holding a token the other has already superseded. An
    in-process lock would not help across processes anyway: run exactly one token
    owner per broker app and have other workers read the token it publishes.
    """

    def __init__(
        self,
        alert_fn: Optional[Callable[[str], None]] = None,
        max_attempts: int = 3,
        base_delay: float = 0.1,
        max_delay: float = 8.0,
        invalid_codes: Tuple[int, ...] = DEFAULT_INVALID_CODES,
        retryable_codes: Tuple[int, ...] = DEFAULT_RETRYABLE_CODES,
        body_classifier: Optional[Callable[[Any], Optional[str]]] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        rng: Optional[Callable[[], float]] = None,
    ) -> None:
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.invalid_codes = invalid_codes
        self.retryable_codes = retryable_codes
        self.body_classifier = body_classifier
        self.sleep_fn = sleep_fn
        self.rng = rng
        self.empirical_lifespans: Dict[str, List[float]] = {}  # {broker: [lifespan_sec, ...]}

    def _alert(self, message: str) -> None:
        """Fires the operator alert without letting the alert channel change the
        trading verdict.

        A Slack webhook or pager call that raises would otherwise replace
        AmbiguousProbeError with a ConnectionError, and the caller's `except
        AmbiguousProbeError` -- the branch that decides not to spend a login --
        would never run. The broad catch is deliberate and confined to this
        side-channel.
        """
        try:
            self.alert_fn(message)
        except Exception:  # noqa: BLE001 - alerting must never mask the verdict
            logger.exception("alert_fn raised while reporting a token probe failure.")

    def _probe(self, probe_fn: Callable[[str], Sequence[Any]], token: str) -> str:
        return probe_with_backoff(
            lambda: probe_fn(token),
            max_attempts=self.max_attempts,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            invalid_codes=self.invalid_codes,
            retryable_codes=self.retryable_codes,
            body_classifier=self.body_classifier,
            sleep_fn=self.sleep_fn,
            rng=self.rng,
        )

    def verify_and_refresh_token(
        self,
        broker_name: str,
        cached_token: Optional[str],
        probe_fn: Callable[[str], Sequence[Any]],
        reauth_fn: Callable[[], str],
    ) -> Tuple[str, bool]:
        """Returns (token, was_refreshed) for a token confirmed live by a probe.

        1. VALID cached token   -> returned unchanged; reauth_fn is not called.
        2. INVALID cached token -> reauth_fn(), then verify the new token.
        3. AMBIGUOUS            -> raises AmbiguousProbeError. reauth_fn is NOT
           called: the token is not known to be dead, and logging in against a
           degraded broker risks a rate-limit lockout that outlasts the outage.
        4. No cached token      -> reauth_fn(), then verify.

        Raises TokenVerificationError if a freshly issued token does not probe
        VALID; the new token is attached to the exception so the caller can
        persist it instead of burning another login on the next start.
        """
        broker = broker_name.lower()

        if cached_token:
            probe_result = self._probe(probe_fn, cached_token)
            if probe_result == VALID:
                logger.info(f"Cached token for '{broker}' probed VALID.")
                return cached_token, False

            if probe_result == AMBIGUOUS:
                msg = (
                    f"Token probe for '{broker}' stayed AMBIGUOUS after "
                    f"{self.max_attempts} attempt(s). Not re-authenticating: the cached "
                    f"token is not known to be invalid, and a login against a degraded "
                    f"broker risks a rate-limit lockout. Hold trading and retry."
                )
                self._alert(msg)
                raise AmbiguousProbeError(msg, token=cached_token, outcome=AMBIGUOUS)

            logger.warning(
                f"Cached token for '{broker}' probed INVALID. Triggering headless re-auth..."
            )

        new_token = reauth_fn()
        if not new_token:
            msg = f"Re-authentication for '{broker}' returned no token."
            self._alert(msg)
            raise TokenVerificationError(msg, outcome=INVALID)

        new_res = self._probe(probe_fn, new_token)
        if new_res != VALID:
            msg = (
                f"Freshly issued token for '{broker}' failed its verification probe "
                f"(outcome={new_res}). Persist the token before retrying so the next "
                f"start does not spend another login."
            )
            self._alert(msg)
            raise TokenVerificationError(msg, token=new_token, outcome=new_res)

        logger.info(f"Re-authentication successful for '{broker}'. New token verified VALID.")
        return new_token, True

    def record_lifespan(
        self, broker_name: str, issued_at_ts: float, invalidated_at_ts: float
    ) -> float:
        """Records one observed token lifespan, in seconds, and returns it.

        Raises ValueError on a negative lifespan. The earlier revision clamped it
        to 0.0, which silently poisoned the empirical baseline that
        `should_proactively_refresh` reads from: one clock-skew or bookkeeping
        error produced a 0-second "observation", and every later token then looked
        overdue for refresh.
        """
        broker = broker_name.lower()
        lifespan = invalidated_at_ts - issued_at_ts
        if lifespan < 0:
            raise ValueError(
                f"Invalidation timestamp precedes issue timestamp for '{broker}' "
                f"({lifespan:.1f}s); check clock sync and token bookkeeping."
            )
        self.empirical_lifespans.setdefault(broker, []).append(lifespan)
        logger.info(
            f"Recorded empirical token lifespan for '{broker}': {lifespan / 3600.0:.2f} hours "
            f"({len(self.empirical_lifespans[broker])} sample(s))."
        )
        return lifespan

    def should_proactively_refresh(
        self,
        broker_name: str,
        issued_at_ts: float,
        now_ts: float,
        safety_margin_sec: float = 1800.0,
        min_samples: int = 3,
    ) -> bool:
        """True when the current token is within `safety_margin_sec` of the
        shortest lifespan ever observed for this broker.

        Uses the minimum, not the mean: the mean sits happily above a lifespan
        that has already been observed to end sooner. Returns False until
        `min_samples` observations exist -- acting on one sample means a single
        early logout, or a token revoked by hand during testing, permanently drags
        the proactive refresh forward.

        This decides *when* to refresh, never *whether* the token is alive. A
        token inside the window may still be live and one outside it may already
        be dead; only the probe decides that.
        """
        samples = self.empirical_lifespans.get(broker_name.lower(), [])
        if len(samples) < min_samples:
            return False
        return (now_ts - issued_at_ts) >= (min(samples) - safety_margin_sec)
