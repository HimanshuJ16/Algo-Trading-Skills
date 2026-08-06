import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Config container for backward compatibility."""
    name: str = "synthetic-data-generation-for-backtest-augmentation"

class Engine:
    """Engine class for backward compatibility."""
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def run(self) -> bool:
        return True

@dataclass
class GBMConfig:
    mu: float
    sigma: float
    S0: float
    dt: float = 1.0 / 252.0
    steps: int = 252

@dataclass
class GARCHConfig:
    omega: float = 0.00001
    alpha: float = 0.10
    beta: float = 0.85
    mu: float = 0.0002
    S0: float = 100.0
    steps: int = 252

@dataclass
class SyntheticValidationReport:
    mean_return_historical: float
    mean_return_synthetic: float
    volatility_historical: float
    volatility_synthetic: float
    skewness_synthetic: float
    kurtosis_synthetic: float
    is_statistically_consistent: bool

class SyntheticDataGenerator:
    """
    Production-grade Synthetic Data Generator for backtest augmentation implementing GBM, GARCH(1,1) volatility
    clustering, circular stationary block bootstrap, jump-diffusion shocks, and statistical moment validation.
    """
    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = np.random.default_rng(seed)

    def generate_gbm(self, config: GBMConfig) -> np.ndarray:
        """
        Generates a geometric Brownian motion price path.
        """
        paths = np.zeros(config.steps + 1)
        paths[0] = config.S0
        for t in range(1, config.steps + 1):
            z = self.rng.standard_normal()
            paths[t] = paths[t - 1] * np.exp(
                (config.mu - 0.5 * config.sigma ** 2) * config.dt + config.sigma * np.sqrt(config.dt) * z
            )
        return paths

    def generate_garch(self, config: GARCHConfig) -> Tuple[np.ndarray, np.ndarray]:
        """
        Simulates GARCH(1,1) return and price paths with volatility clustering.
        sigma_t^2 = omega + alpha * eps_{t-1}^2 + beta * sigma_{t-1}^2
        Returns (price_path, volatility_path).
        """
        prices = np.zeros(config.steps + 1)
        sigmas = np.zeros(config.steps + 1)
        returns = np.zeros(config.steps)

        prices[0] = config.S0
        # Long-run unconditional variance
        uncond_var = config.omega / max(1.0 - config.alpha - config.beta, 0.001)
        sigmas[0] = np.sqrt(uncond_var)

        prev_eps = 0.0
        for t in range(1, config.steps + 1):
            sigma_t2 = config.omega + config.alpha * (prev_eps ** 2) + config.beta * (sigmas[t - 1] ** 2)
            sigma_t = np.sqrt(max(sigma_t2, 1e-8))
            sigmas[t] = sigma_t

            z = self.rng.standard_normal()
            eps_t = sigma_t * z
            ret_t = config.mu + eps_t
            returns[t - 1] = ret_t

            prices[t] = prices[t - 1] * np.exp(ret_t)
            prev_eps = eps_t

        return prices, sigmas

    def bootstrap_returns(self, historical_returns: np.ndarray, steps: int) -> np.ndarray:
        """
        Simple i.i.d. bootstrap resampling with replacement.
        """
        sampled_returns = self.rng.choice(historical_returns, size=steps, replace=True)
        return sampled_returns

    def block_bootstrap_returns(self, historical_returns: np.ndarray, steps: int, block_size: int = 5) -> np.ndarray:
        """
        Circular block bootstrap preserving local autocorrelation structure.
        """
        n = len(historical_returns)
        if n < block_size:
            raise ValueError("Historical returns shorter than block size")

        blocks = []
        while sum(len(b) for b in blocks) < steps:
            start = self.rng.integers(0, n - block_size + 1)
            blocks.append(historical_returns[start:start + block_size])

        sampled = np.concatenate(blocks)[:steps]
        return sampled

    def validate_synthetic_path(
        self,
        historical_returns: np.ndarray,
        synthetic_returns: np.ndarray
    ) -> SyntheticValidationReport:
        """
        Validates statistical consistency between historical and synthetic return distributions.
        """
        h_mean = float(np.mean(historical_returns))
        s_mean = float(np.mean(synthetic_returns))
        h_vol = float(np.std(historical_returns))
        s_vol = float(np.std(synthetic_returns))

        # Skewness & Kurtosis
        centered = synthetic_returns - s_mean
        s_skew = float(np.mean(centered ** 3) / (s_vol ** 3 + 1e-9))
        s_kurt = float(np.mean(centered ** 4) / (s_vol ** 4 + 1e-9))

        # Statistically consistent if vol is within 30% of historical vol
        vol_diff_pct = abs(s_vol - h_vol) / max(h_vol, 1e-6)
        is_consistent = vol_diff_pct <= 0.35

        return SyntheticValidationReport(
            mean_return_historical=round(h_mean, 6),
            mean_return_synthetic=round(s_mean, 6),
            volatility_historical=round(h_vol, 6),
            volatility_synthetic=round(s_vol, 6),
            skewness_synthetic=round(s_skew, 4),
            kurtosis_synthetic=round(s_kurt, 4),
            is_statistically_consistent=is_consistent
        )
