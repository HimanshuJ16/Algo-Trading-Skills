from dataclasses import dataclass
import scipy.stats as st

@dataclass
class KupiecResult:
    p_value: float
    is_rejected: bool
    exceptions: int

class KupiecVaRBacktester:
    def __init__(self, confidence_level: float = 0.99):
        self.confidence_level = confidence_level

    def run_test(self, total_observations: int, exceptions: int) -> KupiecResult:
        if total_observations == 0:
            return KupiecResult(1.0, False, 0)
        p = 1 - self.confidence_level
        # binomial test
        try:
            res = st.binomtest(exceptions, total_observations, p, alternative='two-sided')
            p_val = res.pvalue
        except AttributeError:
            p_val = st.binom_test(exceptions, total_observations, p, alternative='two-sided')
        is_rejected = p_val < 0.05
        return KupiecResult(float(p_val), is_rejected, exceptions)
