import logging
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class Environment(Enum):
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"

@dataclass
class ExchangeConfig:
    api_key: str
    api_secret: str
    base_url: str
    environment: Environment

class PromotionError(Exception):
    pass

class MainnetPromotionManager:
    """
    Manages the promotion of a trading strategy from Binance Futures Testnet to Mainnet.
    Enforces institutional standards: API validation, risk limits, and environment segregation.
    """
    
    def __init__(
        self, 
        testnet_config: ExchangeConfig, 
        mainnet_config: ExchangeConfig,
        max_leverage_limit: int = 5,
        max_capital_risk_pct: float = 0.02
    ):
        if testnet_config.environment != Environment.TESTNET:
            raise ValueError("testnet_config must have environment TESTNET")
        if mainnet_config.environment != Environment.MAINNET:
            raise ValueError("mainnet_config must have environment MAINNET")
            
        self.testnet_config = testnet_config
        self.mainnet_config = mainnet_config
        self.max_leverage_limit = max_leverage_limit
        self.max_capital_risk_pct = max_capital_risk_pct
        self._promoted = False

    def verify_api_connectivity(self, config: ExchangeConfig) -> bool:
        """
        Simulates API connectivity check.
        In a real scenario, this would ping the Binance Futures API /fapi/v1/ping
        and validate API keys using /fapi/v1/account.
        """
        if not config.api_key or not config.api_secret:
            logger.error(f"Missing API credentials for {config.environment.value}")
            return False
            
        if not config.base_url.startswith("https://"):
            logger.error("Base URL must use HTTPS")
            return False
            
        logger.info(f"Verified connectivity for {config.environment.value} at {config.base_url}")
        return True

    def validate_risk_parameters(self, strategy_params: Dict[str, Any]) -> bool:
        """
        Validates that the strategy complies with strict mainnet risk limits.
        """
        leverage = strategy_params.get("leverage", 0)
        if leverage > self.max_leverage_limit:
            logger.error(f"Strategy leverage {leverage} exceeds limit of {self.max_leverage_limit}")
            return False
            
        capital_risk = strategy_params.get("capital_risk_pct", 1.0)
        if capital_risk > self.max_capital_risk_pct:
            logger.error(f"Capital risk {capital_risk} exceeds limit of {self.max_capital_risk_pct}")
            return False
            
        if not strategy_params.get("hard_stop_loss_enabled", False):
            logger.error("Hard stop-loss must be enabled for mainnet promotion")
            return False

        logger.info("Risk parameters validated successfully.")
        return True

    def run_pre_flight_checks(self, strategy_params: Dict[str, Any]) -> bool:
        """
        Runs all necessary checks before allowing promotion.
        """
        logger.info("Starting pre-flight checks for Mainnet promotion...")
        
        if not self.verify_api_connectivity(self.mainnet_config):
            logger.error("Mainnet API connectivity failed.")
            return False
            
        if not self.validate_risk_parameters(strategy_params):
            logger.error("Mainnet risk parameter validation failed.")
            return False
            
        logger.info("All pre-flight checks passed.")
        return True

    def promote_to_mainnet(self, strategy_params: Dict[str, Any]) -> ExchangeConfig:
        """
        Executes the promotion to Mainnet if all checks pass.
        Returns the active configuration to be used by the trading bot.
        """
        if self._promoted:
            logger.warning("Strategy has already been promoted.")
            return self.mainnet_config
            
        if not self.run_pre_flight_checks(strategy_params):
            raise PromotionError("Failed pre-flight checks. Cannot promote to mainnet.")
            
        self._promoted = True
        logger.info("SUCCESS: Strategy promoted to Mainnet.")
        return self.mainnet_config

    @property
    def is_promoted(self) -> bool:
        return self._promoted
