"""
adversarial-robustness-of-trading-signals: Evaluates the robustness of ML trading
signals against epsilon-bounded adversarial perturbations (simulating evasion attacks or market noise).
"""
import dataclasses
import logging
import numpy as np
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class AdversarialRobustnessConfig:
    epsilon: float = 0.01             # Maximum perturbation magnitude (e.g. 1% of feature range)
    flip_tolerance_pct: float = 5.0   # Max acceptable % of signals that flip before failing
    noise_type: str = "uniform"       # 'uniform' or 'worst_case_sign'


@dataclasses.dataclass
class RobustnessReport:
    total_samples: int
    flipped_signals: int
    vulnerability_score_pct: float
    is_robust: bool
    message: str


class SignalAdversarialTester:
    """
    Tests an ML model's prediction stability by injecting epsilon-bounded adversarial noise.
    """
    def __init__(self, config: AdversarialRobustnessConfig):
        self.config = config

    def _inject_noise(self, X: np.ndarray) -> np.ndarray:
        """Injects epsilon-bounded noise into the feature matrix X."""
        # Calculate feature ranges (max - min) to scale epsilon appropriately per feature
        feature_ranges = np.ptp(X, axis=0)
        # Handle zero variance features
        feature_ranges[feature_ranges == 0] = 1.0 
        
        if self.config.noise_type == "uniform":
            # Random noise uniformly distributed between [-epsilon, epsilon] scaled by feature range
            noise = np.random.uniform(
                low=-self.config.epsilon, 
                high=self.config.epsilon, 
                size=X.shape
            ) * feature_ranges
            
        elif self.config.noise_type == "worst_case_sign":
            # Simulates worst-case directional noise (FGSM-lite without explicit gradients)
            # Randomly pushes features to their extreme epsilon boundaries
            signs = np.random.choice([-1, 1], size=X.shape)
            noise = signs * self.config.epsilon * feature_ranges
        else:
            raise ValueError(f"Unknown noise_type: {self.config.noise_type}")
            
        return X + noise

    def evaluate_model(self, model_predict_func: Callable[[np.ndarray], np.ndarray], X_clean: np.ndarray) -> RobustnessReport:
        """
        Evaluates the model's robustness by comparing predictions on clean vs adversarial data.
        
        :param model_predict_func: A callable (like model.predict) that takes X and returns predictions.
        :param X_clean: 2D numpy array of features.
        """
        # 1. Baseline predictions
        y_clean = model_predict_func(X_clean)
        
        # 2. Generate adversarial features
        X_adv = self._inject_noise(X_clean)
        
        # 3. Adversarial predictions
        y_adv = model_predict_func(X_adv)
        
        # 4. Measure vulnerability (Signal Flips)
        flips = np.sum(y_clean != y_adv)
        total = len(y_clean)
        vuln_score_pct = (flips / total) * 100.0
        
        is_robust = vuln_score_pct <= self.config.flip_tolerance_pct
        
        if not is_robust:
            msg = f"VULNERABLE: {vuln_score_pct:.2f}% of trading signals flipped (Tolerance: {self.config.flip_tolerance_pct:.2f}%)."
            logger.warning(msg)
        else:
            msg = f"ROBUST: Only {vuln_score_pct:.2f}% of signals flipped under adversarial perturbation."
            logger.info(msg)
            
        return RobustnessReport(
            total_samples=total,
            flipped_signals=int(flips),
            vulnerability_score_pct=round(vuln_score_pct, 2),
            is_robust=is_robust,
            message=msg
        )
