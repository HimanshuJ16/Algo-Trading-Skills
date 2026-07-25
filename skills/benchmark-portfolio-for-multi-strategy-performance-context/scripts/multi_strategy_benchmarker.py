import numpy as np
from typing import Dict, Any

class MultiStrategyBenchmarker:
    """
    Evaluates a multi-strategy portfolio against a benchmark.
    Isolates Alpha (skill) from Beta (market exposure) and computes Tracking Error and Information Ratio.
    """
    def __init__(self, risk_free_rate_annual: float = 0.04, periods_per_year: int = 252):
        self.rf_annual = risk_free_rate_annual
        self.ppy = periods_per_year
        self.rf_daily = self.rf_annual / self.ppy

    def evaluate_performance(self, portfolio_returns: np.ndarray, benchmark_returns: np.ndarray) -> Dict[str, Any]:
        """
        :param portfolio_returns: 1D array of daily portfolio returns.
        :param benchmark_returns: 1D array of daily benchmark returns over the exact same period.
        :return: Dictionary of benchmarking statistics.
        """
        if len(portfolio_returns) != len(benchmark_returns):
            raise ValueError("Portfolio and benchmark return arrays must be the same length.")
        
        if len(portfolio_returns) < 2:
            return {}

        port = np.array(portfolio_returns)
        bench = np.array(benchmark_returns)

        # 1. Beta Calculation
        cov_matrix = np.cov(port, bench)
        bench_var = np.var(bench, ddof=1)
        beta = cov_matrix[0, 1] / bench_var if bench_var > 1e-8 else 0.0

        # 2. Correlation
        if np.std(port) > 1e-8 and np.std(bench) > 1e-8:
            corr = np.corrcoef(port, bench)[0, 1]
        else:
            corr = 0.0

        # 3. Annualized Alpha (Jensen's Alpha)
        # alpha = (Rp - Rf) - Beta * (Rb - Rf)
        port_ann_return = np.mean(port) * self.ppy
        bench_ann_return = np.mean(bench) * self.ppy
        
        alpha_annual = (port_ann_return - self.rf_annual) - beta * (bench_ann_return - self.rf_annual)

        # 4. Tracking Error
        active_returns = port - bench
        tracking_error_ann = np.std(active_returns, ddof=1) * np.sqrt(self.ppy)

        # 5. Information Ratio
        # IR = (Port_Return - Bench_Return) / Tracking_Error
        active_return_ann = port_ann_return - bench_ann_return
        ir = active_return_ann / tracking_error_ann if tracking_error_ann > 1e-8 else 0.0

        return {
            "Beta": float(round(beta, 4)),
            "Correlation": float(round(corr, 4)),
            "Annualized Alpha": float(round(alpha_annual, 4)),
            "Tracking Error (Ann)": float(round(tracking_error_ann, 4)),
            "Information Ratio": float(round(ir, 4)),
            "Active Return (Ann)": float(round(active_return_ann, 4))
        }
