"""
alpaca-paper-live-key-separation: Production-grade Alpaca API environment segregation guard,
credential prefix validator (PK... vs AK...), base URL endpoint matcher,
and live trading safety gate to prevent accidental live capital loss.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import os
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class EnvironmentMismatchError(RuntimeError):
    """Raised when key prefix, base URL, or account probe conflicts with environment mode."""
    pass


class TradingEnvironment(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


PAPER_BASE_URL = "https://paper-api.alpaca.markets"
LIVE_BASE_URL = "https://api.alpaca.markets"


@dataclass
class AlpacaConfig:
    environment: TradingEnvironment
    key_id: str
    secret_key: str
    base_url: str


class AlpacaEnvironmentManager:
    """
    Validates Alpaca API credentials and base URLs, ensuring strict separation
    between paper and live environments before any orders are placed.
    """

    def __init__(self, allow_live_trading_env_var: str = "ALLOW_LIVE_TRADING"):
        self.allow_live_env_var = allow_live_trading_env_var

    def validate_config(self, config: AlpacaConfig) -> bool:
        """
        Validates key prefix, base URL matching, and live safety flags.
        """
        env = config.environment
        key_id = config.key_id.strip()
        base_url = config.base_url.strip().rstrip("/")

        # 1. Key Prefix Validation
        if env == TradingEnvironment.PAPER:
            if base_url == LIVE_BASE_URL:
                raise EnvironmentMismatchError(
                    f"CRITICAL SAFETY BREACH: Paper mode configured but base_url points to LIVE endpoint ({LIVE_BASE_URL})!"
                )
            if key_id.startswith("AK"):
                logger.warning("Key ID starts with 'AK' (live format) while operating in PAPER mode. Verify credentials.")

        elif env == TradingEnvironment.LIVE:
            if base_url != LIVE_BASE_URL:
                raise EnvironmentMismatchError(
                    f"LIVE mode configured but base_url does not match LIVE endpoint ({LIVE_BASE_URL})!"
                )
            if key_id.startswith("PK"):
                raise EnvironmentMismatchError(
                    "LIVE mode configured but key_id starts with 'PK' (paper format)!"
                )

            # Check explicit environment variable flag for LIVE trading
            allow_live = os.environ.get(self.allow_live_env_var, "").lower() == "true"
            if not allow_live:
                raise EnvironmentMismatchError(
                    f"LIVE trading blocked! Environment variable '{self.allow_live_env_var}=true' is required."
                )

        logger.info(f"Alpaca configuration validated successfully for {env.value} mode at {base_url}.")
        return True

    def probe_account(
        self, config: AlpacaConfig, get_account_fn: Callable[[], Dict[str, Any]]
    ) -> bool:
        """
        Probes Alpaca GET /v2/account endpoint and verifies account flags match environment.
        """
        self.validate_config(config)

        try:
            account_data = get_account_fn()
        except Exception as e:
            raise EnvironmentMismatchError(f"Failed to probe Alpaca account endpoint: {e}")

        is_paper = account_data.get("is_paper", True)
        status = account_data.get("status", "ACTIVE")

        if status != "ACTIVE":
            raise EnvironmentMismatchError(f"Alpaca account is not ACTIVE (status='{status}').")

        if config.environment == TradingEnvironment.PAPER and not is_paper:
            raise EnvironmentMismatchError(
                "CRITICAL MISMATCH: Bot configured for PAPER mode, but probed account is a LIVE real-money account!"
            )

        if config.environment == TradingEnvironment.LIVE and is_paper:
            raise EnvironmentMismatchError(
                "MISMATCH: Bot configured for LIVE mode, but probed account is a PAPER account!"
            )

        logger.info(f"Account probe verified: is_paper={is_paper}, status='{status}'.")
        return True

    def guard_order(
        self,
        config: AlpacaConfig,
        symbol: str,
        qty: float,
        side: str,
        get_account_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    ) -> bool:
        """
        Veto gate executed before submitting any order to Alpaca.
        """
        self.validate_config(config)

        if get_account_fn:
            self.probe_account(config, get_account_fn)

        logger.info(f"Order submission authorized for {config.environment.value}: {side} {qty} '{symbol}'")
        return True
