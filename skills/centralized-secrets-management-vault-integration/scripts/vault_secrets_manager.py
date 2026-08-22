"""Vault KV v2 secret retrieval for long-running trading processes.

Scope of this module
--------------------
It covers the *client* half of the AppRole workflow: login, token lifetime
management, KV v2 reads, a bounded in-memory cache, and a client-side path
guard. It deliberately does not configure Vault: policies, AppRole
provisioning, and SecretID delivery live on the Vault/orchestrator side and are
described in ``references/workflows.md``.

Two facts drive most of the design:

1. **Vault is the access-control boundary, this client is not.** The
   ``environment`` guard below is defence in depth against a misconstructed
   *path string*; it cannot stop a token whose policy is too broad. Vault
   returns HTTP 404 both when a path does not exist and when the token may not
   view it (https://developer.hashicorp.com/vault/api-docs), which is exactly
   the behaviour you want from the server and exactly why the client must not
   pretend to know which case it hit.
2. **AppRole tokens expire and cannot be renewed past their max TTL**
   (https://developer.hashicorp.com/vault/docs/concepts/tokens). A process that
   logs in at boot and holds the token forever will start taking 403s at an
   arbitrary later moment — often the moment it needs to re-read a credential
   after a rotation. Token lifetime is therefore tracked explicitly, and
   re-authentication is bounded, never a retry loop.

No third-party dependency is required; ``HttpVaultTransport`` is built on
``urllib``. Swap in ``hvac`` behind the same :class:`VaultTransport` protocol if
your firm already standardises on it.
"""

from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Protocol

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_SECONDS = 300.0
DEFAULT_RENEW_MARGIN_SECONDS = 60.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_KV_MOUNT = "secret"

# Conservative allowlist for one Vault path segment. Vault itself accepts more,
# but a trading bot has no reason to build paths out of anything else, and the
# narrow set removes any question about percent-encoding or traversal.
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._+@:-]+$")


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class VaultError(RuntimeError):
    """Base class for every failure raised by this module."""


class VaultAuthenticationError(VaultError):
    """AppRole login or token renewal failed. May be transient."""


class VaultCredentialExhausted(VaultAuthenticationError):
    """The SecretID is spent or expired: re-login can never succeed again.

    Vault expires a SecretID after ``secret_id_ttl`` and after
    ``secret_id_num_uses`` logins
    (https://developer.hashicorp.com/vault/api-docs/auth/approle). Retrying is
    pointless; the orchestrator must deliver a fresh SecretID.
    """


class VaultPathViolation(VaultError, PermissionError):
    """The requested path is malformed or outside this process's environment.

    Raised by the client before any network call.
    """


class VaultPermissionDenied(VaultError, PermissionError):
    """Vault answered 403 for a request made with a token it had just issued."""


class VaultSecretNotFound(VaultError, KeyError):
    """Vault answered 404, or returned a soft-deleted KV v2 version.

    A 404 means *either* "no such path" *or* "your policy does not permit you to
    see this path" — Vault does not distinguish the two, so neither does this
    exception.
    """

    def __str__(self) -> str:  # KeyError would otherwise repr() the message
        return self.args[0] if self.args else ""


class VaultTransportError(VaultError):
    """Vault was unreachable, sealed, overloaded, or returned an unusable body."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VaultResponse:
    """A decoded Vault HTTP response. ``body`` is ``{}`` for 204s."""

    status: int
    body: Mapping[str, Any]

    def errors(self) -> List[str]:
        raw = self.body.get("errors") if isinstance(self.body, Mapping) else None
        return [str(e) for e in raw] if isinstance(raw, list) else []


class VaultTransport(Protocol):
    """Minimal Vault HTTP surface, so the manager can be tested without a server."""

    def request(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> VaultResponse:
        """Issue one request against ``/v1/{path}``; never raise on HTTP status."""


class HttpVaultTransport:
    """``urllib``-backed transport against a real Vault server."""

    def __init__(
        self,
        vault_addr: str,
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        namespace: Optional[str] = None,
        ca_cert_path: Optional[str] = None,
        allow_insecure_http: bool = False,
    ) -> None:
        parsed = urllib.parse.urlparse(vault_addr)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"vault_addr must be an http(s) URL, got {vault_addr!r}")
        if parsed.scheme == "http" and not allow_insecure_http:
            raise ValueError(
                "Refusing plaintext http:// to Vault: the client token and every "
                "secret would cross the network in clear text. Pass "
                "allow_insecure_http=True only for a loopback dev server."
            )
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self._base = vault_addr.rstrip("/")
        self._timeout = float(timeout)
        self._namespace = namespace
        self._ssl_context = (
            ssl.create_default_context(cafile=ca_cert_path)
            if parsed.scheme == "https"
            else None
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> VaultResponse:
        url = f"{self._base}/v1/{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method.upper())
        request.add_header("X-Vault-Request", "true")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-Vault-Token", token)
        if self._namespace:
            request.add_header("X-Vault-Namespace", self._namespace)

        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout, context=self._ssl_context
            ) as response:
                status = int(response.status)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # Vault encodes 4xx/5xx detail as {"errors": [...]} — no secret material.
            status = int(exc.code)
            raw = exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VaultTransportError(
                f"Vault request to {method} {path} failed: {exc}"
            ) from exc

        if not raw:
            return VaultResponse(status=status, body={})
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VaultTransportError(
                f"Vault returned a non-JSON body for {method} {path} (status {status})"
            ) from exc
        return VaultResponse(status=status, body=body if isinstance(body, Mapping) else {})


# --------------------------------------------------------------------------- #
# Secret container
# --------------------------------------------------------------------------- #
class SecretBundle(Mapping[str, Any]):
    """Read-only view of one KV v2 secret whose ``repr`` never shows values.

    ``bundle["api_key"]`` works exactly like the dict Vault returned, but
    ``print(bundle)``, ``logger.info("%s", bundle)``, and a traceback that
    happens to include it print key *names* only. This is a guardrail against
    accidental disclosure, not a security boundary: ``dict(bundle)`` and
    :meth:`as_dict` still expose the values, which is the point — you have to
    ask for them explicitly.
    """

    __slots__ = ("_path", "_data", "_version")

    def __init__(self, path: str, data: Mapping[str, Any], version: Optional[int]) -> None:
        self._path = path
        self._data: Dict[str, Any] = dict(data)
        self._version = version

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    @property
    def path(self) -> str:
        return self._path

    @property
    def version(self) -> Optional[int]:
        """KV v2 ``metadata.version``; it changes when the secret is rotated."""
        return self._version

    def as_dict(self) -> Dict[str, Any]:
        """Explicit, copied plaintext view for handing to an exchange client."""
        return dict(self._data)

    def __repr__(self) -> str:
        return (
            f"SecretBundle(path={self._path!r}, version={self._version!r}, "
            f"keys={sorted(self._data)!r})"
        )

    __str__ = __repr__


@dataclass
class _CacheEntry:
    bundle: SecretBundle
    fetched_at: float
    ttl: float

    def is_fresh(self, now: float) -> bool:
        return (now - self.fetched_at) < self.ttl


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
class VaultSecretsManager:
    """AppRole-authenticated, TTL-cached reader for one environment's secrets.

    Args:
        vault_addr: Base Vault URL, recorded for logging and used to build the
            default :class:`HttpVaultTransport`.
        environment: The single leading path segment this process may read
            (``"prod"``, ``"staging"``, ``"dev"``). Enforced client-side *and*,
            authoritatively, by the AppRole's Vault policy.
        transport: Injected transport. Defaults to a real HTTPS transport.
        mount: KV v2 mount point; reads go to ``{mount}/data/{path}``.
        cache_ttl: Seconds a fetched secret is served from memory. This bounds
            how long the process can keep using a credential that has since
            been rotated — see ``secrets-rotation-without-bot-downtime``.
        renew_margin: Re-authenticate once the token has fewer than this many
            seconds left, so a read never races the expiry.
        stale_if_error: On a transport failure, keep serving the last known
            value for a path instead of raising. A Vault outage should not take
            a live trading process down; a rotation during that outage will
            surface as broker authentication errors, not as a Vault error.
            This deliberately does **not** extend to authentication failures: a
            spent SecretID is a persistent, operator-actionable state, and
            hiding it behind a stale cache would keep it hidden indefinitely.
        clock: Monotonic time source, injectable for tests.

    Threading:
        Every public method serialises on one re-entrant lock, so concurrent
        callers share a single login and a single read per path rather than
        stampeding Vault. The cost is that a read which reaches Vault blocks
        other callers for up to the transport timeout — fetch credentials at
        boot and on rotation, never on the order path.
    """

    def __init__(
        self,
        vault_addr: str,
        environment: str,
        *,
        transport: Optional[VaultTransport] = None,
        mount: str = DEFAULT_KV_MOUNT,
        cache_ttl: float = DEFAULT_CACHE_TTL_SECONDS,
        renew_margin: float = DEFAULT_RENEW_MARGIN_SECONDS,
        stale_if_error: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not environment or not _SEGMENT_RE.match(environment):
            raise ValueError(
                f"environment must be a single path segment, got {environment!r}"
            )
        if not mount or not _SEGMENT_RE.match(mount):
            raise ValueError(f"mount must be a single path segment, got {mount!r}")
        if cache_ttl <= 0:
            raise ValueError("cache_ttl must be positive")
        if renew_margin < 0:
            raise ValueError("renew_margin must not be negative")

        self.vault_addr = vault_addr
        self.environment = environment
        self.mount = mount
        self.cache_ttl = float(cache_ttl)
        self.renew_margin = float(renew_margin)
        self.stale_if_error = bool(stale_if_error)

        self._clock = clock
        self._transport: VaultTransport = transport or HttpVaultTransport(vault_addr)
        self._lock = threading.RLock()

        self._client_token: Optional[str] = None
        self._token_accessor: Optional[str] = None
        self._token_expires_at: Optional[float] = None
        self._token_renewable = False
        self._role_id: Optional[str] = None
        self._secret_id: Optional[str] = None
        self._credentials_spent = False
        self._cache: Dict[str, _CacheEntry] = {}

    # -- authentication ---------------------------------------------------- #
    @property
    def is_authenticated(self) -> bool:
        """True while a token is held and has not passed its lease expiry."""
        with self._lock:
            if not self._client_token:
                return False
            return self._token_expires_at is None or self._clock() < self._token_expires_at

    @property
    def token_accessor(self) -> Optional[str]:
        """Token accessor — safe to log; the token itself is never exposed."""
        return self._token_accessor

    def token_ttl_remaining(self) -> Optional[float]:
        """Seconds until the current token expires; ``None`` if it never does."""
        with self._lock:
            if self._token_expires_at is None:
                return None
            return max(0.0, self._token_expires_at - self._clock())

    def login_approle(self, role_id: str, secret_id: str) -> None:
        """Authenticate via AppRole and retain the credentials for re-login.

        The SecretID is kept in memory so the process can re-authenticate when
        its token reaches max TTL without operator involvement. If the AppRole
        sets ``secret_id_num_uses=1`` (HashiCorp's recommended hardening) that
        re-login will fail with :class:`VaultCredentialExhausted`, which is the
        signal for the orchestrator to deliver a freshly wrapped SecretID.

        Raises:
            ValueError: Either credential is empty.
            VaultCredentialExhausted: Vault rejected the SecretID as invalid or
                expired — do not retry.
            VaultAuthenticationError: Login failed for another reason.
            VaultTransportError: Vault was unreachable.
        """
        if not role_id or not secret_id:
            raise ValueError("Must provide non-empty role_id and secret_id for AppRole login.")
        with self._lock:
            self._role_id = role_id
            self._secret_id = secret_id
            self._credentials_spent = False
            self._login_locked()

    def _login_locked(self) -> None:
        response = self._transport.request(
            "POST",
            "auth/approle/login",
            payload={"role_id": self._role_id, "secret_id": self._secret_id},
        )
        if response.status in (400, 403):
            # Vault answers 400 "invalid secret id" for a spent or expired
            # SecretID; neither that nor a policy-level 403 improves on retry.
            self._credentials_spent = True
            self._clear_token_locked()
            raise VaultCredentialExhausted(
                f"AppRole login rejected (status {response.status}): "
                f"{'; '.join(response.errors()) or 'invalid role_id/secret_id'}. "
                "The SecretID is spent, expired, or the login came from outside "
                "secret_id_bound_cidrs; a new SecretID must be provisioned."
            )
        if response.status == 429 or response.status >= 500:
            raise VaultTransportError(
                f"Vault could not serve the AppRole login (status {response.status})."
            )
        if response.status != 200:
            raise VaultAuthenticationError(
                f"Unexpected AppRole login status {response.status}: "
                f"{'; '.join(response.errors()) or 'no detail'}"
            )

        auth = response.body.get("auth")
        if not isinstance(auth, Mapping) or not auth.get("client_token"):
            raise VaultAuthenticationError(
                "AppRole login succeeded but returned no client_token."
            )

        self._client_token = str(auth["client_token"])
        self._token_accessor = str(auth.get("accessor")) if auth.get("accessor") else None
        self._token_renewable = bool(auth.get("renewable", False))
        lease = auth.get("lease_duration")
        self._token_expires_at = (
            self._clock() + float(lease)
            if isinstance(lease, (int, float)) and lease > 0
            else None
        )
        logger.info(
            "Authenticated to Vault at %s via AppRole "
            "(accessor=%s, lease=%ss, renewable=%s, policies=%s)",
            self.vault_addr,
            self._token_accessor,
            lease,
            self._token_renewable,
            auth.get("token_policies"),
        )

    def _clear_token_locked(self) -> None:
        self._client_token = None
        self._token_accessor = None
        self._token_expires_at = None
        self._token_renewable = False

    def _ensure_token_locked(self) -> None:
        """Guarantee a usable token, renewing or re-logging in at most once each."""
        if not self._client_token:
            if self._credentials_spent or not self._role_id:
                raise VaultAuthenticationError(
                    "Not authenticated: call login_approle() with a valid SecretID first."
                )
            self._login_locked()
            return

        remaining = self.token_ttl_remaining()
        if remaining is None or remaining > self.renew_margin:
            return

        if self._token_renewable:
            self._renew_locked()
            remaining = self.token_ttl_remaining()
            if remaining is None or remaining > self.renew_margin:
                return
            # Renewal no longer buys headroom: the token has reached its max
            # TTL, which renewal cannot extend. Re-login is the only option.
            logger.info("Vault token is at its max TTL; re-authenticating via AppRole.")

        self._clear_token_locked()
        if self._credentials_spent or not self._role_id:
            raise VaultAuthenticationError(
                "Vault token expired and no reusable AppRole SecretID is held; "
                "the orchestrator must deliver a new SecretID."
            )
        self._login_locked()

    def _renew_locked(self) -> None:
        try:
            response = self._transport.request(
                "POST", "auth/token/renew-self", token=self._client_token, payload={}
            )
        except VaultTransportError:
            logger.warning("Vault token renewal could not reach Vault; will re-authenticate.")
            self._token_renewable = False
            return
        if response.status != 200:
            logger.info(
                "Vault token renewal refused (status %s); will re-authenticate.",
                response.status,
            )
            self._token_renewable = False
            return
        auth = response.body.get("auth")
        if not isinstance(auth, Mapping):
            self._token_renewable = False
            return
        lease = auth.get("lease_duration")
        if isinstance(lease, (int, float)) and lease > 0:
            self._token_expires_at = self._clock() + float(lease)
        self._token_renewable = bool(auth.get("renewable", False))

    # -- path guard -------------------------------------------------------- #
    def _validate_path(self, secret_path: Any) -> str:
        """Normalise and environment-check a KV path before any network call.

        Rejects traversal (``prod/../dev/keys``), empty and duplicated
        segments, absolute paths, and anything outside the segment allowlist.
        A prefix test alone is not sufficient: ``"prod/../dev/keys"`` starts
        with ``"prod/"``.
        """
        if not isinstance(secret_path, str) or not secret_path.strip():
            raise VaultPathViolation("secret_path must be a non-empty string.")
        segments = secret_path.strip().strip("/").split("/")
        for segment in segments:
            if segment in ("", ".", "..") or not _SEGMENT_RE.match(segment):
                logger.critical(
                    "Rejected malformed Vault path %r from a bot running in %r.",
                    secret_path,
                    self.environment,
                )
                raise VaultPathViolation(
                    f"Malformed Vault path segment {segment!r} in {secret_path!r}."
                )
        if segments[0] != self.environment:
            logger.critical(
                "Security violation: bot running in %r requested %r.",
                self.environment,
                secret_path,
            )
            raise VaultPathViolation(
                f"Environment mismatch: this process may only read "
                f"'{self.environment}/...', not {secret_path!r}."
            )
        return "/".join(segments)

    # -- reads ------------------------------------------------------------- #
    def get_secret(self, secret_path: str, *, refresh: bool = False) -> SecretBundle:
        """Return the secret at ``secret_path``, from cache while it is fresh.

        Args:
            secret_path: Path *below* the KV mount, starting with this
                process's environment, e.g. ``"prod/binance/market-maker"``.
            refresh: Bypass the cache and read from Vault. Use this on a
                rotation signal, not on every order.

        Raises:
            VaultPathViolation: Path malformed or outside this environment.
            VaultAuthenticationError: No usable token and none obtainable.
            VaultPermissionDenied: Vault answered 403 even with a fresh token,
                i.e. the policy — not the token — forbids this path.
            VaultSecretNotFound: Path absent, invisible to this policy, or the
                latest KV v2 version is soft-deleted.
            VaultTransportError: Vault unreachable and no cached value exists.
        """
        path = self._validate_path(secret_path)
        with self._lock:
            entry = self._cache.get(path)
            if entry is not None and not refresh and entry.is_fresh(self._clock()):
                logger.debug("Serving %s from in-memory cache.", path)
                return entry.bundle

            self._ensure_token_locked()
            try:
                bundle = self._read_locked(path, allow_reauth=True)
            except VaultTransportError:
                if entry is not None and self.stale_if_error:
                    logger.warning(
                        "Vault unreachable for %s; continuing on the cached value "
                        "(age %.0fs). A rotation during this outage will show up as "
                        "broker authentication failures, not as a Vault error.",
                        path,
                        self._clock() - entry.fetched_at,
                    )
                    return entry.bundle
                raise

            self._cache[path] = _CacheEntry(
                bundle=bundle, fetched_at=self._clock(), ttl=self.cache_ttl
            )
            return bundle

    def _read_locked(self, path: str, *, allow_reauth: bool) -> SecretBundle:
        encoded = "/".join(urllib.parse.quote(seg, safe="") for seg in path.split("/"))
        response = self._transport.request(
            "GET", f"{self.mount}/data/{encoded}", token=self._client_token
        )

        if response.status == 403:
            if allow_reauth and self._role_id and not self._credentials_spent:
                # 403 covers both "token expired/revoked" and "policy says no".
                # Exactly one re-login distinguishes them; never loop.
                logger.info("Vault returned 403 for %s; re-authenticating once.", path)
                self._clear_token_locked()
                self._login_locked()
                return self._read_locked(path, allow_reauth=False)
            raise VaultPermissionDenied(
                f"Vault denied access to '{self.mount}/data/{path}'. The AppRole policy "
                "does not grant read on this path."
            )
        if response.status == 404:
            raise VaultSecretNotFound(self._describe_missing(path, response))
        if response.status == 429:
            raise VaultTransportError(
                "Vault rate-limit quota exceeded (429). Back off; do not retry in a tight loop."
            )
        if response.status == 503:
            raise VaultTransportError("Vault is sealed or unavailable (503).")
        if response.status >= 500:
            raise VaultTransportError(f"Vault server error (status {response.status}).")
        if response.status != 200:
            raise VaultError(f"Unexpected Vault status {response.status} reading {path}.")

        outer = response.body.get("data")
        if not isinstance(outer, Mapping):
            raise VaultTransportError(
                f"Malformed KV v2 response for {path}: no 'data' object."
            )
        metadata = outer.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        payload = outer.get("data")
        if payload is None:
            raise VaultSecretNotFound(self._describe_missing(path, response))
        if not isinstance(payload, Mapping):
            raise VaultTransportError(
                f"Malformed KV v2 response for {path}: 'data.data' is not an object."
            )

        version = metadata.get("version")
        bundle = SecretBundle(
            path=path,
            data=payload,
            version=int(version) if isinstance(version, int) else None,
        )
        logger.info(
            "Read %s from Vault (version=%s, keys=%s).", path, bundle.version, sorted(bundle)
        )
        return bundle

    def _describe_missing(self, path: str, response: VaultResponse) -> str:
        """Explain a 404 / null-data read without guessing which cause applies."""
        outer = response.body.get("data")
        metadata = outer.get("metadata") if isinstance(outer, Mapping) else None
        if isinstance(metadata, Mapping) and metadata.get("deletion_time"):
            return (
                f"KV v2 version {metadata.get('version')} of '{path}' has been soft-deleted "
                f"(deletion_time={metadata.get('deletion_time')!r}), so Vault returned no data. "
                "Undelete the version or write a new one."
            )
        return (
            f"No readable secret at '{self.mount}/data/{path}'. Vault returns 404 both for a "
            "path that does not exist and for one this policy may not view, so check the "
            "AppRole policy before concluding the secret is missing."
        )

    # -- cache control ----------------------------------------------------- #
    def invalidate(self, secret_path: Optional[str] = None) -> None:
        """Drop one cached path, or the whole cache when called with no argument.

        Call this on a rotation notification so the next read goes to Vault.
        """
        with self._lock:
            if secret_path is None:
                self._cache.clear()
                return
            self._cache.pop(self._validate_path(secret_path), None)

    def logout(self) -> None:
        """Forget the token, the AppRole credentials, and every cached secret."""
        with self._lock:
            self._clear_token_locked()
            self._role_id = None
            self._secret_id = None
            self._credentials_spent = False
            self._cache.clear()

    def __repr__(self) -> str:
        return (
            f"VaultSecretsManager(vault_addr={self.vault_addr!r}, "
            f"environment={self.environment!r}, mount={self.mount!r}, "
            f"authenticated={self.is_authenticated}, cached_paths={sorted(self._cache)!r})"
        )

    __str__ = __repr__


# --------------------------------------------------------------------------- #
# Offline double
# --------------------------------------------------------------------------- #
@dataclass
class _FakeRole:
    secret_id: str
    allowed_prefixes: List[str]
    token_ttl: float
    token_max_ttl: float
    renewable: bool
    secret_id_num_uses: int


@dataclass
class _FakeToken:
    role_id: str
    expires_at: float
    max_expires_at: float
    renewable: bool
    allowed_prefixes: List[str] = field(default_factory=list)


class InMemoryVaultTransport:
    """Deterministic in-process stand-in for Vault, for tests and offline dev.

    It models the parts of Vault whose behaviour a client must get right:
    AppRole login with SecretID use limits, token TTL and max TTL, policy-prefix
    enforcement (answering 404, matching Vault's "invisible path" semantics),
    the KV v2 response shape including ``metadata.version``, and soft-deleted
    versions.

    It is **not** a security boundary and must never be used in production.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._roles: Dict[str, _FakeRole] = {}
        self._tokens: Dict[str, _FakeToken] = {}
        self._secrets: Dict[str, List[Optional[Dict[str, Any]]]] = {}
        self._token_counter = 0
        self.request_log: List[str] = []
        self.rate_limited = False
        self.unreachable = False

    # -- fixture helpers --------------------------------------------------- #
    def register_role(
        self,
        role_id: str,
        secret_id: str,
        *,
        allowed_prefixes: Optional[List[str]] = None,
        token_ttl: float = 1200.0,
        token_max_ttl: float = 3600.0,
        renewable: bool = True,
        secret_id_num_uses: int = 0,
    ) -> None:
        """Register an AppRole. ``secret_id_num_uses=0`` means unlimited."""
        self._roles[role_id] = _FakeRole(
            secret_id=secret_id,
            allowed_prefixes=list(allowed_prefixes or []),
            token_ttl=token_ttl,
            token_max_ttl=token_max_ttl,
            renewable=renewable,
            secret_id_num_uses=secret_id_num_uses,
        )

    def put_secret(
        self, path: str, data: Mapping[str, Any], *, mount: str = DEFAULT_KV_MOUNT
    ) -> int:
        """Write a new KV v2 version and return its version number."""
        versions = self._secrets.setdefault(f"{mount}/{path}", [])
        versions.append(dict(data))
        return len(versions)

    def soft_delete_latest(self, path: str, *, mount: str = DEFAULT_KV_MOUNT) -> None:
        """Soft-delete the latest version, as ``vault kv delete`` does."""
        versions = self._secrets.get(f"{mount}/{path}")
        if not versions:
            raise KeyError(path)
        versions[-1] = None

    # -- transport --------------------------------------------------------- #
    def request(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> VaultResponse:
        self.request_log.append(f"{method} {path}")
        if self.unreachable:
            raise VaultTransportError(f"simulated outage on {method} {path}")
        if self.rate_limited:
            return VaultResponse(429, {"errors": ["rate limit quota exceeded"]})
        if method == "POST" and path == "auth/approle/login":
            return self._login(payload or {})
        if method == "POST" and path == "auth/token/renew-self":
            return self._renew(token)
        if method == "GET" and "/data/" in path:
            return self._read(path, token)
        return VaultResponse(404, {"errors": ["unsupported path"]})

    def _login(self, payload: Mapping[str, Any]) -> VaultResponse:
        role_id = str(payload.get("role_id"))
        role = self._roles.get(role_id)
        if role is None or not role.secret_id or role.secret_id != payload.get("secret_id"):
            return VaultResponse(400, {"errors": ["invalid role or secret id"]})
        if role.secret_id_num_uses:
            role.secret_id_num_uses -= 1
            if role.secret_id_num_uses == 0:
                role.secret_id = ""  # spent, exactly as Vault expires it
        now = self._clock()
        self._token_counter += 1
        token = f"hvs.fake-{self._token_counter}"
        self._tokens[token] = _FakeToken(
            role_id=role_id,
            expires_at=now + role.token_ttl,
            max_expires_at=now + role.token_max_ttl,
            renewable=role.renewable,
            allowed_prefixes=list(role.allowed_prefixes),
        )
        return VaultResponse(
            200,
            {
                "auth": {
                    "client_token": token,
                    "accessor": f"acc-{self._token_counter}",
                    "lease_duration": role.token_ttl,
                    "renewable": role.renewable,
                    "token_policies": ["default", f"{role_id}-policy"],
                }
            },
        )

    def _renew(self, token: Optional[str]) -> VaultResponse:
        entry = self._tokens.get(token or "")
        now = self._clock()
        if entry is None or now >= entry.expires_at:
            return VaultResponse(403, {"errors": ["permission denied"]})
        if not entry.renewable or now >= entry.max_expires_at:
            return VaultResponse(400, {"errors": ["lease is not renewable"]})
        role = self._roles[entry.role_id]
        entry.expires_at = min(now + role.token_ttl, entry.max_expires_at)
        return VaultResponse(
            200,
            {
                "auth": {
                    "client_token": token,
                    "lease_duration": entry.expires_at - now,
                    "renewable": True,
                }
            },
        )

    def _read(self, path: str, token: Optional[str]) -> VaultResponse:
        entry = self._tokens.get(token or "")
        if entry is None or self._clock() >= entry.expires_at:
            return VaultResponse(403, {"errors": ["permission denied"]})
        mount, _, secret_path = path.partition("/data/")
        secret_path = urllib.parse.unquote(secret_path)
        if entry.allowed_prefixes and not any(
            secret_path == prefix or secret_path.startswith(prefix.rstrip("*"))
            for prefix in entry.allowed_prefixes
        ):
            # Vault hides paths a policy cannot see behind a 404, not a 403.
            return VaultResponse(404, {"errors": []})
        versions = self._secrets.get(f"{mount}/{secret_path}")
        if not versions:
            return VaultResponse(404, {"errors": []})
        latest = versions[-1]
        metadata = {
            "version": len(versions),
            "created_time": "2026-01-01T00:00:00Z",
            "deletion_time": "" if latest is not None else "2026-01-02T00:00:00Z",
            "destroyed": False,
        }
        if latest is None:
            return VaultResponse(404, {"data": {"data": None, "metadata": metadata}})
        return VaultResponse(200, {"data": {"data": dict(latest), "metadata": metadata}})
