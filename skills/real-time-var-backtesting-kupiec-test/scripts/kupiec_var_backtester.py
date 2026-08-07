"""
kupiec_var_backtester: Kupiec POF (Proportion of Failures) Likelihood Ratio test
for VaR model backtesting.
"""
from dataclasses import dataclass
import math
from typing import Optional

try:
    import scipy.stats as st
except ImportError:
    st = None


@dataclass
class KupiecResult:
    p_value: float
    is_rejected: bool
    exceptions: int
    stat: float = 0.0


class KupiecVaRBacktester:
    def __init__(self, confidence_level: float = 0.99, alpha: float = 0.05):
        self.confidence_level = confidence_level
        self.alpha = alpha

    def run_test(self, total_observations: int, exceptions: int) -> KupiecResult:
        if total_observations <= 0:
            return KupiecResult(1.0, False, 0, 0.0)

        p = 1.0 - self.confidence_level

        if st is not None:
            try:
                res = st.binomtest(exceptions, total_observations, p, alternative='two-sided')
                p_val = float(res.pvalue)
            except AttributeError:
                p_val = float(st.binom_test(exceptions, total_observations, p, alternative='two-sided'))
            is_rejected = p_val < self.alpha
            return KupiecResult(p_val, is_rejected, exceptions)

        # Pure Python Kupiec POF Likelihood Ratio test fallback
        x = exceptions
        N = total_observations
        if x == 0:
            lr_stat = -2.0 * (N * math.log(1.0 - p))
        elif x == N:
            lr_stat = -2.0 * (N * math.log(p))
        else:
            pi_hat = x / N
            num = ((1.0 - p) ** (N - x)) * (p ** x)
            den = ((1.0 - pi_hat) ** (N - x)) * (pi_hat ** x)
            if den > 0 and num > 0:
                lr_stat = -2.0 * math.log(num / den)
            else:
                lr_stat = 0.0

        # Critical value for Chi-Square(1) at alpha=0.05 is 3.841459
        is_rejected = lr_stat > 3.841459
        p_val = math.exp(-lr_stat / 2.0)  # Approx p-value under Chi2(1)
        return KupiecResult(p_val, is_rejected, exceptions, stat=lr_stat)
