"""Dual-credential hot-swap rotation for a trading bot that must not restart.

Scope of this module
--------------------
It manages the *client-side* half of an API-credential rotation: probe a
candidate credential, publish it atomically to a running trading loop, keep the
outgoing credential available as a fallback for a bounded overlap window, and
then drive the caller's revocation of the outgoing credential at the venue.

It deliberately does **not** mint credentials. Many venues gate API-key creation
behind a 2FA-protected console with no programmatic equivalent, so "automated
rotation" in practice means an operator (or an orchestrator holding console
credentials) creates the key and this module performs the unattended part. See
``references/workflows.md``.

Four facts drive the design:

1. **The trading loop reads the credential from another thread.** Publishing a
   new credential is therefore a lock-guarded operation, and callers take the
   credential through :meth:`SecretsRotator.use`, which both hands out a
   consistent snapshot and counts the request as in flight.

2. **Revocation is the point of the exercise, and it happens at the venue.**
   Marking a credential dead in local memory does not stop anyone holding the
   leaked key. :meth:`SecretsRotator.revoke_previous` calls a caller-supplied
   ``revoke_fn`` and reports :attr:`RotationState.REVOCATION_FAILED` when the
   venue did not confirm — that state means *the old key is still live*.

3. **A probe that times out has not proven anything.** ``validate_fn`` raising
   is the expected failure mode when probing a broker endpoint, so it is caught
   and treated as *indeterminate*: the swap does not happen, and the state
   machine does not strand itself in ``VALIDATING_NEW``.

4. **Some credentials cannot be rotated this way at all.** OAuth refresh-token
   rotation invalidates the outgoing token at the moment the new one is issued,
   and presenting the old one afterwards is what replay detection exists to
   punish — RFC 9700 Section 4.14.2 has the authorization server revoke the
   active refresh token on detection. Holding a "fallback" refresh token is
   therefore not a safety net but a way to lose the whole grant mid-session.
   Use ``upstox-oauth-refresh-token-rotation`` for that shape of credential.

No third-party dependency is required.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, Dict, Iterator, Optional

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_LIMIT = 256

_lease_tokens = itertools.count(1)


class RotationState(Enum):
    """Where the rotator is in the dual-credential lifecycle.

    The distinction between ``VALIDATION_FAILED`` and ``ROLLED_BACK`` is
    operationally load-bearing, and is why they are separate members: the first
    means nothing changed and no trading was affected, the second means a
    credential was live on real order flow and failed. An alert routed off
    ``ROLLED_BACK`` should page; one routed off ``VALIDATION_FAILED`` should
    not.
    """

    IDLE = "IDLE"
    VALIDATING_NEW = "VALIDATING_NEW"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    SWAPPED = "SWAPPED"
    ROLLED_BACK = "ROLLED_BACK"
    REVOKED_OLD = "REVOKED_OLD"
    REVOCATION_FAILED = "REVOCATION_FAILED"


class RotationError(RuntimeError):
    """Base class for every failure raised by this module."""


class NoActiveCredential(RotationError):
    """The rotator holds no credential; call ``set_initial_credential`` first."""


class RotationInProgress(RotationError):
    """A previous credential is still un-revoked.

    Rotating again would drop the rotator's only reference to it while it is
    still valid at the venue, leaving a live credential that nothing tracks and
    nobody will revoke. Finish the outstanding rotation, or pass ``force=True``
    having arranged the revocation by other means.
    """


class OverlapWindowOpen(RotationError):
    """Revocation was attempted before the overlap window closed."""


class CredentialInUse(RotationError):
    """Requests leased against this credential are still in flight."""


class NoFallbackAvailable(RotationError):
    """There is no valid previous credential to fall back to."""


def accept_without_validation(credential: "Credential") -> bool:
    """A ``validate_fn`` that approves every candidate. Unsafe by construction.

    Passing this means a credential that has never been proven to authenticate
    is published straight onto live order flow, and the first evidence that it
    is wrong will be rejected orders. It exists so the unsafe choice has to be
    made by name rather than inherited from a default, and it logs every use.
    """
    logger.warning(
        "Credential '%s' accepted WITHOUT validation; the first proof it works "
        "will be live order flow.",
        credential.key_id,
    )
    return True


@dataclass
class Credential:
    """One API credential.

    ``repr`` redacts ``secret``: a credential reaches log pipelines and
    tracebacks by accident far more often than by design. ``reveal()`` is the
    deliberate accessor; ``.secret`` remains available for callers that already
    use it.
    """

    key_id: str
    secret: str
    created_at: float = 0.0
    is_valid: bool = True
    lease_token: int = field(
        default_factory=lambda: next(_lease_tokens), compare=False
    )

    def reveal(self) -> str:
        """Return the plaintext secret. Explicit, so it never happens by accident."""
        return self.secret

    def __repr__(self) -> str:
        return (
            f"Credential(key_id={self.key_id!r}, secret=<redacted "
            f"{len(self.secret)} chars>, created_at={self.created_at!r}, "
            f"is_valid={self.is_valid!r})"
        )

    __str__ = __repr__


@dataclass
class RotationResult:
    """Outcome of one rotator operation.

    ``success`` is about the operation, not about safety. A ``revoke_previous``
    result with ``success=False`` and ``state=REVOCATION_FAILED`` means the old
    credential is **still live at the venue** and needs a human.
    """

    success: bool
    state: RotationState
    active_key_id: str
    message: str
    indeterminate: bool = False


@dataclass(frozen=True)
class RotatorStatus:
    """Point-in-time snapshot for monitoring, carrying no secret material."""

    state: RotationState
    active_key_id: Optional[str]
    previous_key_id: Optional[str]
    active_leases: int
    previous_leases: int
    overlap_elapsed: Optional[float]


class SecretsRotator:
    """Hot-swap credential rotation for a live trading process.

    Thread-safe: every mutation and every read of the credential pair happens
    under one re-entrant lock, so a trading thread calling :meth:`use` can never
    observe the half-applied state between "previous recorded" and "new
    published".

    Args:
        validate_fn: Proves a candidate credential authenticates at the venue,
            typically via a signed read-only call. Returning ``False`` or
            raising both prevent the swap; raising is additionally recorded as
            *indeterminate*, because a probe that timed out has not shown the
            credential to be bad, only unproven. Pass
            :func:`accept_without_validation` to opt out explicitly.
        revoke_fn: Invalidates a credential **at the venue**. Returning normally
            means the venue confirmed; raising means it did not. Omitting it
            means :meth:`revoke_previous` can only forget the credential
            locally, which it refuses to call a revocation.
        on_activate: Called under the lock with a credential about to become
            active, for per-credential state the venue ties to the key — most
            often an HMAC nonce floor. Kraken rejects a nonce lower than one
            already used with that key, so a fallback that resumes a previous
            key with a reset counter is refused on every request at exactly the
            wrong moment. Raising here aborts a *rotation* (safe: the old
            credential stays active) but only warns during a *fallback*, where
            refusing would leave the bot with no working credential at all.
        min_overlap_seconds: Lower bound, measured on the monotonic clock,
            between a swap and revocation of the outgoing credential. The
            in-flight lease count already gates requests this rotator handed
            out; this covers users it cannot see, such as a websocket session
            authenticated with the old key. There is no defensible universal
            value — it is the caller's maximum request lifetime plus retry
            budget — so the default of 0.0 enforces only the lease gate.
        history_limit: Bound on retained audit records.
    """

    def __init__(
        self,
        validate_fn: Optional[Callable[["Credential"], bool]] = None,
        *,
        revoke_fn: Optional[Callable[["Credential"], None]] = None,
        on_activate: Optional[Callable[["Credential"], None]] = None,
        min_overlap_seconds: float = 0.0,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> None:
        if validate_fn is None:
            raise ValueError(
                "validate_fn is required. A rotator that swaps in an unproven "
                "credential defeats its own purpose; pass "
                "accept_without_validation to opt out deliberately."
            )
        if min_overlap_seconds < 0:
            raise ValueError("min_overlap_seconds must be non-negative.")
        if history_limit < 1:
            raise ValueError("history_limit must be at least 1.")

        self.validate_fn = validate_fn
        self.revoke_fn = revoke_fn
        self.on_activate = on_activate
        self.min_overlap_seconds = float(min_overlap_seconds)

        self.active_credential: Optional[Credential] = None
        self.previous_credential: Optional[Credential] = None
        self.state = RotationState.IDLE
        self.rotation_history: Deque[Dict[str, object]] = deque(maxlen=history_limit)

        self._lock = threading.RLock()
        self._drained = threading.Condition(self._lock)
        self._leases: Dict[int, int] = {}
        self._swapped_at: Optional[float] = None
        self._rotating = False

    # ------------------------------------------------------------------ #
    # Reading the credential
    # ------------------------------------------------------------------ #
    def current(self) -> Credential:
        """Return the active credential atomically.

        Prefer :meth:`use` for anything that makes a request: a bare read is not
        counted as in flight, so a concurrent revocation has no way to know the
        credential is still on the wire.
        """
        with self._lock:
            if self.active_credential is None:
                raise NoActiveCredential("No active credential has been set.")
            return self.active_credential

    @contextmanager
    def use(self) -> Iterator[Credential]:
        """Lease the active credential for the duration of one venue call.

        The lease is what makes the overlap window mean anything: a credential
        with outstanding leases will not be revoked, so a request signed just
        before a swap still completes against a key the venue accepts.

        Hold the lease for the request only. Wrapping a whole trading session in
        one lease blocks revocation for the life of the session.
        """
        with self._lock:
            credential = self.current()
            token = credential.lease_token
            self._leases[token] = self._leases.get(token, 0) + 1
        try:
            yield credential
        finally:
            with self._drained:
                remaining = self._leases.get(token, 0) - 1
                if remaining > 0:
                    self._leases[token] = remaining
                else:
                    self._leases.pop(token, None)
                self._drained.notify_all()

    def status(self) -> RotatorStatus:
        """Snapshot for monitoring. Contains no secret material."""
        with self._lock:
            elapsed = (
                None
                if self._swapped_at is None
                else time.monotonic() - self._swapped_at
            )
            return RotatorStatus(
                state=self.state,
                active_key_id=(
                    self.active_credential.key_id if self.active_credential else None
                ),
                previous_key_id=(
                    self.previous_credential.key_id
                    if self.previous_credential
                    else None
                ),
                active_leases=self._lease_count(self.active_credential),
                previous_leases=self._lease_count(self.previous_credential),
                overlap_elapsed=elapsed,
            )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def set_initial_credential(self, key_id: str, secret: str) -> None:
        """Seed the rotator with the credential the bot boots on.

        Refuses to overwrite an in-flight rotation: doing so would silently drop
        the un-revoked previous credential.
        """
        _require_credential_material(key_id, secret)
        with self._lock:
            if self.previous_credential is not None:
                raise RotationInProgress(
                    f"Previous credential '{self.previous_credential.key_id}' is "
                    "still un-revoked; resolve that rotation before reseeding."
                )
            self.active_credential = Credential(
                key_id=key_id, secret=secret, created_at=time.time()
            )
            self.state = RotationState.IDLE
            self._swapped_at = None
        logger.info("Initial credential set: %s", key_id)

    def rotate(
        self, new_key_id: str, new_secret: str, *, force: bool = False
    ) -> RotationResult:
        """Validate a candidate credential and, if it proves out, publish it.

        The outgoing credential stays in place as the fallback until
        :meth:`revoke_previous` closes the overlap. Nothing is published unless
        ``validate_fn`` returned true; a probe that raised leaves the active
        credential untouched and reports ``indeterminate=True``.

        Args:
            force: Rotate even though a previous credential is still un-revoked.
                Only correct when its revocation has been arranged elsewhere;
                otherwise it strands a live credential. ``force`` does **not**
                permit two rotations to run at once — that is never valid.
        """
        _require_credential_material(new_key_id, new_secret)

        with self._lock:
            if self.active_credential is None:
                raise NoActiveCredential("No active credential to rotate from.")

            # The probe below runs unlocked, so a second rotation entering here
            # meanwhile would validate against a credential pair that is about
            # to change under it and publish a stale 'outgoing', dropping one
            # credential while it is still live at the venue. Rotations are
            # therefore serialised end to end, not just at their mutation
            # points.
            if self._rotating:
                raise RotationInProgress(
                    "Another rotation is already validating. Concurrent "
                    "rotations would leave one credential live at the venue "
                    "and untracked here."
                )

            outgoing = self.active_credential

            if new_key_id == outgoing.key_id:
                raise ValueError(
                    f"Candidate key_id '{new_key_id}' is already active. An "
                    "overlap between a credential and itself is not a fallback."
                )
            if self.previous_credential is not None and not force:
                raise RotationInProgress(
                    f"Previous credential '{self.previous_credential.key_id}' is "
                    "still un-revoked. Rotating now would leave it live at the "
                    "venue and untracked here. Revoke it, or pass force=True."
                )

            candidate = Credential(
                key_id=new_key_id, secret=new_secret, created_at=time.time()
            )

            self.state = RotationState.VALIDATING_NEW
            logger.info("Validating candidate credential '%s'...", new_key_id)
            # Set last, and immediately before the try/finally that clears it,
            # so nothing between here and the guard can leave it stuck True.
            self._rotating = True

        try:
            return self._validate_and_publish(
                candidate, outgoing, new_key_id
            )
        finally:
            with self._lock:
                self._rotating = False

    def _validate_and_publish(
        self, candidate: "Credential", outgoing: "Credential", new_key_id: str
    ) -> RotationResult:
        """Probe the candidate unlocked, then publish it under the lock."""
        # Probe outside the lock: it is a network call, and holding the lock for
        # its duration would stall every use() in the trading loop.
        indeterminate = False
        try:
            approved = bool(self.validate_fn(candidate))
        except Exception as exc:  # noqa: BLE001 - any probe failure is a non-proof
            approved = False
            indeterminate = True
            logger.warning(
                "Validation of candidate '%s' raised %s: %s. Treating as "
                "unproven and keeping '%s' active.",
                new_key_id,
                type(exc).__name__,
                exc,
                outgoing.key_id,
            )

        with self._lock:
            if approved and self.active_credential is not outgoing:
                # A fallback or reseed landed while the probe was in flight, so
                # 'outgoing' is stale. Publishing now would record the wrong
                # fallback and drop a live credential.
                self.state = RotationState.VALIDATION_FAILED
                msg = (
                    f"ROTATION NOT PERFORMED: the active credential changed from "
                    f"'{outgoing.key_id}' while '{new_key_id}' was being "
                    "validated. Re-run the rotation against the current "
                    "credential."
                )
                logger.warning(msg)
                self._record(
                    "ROTATION_ABORTED",
                    old_key=outgoing.key_id,
                    new_key=new_key_id,
                    reason="active credential changed during validation",
                )
                return RotationResult(
                    success=False,
                    state=self.state,
                    active_key_id=(
                        self.active_credential.key_id
                        if self.active_credential
                        else ""
                    ),
                    message=msg,
                )

            if not approved:
                self.state = RotationState.VALIDATION_FAILED
                reason = (
                    "could not be validated" if indeterminate else "failed validation"
                )
                msg = (
                    f"ROTATION NOT PERFORMED: candidate '{new_key_id}' {reason}. "
                    f"Active credential '{outgoing.key_id}' is unchanged; no "
                    "trading was affected."
                )
                logger.warning(msg)
                self._record(
                    "ROTATION_ABORTED",
                    old_key=outgoing.key_id,
                    new_key=new_key_id,
                    indeterminate=indeterminate,
                )
                return RotationResult(
                    success=False,
                    state=self.state,
                    active_key_id=outgoing.key_id,
                    message=msg,
                    indeterminate=indeterminate,
                )

            if self.on_activate is not None:
                try:
                    self.on_activate(candidate)
                except Exception as exc:  # noqa: BLE001
                    self.state = RotationState.VALIDATION_FAILED
                    msg = (
                        f"ROTATION NOT PERFORMED: on_activate for '{new_key_id}' "
                        f"raised {type(exc).__name__}: {exc}. Active credential "
                        f"'{outgoing.key_id}' is unchanged."
                    )
                    logger.warning(msg)
                    self._record(
                        "ROTATION_ABORTED",
                        old_key=outgoing.key_id,
                        new_key=new_key_id,
                        reason="on_activate failed",
                    )
                    return RotationResult(
                        success=False,
                        state=self.state,
                        active_key_id=outgoing.key_id,
                        message=msg,
                    )

            self.previous_credential = outgoing
            self.active_credential = candidate
            self.state = RotationState.SWAPPED
            self._swapped_at = time.monotonic()

            self._record("ROTATED", old_key=outgoing.key_id, new_key=new_key_id)
            msg = (
                f"ROTATION SUCCESS: swapped from '{outgoing.key_id}' to "
                f"'{new_key_id}'. Outgoing credential retained as fallback and "
                "is STILL LIVE at the venue until revoke_previous() succeeds."
            )
            logger.info(msg)
            return RotationResult(
                success=True,
                state=self.state,
                active_key_id=new_key_id,
                message=msg,
            )

    def fallback_to_previous(self) -> RotationResult:
        """Revert to the previous credential after the new one failed live.

        Only meaningful while the overlap is open. Once :meth:`revoke_previous`
        has succeeded there is nothing to fall back to, which is the whole
        reason revocation is a separate, deliberate step.
        """
        with self._lock:
            previous = self.previous_credential
            if previous is None or not previous.is_valid:
                raise NoFallbackAvailable(
                    "No valid previous credential is available. The overlap "
                    "window is closed; recovery requires a new credential."
                )

            failing = self.active_credential
            failing_key = failing.key_id if failing else "unknown"

            if self.on_activate is not None:
                try:
                    self.on_activate(previous)
                except Exception as exc:  # noqa: BLE001
                    # Proceeding anyway: the alternative is a bot with no
                    # working credential at all. Logged at ERROR because the
                    # venue may now reject the fallback too (a stale nonce
                    # floor, for instance).
                    logger.error(
                        "on_activate for fallback credential '%s' raised %s: %s. "
                        "Falling back regardless; the venue may reject it.",
                        previous.key_id,
                        type(exc).__name__,
                        exc,
                    )

            self.active_credential = previous
            self.previous_credential = None
            self.state = RotationState.ROLLED_BACK
            self._swapped_at = None

            self._record("ROLLED_BACK", old_key=failing_key, new_key=previous.key_id)
            msg = (
                f"FALLBACK EXECUTED: reverted from '{failing_key}' to "
                f"'{previous.key_id}'. '{failing_key}' was live on real order "
                "flow and must be investigated before it is retried."
            )
            logger.warning(msg)
            return RotationResult(
                success=True,
                state=self.state,
                active_key_id=previous.key_id,
                message=msg,
            )

    def drain_previous(self, timeout: Optional[float] = None) -> bool:
        """Block until no leases remain on the previous credential.

        Returns ``True`` once drained, ``False`` on timeout. There is nothing to
        drain if no previous credential is held, which counts as drained.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._drained:
            while True:
                if self.previous_credential is None:
                    return True
                if self._lease_count(self.previous_credential) == 0:
                    return True
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._drained.wait(remaining)

    def revoke_previous(self, *, force: bool = False) -> RotationResult:
        """Revoke the outgoing credential at the venue and close the overlap.

        This is the step that actually removes the old credential's access.
        Until it reports success the old key still authenticates, which is why a
        failure here is reported as ``REVOCATION_FAILED`` with ``success=False``
        rather than being swallowed.

        Refuses while requests are in flight against the outgoing credential, or
        while the overlap window is still open, unless ``force=True`` — which
        accepts that in-flight requests may be rejected mid-session.
        """
        with self._lock:
            previous = self.previous_credential
            if previous is None:
                return RotationResult(
                    success=False,
                    state=self.state,
                    active_key_id=(
                        self.active_credential.key_id
                        if self.active_credential
                        else ""
                    ),
                    message="No previous credential to revoke.",
                )

            if not force:
                in_flight = self._lease_count(previous)
                if in_flight:
                    raise CredentialInUse(
                        f"{in_flight} request(s) are still in flight against "
                        f"'{previous.key_id}'. Call drain_previous() first, or "
                        "pass force=True and accept that they may be rejected."
                    )
                if self.min_overlap_seconds > 0 and self._swapped_at is not None:
                    elapsed = time.monotonic() - self._swapped_at
                    if elapsed < self.min_overlap_seconds:
                        raise OverlapWindowOpen(
                            f"Overlap window is {elapsed:.2f}s of a required "
                            f"{self.min_overlap_seconds:.2f}s. Revoking now can "
                            "cut off users this rotator does not lease, such as "
                            "an open websocket session."
                        )

            old_key = previous.key_id
            active_key = (
                self.active_credential.key_id if self.active_credential else ""
            )

            if self.revoke_fn is None:
                self.state = RotationState.REVOCATION_FAILED
                msg = (
                    f"NOT REVOKED: no revoke_fn is configured, so '{old_key}' has "
                    "only been forgotten locally and REMAINS VALID at the venue. "
                    "Revoke it in the venue console."
                )
                logger.error(msg)
                self._record(
                    "REVOCATION_UNAVAILABLE", old_key=old_key, new_key=active_key
                )
                self.previous_credential = None
                self._swapped_at = None
                return RotationResult(
                    success=False,
                    state=self.state,
                    active_key_id=active_key,
                    message=msg,
                )

            try:
                self.revoke_fn(previous)
            except Exception as exc:  # noqa: BLE001
                self.state = RotationState.REVOCATION_FAILED
                msg = (
                    f"REVOCATION FAILED for '{old_key}': {type(exc).__name__}: "
                    f"{exc}. The credential REMAINS VALID at the venue and is "
                    "still retained here as a fallback."
                )
                logger.error(msg)
                self._record(
                    "REVOCATION_FAILED",
                    old_key=old_key,
                    new_key=active_key,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return RotationResult(
                    success=False,
                    state=self.state,
                    active_key_id=active_key,
                    message=msg,
                )

            previous.is_valid = False
            self.previous_credential = None
            self.state = RotationState.REVOKED_OLD
            self._swapped_at = None

            self._record("REVOKED_OLD", old_key=old_key, new_key=active_key)
            msg = f"Previous credential '{old_key}' revoked at the venue."
            logger.info(msg)
            return RotationResult(
                success=True,
                state=self.state,
                active_key_id=active_key,
                message=msg,
            )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _lease_count(self, credential: Optional[Credential]) -> int:
        if credential is None:
            return 0
        return self._leases.get(credential.lease_token, 0)

    def _record(self, action: str, **fields: object) -> None:
        record: Dict[str, object] = {"action": action, "timestamp": time.time()}
        record.update(fields)
        self.rotation_history.append(record)

    def __repr__(self) -> str:
        with self._lock:
            active = self.active_credential.key_id if self.active_credential else None
            previous = (
                self.previous_credential.key_id if self.previous_credential else None
            )
        return (
            f"SecretsRotator(state={self.state.value}, active_key_id={active!r}, "
            f"previous_key_id={previous!r})"
        )


def _require_credential_material(key_id: str, secret: str) -> None:
    """Reject credential material that cannot authenticate anywhere."""
    if not isinstance(key_id, str) or not key_id.strip():
        raise ValueError("key_id must be a non-empty string.")
    if not isinstance(secret, str) or not secret:
        raise ValueError(f"secret for key_id '{key_id}' must be a non-empty string.")
