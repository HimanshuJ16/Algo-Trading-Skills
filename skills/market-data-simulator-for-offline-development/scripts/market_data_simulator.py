import math
import random
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class SimulationConfig:
    symbol: str                         # e.g. 'AAPL', 'BTC-USD'
    initial_price: float                # Starting mid-price (e.g. 100.0)
    drift_mu: float = 0.05              # Annualized drift mu (5%)
    volatility_sigma: float = 0.20      # Annualized volatility sigma (20%)
    time_step_sec: float = 1.0          # Time step per tick (1 second)
    num_steps: int = 1000               # Number of ticks to generate
    spread_bps: float = 10.0            # Bid-ask spread in basis points (10 bps = 0.10%)
    start_timestamp_epoch: float = field(default_factory=time.time)
    random_seed: Optional[int] = 42

@dataclass
class SimulatedTick:
    sequence_id: int
    symbol: str
    timestamp_epoch: float
    bid_price: float
    ask_price: float
    last_price: float
    bid_volume: float
    ask_volume: float

@dataclass
class SimulationReport:
    symbol: str
    total_ticks_generated: int
    initial_price: float
    final_price: float
    min_price: float
    max_price: float
    total_simulated_duration_sec: float
    random_seed: Optional[int]
    ticks: List[SimulatedTick]
    status: str                         # 'SIMULATION_COMPLETED'
    audit_notes: str

class MarketDataSimulatorEngine:
    """
    Synthetic market data generation engine using Geometric Brownian Motion (GBM)
    and bid-ask spread synthesis to produce offline tick and order book feeds for sandbox strategy development.
    """
    def __init__(self):
        pass

    def generate_synthetic_tick_stream(self, config: SimulationConfig) -> SimulationReport:
        """
        Generates deterministic synthetic tick stream using log-normal Geometric Brownian Motion (GBM).
        """
        if config.initial_price <= 0.0:
            raise ValueError("Initial price must be strictly positive.")
        if config.num_steps <= 0:
            raise ValueError("Number of simulation steps must be positive.")

        if config.random_seed is not None:
            random.seed(config.random_seed)

        # Convert annual parameters to per-tick step dt
        # Assuming 252 trading days/yr * 6.5 hours/day * 3600 sec/hour = ~5,896,800 sec/yr
        trading_sec_per_year = 5_896_800.0
        dt = config.time_step_sec / trading_sec_per_year

        drift_term = (config.drift_mu - 0.5 * (config.volatility_sigma ** 2)) * dt
        vol_term = config.volatility_sigma * math.sqrt(dt)

        half_spread_pct = (config.spread_bps / 10000.0) / 2.0

        current_price = config.initial_price
        curr_ts = config.start_timestamp_epoch

        ticks: List[SimulatedTick] = []
        min_p = current_price
        max_p = current_price

        for i in range(1, config.num_steps + 1):
            # Box-Muller transformation or Gaussian random draw
            z = random.gauss(0.0, 1.0)
            
            # Geometric Brownian Motion step: S_{t+1} = S_t * exp(drift_term + vol_term * Z)
            current_price = current_price * math.exp(drift_term + vol_term * z)
            current_price = round(current_price, 4)

            min_p = min(min_p, current_price)
            max_p = max(max_p, current_price)

            half_spread = round(current_price * half_spread_pct, 4)
            bid = round(current_price - half_spread, 4)
            ask = round(current_price + half_spread, 4)

            # Synthesize bid/ask volume depth using uniform distributions
            bid_vol = round(random.uniform(10.0, 500.0), 2)
            ask_vol = round(random.uniform(10.0, 500.0), 2)

            ticks.append(
                SimulatedTick(
                    sequence_id=i,
                    symbol=config.symbol,
                    timestamp_epoch=round(curr_ts, 3),
                    bid_price=bid,
                    ask_price=ask,
                    last_price=current_price,
                    bid_volume=bid_vol,
                    ask_volume=ask_vol
                )
            )

            curr_ts += config.time_step_sec

        total_duration = config.num_steps * config.time_step_sec
        notes = (
            f"MARKET DATA SIMULATION COMPLETE [{config.symbol}]: Generated {config.num_steps:,} ticks "
            f"over {total_duration:.1f}s simulated time. Price: Start=${config.initial_price:.2f}, "
            f"End=${current_price:.2f}, Min=${min_p:.2f}, Max=${max_p:.2f}. Seed={config.random_seed}."
        )
        logger.info(notes)

        return SimulationReport(
            symbol=config.symbol,
            total_ticks_generated=len(ticks),
            initial_price=config.initial_price,
            final_price=current_price,
            min_price=min_p,
            max_price=max_p,
            total_simulated_duration_sec=total_duration,
            random_seed=config.random_seed,
            ticks=ticks,
            status="SIMULATION_COMPLETED",
            audit_notes=notes
        )
