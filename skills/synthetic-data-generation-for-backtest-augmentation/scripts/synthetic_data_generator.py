import numpy as np
from dataclasses import dataclass

@dataclass
class GBMConfig:
    mu: float
    sigma: float
    S0: float
    dt: float = 1.0 / 252.0
    steps: int = 252

class SyntheticDataGenerator:
    def __init__(self, seed: int = 42):
        self.rng = np.random.default_rng(seed)

    def generate_gbm(self, config: GBMConfig) -> np.ndarray:
        paths = np.zeros(config.steps + 1)
        paths[0] = config.S0
        for t in range(1, config.steps + 1):
            z = self.rng.standard_normal()
            paths[t] = paths[t - 1] * np.exp(
                (config.mu - 0.5 * config.sigma ** 2) * config.dt + config.sigma * np.sqrt(config.dt) * z
            )
        return paths

    def bootstrap_returns(self, historical_returns: np.ndarray, steps: int) -> np.ndarray:
        # Simple bootstrap with replacement
        sampled_returns = self.rng.choice(historical_returns, size=steps, replace=True)
        return sampled_returns

    def block_bootstrap_returns(self, historical_returns: np.ndarray, steps: int, block_size: int = 5) -> np.ndarray:
        n = len(historical_returns)
        if n < block_size:
            raise ValueError("Historical returns shorter than block size")
        
        blocks = []
        while sum(len(b) for b in blocks) < steps:
            start = self.rng.integers(0, n - block_size + 1)
            blocks.append(historical_returns[start:start+block_size])
            
        sampled = np.concatenate(blocks)[:steps]
        return sampled
