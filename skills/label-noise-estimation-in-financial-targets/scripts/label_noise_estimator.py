"""Confident Learning label-noise estimation for binary financial targets.

Implements the Confident Learning (CL) estimator of Northcutt, Jiang & Chuang,
"Confident Learning: Estimating Uncertainty in Dataset Labels", Journal of
Artificial Intelligence Research 70 (2021) 1373-1411, restricted to the binary
case (m = 2) that covers Triple-Barrier / Trend-Scanning / fixed-horizon
financial targets.

Specifically this module implements:
  * Eqn 2 - the class-specific self-confidence thresholds t_j.
  * Eqn 1 - the confident joint C[i][j], including the arg max collision-
    handling term and the exclusion of examples that meet no class threshold.
  * Eqn 3 - the calibrated joint Q_hat, from which the noise transition matrix
    P(y_tilde | y_star) and the inverse noise matrix P(y_star | y_tilde) are
    derived.
  * CL method 2 (Sec. 3.2) - label errors are the off-diagonal members of the
    confident joint.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

# Neutral threshold used when a class has no observed samples and its
# expected self-confidence (Eqn 2) is therefore undefined.
_ABSENT_CLASS_THRESHOLD = 0.5

# Tolerance for the `>= t_k` comparison in Eqn 1. t_k is the *mean* of the very
# self-confidences being compared against it, so when a class has near-identical
# probabilities the accumulated rounding error of the summation lifts t_k a few
# ULPs above its own members (e.g. mean([0.99] * 50) == 0.9900000000000003).
# Without tolerance those samples fail the comparison, clear no threshold, and are
# all excluded -- reporting a perfectly clean dataset as 0% noise on zero
# evidence. Compare inclusively instead.
_THRESHOLD_REL_TOL = 1e-9
_THRESHOLD_ABS_TOL = 1e-12


def _clears_threshold(probability: float, threshold: float) -> bool:
    """Returns True if `probability` meets `threshold`, tolerating float error."""
    return probability >= threshold or math.isclose(
        probability, threshold,
        rel_tol=_THRESHOLD_REL_TOL, abs_tol=_THRESHOLD_ABS_TOL)


@dataclass
class LabelNoiseReport:
    """Audit record of a Confident Learning pass over a binary target vector."""

    total_samples_count: int
    mislabeled_samples_count: int
    estimated_noise_ratio_pct: float      # 100 * mislabeled / total, rounded to 2dp
    class_thresholds: Dict[int, float]    # Eqn 2 thresholds t_0, t_1 (rounded to 4dp)
    mislabeled_indices: List[int]         # Off-diagonal members of the confident joint
    # P(y_tilde = i | y_star = j): rows = observed label, cols = true label.
    # Columns sum to 1. This is Q_{y_tilde|y_star} in the paper's notation.
    noise_transition_matrix: List[List[float]]
    status: str                           # 'TARGET_NOISE_CLEANED' | 'HIGH_LABEL_NOISE_WARNING'
    audit_notes: str
    # P(y_star = j | y_tilde = i): rows = observed label, cols = true label.
    # Rows sum to 1. This is Q_{y_star|y_tilde} in the paper's notation.
    inverse_noise_matrix: List[List[float]] = field(
        default_factory=lambda: [[0.0, 0.0], [0.0, 0.0]])
    # Raw confident joint counts C[i][j] (Eqn 1), before calibration.
    confident_joint_counts: List[List[int]] = field(
        default_factory=lambda: [[0, 0], [0, 0]])
    # Calibrated joint Q_hat[i][j] (Eqn 3); sums to 1 over all cells.
    estimated_joint_distribution: List[List[float]] = field(
        default_factory=lambda: [[0.0, 0.0], [0.0, 0.0]])
    # Samples meeting no class threshold; excluded from the confident joint.
    unconfident_samples_count: int = 0


class LabelNoiseEstimatorEngine:
    """Estimates binary target label noise via Confident Learning.

    Args:
        high_noise_threshold_pct: Noise ratio, in percent, at or above which the
            report is flagged ``HIGH_LABEL_NOISE_WARNING``. The comparison is
            inclusive (``>=``), matching ``references/standards.md``.
    """

    def __init__(self, high_noise_threshold_pct: float = 20.0) -> None:
        threshold = float(high_noise_threshold_pct)
        if not math.isfinite(threshold) or not 0.0 <= threshold <= 100.0:
            raise ValueError(
                f"high_noise_threshold_pct must be a finite value in [0, 100], "
                f"got {high_noise_threshold_pct!r}")
        self.high_noise_threshold_pct = threshold

    @staticmethod
    def _validate_inputs(
        y_observed: Sequence[int],
        probs_class_1: Sequence[float],
    ) -> Tuple[List[int], List[float]]:
        """Coerces and strictly validates the observed labels and probabilities.

        Accepts any sequence of integer-valued labels (including numpy scalars)
        and finite probabilities. Rejects out-of-domain labels, NaN/Inf, and
        probabilities outside [0, 1] rather than letting them propagate: an
        out-of-domain label would otherwise index the confident joint silently,
        and a NaN probability would compare False against every threshold and be
        silently reported as a clean sample.
        """
        if len(y_observed) != len(probs_class_1):
            raise ValueError(
                f"Mismatch: len(y_observed)={len(y_observed)} vs "
                f"len(probs_class_1)={len(probs_class_1)}")
        if len(y_observed) == 0:
            raise ValueError("Target dataset cannot be empty.")

        labels: List[int] = []
        for i, raw in enumerate(y_observed):
            try:
                label = int(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"y_observed[{i}]={raw!r} is not an integer label.") from exc
            if label != raw or label not in (0, 1):
                raise ValueError(
                    f"y_observed[{i}]={raw!r} is outside the supported binary "
                    f"label domain {{0, 1}}. This engine implements the binary "
                    f"(m=2) case of Confident Learning only.")
            labels.append(label)

        probs: List[float] = []
        for i, raw in enumerate(probs_class_1):
            try:
                prob = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"probs_class_1[{i}]={raw!r} is not a float.") from exc
            if not math.isfinite(prob):
                raise ValueError(
                    f"probs_class_1[{i}]={raw!r} is not finite. Out-of-fold "
                    f"probabilities must be finite; NaN typically means the "
                    f"sample was never held out by the cross-validation split.")
            if not 0.0 <= prob <= 1.0:
                raise ValueError(
                    f"probs_class_1[{i}]={raw!r} is outside [0, 1]. Pass "
                    f"calibrated class-1 probabilities, not raw model scores.")
            probs.append(prob)

        return labels, probs

    def estimate_label_noise(
        self,
        y_observed: Sequence[int],
        probs_class_1: Sequence[float],
    ) -> Tuple[LabelNoiseReport, List[int], List[float]]:
        """Estimates target label noise and returns the report plus both
        remediation vectors.

        Args:
            y_observed: Observed (noisy) binary labels in {0, 1}.
            probs_class_1: **Out-of-fold** predicted probabilities P(y=1|x).
                In-sample probabilities make the thresholds self-fulfilling and
                systematically understate the noise ratio.

        Returns:
            ``(report, y_clean, sample_weights)``.

            ``y_clean`` and ``sample_weights`` encode two **mutually exclusive**
            remediation strategies for the same confident errors:

            * **Relabel** - train on ``y_clean`` (errors flipped to their
              estimated true label) with uniform weights.
            * **Prune** - train on the original ``y_observed`` with
              ``sample_weights`` (0.0 for confident errors).

            Applying both together zeroes out exactly the samples that were
            relabelled, discarding the relabelling entirely. Pick one.

        Raises:
            ValueError: On length mismatch, empty input, non-binary labels, or
                non-finite / out-of-range probabilities.
        """
        labels, probs = self._validate_inputs(y_observed, probs_class_1)
        n = len(labels)

        # --- Step 1: class-specific self-confidence thresholds t_j (Eqn 2) ----
        # t_j = mean over samples observed as j of the predicted probability of j.
        c0_self_conf = [1.0 - probs[i] for i in range(n) if labels[i] == 0]
        c1_self_conf = [probs[i] for i in range(n) if labels[i] == 1]

        if not c0_self_conf or not c1_self_conf:
            absent = 0 if not c0_self_conf else 1
            logger.warning(
                "Class %d has no observed samples; its threshold t_%d is "
                "undefined (Eqn 2) and falls back to %.2f. Noise estimates on a "
                "single-class target vector are not meaningful.",
                absent, absent, _ABSENT_CLASS_THRESHOLD)

        t0 = (sum(c0_self_conf) / float(len(c0_self_conf))
              if c0_self_conf else _ABSENT_CLASS_THRESHOLD)
        t1 = (sum(c1_self_conf) / float(len(c1_self_conf))
              if c1_self_conf else _ABSENT_CLASS_THRESHOLD)

        thresholds = {0: round(t0, 4), 1: round(t1, 4)}

        # --- Step 2: confident joint with collision handling (Eqn 1) ----------
        mislabeled_indices: List[int] = []
        y_clean = list(labels)
        sample_weights = [1.0] * n
        # matrix_counts[i][j]: observed label i, estimated true label j.
        matrix_counts = [[0, 0], [0, 0]]
        unconfident_count = 0

        for i in range(n):
            y_given = labels[i]
            p1 = probs[i]
            p0 = 1.0 - p1

            # Classes whose predicted probability clears their own threshold.
            qualifying: List[int] = []
            if _clears_threshold(p0, t0):
                qualifying.append(0)
            if _clears_threshold(p1, t1):
                qualifying.append(1)

            if not qualifying:
                # Near-uniform / low-confidence sample. Per Eqn 1 it belongs to
                # no bin of the confident joint and is neither counted nor
                # pruned. This is what makes CL robust to pure noise.
                unconfident_count += 1
                continue

            if len(qualifying) == 1:
                true_label = qualifying[0]
            elif p1 > p0:
                true_label = 1
            elif p0 > p1:
                true_label = 0
            else:
                # Exact collision tie (p0 == p1 == 0.5). The paper does not
                # specify a tie-break; resolve in favour of the observed label so
                # an uninformative model can never manufacture a label error.
                true_label = y_given

            matrix_counts[y_given][true_label] += 1

            if true_label != y_given:
                mislabeled_indices.append(i)
                y_clean[i] = true_label
                sample_weights[i] = 0.0

        # --- Step 3: calibrate the joint and derive both matrices (Eqn 3) -----
        transition_matrix, inverse_matrix, joint = self._estimate_matrices(
            matrix_counts, labels)

        # --- Step 4: noise ratio and status ------------------------------------
        noise_count = len(mislabeled_indices)
        noise_ratio_pct = round((noise_count / float(n)) * 100.0, 2)

        unconfident_note = (
            f" {unconfident_count:,} sample(s) met no class threshold and were "
            f"excluded from the confident joint."
            if unconfident_count else "")

        # Inclusive comparison: standards.md mandates a warning at eta >= threshold.
        # Note the confident joint can never be empty: t_k is the mean of its own
        # class's self-confidences, so that class's most-confident sample always
        # clears it. At least one sample per non-empty class is therefore counted.
        if noise_ratio_pct >= self.high_noise_threshold_pct:
            status = "HIGH_LABEL_NOISE_WARNING"
            notes = (
                f"HIGH LABEL NOISE WARNING: Estimated target noise ratio = {noise_ratio_pct:.1f}% "
                f"({noise_count:,}/{n:,} mislabeled samples) meets or exceeds threshold "
                f"({self.high_noise_threshold_pct:.1f}%). "
                f"Thresholds: t0={t0:.3f}, t1={t1:.3f}.{unconfident_note}"
            )
            logger.warning(notes)
        else:
            status = "TARGET_NOISE_CLEANED"
            notes = (
                f"TARGET NOISE CLEANED: Identified {noise_count:,}/{n:,} mislabeled samples "
                f"(Estimated Noise Ratio = {noise_ratio_pct:.1f}%). "
                f"Thresholds: t0={t0:.3f}, t1={t1:.3f}.{unconfident_note}"
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
            audit_notes=notes,
            inverse_noise_matrix=inverse_matrix,
            confident_joint_counts=[row[:] for row in matrix_counts],
            estimated_joint_distribution=joint,
            unconfident_samples_count=unconfident_count,
        )

        return report, y_clean, sample_weights

    @staticmethod
    def _estimate_matrices(
        matrix_counts: List[List[int]],
        labels: List[int],
    ) -> Tuple[List[List[float]], List[List[float]], List[List[float]]]:
        """Calibrates the confident joint (Eqn 3) and derives both noise matrices.

        Calibration rescales each row of the confident joint back to the observed
        class count ``|X_{y_tilde=i}|`` before normalising to a distribution.
        Without it, a class that lost more samples to the no-threshold exclusion
        would be under-represented and the column-normalised transition matrix
        would be biased.

        Returns:
            ``(noise_transition_matrix, inverse_noise_matrix, joint)`` where the
            transition matrix is P(y_tilde=i | y_star=j) (columns sum to 1) and
            the inverse noise matrix is P(y_star=j | y_tilde=i) (rows sum to 1).
        """
        class_counts = [0, 0]
        for label in labels:
            class_counts[label] += 1

        # Row-calibrated counts: (C[i][j] / sum_j C[i][j]) * |X_{y_tilde=i}|
        calibrated = [[0.0, 0.0], [0.0, 0.0]]
        for i in range(2):
            row_sum = float(sum(matrix_counts[i]))
            if row_sum <= 0.0:
                continue
            for j in range(2):
                calibrated[i][j] = (matrix_counts[i][j] / row_sum) * class_counts[i]

        grand_total = sum(sum(row) for row in calibrated)
        joint = [[0.0, 0.0], [0.0, 0.0]]
        if grand_total > 0.0:
            for i in range(2):
                for j in range(2):
                    joint[i][j] = calibrated[i][j] / grand_total

        # P(y_star = j | y_tilde = i): normalise each row.
        inverse_matrix = [[0.0, 0.0], [0.0, 0.0]]
        for i in range(2):
            row_sum = sum(joint[i])
            if row_sum > 0.0:
                for j in range(2):
                    inverse_matrix[i][j] = round(joint[i][j] / row_sum, 4)

        # P(y_tilde = i | y_star = j): normalise each column.
        transition_matrix = [[0.0, 0.0], [0.0, 0.0]]
        for j in range(2):
            col_sum = sum(joint[i][j] for i in range(2))
            if col_sum > 0.0:
                for i in range(2):
                    transition_matrix[i][j] = round(joint[i][j] / col_sum, 4)

        joint = [[round(value, 6) for value in row] for row in joint]
        return transition_matrix, inverse_matrix, joint
