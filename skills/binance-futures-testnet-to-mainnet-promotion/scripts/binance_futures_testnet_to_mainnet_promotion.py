"""binance-futures-testnet-to-mainnet-promotion: Binance Futures testnet-to-mainnet
promotion gate — environment/host binding, credential cross-contamination detection,
fail-closed risk-parameter validation, and an explicit live-capital authorization flag.

This module performs NO network I/O. It is the deterministic, offline-testable decision
gate that must pass before a caller is permitted to point a live order router at a
Binance Futures mainnet base URL. Endpoint probing (`GET /fapi/v1/ping`,
`GET /fapi/v3/account`, `GET /fapi/v1/leverageBracket`) is the caller's responsibility;
see `references/workflows.md` for the required probe sequence.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import Any, FrozenSet, Mapping, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class Environment(Enum):
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


# Binance Futures REST hosts, verified against developers.binance.com (see
# references/standards.md for the per-host citation and the documentation
# inconsistency around the testnet hostname). Callers on a venue variant not
# listed here (e.g. Portfolio Margin, or a documented alternate host) must pass
# an explicit allowlist rather than relaxing the HTTPS/host check.
DEFAULT_MAINNET_HOSTS: FrozenSet[str] = frozenset({
    "fapi.binance.com",   # USDⓈ-M futures, production
    "dapi.binance.com",   # COIN-M futures, production
})
DEFAULT_TESTNET_HOSTS: FrozenSet[str] = frozenset({
    "demo-fapi.binance.com",       # USDⓈ-M futures testnet (current general-info docs)
    "demo-dapi.binance.com",       # COIN-M futures testnet (current general-info docs)
    "testnet.binancefuture.com",   # legacy testnet host, still cited in market-data docs
})

# Risk parameters that must be present explicitly. A promotion gate must never
# infer a missing risk limit from a default: a typo'd or absent key would then
# promote an unbounded strategy to live capital.
REQUIRED_RISK_KEYS = ("leverage", "capital_risk_pct", "hard_stop_loss_enabled")


class PromotionError(Exception):
    """Raised when promotion to Binance Futures mainnet is refused."""
    pass


def _redact(secret: Optional[str]) -> str:
    """Renders a credential for logs without disclosing it."""
    if not secret:
        return "<unset>"
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}...***"


@dataclass
class ExchangeConfig:
    """Binance Futures environment configuration.

    `__repr__` is overridden so that logging or tracebacks involving this object
    can never disclose `api_secret` — a live Binance Futures secret in an
    aggregated log store is a full account compromise.
    """

    api_key: str
    api_secret: str
    base_url: str
    environment: Environment

    def __repr__(self) -> str:
        return (
            f"ExchangeConfig(api_key={_redact(self.api_key)}, "
            f"api_secret=<redacted>, base_url={self.base_url!r}, "
            f"environment={self.environment!r})"
        )


class MainnetPromotionManager:
    """
    Gates the promotion of a trading strategy from Binance Futures Testnet to Mainnet.

    Enforces, in order: environment/host binding, credential segregation between the
    two environments, fail-closed strategy risk limits, and an explicit human
    authorization flag before any mainnet configuration is handed back to a router.

    All checks are re-run on every call to `promote_to_mainnet`; a prior successful
    promotion never short-circuits validation of a new parameter set.
    """

    def __init__(
        self,
        testnet_config: ExchangeConfig,
        mainnet_config: ExchangeConfig,
        max_leverage_limit: int = 5,
        max_capital_risk_pct: float = 0.02,
        *,
        allow_live_promotion: bool = False,
        mainnet_hosts: FrozenSet[str] = DEFAULT_MAINNET_HOSTS,
        testnet_hosts: FrozenSet[str] = DEFAULT_TESTNET_HOSTS,
    ) -> None:
        """
        Args:
            testnet_config: Configuration whose `environment` must be TESTNET.
            mainnet_config: Configuration whose `environment` must be MAINNET.
            max_leverage_limit: Hard ceiling on strategy leverage for the pilot phase.
                This is an internal policy cap only — it does NOT guarantee the
                mainnet account will accept it. Confirm against
                `GET /fapi/v1/leverageBracket` and the `POST /fapi/v1/leverage` response.
            max_capital_risk_pct: Hard ceiling on capital at risk per trade, expressed
                as a fraction of equity (0.02 == 2%), not as a percentage number.
            allow_live_promotion: Explicit authorization to release a mainnet
                configuration. Defaults to False so that an automated caller (including
                an AI agent following this skill) cannot reach live capital without a
                deliberate, out-of-band operator decision. Wire this from a deployment
                flag such as `BINANCE_ALLOW_MAINNET_PROMOTION` at the call site — this
                module deliberately does not read the environment itself.
            mainnet_hosts: Allowed mainnet REST hostnames.
            testnet_hosts: Allowed testnet REST hostnames.

        Raises:
            ValueError: On any configuration that can never be valid — mismatched
                environment enums, credentials shared across environments, or
                nonsensical risk ceilings.
        """
        if testnet_config.environment != Environment.TESTNET:
            raise ValueError("testnet_config must have environment TESTNET")
        if mainnet_config.environment != Environment.MAINNET:
            raise ValueError("mainnet_config must have environment MAINNET")

        # Binance issues testnet credentials from a separate registration flow; they are
        # not valid on mainnet. Identical credentials across the two configs therefore
        # mean one leg is misconfigured — and if the shared value is the mainnet key,
        # the supposed "testnet" track record was generated against real capital.
        if testnet_config.api_key and testnet_config.api_key == mainnet_config.api_key:
            raise ValueError(
                "testnet_config and mainnet_config share the same api_key; Binance "
                "testnet and mainnet credentials are issued separately and must differ"
            )
        if testnet_config.api_secret and testnet_config.api_secret == mainnet_config.api_secret:
            raise ValueError(
                "testnet_config and mainnet_config share the same api_secret; Binance "
                "testnet and mainnet credentials are issued separately and must differ"
            )

        if not isinstance(max_leverage_limit, int) or isinstance(max_leverage_limit, bool):
            raise ValueError("max_leverage_limit must be an int")
        if max_leverage_limit < 1:
            raise ValueError("max_leverage_limit must be >= 1")
        if (
            isinstance(max_capital_risk_pct, bool)
            or not isinstance(max_capital_risk_pct, (int, float))
            or not math.isfinite(max_capital_risk_pct)
            or not 0 < max_capital_risk_pct <= 1
        ):
            raise ValueError(
                "max_capital_risk_pct must be a finite fraction in (0, 1]; "
                "0.02 means 2%, not 2"
            )

        self.testnet_config = testnet_config
        self.mainnet_config = mainnet_config
        self.max_leverage_limit = max_leverage_limit
        self.max_capital_risk_pct = max_capital_risk_pct
        self.allow_live_promotion = allow_live_promotion
        self.mainnet_hosts = frozenset(h.lower() for h in mainnet_hosts)
        self.testnet_hosts = frozenset(h.lower() for h in testnet_hosts)
        self._promoted = False

    def _expected_hosts(self, environment: Environment) -> FrozenSet[str]:
        return (
            self.mainnet_hosts
            if environment == Environment.MAINNET
            else self.testnet_hosts
        )

    def verify_api_connectivity(self, config: ExchangeConfig) -> bool:
        """
        Validates that a configuration is structurally safe to connect with.

        This is a static pre-connection check, not a live probe: it confirms that
        credentials are present, that transport is HTTPS, and — critically — that the
        base URL host actually belongs to the declared environment. A `base_url`
        pointing at a mainnet host while labelled TESTNET is the failure mode that
        turns "paper trading" into real leveraged orders, and it is invisible to any
        check that only asserts the URL starts with `https://`.

        Returns:
            True if the configuration passes; False (with an error log) otherwise.
        """
        if not config.api_key or not config.api_secret:
            logger.error(f"Missing API credentials for {config.environment.value}")
            return False

        base_url = config.base_url if isinstance(config.base_url, str) else ""
        parsed = urlparse(base_url.strip())

        if parsed.scheme.lower() != "https":
            logger.error(
                f"Base URL must use HTTPS for {config.environment.value}; got "
                f"scheme {parsed.scheme!r}"
            )
            return False

        host = (parsed.hostname or "").lower()
        expected = self._expected_hosts(config.environment)
        # Exact hostname comparison: a suffix or substring match would accept
        # https://fapi.binance.com.attacker.example as a mainnet host.
        if host not in expected:
            logger.error(
                f"Base URL host {host!r} is not a recognised "
                f"{config.environment.value} host (expected one of {sorted(expected)})"
            )
            return False

        logger.info(
            f"Verified configuration for {config.environment.value} at {parsed.scheme}://{host}"
        )
        return True

    def validate_risk_parameters(self, strategy_params: Mapping[str, Any]) -> bool:
        """
        Validates strategy risk configuration against the mainnet pilot ceilings.

        Fails closed in every ambiguous case: a missing key, a non-numeric value, a
        NaN, or a non-boolean stop-loss flag is a rejection, not a default. NaN is
        called out specifically because `float('nan') > limit` evaluates False, so a
        naive comparison silently passes an unbounded leverage value.

        Returns:
            True only if every required parameter is present, well-formed, and within limits.
        """
        missing = [k for k in REQUIRED_RISK_KEYS if k not in strategy_params]
        if missing:
            logger.error(
                f"Strategy parameters missing required risk keys: {sorted(missing)}"
            )
            return False

        leverage = strategy_params["leverage"]
        if isinstance(leverage, bool) or not isinstance(leverage, int):
            logger.error(
                f"Strategy leverage must be an int (Binance accepts integer leverage "
                f"only); got {type(leverage).__name__}"
            )
            return False
        if leverage < 1:
            logger.error(f"Strategy leverage {leverage} must be >= 1")
            return False
        if leverage > self.max_leverage_limit:
            logger.error(
                f"Strategy leverage {leverage} exceeds limit of {self.max_leverage_limit}"
            )
            return False

        capital_risk = strategy_params["capital_risk_pct"]
        if isinstance(capital_risk, bool) or not isinstance(capital_risk, (int, float)):
            logger.error(
                f"capital_risk_pct must be numeric; got {type(capital_risk).__name__}"
            )
            return False
        if not math.isfinite(capital_risk):
            logger.error(f"capital_risk_pct must be finite; got {capital_risk!r}")
            return False
        if capital_risk <= 0:
            logger.error(
                f"capital_risk_pct {capital_risk} must be > 0; a zero or negative "
                "risk budget indicates a misconfigured sizing model, not a safe one"
            )
            return False
        if capital_risk > self.max_capital_risk_pct:
            logger.error(
                f"Capital risk {capital_risk} exceeds limit of {self.max_capital_risk_pct}"
            )
            return False

        # Strict identity check against True: a config loader that yields the string
        # "false" would pass a truthiness test and disable the stop-loss gate.
        if strategy_params["hard_stop_loss_enabled"] is not True:
            logger.error(
                "Hard stop-loss must be enabled for mainnet promotion "
                "(hard_stop_loss_enabled must be the boolean True)"
            )
            return False

        logger.info("Risk parameters validated successfully.")
        return True

    def run_pre_flight_checks(self, strategy_params: Mapping[str, Any]) -> bool:
        """
        Runs every gate that must pass before a mainnet configuration may be released.

        Both configurations are checked, not just the mainnet one: if the testnet
        config points at a mainnet host, the testnet track record being used to justify
        this promotion was not produced on testnet at all.
        """
        logger.info("Starting pre-flight checks for Mainnet promotion...")

        if not self.verify_api_connectivity(self.testnet_config):
            logger.error("Testnet configuration failed environment binding checks.")
            return False

        if not self.verify_api_connectivity(self.mainnet_config):
            logger.error("Mainnet API connectivity failed.")
            return False

        if not self.validate_risk_parameters(strategy_params):
            logger.error("Mainnet risk parameter validation failed.")
            return False

        if not self.allow_live_promotion:
            logger.error(
                "Mainnet promotion not authorized: allow_live_promotion is False. "
                "Set it explicitly from an operator-controlled deployment flag."
            )
            return False

        logger.info("All pre-flight checks passed.")
        return True

    def promote_to_mainnet(self, strategy_params: Mapping[str, Any]) -> ExchangeConfig:
        """
        Releases the mainnet configuration if — and only if — all pre-flight checks pass.

        Checks are re-run on every call. An earlier successful promotion does not
        authorize a later call carrying different parameters: returning a mainnet
        config for a 50x strategy because a 3x strategy passed an hour ago is a
        fail-open, not idempotency.

        Returns:
            The mainnet `ExchangeConfig` for the order router to use.

        Raises:
            PromotionError: If any pre-flight check fails.
        """
        if not self.run_pre_flight_checks(strategy_params):
            raise PromotionError("Failed pre-flight checks. Cannot promote to mainnet.")

        if self._promoted:
            logger.info("Strategy already promoted; re-validated current parameters.")
        else:
            self._promoted = True
            logger.info("SUCCESS: Strategy promoted to Mainnet.")
        return self.mainnet_config

    @property
    def is_promoted(self) -> bool:
        """Whether a promotion has succeeded at least once.

        This is history, not standing authorization: it does NOT mean the parameters
        you are holding right now are approved. Never branch to live order routing on
        this flag — call `promote_to_mainnet` and use the config it returns.
        """
        return self._promoted
