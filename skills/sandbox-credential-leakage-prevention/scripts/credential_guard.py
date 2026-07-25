"""
sandbox-credential-leakage-prevention: Runtime environment guard enforcing strict isolation
between sandbox/paper test credentials and live production endpoints.
"""
from dataclasses import dataclass
import logging
import re
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class TradingEnvironment(Enum):
    SANDBOX = "SANDBOX"
    PRODUCTION = "PRODUCTION"


class SecurityViolationError(Exception):
    """Raised when sandbox credentials leak into production code paths or vice versa."""
    pass


@dataclass
class BrokerEnvironmentRules:
    broker_name: str
    sandbox_key_prefixes: List[str]
    production_key_prefixes: List[str]
    sandbox_url_keywords: List[str]
    production_url_keywords: List[str]


# Default security rules per broker
BROKER_RULES: Dict[str, BrokerEnvironmentRules] = {
    "alpaca": BrokerEnvironmentRules(
        broker_name="alpaca",
        sandbox_key_prefixes=["PK", "PAPER_"],
        production_key_prefixes=["AK", "LIVE_"],
        sandbox_url_keywords=["paper-api.alpaca.markets", "paper"],
        production_url_keywords=["api.alpaca.markets"],
    ),
    "binance": BrokerEnvironmentRules(
        broker_name="binance",
        sandbox_key_prefixes=["testnet_", "test_"],
        production_key_prefixes=["live_", "prod_"],
        sandbox_url_keywords=["testnet.binance.vision"],
        production_url_keywords=["api.binance.com"],
    ),
    "saxo": BrokerEnvironmentRules(
        broker_name="saxo",
        sandbox_key_prefixes=["sim_", "test_"],
        production_key_prefixes=["prod_", "live_"],
        sandbox_url_keywords=["gateway.saxobank.com/sim"],
        production_url_keywords=["gateway.saxobank.com/openapi"],
    ),
}


class CredentialEnvironmentGuard:
    """
    Enforces runtime credential and endpoint URL boundary checks to prevent
    sandbox credentials from reaching live production targets.
    """

    def __init__(
        self,
        environment: TradingEnvironment,
        custom_rules: Optional[Dict[str, BrokerEnvironmentRules]] = None,
    ):
        self.environment = environment
        self.rules = custom_rules or BROKER_RULES

    def validate_request_boundary(
        self,
        broker_name: str,
        api_key: str,
        target_url: str,
    ) -> bool:
        """
        Validates that the API key signature and target URL strictly match the declared
        trading environment. Raises SecurityViolationError if a leak is detected.
        """
        b_key = broker_name.lower()
        rule = self.rules.get(b_key)

        if not rule:
            logger.warning(f"No specific environment rules defined for '{broker_name}'. Performing generic check.")
            # Generic sanity check for keywords
            if self.environment == TradingEnvironment.SANDBOX and "live" in target_url.lower():
                raise SecurityViolationError(
                    f"CRITICAL SECURITY VIOLATION: SANDBOX mode attempting to call LIVE URL: {target_url}"
                )
            return True

        url_lower = target_url.lower()

        if self.environment == TradingEnvironment.SANDBOX:
            # 1. Key check: should not use production key prefix
            for prod_prefix in rule.production_key_prefixes:
                if api_key.startswith(prod_prefix):
                    raise SecurityViolationError(
                        f"CREDENTIAL LEAK DETECTED: Production API key prefix '{prod_prefix}' "
                        f"found in SANDBOX mode for broker '{broker_name}'."
                    )

            # 2. URL check: target URL must not match production keywords
            for prod_kw in rule.production_url_keywords:
                # Make sure it's not a sandbox sub-domain that happens to contain prod_kw
                is_sandbox_url = any(sb_kw in url_lower for sb_kw in rule.sandbox_url_keywords)
                if prod_kw in url_lower and not is_sandbox_url:
                    raise SecurityViolationError(
                        f"ENDPOINT LEAK DETECTED: SANDBOX mode attempting to call PRODUCTION gateway: '{target_url}'."
                    )

        elif self.environment == TradingEnvironment.PRODUCTION:
            # 1. Key check: should not use sandbox key prefix
            for sb_prefix in rule.sandbox_key_prefixes:
                if api_key.startswith(sb_prefix):
                    raise SecurityViolationError(
                        f"CREDENTIAL LEAK DETECTED: Sandbox API key prefix '{sb_prefix}' "
                        f"found in PRODUCTION mode for broker '{broker_name}'."
                    )

            # 2. URL check: target URL must match production keywords
            for sb_kw in rule.sandbox_url_keywords:
                if sb_kw in url_lower:
                    raise SecurityViolationError(
                        f"ENDPOINT LEAK DETECTED: PRODUCTION mode attempting to call SANDBOX gateway: '{target_url}'."
                    )

        logger.info(f"Credential boundary validated successfully for {broker_name} in {self.environment.value} mode.")
        return True
