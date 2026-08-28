"""Phishing-resistant authentication gate for institutional crypto custody access.

This module is a **WebAuthn Relying Party policy gate**, not a WebAuthn library.
It consumes the parsed output of a real WebAuthn implementation (``py_webauthn``,
``python-fido2``, or a custodian's own verifier) and decides whether that
assertion may unlock custody or signing-key operations.

The division of labour matters, because getting it wrong is how phishing-
resistant deployments end up merely phishing-*flavoured*:

* The **library** parses ``clientDataJSON`` and ``authenticatorData``, and
  verifies the COSE signature over ``authData || SHA-256(clientDataJSON)``.
* **This engine** enforces the checks a library cannot make for you, because
  they depend on server-side state and firm policy: that the challenge is one
  *this server* issued and has never been used before, that the origin is one
  *this server* expects, that the credential belongs to the user being
  authenticated, that the UP/UV flags meet policy, that the signature counter
  has not gone backwards, and that the credential's backup posture is allowed.

Every one of those checks is a numbered step of W3C WebAuthn Level 3 §7.2
(Recommendation, 2026-08-25). See ``references/standards.md`` for the mapping
from step to code path, and for which defaults here are firm policy rather than
a specification or regulatory requirement.

Deny-by-default is deliberate throughout. ``signature_verified``,
``user_present`` and ``user_verified`` all default to ``False``: an assertion
that was never wired up to a real verifier must fail closed, not inherit an
optimistic default. That is a behavioural break from version 1.x, and it is the
point of the change.
"""

import hashlib
import hmac
import logging
import math
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# WebAuthn L3 §5.8.1: the client data type for an authentication ceremony.
# A registration response ("webauthn.create") replayed into the authentication
# path must never be accepted.
CLIENT_DATA_TYPE_GET = "webauthn.get"

# WebAuthn L3 §13.4.3: "Challenges SHOULD therefore be at least 16 bytes long."
MIN_CHALLENGE_ENTROPY_BYTES = 16


class PhishingResistantAuthError(Exception):
    """Raised on malformed policy or a structurally unusable assertion.

    Raised rather than returned as a rejected report, because an assertion the
    engine cannot evaluate must never be recorded in the audit trail as an
    assertion the engine evaluated and declined. The two mean different things
    to whoever reads the log after an incident.
    """


def _require_identifier(value: object, label: str) -> str:
    text = str(value).strip()
    if not text:
        raise PhishingResistantAuthError(f"{label} must be a non-empty identifier.")
    return text


def _require_finite(value: object, label: str) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PhishingResistantAuthError(f"{label} must be numeric, got {value!r}.") from exc
    if not math.isfinite(numeric):
        raise PhishingResistantAuthError(f"{label} must be finite, got {value!r}.")
    return numeric


def _require_https_origin(value: object, label: str) -> str:
    """Validates an expected-origin entry at configuration time.

    Origins are compared later by exact string match (WebAuthn L3 §13.4.9), so a
    configured origin carrying a trailing slash or a path would silently never
    match any real ``clientDataJSON.origin`` and lock every user out. Failing
    here turns that into a startup error instead of a production outage.
    """
    text = str(value).strip()
    if not text.startswith("https://"):
        raise PhishingResistantAuthError(
            f"{label} must be an https:// origin, got {value!r}. "
            "WebAuthn credentials are scoped to secure contexts."
        )
    remainder = text[len("https://"):]
    if not remainder or "/" in remainder:
        raise PhishingResistantAuthError(
            f"{label} must be a bare scheme://host[:port] origin with no path or "
            f"trailing slash, got {value!r}; origins are compared by exact match."
        )
    return text


def _require_digest(value: object, label: str) -> bytes:
    """Coerces a raw digest, refusing the hex/base64 text callers often reach for.

    ``authenticatorData[0:32]`` is raw bytes. Accepting a hex string here and
    comparing it against raw bytes would fail every assertion; silently encoding
    it would compare the wrong thing. Both outcomes are worse than an explicit
    configuration error.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    raise PhishingResistantAuthError(
        f"{label} must be raw bytes (authenticatorData[0:32]), got "
        f"{type(value).__name__}."
    )


def _require_count(value: object, label: str) -> int:
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PhishingResistantAuthError(f"{label} must be an integer, got {value!r}.") from exc
    if count < 0:
        raise PhishingResistantAuthError(f"{label} must be non-negative, got {count}.")
    return count


def _constant_time_equal(left: str, right: str) -> bool:
    """Compares two identifiers without leaking their contents through timing.

    Encoded to UTF-8 first because ``hmac.compare_digest`` rejects ``str``
    arguments outside ASCII, and a non-ASCII user identifier must produce a
    rejection, not a ``TypeError`` from inside the security check.
    """
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def compute_rp_id_hash(rp_id: str) -> bytes:
    """SHA-256 of the RP ID, as it appears in the first 32 bytes of authData.

    WebAuthn L3 §7.2 requires the RP to compare ``authData.rpIdHash`` against a
    hash of *the RP ID the server expects*, never against an RP ID taken from
    the response being verified.
    """
    return hashlib.sha256(_require_identifier(rp_id, "rp_id").encode("utf-8")).digest()


@dataclass(frozen=True)
class RegisteredCredential:
    """A credential the RP has registered for exactly one user account.

    ``sign_count`` is the last counter value accepted for this credential.
    ``backup_eligible`` mirrors the BE flag captured at registration; WebAuthn
    L3 §6.1.3 states BE "MUST NOT change" over the credential's life, so a later
    assertion contradicting it is evidence of a substituted credential.
    """

    credential_id: str
    user_id: str
    sign_count: int = 0
    aaguid: str = ""
    backup_eligible: bool = False
    is_revoked: bool = False


@dataclass(frozen=True)
class IssuedChallenge:
    """A server-generated, single-use challenge bound to one user."""

    value: str
    user_id: str
    issued_at: float


@dataclass
class WebAuthnAssertion:
    """One parsed authentication assertion, as produced by a WebAuthn library.

    Field names follow the specification's own vocabulary so the mapping back to
    §7.2 stays obvious. Everything here except ``user_id`` is **attacker-
    influenced input**: it is what the client returned, not what the server
    knows. The engine treats it accordingly.

    ``signature_verified`` must be set from the result of the library's
    signature check. This engine performs no cryptography on the assertion and
    will not report success while this is ``False``.
    """

    user_id: str
    credential_id: str
    client_origin: str                      # clientDataJSON.origin
    challenge: str                          # clientDataJSON.challenge (base64url)
    rp_id_hash: Optional[bytes] = None      # authData[0:32]
    client_data_type: str = CLIENT_DATA_TYPE_GET
    user_present: bool = False              # authData flags UP
    user_verified: bool = False             # authData flags UV
    signature_verified: bool = False        # result of the library's signature check
    sign_count: int = 0                     # authData signCount
    backup_eligible: bool = False           # authData flags BE
    backup_state: bool = False              # authData flags BS
    aaguid: str = ""                        # attestedCredentialData AAGUID, when available


@dataclass
class AuthPolicyConfig:
    """Relying Party expectations and firm policy.

    ``rp_id`` and ``allowed_origins`` are the *server's* expectations. They are
    the anchor of phishing resistance: an AiTM proxy can reproduce every field
    of an assertion except an origin the RP is willing to accept.

    Defaults marked "firm policy" below carry no specification or regulatory
    force; see ``references/standards.md``.
    """

    rp_id: str = "custody.firm.com"
    allowed_origins: Tuple[str, ...] = ("https://custody.firm.com",)
    require_user_verification: bool = True          # firm policy; §7.2 makes UV conditional
    require_signature_verification: bool = True     # firm policy: fail closed if unwired
    require_rp_id_hash: bool = True                 # firm policy: fail closed if unwired
    max_challenge_age_sec: float = 60.0             # firm policy
    clock_skew_tolerance_sec: float = 5.0           # firm policy
    reject_sign_count_regression: bool = True       # firm policy; §7.2 leaves this to the RP
    require_device_bound_credential: bool = False   # firm policy: reject BE=1 syncable passkeys
    allowed_aaguids: Tuple[str, ...] = ()           # empty tuple: any authenticator model

    def __post_init__(self) -> None:
        self.rp_id = _require_identifier(self.rp_id, "rp_id")
        if isinstance(self.allowed_origins, str):
            raise PhishingResistantAuthError(
                "allowed_origins must be a sequence of origins, not a single string; "
                "a bare string would be iterated character by character."
            )
        origins = tuple(
            _require_https_origin(o, "allowed_origins entry") for o in self.allowed_origins
        )
        if not origins:
            raise PhishingResistantAuthError(
                "allowed_origins must not be empty; an empty allowlist rejects every "
                "assertion, which reads in the audit trail as a phishing attack rather "
                "than as a misconfiguration."
            )
        self.allowed_origins = origins

        max_age = _require_finite(self.max_challenge_age_sec, "max_challenge_age_sec")
        if max_age <= 0.0:
            raise PhishingResistantAuthError("max_challenge_age_sec must be positive.")
        self.max_challenge_age_sec = max_age

        skew = _require_finite(self.clock_skew_tolerance_sec, "clock_skew_tolerance_sec")
        if skew < 0.0:
            raise PhishingResistantAuthError("clock_skew_tolerance_sec must be non-negative.")
        self.clock_skew_tolerance_sec = skew

        self.allowed_aaguids = tuple(str(a).strip().lower() for a in self.allowed_aaguids)


@dataclass
class PhishingResistantAuthenticationForCustodyAccessConfig:
    """Engine-level switch, retained from version 1.x."""

    enabled: bool = True


@dataclass
class AuthVerificationReport:
    """Audit record of one authentication decision.

    ``is_user_verified`` reports what the engine *established*, never what the
    assertion claimed. On any rejection path it is ``False``, because an
    assertion rejected before its flags were trusted has verified nothing.
    """

    user_id: str
    credential_id: str
    rp_id: str
    client_origin: str
    is_origin_valid: bool
    is_user_verified: bool
    is_authenticated: bool
    status: str
    audit_notes: str
    sign_count: int = 0
    previous_sign_count: int = 0
    warnings: Tuple[str, ...] = ()


class PhishingResistantAuthenticationForCustodyAccessEngine:
    """FIDO2/WebAuthn assertion policy gate for custody and signing-key access.

    State held here -- registered credentials, issued challenges, accepted
    signature counters -- is in-process only, guarded by an internal lock that
    serialises concurrent callers within one process. A multi-process or
    multi-host deployment MUST back this state with a shared store: two workers
    each holding their own challenge table will each accept the same replayed
    assertion once, which is precisely the attack the single-use rule exists to
    stop.
    """

    STATUSES = (
        "AUTH_SUCCESSFUL",
        "ENGINE_DISABLED",
        "CLIENT_DATA_TYPE_INVALID",
        "CHALLENGE_UNKNOWN",
        "CHALLENGE_REPLAYED",
        "CHALLENGE_USER_MISMATCH",
        "CHALLENGE_EXPIRED",
        "ORIGIN_MISMATCH_PHISHING_ATTEMPT",
        "RP_ID_HASH_MISMATCH",
        "CREDENTIAL_UNKNOWN",
        "CREDENTIAL_REVOKED",
        "CREDENTIAL_USER_MISMATCH",
        "AUTHENTICATOR_NOT_ALLOWED",
        "BACKUP_STATE_INVALID",
        "DEVICE_BOUND_CREDENTIAL_REQUIRED",
        "USER_PRESENCE_MISSING",
        "USER_VERIFICATION_FAILED",
        "SIGNATURE_NOT_VERIFIED",
        "SIGN_COUNT_REGRESSION_CLONE_SUSPECTED",
    )

    def __init__(
        self,
        config: Optional[PhishingResistantAuthenticationForCustodyAccessConfig] = None,
        policy: Optional[AuthPolicyConfig] = None,
    ):
        self.config = config or PhishingResistantAuthenticationForCustodyAccessConfig()
        self.policy = policy or AuthPolicyConfig()
        self._lock = threading.RLock()
        self._credentials: Dict[str, RegisteredCredential] = {}
        self._challenges: Dict[str, IssuedChallenge] = {}
        self._expected_rp_id_hash = compute_rp_id_hash(self.policy.rp_id)

    # ------------------------------------------------------------- credentials

    def register_credential(
        self,
        credential_id: str,
        user_id: str,
        sign_count: int = 0,
        aaguid: str = "",
        backup_eligible: bool = False,
    ) -> RegisteredCredential:
        """Binds a credential to exactly one user account.

        The binding is what makes WebAuthn L3 §7.2's "identify the user being
        authenticated" step enforceable: without it, any valid assertion from
        any registered key would authenticate any claimed ``user_id``.
        """
        credential_id = _require_identifier(credential_id, "credential_id")
        user_id = _require_identifier(user_id, "user_id")
        count = _require_count(sign_count, "sign_count")
        record = RegisteredCredential(
            credential_id=credential_id,
            user_id=user_id,
            sign_count=count,
            aaguid=str(aaguid).strip().lower(),
            backup_eligible=bool(backup_eligible),
        )
        with self._lock:
            existing = self._credentials.get(credential_id)
            if existing is not None and existing.user_id != user_id:
                raise PhishingResistantAuthError(
                    f"credential_id '{credential_id}' is already registered to a different "
                    "user; re-binding a credential would transfer custody access silently."
                )
            self._credentials[credential_id] = record
        return record

    def revoke_credential(self, credential_id: str) -> bool:
        """Marks a credential unusable, e.g. on offboarding or key loss."""
        credential_id = _require_identifier(credential_id, "credential_id")
        with self._lock:
            record = self._credentials.get(credential_id)
            if record is None:
                return False
            self._credentials[credential_id] = RegisteredCredential(
                credential_id=record.credential_id,
                user_id=record.user_id,
                sign_count=record.sign_count,
                aaguid=record.aaguid,
                backup_eligible=record.backup_eligible,
                is_revoked=True,
            )
        return True

    def get_credential(self, credential_id: str) -> Optional[RegisteredCredential]:
        with self._lock:
            return self._credentials.get(str(credential_id).strip())

    # -------------------------------------------------------------- challenges

    def issue_challenge(self, user_id: str, now: Optional[float] = None) -> IssuedChallenge:
        """Generates and stores a single-use challenge for one user.

        WebAuthn L3 §13.4.3 requires the challenge to be randomly generated
        server-side and requires the returned value to match what was generated;
        it also says the RP SHOULD store the challenge until the ceremony
        completes. ``secrets.token_urlsafe`` provides base64url text over at
        least the 16 bytes of entropy the specification asks for.
        """
        user_id = _require_identifier(user_id, "user_id")
        issued_at = self._resolve_now(now)
        challenge = IssuedChallenge(
            value=secrets.token_urlsafe(MIN_CHALLENGE_ENTROPY_BYTES * 2),
            user_id=user_id,
            issued_at=issued_at,
        )
        with self._lock:
            self._purge_expired_locked(issued_at)
            self._challenges[challenge.value] = challenge
        return challenge

    def purge_expired_challenges(self, now: Optional[float] = None) -> int:
        """Drops challenges past their maximum age. Returns the number removed."""
        current = self._resolve_now(now)
        with self._lock:
            return self._purge_expired_locked(current)

    def _purge_expired_locked(self, now: float) -> int:
        cutoff = self.policy.max_challenge_age_sec
        stale: List[str] = [
            value
            for value, issued in self._challenges.items()
            if (now - issued.issued_at) > cutoff
        ]
        for value in stale:
            del self._challenges[value]
        return len(stale)

    @staticmethod
    def _resolve_now(now: Optional[float]) -> float:
        """Uses the caller's clock when supplied, so decisions stay reproducible."""
        if now is None:
            return time.time()
        return _require_finite(now, "now")

    # ------------------------------------------------------------ verification

    def execute(self) -> bool:
        """Legacy execution method retained for backward compatibility."""
        return bool(self.config.enabled)

    def verify_assertion(
        self, assertion: WebAuthnAssertion, now: Optional[float] = None
    ) -> AuthVerificationReport:
        """Applies WebAuthn L3 §7.2 server-side steps plus firm policy.

        Ordering is deliberate. The challenge is looked up and consumed before
        any other check, so a single issued challenge can never be probed twice
        against different origins or flag combinations. Origin and RP ID
        binding -- the checks an AiTM proxy cannot satisfy -- are evaluated
        before the flag and counter checks, so the audit trail attributes a
        phished assertion to phishing rather than to a missing PIN.
        """
        if not isinstance(assertion, WebAuthnAssertion):
            raise PhishingResistantAuthError(
                f"assertion must be a WebAuthnAssertion, got {type(assertion).__name__}."
            )
        user_id = _require_identifier(assertion.user_id, "assertion.user_id")
        credential_id = _require_identifier(assertion.credential_id, "assertion.credential_id")
        client_origin = _require_identifier(assertion.client_origin, "assertion.client_origin")
        challenge = _require_identifier(assertion.challenge, "assertion.challenge")
        current = self._resolve_now(now)

        def reject(
            status: str,
            notes: str,
            origin_valid: bool = False,
            warnings: Tuple[str, ...] = (),
        ) -> AuthVerificationReport:
            """Builds a rejection report.

            ``origin_valid`` defaults to ``False`` and is passed as ``True``
            only by call sites downstream of the origin and rpIdHash checks.
            A rejection raised before those checks ran has established nothing
            about the origin, and must not record that it did.
            """
            logger.warning("WEBAUTHN REJECTION [%s]: %s", status, notes)
            return AuthVerificationReport(
                user_id=user_id,
                credential_id=credential_id,
                rp_id=self.policy.rp_id,
                client_origin=client_origin,
                is_origin_valid=origin_valid,
                is_user_verified=False,
                is_authenticated=False,
                status=status,
                audit_notes=notes,
                warnings=warnings,
            )

        if not self.config.enabled:
            return reject("ENGINE_DISABLED", "Engine is disabled; no assertion was evaluated.")

        # §7.2: verify that the value of C.type is the string "webauthn.get".
        if str(assertion.client_data_type) != CLIENT_DATA_TYPE_GET:
            return reject(
                "CLIENT_DATA_TYPE_INVALID",
                f"clientData.type is '{assertion.client_data_type}', expected "
                f"'{CLIENT_DATA_TYPE_GET}'. A registration response must not be "
                "replayed into the authentication path.",
            )

        # §7.2 / §13.4.3: the challenge must be one this server issued, unused,
        # bound to this user, and still fresh. Consumed on first use whatever the
        # outcome, so a captured challenge cannot be probed repeatedly.
        with self._lock:
            issued = self._challenges.pop(challenge, None)
        if issued is None:
            return reject(
                "CHALLENGE_UNKNOWN",
                f"Challenge presented by user '{user_id}' was never issued by this server, "
                "or has already been consumed by an earlier assertion (replay).",
            )
        if not _constant_time_equal(issued.user_id, user_id):
            return reject(
                "CHALLENGE_USER_MISMATCH",
                f"Challenge was issued to user '{issued.user_id}' but presented by "
                f"'{user_id}'; a challenge is not transferable between accounts.",
            )
        age = current - issued.issued_at
        if age > self.policy.max_challenge_age_sec:
            return reject(
                "CHALLENGE_EXPIRED",
                f"Challenge expired for user '{user_id}' "
                f"(age {age:.1f}s > {self.policy.max_challenge_age_sec:.1f}s).",
            )
        if age < -self.policy.clock_skew_tolerance_sec:
            return reject(
                "CHALLENGE_EXPIRED",
                f"Challenge for user '{user_id}' is dated {abs(age):.1f}s in the future, "
                f"beyond the {self.policy.clock_skew_tolerance_sec:.1f}s skew tolerance; "
                "the verifying clock and the issuing clock disagree.",
            )

        # §7.2: verify that C.origin is an origin expected by the Relying Party
        # (§13.4.9). Compared by exact match against the server's own allowlist,
        # never against an origin derived from the response.
        if client_origin not in self.policy.allowed_origins:
            return reject(
                "ORIGIN_MISMATCH_PHISHING_ATTEMPT",
                f"SECURITY REJECTION [PHISHING ATTEMPT DETECTED]: user '{user_id}' presented "
                f"client origin '{client_origin}', which is not in the expected origin "
                f"allowlist {list(self.policy.allowed_origins)} for RP ID "
                f"'{self.policy.rp_id}'.",
            )

        # §7.2: verify that rpIdHash in authData is SHA-256 of the RP ID the
        # server expects.
        if assertion.rp_id_hash is None:
            if self.policy.require_rp_id_hash:
                return reject(
                    "RP_ID_HASH_MISMATCH",
                    f"No rpIdHash supplied for user '{user_id}' while policy requires it; "
                    "pass authenticatorData[0:32] from your WebAuthn library.",
                    origin_valid=True,
                )
        elif not hmac.compare_digest(
            _require_digest(assertion.rp_id_hash, "assertion.rp_id_hash"),
            self._expected_rp_id_hash,
        ):
            return reject(
                "RP_ID_HASH_MISMATCH",
                f"SECURITY REJECTION: rpIdHash in authenticator data does not match "
                f"SHA-256 of the expected RP ID '{self.policy.rp_id}' for user '{user_id}'.",
                origin_valid=True,
            )

        # §7.2: identify the user, and verify the credential belongs to them.
        with self._lock:
            record = self._credentials.get(credential_id)
        if record is None:
            return reject(
                "CREDENTIAL_UNKNOWN",
                f"Credential '{credential_id}' is not registered; no public key exists "
                f"against which user '{user_id}' could have been authenticated.",
                origin_valid=True,
            )
        if record.is_revoked:
            return reject(
                "CREDENTIAL_REVOKED",
                f"Credential '{credential_id}' is revoked and must not unlock custody access.",
                origin_valid=True,
            )
        if not _constant_time_equal(record.user_id, user_id):
            return reject(
                "CREDENTIAL_USER_MISMATCH",
                f"Credential '{credential_id}' is registered to '{record.user_id}' but was "
                f"presented for '{user_id}'; a valid assertion authenticates its own owner "
                "only.",
                origin_valid=True,
            )

        if self.policy.allowed_aaguids:
            aaguid = str(assertion.aaguid).strip().lower()
            if aaguid not in self.policy.allowed_aaguids:
                return reject(
                    "AUTHENTICATOR_NOT_ALLOWED",
                    f"Authenticator AAGUID '{aaguid or '(absent)'}' is not on the approved "
                    f"model allowlist for custody access (user '{user_id}').",
                    origin_valid=True,
                )

        # §7.2: if the BE bit is not set, verify that the BS bit is not set
        # (§6.1.3 marks BE=0, BS=1 as a combination that is not allowed).
        if not assertion.backup_eligible and assertion.backup_state:
            return reject(
                "BACKUP_STATE_INVALID",
                f"Authenticator reported BE=0 with BS=1 for user '{user_id}', a combination "
                "WebAuthn L3 section 6.1.3 does not allow; the authenticator data is unsound.",
                origin_valid=True,
            )
        # §6.1.3: BE "MUST NOT change" for a credential, so a contradiction here
        # means this is not the credential that was registered.
        if assertion.backup_eligible != record.backup_eligible:
            return reject(
                "BACKUP_STATE_INVALID",
                f"Backup eligibility for credential '{credential_id}' changed from "
                f"{record.backup_eligible} at registration to {assertion.backup_eligible}; "
                "BE is immutable, so the credential has been substituted.",
                origin_valid=True,
            )
        if self.policy.require_device_bound_credential and assertion.backup_eligible:
            return reject(
                "DEVICE_BOUND_CREDENTIAL_REQUIRED",
                f"Credential '{credential_id}' is a multi-device (syncable) credential, but "
                "policy requires a device-bound authenticator for custody access.",
                origin_valid=True,
            )

        # §7.2: verify the UP bit is set. Presence and verification are separate
        # steps and separate failures.
        if not assertion.user_present:
            return reject(
                "USER_PRESENCE_MISSING",
                f"User presence flag (UP) not set for user '{user_id}'; the assertion was "
                "produced without a physical interaction with the authenticator.",
                origin_valid=True,
            )
        if self.policy.require_user_verification and not assertion.user_verified:
            return reject(
                "USER_VERIFICATION_FAILED",
                f"User verification (UV) required by policy but not set for user "
                f"'{user_id}'; an unattended key alone must not unlock custody access.",
                origin_valid=True,
            )

        # The library's signature check. This engine performs no cryptography on
        # the assertion, so it will not call an unverified assertion successful.
        if self.policy.require_signature_verification and not assertion.signature_verified:
            return reject(
                "SIGNATURE_NOT_VERIFIED",
                f"Assertion for user '{user_id}' was not marked signature-verified. This "
                "engine does not verify signatures; pass the result of your WebAuthn "
                "library's verification step.",
                origin_valid=True,
            )

        # §7.2 signature counter step, then the deferred state update. Both run
        # under one lock and against one freshly read record: comparing against a
        # counter read earlier would let two concurrent assertions each pass the
        # check against the same stale value.
        warnings: Tuple[str, ...] = ()
        new_count = _require_count(assertion.sign_count, "assertion.sign_count")
        with self._lock:
            stored = self._credentials.get(credential_id)
            if stored is None or stored.is_revoked:
                return reject(
                    "CREDENTIAL_REVOKED",
                    f"Credential '{credential_id}' was revoked while this assertion was "
                    "being evaluated.",
                    origin_valid=True,
                )
            previous = stored.sign_count
            # Only meaningful when either value is nonzero: authenticators that do
            # not implement a counter always report 0.
            if (new_count != 0 or previous != 0) and new_count <= previous:
                notes = (
                    f"Signature counter for credential '{credential_id}' did not advance "
                    f"({new_count} <= {previous}); WebAuthn L3 section 7.2 treats this as a signal, "
                    "though not proof, that the authenticator may be cloned."
                )
                if self.policy.reject_sign_count_regression:
                    return reject(
                        "SIGN_COUNT_REGRESSION_CLONE_SUSPECTED", notes, origin_valid=True
                    )
                warnings = (notes,)
                logger.warning(notes)

            # §7.2: state updates are deferred until every check above has passed.
            self._credentials[credential_id] = RegisteredCredential(
                credential_id=stored.credential_id,
                user_id=stored.user_id,
                sign_count=max(previous, new_count),
                aaguid=stored.aaguid,
                backup_eligible=stored.backup_eligible,
                is_revoked=False,
            )

        if not self.policy.require_signature_verification and not assertion.signature_verified:
            # The report must not imply a cryptographic check that never ran.
            warnings += (
                f"Credential '{credential_id}' authenticated without signature "
                "verification because require_signature_verification is disabled; this "
                "decision rests on policy alone.",
            )

        notes = (
            f"WEBAUTHN AUTHENTICATION SUCCESSFUL: user '{user_id}' verified via credential "
            f"'{credential_id}' at origin '{client_origin}' (RP ID '{self.policy.rp_id}', "
            f"UV={assertion.user_verified}, signCount {previous} -> {new_count})."
        )
        logger.info(notes)
        return AuthVerificationReport(
            user_id=user_id,
            credential_id=credential_id,
            rp_id=self.policy.rp_id,
            client_origin=client_origin,
            is_origin_valid=True,
            is_user_verified=bool(assertion.user_verified),
            is_authenticated=True,
            status="AUTH_SUCCESSFUL",
            audit_notes=notes,
            sign_count=new_count,
            previous_sign_count=previous,
            warnings=warnings,
        )
