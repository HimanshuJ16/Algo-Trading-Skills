import numpy as np
from typing import Dict, Any

class StandardizedTearsheetGenerator:
    def __init__(self, risk_free_rate: float = 0.0, periods_per_year: int = 252):
        self.rf = risk_free_rate
        self.ppy = periods_per_year

    def generate(self, returns: np.ndarray) -> Dict[str, Any]:
        if len(returns) == 0:
            return {}
            
        cum_returns = np.cumprod(1 + returns)
        total_return = cum_returns[-1] - 1
        
        # Annualized Return
        ann_return = (1 + total_return) ** (self.ppy / len(returns)) - 1
        
        # Volatility
        vol = np.std(returns, ddof=1) * np.sqrt(self.ppy) if len(returns) > 1 else 0.0
        
        # Sharpe Ratio
        sharpe = (ann_return - self.rf) / vol if vol != 0 else 0.0
        
        # Max Drawdown
        running_max = np.maximum.accumulate(cum_returns)
        drawdowns = (cum_returns - running_max) / running_max
        max_dd = np.min(drawdowns)
        
        # Sortino Ratio
        downside_returns = returns[returns < 0]
        downside_vol = np.std(downside_returns, ddof=1) * np.sqrt(self.ppy) if len(downside_returns) > 1 else 0.0
        sortino = (ann_return - self.rf) / downside_vol if downside_vol != 0 else 0.0
        
        # Calmar Ratio
        calmar = (ann_return - self.rf) / abs(max_dd) if max_dd != 0 else 0.0
        
        # Win Rate
        wins = len(returns[returns > 0])
        hit_rate = wins / len(returns)
        
        # Profit Factor
        gross_profits = np.sum(returns[returns > 0])
        gross_losses = np.abs(np.sum(returns[returns < 0]))
        profit_factor = gross_profits / gross_losses if gross_losses != 0 else float('inf')
        
        # Avg Win / Avg Loss
        avg_win = np.mean(returns[returns > 0]) if wins > 0 else 0.0
        avg_loss = np.mean(returns[returns < 0]) if len(returns[returns < 0]) > 0 else 0.0
        
        return {
            "Total Return": total_return,
            "Annualized Return": ann_return,
            "Annualized Volatility": vol,
            "Sharpe Ratio": sharpe,
            "Sortino Ratio": sortino,
            "Calmar Ratio": calmar,
            "Max Drawdown": max_dd,
            "Hit Rate": hit_rate,
            "Profit Factor": profit_factor,
            "Average Win": avg_win,
            "Average Loss": avg_loss
        }
