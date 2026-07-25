import numpy as np
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class MlTcaBacktesterConfig:
    bps_cost_half_turn: float = 5.0  # Cost per execution (e.g., 5 bps)
    signal_threshold: float = 0.001  # Minimum expected return to enter a trade

class MlTcaBacktester:
    """
    Evaluates Machine Learning signals against transaction costs.
    Calculates position turnover, applies cost drag, and outputs gross vs net performance.
    """
    def __init__(self, config: MlTcaBacktesterConfig):
        self.config = config
        self.cost_rate = self.config.bps_cost_half_turn / 10000.0

    def process(self, predictions: np.ndarray, actual_returns: np.ndarray) -> Dict[str, Any]:
        """
        :param predictions: 1D array of ML predicted returns for time t.
        :param actual_returns: 1D array of actual asset returns for time t+1.
        :return: Dictionary containing gross and net performance metrics.
        """
        if len(predictions) != len(actual_returns):
            raise ValueError("Predictions and actual_returns must have the same length.")

        n_periods = len(predictions)
        if n_periods == 0:
            return {}

        # 1. Thresholding: Generate target positions (+1 for Long, -1 for Short, 0 for Flat)
        positions = np.zeros(n_periods)
        positions[predictions >= self.config.signal_threshold] = 1.0
        positions[predictions <= -self.config.signal_threshold] = -1.0

        # 2. Calculate Gross Returns
        gross_returns = positions * actual_returns

        # 3. Calculate Turnover and Cost Drag
        # Shift positions by 1 to compare t with t-1. Assumes starting flat (0).
        pos_shifted = np.roll(positions, 1)
        pos_shifted[0] = 0.0
        
        # Absolute change in position size
        turnover = np.abs(positions - pos_shifted)
        
        # Transaction costs
        costs = turnover * self.cost_rate
        
        # 4. Net Returns
        net_returns = gross_returns - costs

        # Summary Statistics
        cum_gross = np.prod(1 + gross_returns) - 1
        cum_net = np.prod(1 + net_returns) - 1
        
        total_trades = np.sum(turnover > 0)
        
        return {
            "Total Gross Return": float(cum_gross),
            "Total Net Return": float(cum_net),
            "Total Turnover (Units)": float(np.sum(turnover)),
            "Total Trade Count": int(total_trades),
            "Cost Drag (%)": float((cum_gross - cum_net) * 100),
            "Net Returns Series": net_returns.tolist(),
            "Gross Returns Series": gross_returns.tolist()
        }
