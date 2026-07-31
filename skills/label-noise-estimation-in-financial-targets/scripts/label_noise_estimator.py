import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class LabelNoiseReport:
    total_samples_count: int
    mislabeled_samples_count: int
    estimated_noise_ratio_pct: float     # Percentage of mislabeled target noise (e.g. 15.0%)
    class_thresholds: Dict[int, float]  # Class-specific confidence thresholds t_k
    mislabeled_indices: List[int]       # List of sample indices identified as noise
    noise_transition_matrix: List[List[float]] # 2x2 C_given_true matrix
    status: str                         # 'TARGET_NOISE_CLEANED', 'HIGH_LABEL_NOISE_WARNING'
    audit_notes: str

class LabelNoiseEstimatorEngine:
    """
    Quantitative machine learning noise estimation engine implementing Confident Learning (Cleanlab framework),
    computing noise transition matrices, identifying mislabeled financial targets, and generating clean sample weights.
    """
    def __init__(self, high_noise_threshold_pct: float = 20.0):
        self.high_noise_threshold_pct = high_noise_threshold_pct

    def estimate_label_noise(
        self,
        y_observed: List[int],
        probs_class_1: List[float]
    ) -> Tuple[LabelNoiseReport, List[int], List[float]]:
        """
        Estimates target label noise ratio, identifies mislabeled samples via Confident Learning,
        and outputs cleaned targets y_clean and sample weights W.
        """
        if len(y_observed) != len(probs_class_1):
            raise ValueError(f"Mismatch: len(y_observed)={len(y_observed)} vs len(probs_class_1)={len(probs_class_1)}")

        n = len(y_observed)
        if n == 0:
            raise ValueError("Target dataset cannot be empty.")

        # 1. Compute Class-Specific Confidence Thresholds t_0 and t_1
        c0_probs = [probs_class_1[i] for i in range(n) if y_observed[i] == 0]
        c1_probs = [probs_class_1[i] for i in range(n) if y_observed[i] == 1]

        # t_0: Expected probability of class 0 for samples with observed y = 0
        t0 = sum(1.0 - p for p in c0_probs) / float(len(c0_probs)) if c0_probs else 0.5
        # t_1: Expected probability of class 1 for samples with observed y = 1
        t1 = sum(p for p in c1_probs) / float(len(c1_probs)) if c1_probs else 0.5

        thresholds = {0: round(t0, 4), 1: round(t1, 4)}

        # 2. Confident Learning Error Identification
        mislabeled_indices: List[int] = []
        y_clean = list(y_observed)
        sample_weights = [1.0] * n

        # Transition matrix counts: [ [C_00, C_01], [C_10, C_11] ]
        # Row = Given Label, Col = True Latent Label
        matrix_counts = [[0, 0], [0, 0]]

        for i in range(n):
            y_given = y_observed[i]
            p1 = probs_class_1[i]
            p0 = 1.0 - p1

            is_error = False
            if y_given == 0 and p1 >= t1:
                # Given 0, but model is confident it is 1 -> Mislabeled!
                is_error = True
                true_label = 1
            elif y_given == 1 and p0 >= t0:
                # Given 1, but model is confident it is 0 -> Mislabeled!
                is_error = True
                true_label = 0
            else:
                true_label = y_given

            matrix_counts[y_given][true_label] += 1

            if is_error:
                mislabeled_indices.append(i)
                y_clean[i] = true_label
                sample_weights[i] = 0.0 # Down-weight / prune noisy sample

        # 3. Calculate Noise Ratio & Transition Matrix
        noise_count = len(mislabeled_indices)
        noise_ratio_pct = round((noise_count / float(n)) * 100.0, 2)

        # Normalize Transition Matrix
        transition_matrix = [[0.0, 0.0], [0.0, 0.0]]
        for r in range(2):
            row_sum = sum(matrix_counts[r])
            if row_sum > 0:
                transition_matrix[r][0] = round(matrix_counts[r][0] / float(row_sum), 4)
                transition_matrix[r][1] = round(matrix_counts[r][1] / float(row_sum), 4)

        if noise_ratio_pct > self.high_noise_threshold_pct:
            status = "HIGH_LABEL_NOISE_WARNING"
            notes = (
                f"HIGH LABEL NOISE WARNING: Estimated target noise ratio = {noise_ratio_pct:.1f}% "
                f"({noise_count:,}/{n:,} mislabeled samples) exceeds threshold ({self.high_noise_threshold_pct:.1f}%). "
                f"Thresholds: t0={t0:.3f}, t1={t1:.3f}. Pruned noisy samples."
            )
            logger.warning(notes)
        else:
            status = "TARGET_NOISE_CLEANED"
            notes = (
                f"TARGET NOISE CLEANED: Identified {noise_count:,}/{n:,} mislabeled samples "
                f"(Estimated Noise Ratio = {noise_ratio_pct:.1f}%). Thresholds: t0={t0:.3f}, t1={t1:.3f}."
            )
            logger.info(notes)

        report = LabelNoiseReport(
            total_samples_count=n,
            mislabeled_samples_count=noise_count,
            estimated_noise_ratio_pct=noise_ratio_pct,
            class_thresholds=thresholds,
            mislabeled_indices=mislabeled_indices,
            noise_transition_matrix=transition_matrix,
            status=status,
            audit_notes=notes
        )

        return report, y_clean, sample_weights
