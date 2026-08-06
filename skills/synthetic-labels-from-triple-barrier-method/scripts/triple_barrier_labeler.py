import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Config container for backward compatibility."""
    name: str = "synthetic-labels-from-triple-barrier-method"
    take_profit_mult: float = 2.0
    stop_loss_mult: float = 1.0
    num_bars_vertical: int = 10

class Engine:
    """Engine class for backward compatibility."""
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def run(self) -> bool:
        return True

class BarrierTouched(IntEnum):
    STOP_LOSS = -1
    VERTICAL_TIMEOUT = 0
    TAKE_PROFIT = 1

@dataclass
class TripleBarrierLabel:
    entry_timestamp: str
    entry_price: float
    exit_timestamp: str
    exit_price: float
    barrier_touched: BarrierTouched
    realized_return: float

class TripleBarrierLabelerEngine:
    """
    Production-grade Triple Barrier Labeler implementation (Marcos López de Prado framework).
    Evaluates take-profit, stop-loss, and vertical time-out barriers using dynamic volatility scaling.
    """
    def __init__(
        self,
        pt_mult: float = 2.0,
        sl_mult: float = 1.0,
        vertical_bars: int = 10,
        volatility_span: int = 20
    ):
        self.pt_mult = pt_mult
        self.sl_mult = sl_mult
        self.vertical_bars = vertical_bars
        self.volatility_span = volatility_span

    def compute_volatility(self, prices: pd.Series) -> pd.Series:
        """
        Computes rolling log return standard deviation as dynamic volatility proxy.
        """
        returns = np.log(prices / prices.shift(1))
        vol = returns.ewm(span=self.volatility_span).std()
        return vol.fillna(0.01)

    def generate_labels(
        self,
        prices: pd.Series,
        events: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Generates synthetic triple barrier labels for prices Series indexed by timestamp/integer index.
        """
        if len(prices) < self.vertical_bars + 1:
            raise ValueError("Price series length must exceed vertical_bars window.")

        volatility = self.compute_volatility(prices)
        records = []

        timestamps = list(prices.index)
        n = len(prices)

        target_indices = range(0, n - self.vertical_bars) if events is None else [
            timestamps.index(e) for e in events if e in timestamps and timestamps.index(e) < n - self.vertical_bars
        ]

        for idx in target_indices:
            t0 = timestamps[idx]
            p0 = float(prices.iloc[idx])
            vol_t = float(volatility.iloc[idx])

            # Horizontal barrier offsets
            upper_barrier = p0 * (1.0 + self.pt_mult * vol_t)
            lower_barrier = p0 * (1.0 - self.sl_mult * vol_t)

            # Vertical barrier index limit
            vert_idx = min(idx + self.vertical_bars, n - 1)

            touched = BarrierTouched.VERTICAL_TIMEOUT
            exit_idx = vert_idx
            exit_p = float(prices.iloc[vert_idx])

            # Scan forward through the holding period window
            for sub_idx in range(idx + 1, vert_idx + 1):
                p_sub = float(prices.iloc[sub_idx])
                if p_sub >= upper_barrier:
                    touched = BarrierTouched.TAKE_PROFIT
                    exit_idx = sub_idx
                    exit_p = p_sub
                    break
                elif p_sub <= lower_barrier:
                    touched = BarrierTouched.STOP_LOSS
                    exit_idx = sub_idx
                    exit_p = p_sub
                    break

            realized_ret = (exit_p - p0) / p0

            records.append({
                "entry_timestamp": t0,
                "entry_price": p0,
                "exit_timestamp": timestamps[exit_idx],
                "exit_price": exit_p,
                "barrier_touched": touched.value,
                "realized_return": realized_ret
            })

        return pd.DataFrame(records)

class TripleBarrierLabeler:
    """
    Legacy wrapper class for backward compatibility.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.engine = TripleBarrierLabelerEngine(
            pt_mult=self.config.take_profit_mult,
            sl_mult=self.config.stop_loss_mult,
            vertical_bars=self.config.num_bars_vertical
        )

    def process(self) -> bool:
        return True
