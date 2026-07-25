from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np

@dataclass
class BenchmarkMetrics:
    name: str
    correlation: float
    tracking_error: float
    information_ratio: float

class BenchmarkSelector:
    def __init__(self, benchmark_returns: Dict[str, List[float]]):
        """
        benchmark_returns: dict mapping benchmark name to list of daily returns
        """
        self.benchmark_returns = benchmark_returns

    def evaluate_benchmarks(self, strategy_returns: List[float]) -> List[BenchmarkMetrics]:
        results = []
        s_ret = np.array(strategy_returns)
        
        for name, b_ret_list in self.benchmark_returns.items():
            b_ret = np.array(b_ret_list)
            
            if len(s_ret) != len(b_ret):
                raise ValueError(f"Length mismatch for benchmark {name}")
                
            # Correlation
            if len(s_ret) > 1 and np.std(s_ret) > 0 and np.std(b_ret) > 0:
                corr = np.corrcoef(s_ret, b_ret)[0, 1]
            else:
                corr = 0.0
                
            # Tracking Error (annualized standard deviation of excess returns)
            # Assuming 252 trading days
            excess_returns = s_ret - b_ret
            tracking_error = np.std(excess_returns) * np.sqrt(252)
            
            # Information Ratio
            if tracking_error > 0:
                annualized_excess_return = np.mean(excess_returns) * 252
                ir = annualized_excess_return / tracking_error
            else:
                ir = 0.0
                
            results.append(BenchmarkMetrics(
                name=name,
                correlation=corr,
                tracking_error=tracking_error,
                information_ratio=ir
            ))
            
        # Sort results by correlation descending (highest correlation is often the most appropriate beta match)
        results.sort(key=lambda x: x.correlation, reverse=True)
        return results

    def recommend_benchmark(self, strategy_returns: List[float]) -> Optional[str]:
        metrics = self.evaluate_benchmarks(strategy_returns)
        if not metrics:
            return None
        # Recommend the one with highest correlation (closest beta profile)
        # In reality, you'd want a mix of high correlation but reasonable IR.
        return metrics[0].name
