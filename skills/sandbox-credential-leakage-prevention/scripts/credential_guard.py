"""sandbox-credential-leakage-prevention: pre-request guard that refuses to let a
sandbox/paper credential reach a live broker gateway, or a production credential
reach a sandbox gateway.

Design note — why this module is an allow-list, not a deny-list
---------------------------------------------------------------
The obvious implementation ("reject the request if the URL contains a known
production hostname") fails open in every case the author did not enumerate.
This module instead requires the destination to *positively match* a declared
endpoint for the currently declared environment. Anything unrecognised is a
violation, including an unrecognised broker.

Matching is performed on the parsed URL — exact hostname comparison plus an
optional normalised path prefix — never on substrings of the raw URL string.
Substring matching is unsafe here for three independent reasons:

* ``"api.alpaca.markets" in url`` also matches
  ``https://api.alpaca.markets.attacker.example/v2/orders``;
* ``"paper" in url`` also matches
  ``https://api.alpaca.markets/v2/orders?client_tag=paper``, i.e. attacker- or
  operator-controlled query text can flip an environment decision;
* Binance serves production market data from ``data-api.binance.vision`` and the
  spot testnet from ``testnet.binance.vision`` — the same registrable domain, so
  no substring of the domain separates the two environments.

Endpoint facts were verified against vendor documentation in August 2026; see
``references/standards.md`` for the per-fact citations. Hostnames change: treat
``BROKER_RULES`` as a starting point to be reviewed against current vendor docs,
and pass ``custom_rules`` for anything not shipped here.
"""
from __future__ import annotations

import logging
import posixpath
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


class TradingEnvironment(Enum):
    SANDBOX = "SANDBOX"
    PRODUCTION = "PRODUCTION"


class SecurityViolationError(Exception):
    """Raised when a credential or destination does not match the declared environment.

    Also raised when the guard cannot *prove* the request is safe — an unknown
    broker or an unparseable URL is a violation, not a pass.
    """
    pass


def _normalise_path(path: str) -> str:
    """Collapses ``.``/``..``/duplicate separators so ``/openapi/../sim`` cannot pose as ``/openapi``."""
    if not path:
        return ""
    if not path.startswith("/"):
        path = "/" + path
    collapsed = posixpath.normpath(path)
    return "" if collapsed == "/" else collapsed


@dataclass(frozen=True)
class EndpointRule:
    """A single permitted gateway: exact hostname, plus an optional path prefix.

    The path prefix exists because some venues separate environments by path
    rather than by host. Saxo Bank is the shipped example: simulation is
    ``https://gateway.saxobank.com/sim/openapi`` and live is
    ``https://gateway.saxobank.com/openapi`` — identical hostnames. A host-only
    check would treat the two as interchangeable.
    """

    host: str
    path_prefix: str = ""

    @classmethod
    def parse(cls, spec: Union[str, "EndpointRule"]) -> "EndpointRule":
        """Builds a rule from ``"host"`` or ``"host/path/prefix"`` shorthand."""
        if isinstance(spec, EndpointRule):
            return cls(spec.host.lower().strip(), _normalise_path(spec.path_prefix))
        if not isinstance(spec, str) or not spec.strip():
            raise ValueError(f"Endpoint specification must be a non-empty string; got {spec!r}")
        host, _, path = spec.strip().partition("/")
        host = host.lower()
        if not host or ":" in host:
            raise ValueError(
                f"Endpoint specification {spec!r} must start with a bare hostname "
                f"(no scheme, no port)."
            )
        return cls(host, _normalise_path(path))

    def matches(self, host: str, path: str) -> bool:
        """True if ``host`` matches exactly and ``path`` sits under ``path_prefix``."""
        if host != self.host:
            return False
        if not self.path_prefix:
            return True
        return path == self.path_prefix or path.startswith(self.path_prefix.rstrip("/") + "/")

    def __str__(self) -> str:
        return f"{self.host}{self.path_prefix}"


@dataclass
class BrokerEnvironmentRules:
    """Per-broker environment boundary definition.

    Attributes:
        broker_name: Lower-case broker identifier.
        sandbox_key_prefixes: Observed credential prefixes for the sandbox
            environment. Advisory — see the note below.
        production_key_prefixes: Observed credential prefixes for the production
            environment. Advisory — see the note below.
        sandbox_endpoints: Permitted sandbox gateways (``EndpointRule`` or
            ``"host/path"`` shorthand).
        production_endpoints: Permitted production gateways.

    Key prefixes are a *secondary* control. Alpaca key IDs are widely observed to
    begin with ``PK`` (paper) and ``AK`` (live), but Alpaca's authentication
    documentation specifies no key format at all, so the convention is not a
    contract and may change without notice. Binance issues unprefixed HMAC/RSA/
    Ed25519 keys and Saxo issues OAuth2 bearer tokens; for those venues the
    prefix lists are empty and no key inference is possible. Accordingly:

    * a key carrying the *opposing* environment's prefix is a hard violation
      (a high-confidence positive signal), while
    * a key carrying *no* recognised prefix only warns, because absence of a
      prefix proves nothing.

    The endpoint allow-list, not the prefix, is the control that actually decides
    whether the request is permitted.
    """

    broker_name: str
    sandbox_key_prefixes: List[str] = field(default_factory=list)
    production_key_prefixes: List[str] = field(default_factory=list)
    sandbox_endpoints: Sequence[Union[str, EndpointRule]] = field(default_factory=tuple)
    production_endpoints: Sequence[Union[str, EndpointRule]] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.broker_name, str) or not self.broker_name.strip():
            raise ValueError("broker_name must be a non-empty string.")
        self.broker_name = self.broker_name.strip().lower()
        self.sandbox_key_prefixes = [p for p in self.sandbox_key_prefixes if p]
        self.production_key_prefixes = [p for p in self.production_key_prefixes if p]
        self.sandbox_endpoints = tuple(EndpointRule.parse(e) for e in self.sandbox_endpoints)
        self.production_endpoints = tuple(EndpointRule.parse(e) for e in self.production_endpoints)
        if not self.sandbox_endpoints and not self.production_endpoints:
            raise ValueError(
                f"Broker '{self.broker_name}' declares no endpoints; the guard would "
                f"have nothing to validate against and would reject every request."
            )

    def endpoints_for(self, environment: TradingEnvironment) -> Tuple[EndpointRule, ...]:
        return (
            self.sandbox_endpoints
            if environment == TradingEnvironment.SANDBOX
            else self.production_endpoints
        )

    def key_prefixes_for(self, environment: TradingEnvironment) -> List[str]:
        return (
            self.sandbox_key_prefixes
            if environment == TradingEnvironment.SANDBOX
            else self.production_key_prefixes
        )


def _opposite(environment: TradingEnvironment) -> TradingEnvironment:
    return (
        TradingEnvironment.PRODUCTION
        if environment == TradingEnvironment.SANDBOX
        else TradingEnvironment.SANDBOX
    )


# Default rules. Endpoint hostnames verified against vendor documentation in
# August 2026 (citations in references/standards.md). Review before relying on
# them; vendors add and retire hosts without changing their API version.
BROKER_RULES: Dict[str, BrokerEnvironmentRules] = {
    "alpaca": BrokerEnvironmentRules(
        broker_name="alpaca",
        # Undocumented but consistently observed; advisory only.
        sandbox_key_prefixes=["PK"],
        production_key_prefixes=["AK"],
        sandbox_endpoints=["paper-api.alpaca.markets"],
        production_endpoints=["api.alpaca.markets"],
    ),
    "binance": BrokerEnvironmentRules(
        broker_name="binance",
        # Binance API keys carry no environment prefix — inference is impossible.
        sandbox_key_prefixes=[],
        production_key_prefixes=[],
        sandbox_endpoints=[
            "testnet.binance.vision",       # spot testnet
            "demo-fapi.binance.com",        # USD-M futures testnet
            "demo-dapi.binance.com",        # COIN-M futures testnet
            "testnet.binancefuture.com",    # legacy futures testnet, still served
        ],
        production_endpoints=[
            "api.binance.com",
            "api-gcp.binance.com",
            "api1.binance.com",
            "api2.binance.com",
            "api3.binance.com",
            "api4.binance.com",
            "fapi.binance.com",             # USD-M futures
            "dapi.binance.com",             # COIN-M futures
            "data-api.binance.vision",      # production market data, .vision domain
        ],
    ),
    "saxo": BrokerEnvironmentRules(
        broker_name="saxo",
        # Saxo uses OAuth2 bearer tokens; no prefix convention exists.
        sandbox_key_prefixes=[],
        production_key_prefixes=[],
        # Same hostname for both environments — separated only by path prefix.
        sandbox_endpoints=["gateway.saxobank.com/sim/openapi", "sim-streaming.saxobank.com"],
        production_endpoints=["gateway.saxobank.com/openapi", "live-streaming.saxobank.com"],
    ),
}


def _redact_url(target_url: str) -> str:
    """Strips userinfo, query, and fragment before a URL reaches a log or exception.

    Broker request URLs routinely carry secrets in the query string — Binance
    appends ``&signature=<hmac>`` to signed REST calls, and OAuth flows carry
    tokens as query parameters. A guard that echoes the raw URL into an exception
    message leaks those secrets into logs, tracebacks, and error reporters, which
    is the failure this skill exists to prevent.
    """
    if not isinstance(target_url, str):
        return "<unparseable-url>"
    try:
        parsed = urlparse(target_url)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
    except ValueError:
        return "<unparseable-url>"
    if not host:
        return "<unparseable-url>"
    redacted = urlunparse((parsed.scheme, host, parsed.path, "", "", ""))
    if parsed.query or parsed.fragment:
        redacted += "?<redacted>"
    return redacted


class CredentialEnvironmentGuard:
    """Pre-request boundary check binding a credential and a destination URL to one environment.

    Call :meth:`validate_request_boundary` immediately before every outbound
    broker HTTP call. It returns ``True`` only when the destination is a declared
    endpoint of the declared environment; every other outcome raises
    :class:`SecurityViolationError`.
    """

    def __init__(
        self,
        environment: TradingEnvironment,
        custom_rules: Optional[Dict[str, BrokerEnvironmentRules]] = None,
        allow_unknown_brokers: bool = False,
    ):
        """
        Args:
            environment: The environment this process is declared to be running in.
            custom_rules: Replaces ``BROKER_RULES`` entirely when supplied. Keys are
                matched case-insensitively against ``broker_name``.
            allow_unknown_brokers: Default ``False`` (fail closed). When ``True``, a
                broker with no rules is permitted through with a warning — an
                explicit, logged decision to run without a boundary check for that
                broker, never a silent default.

        Raises:
            TypeError: If ``environment`` is not a ``TradingEnvironment``.
        """
        if not isinstance(environment, TradingEnvironment):
            raise TypeError(
                f"environment must be a TradingEnvironment, got {type(environment).__name__}."
            )
        self.environment = environment
        source = BROKER_RULES if custom_rules is None else custom_rules
        self.rules: Dict[str, BrokerEnvironmentRules] = {
            str(name).strip().lower(): rule for name, rule in source.items()
        }
        self.allow_unknown_brokers = bool(allow_unknown_brokers)

    def validate_request_boundary(
        self,
        broker_name: str,
        api_key: str,
        target_url: str,
    ) -> bool:
        """Validates that credential and destination both belong to the declared environment.

        Args:
            broker_name: Broker identifier, matched case-insensitively.
            api_key: The API key ID / token about to be sent. Never logged; only a
                matched prefix is ever reported.
            target_url: The absolute HTTPS URL about to be called.

        Returns:
            True if the request is permitted.

        Raises:
            SecurityViolationError: On any credential/endpoint mismatch, on an
                unknown broker (unless ``allow_unknown_brokers``), on a malformed
                or non-HTTPS URL, or on credentials embedded in the URL.
            ValueError: If ``broker_name``, ``api_key``, or ``target_url`` is empty
                or not a string.
        """
        broker_name = self._require_str(broker_name, "broker_name")
        api_key = self._require_str(api_key, "api_key")
        target_url = self._require_str(target_url, "target_url")

        b_key = broker_name.lower()
        host, path = self._parse_destination(target_url)
        safe_url = _redact_url(target_url)

        rule = self.rules.get(b_key)
        if rule is None:
            if not self.allow_unknown_brokers:
                raise SecurityViolationError(
                    f"ENDPOINT LEAK DETECTED: no environment rules defined for broker "
                    f"'{broker_name}', so the guard cannot prove '{safe_url}' belongs to "
                    f"{self.environment.value}. Register a BrokerEnvironmentRules entry, or "
                    f"construct the guard with allow_unknown_brokers=True to accept the risk."
                )
            logger.warning(
                "No environment rules defined for broker '%s'; boundary check SKIPPED for %s "
                "because allow_unknown_brokers=True.",
                broker_name,
                safe_url,
            )
            return True

        self._check_key_prefix(rule, api_key, broker_name)
        self._check_endpoint(rule, host, path, broker_name, safe_url)

        logger.info(
            "Credential boundary validated for broker '%s' in %s mode at %s.",
            broker_name,
            self.environment.value,
            safe_url,
        )
        return True

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _require_str(value: str, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string; got {value!r}.")
        return value.strip()

    @staticmethod
    def _parse_destination(target_url: str) -> Tuple[str, str]:
        """Parses ``target_url`` into (lower-case hostname, normalised path).

        Rejects anything that would make the later comparison meaningless: a
        non-HTTPS scheme (the credential would travel in cleartext), a missing
        host, a non-default port, or userinfo — credentials embedded in a URL are
        precisely the leak this module exists to stop, and
        ``https://user@a.com@b.com`` is a classic way to make a destination read
        as one host and resolve to another.
        """
        safe_url = _redact_url(target_url)
        try:
            parsed = urlparse(target_url)
        except ValueError as exc:
            raise SecurityViolationError(
                f"ENDPOINT LEAK DETECTED: target URL could not be parsed ({exc})."
            ) from exc

        if parsed.scheme.lower() != "https":
            raise SecurityViolationError(
                f"ENDPOINT LEAK DETECTED: target URL must use HTTPS or the API credential "
                f"travels in cleartext; got scheme '{parsed.scheme}' for '{safe_url}'."
            )
        try:
            if parsed.username or parsed.password:
                raise SecurityViolationError(
                    "CREDENTIAL LEAK DETECTED: target URL embeds userinfo credentials. "
                    "Pass credentials in request headers, never in the URL."
                )
            host = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError as exc:
            raise SecurityViolationError(
                f"ENDPOINT LEAK DETECTED: target URL has an invalid host or port ({exc})."
            ) from exc
        if not host:
            raise SecurityViolationError(
                f"ENDPOINT LEAK DETECTED: target URL has no hostname: '{safe_url}'."
            )
        if port is not None and port != 443:
            raise SecurityViolationError(
                f"ENDPOINT LEAK DETECTED: target URL uses non-standard port {port}; "
                f"broker gateways are served on 443."
            )
        return host, _normalise_path(parsed.path)

    def _check_key_prefix(
        self, rule: BrokerEnvironmentRules, api_key: str, broker_name: str
    ) -> None:
        """Rejects a credential carrying the opposing environment's prefix.

        Comparison is case-insensitive: a lower-cased ``ak_live_...`` is still a
        production-shaped Alpaca key and must not slip through a sandbox guard.
        """
        key_upper = api_key.upper()
        for prefix in rule.key_prefixes_for(_opposite(self.environment)):
            if key_upper.startswith(prefix.upper()):
                raise SecurityViolationError(
                    f"CREDENTIAL LEAK DETECTED: {_opposite(self.environment).value} API key "
                    f"prefix '{prefix}' found in {self.environment.value} mode for broker "
                    f"'{broker_name}'."
                )

        expected = rule.key_prefixes_for(self.environment)
        if expected and not any(key_upper.startswith(p.upper()) for p in expected):
            # Advisory only — the prefix convention is undocumented for every venue
            # shipped here, so an unrecognised prefix is not proof of a wrong key.
            logger.warning(
                "API key for broker '%s' matches none of the expected %s prefixes %s. "
                "This is a heuristic signal only; the endpoint check remains authoritative.",
                broker_name,
                self.environment.value,
                expected,
            )

    def _check_endpoint(
        self,
        rule: BrokerEnvironmentRules,
        host: str,
        path: str,
        broker_name: str,
        safe_url: str,
    ) -> None:
        """Requires a positive match against this environment's endpoint allow-list."""
        permitted = rule.endpoints_for(self.environment)
        if not permitted:
            raise SecurityViolationError(
                f"ENDPOINT LEAK DETECTED: broker '{broker_name}' declares no "
                f"{self.environment.value} endpoints, so no request can be authorised."
            )
        if any(ep.matches(host, path) for ep in permitted):
            return

        opposing = rule.endpoints_for(_opposite(self.environment))
        if any(ep.matches(host, path) for ep in opposing):
            raise SecurityViolationError(
                f"ENDPOINT LEAK DETECTED: {self.environment.value} mode attempting to call "
                f"{_opposite(self.environment).value} gateway '{safe_url}' for broker "
                f"'{broker_name}'."
            )
        raise SecurityViolationError(
            f"ENDPOINT LEAK DETECTED: '{safe_url}' is not a recognised "
            f"{self.environment.value} endpoint for broker '{broker_name}' "
            f"(expected one of {[str(ep) for ep in permitted]})."
        )


def iter_declared_endpoints(
    rules: Optional[Dict[str, BrokerEnvironmentRules]] = None,
) -> Iterable[Tuple[str, str, str]]:
    """Yields ``(broker, environment, endpoint)`` for every declared endpoint.

    Intended for the periodic review the module header calls for: dump the shipped
    allow-list and diff it against current vendor documentation.
    """
    for broker, rule in sorted((rules if rules is not None else BROKER_RULES).items()):
        for env in (TradingEnvironment.SANDBOX, TradingEnvironment.PRODUCTION):
            for endpoint in rule.endpoints_for(env):
                yield broker, env.value, str(endpoint)
