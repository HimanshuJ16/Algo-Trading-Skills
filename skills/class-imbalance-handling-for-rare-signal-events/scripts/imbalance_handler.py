"""Class-imbalance utilities for models that predict rare financial events.

Two families of correction are supported:

* Cost-sensitive learning -- ``compute_class_weights`` (multi-class, for
  estimators taking a ``class_weight`` mapping) and ``compute_scale_pos_weight``
  (binary, for the scalar ``scale_pos_weight`` parameter of gradient-boosting
  libraries). Neither discards data.
* Random undersampling -- ``random_undersample``, which drops majority-class
  rows, plus ``correct_undersampling_bias`` to undo the prior shift that
  undersampling introduces into predicted probabilities.

Both families change the class prior the model sees, so raw predicted
probabilities are no longer calibrated to the real-world event rate. Anything
that turns a probability into a position size or an expected value must be fed
calibrated probabilities -- see ``correct_undersampling_bias``.
"""
import logging
from typing import Dict, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class ImbalanceHandler:
    """
    Utility to handle highly imbalanced datasets for rare financial events.
    Supports calculating class weights (for cost-sensitive learning), random
    undersampling (for reducing majority class dominance), and correcting the
    probability bias that undersampling introduces.

    All methods are stateless and deterministic given their arguments; no global
    NumPy random state is mutated.
    """

    @staticmethod
    def _validate_labels(y: np.ndarray) -> np.ndarray:
        """
        Validates a label array and returns it as a 1-D integer array.

        Rejects empty arrays, arrays with more than one dimension, non-numeric
        dtypes, NaN/inf, and non-integral floats. NaN in particular must never be
        accepted silently: ``np.unique`` can treat NaN values as distinct
        classes, which inflates ``n_classes`` and silently deflates every
        computed weight.
        """
        y_arr = np.asarray(y)

        if y_arr.dtype.kind not in "biuf":
            raise ValueError(
                f"Target array y must have a numeric or boolean dtype, got {y_arr.dtype!r}. "
                "Encode string/categorical labels to integers first.")
        if y_arr.ndim != 1:
            raise ValueError(f"Target array y must be 1-dimensional, got shape {y_arr.shape}.")
        if y_arr.size == 0:
            raise ValueError("Target array y is empty.")

        if y_arr.dtype.kind == "f":
            if not np.all(np.isfinite(y_arr)):
                raise ValueError("Target array y contains NaN or infinite values.")
            if not np.all(np.equal(np.mod(y_arr, 1), 0)):
                raise ValueError(
                    "Target array y contains non-integral values; class labels must be integers.")

        return y_arr.astype(np.int64, copy=False)

    @staticmethod
    def compute_class_weights(y: np.ndarray) -> Dict[int, float]:
        """
        Calculates class weights using the scikit-learn ``class_weight='balanced'``
        formula: ``n_samples / (n_classes * np.bincount(y))``.

        The weighted sample count sums back to ``n_samples``, so the effective
        dataset size is unchanged while each class contributes equally.

        Returns a ``{int label: float weight}`` mapping suitable for estimators
        that accept a ``class_weight`` dict (e.g. scikit-learn's
        ``RandomForestClassifier``). Gradient-boosting libraries that expect the
        scalar ``scale_pos_weight`` instead need ``compute_scale_pos_weight``.

        y must contain integer class labels (0, 1, 2, ...).
        """
        y_arr = ImbalanceHandler._validate_labels(y)

        classes, counts = np.unique(y_arr, return_counts=True)
        n_classes = len(classes)
        n_samples = y_arr.size

        weights = {
            int(c): float(n_samples / (n_classes * count))
            for c, count in zip(classes, counts)
        }

        logger.info(f"Computed class weights over {n_samples} samples: {weights}")
        return weights

    @staticmethod
    def compute_scale_pos_weight(y: np.ndarray, positive_class: int = 1) -> float:
        """
        Calculates the scalar imbalance ratio ``negative_count / positive_count``
        for binary targets.

        This is the value XGBoost documents for ``scale_pos_weight`` ("A typical
        value to consider: sum(negative instances) / sum(positive instances)").
        It is NOT the dict returned by ``compute_class_weights`` -- passing that
        dict to ``scale_pos_weight`` is a type error, and passing this scalar to
        a scikit-learn ``class_weight`` is meaningless.

        Every label not equal to ``positive_class`` counts as negative, so the
        method is well defined for {0, 1} targets and for any binary encoding.
        """
        y_arr = ImbalanceHandler._validate_labels(y)

        classes = np.unique(y_arr)
        if len(classes) > 2:
            raise ValueError(
                "compute_scale_pos_weight requires a binary target; found "
                f"{len(classes)} classes: {classes.tolist()}.")

        positive_count = int(np.sum(y_arr == positive_class))
        negative_count = int(y_arr.size - positive_count)
        if positive_count == 0:
            raise ValueError(
                f"No samples of positive_class={positive_class} present; scale_pos_weight is undefined.")

        ratio = negative_count / positive_count
        logger.info(
            f"scale_pos_weight = {negative_count} negatives / {positive_count} positives = {ratio:.4f}")
        return float(ratio)

    @staticmethod
    def random_undersample(
        X: np.ndarray,
        y: np.ndarray,
        random_state: int = 42,
        majority_ratio: float = 1.0,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Balances a binary classification dataset by randomly undersampling the
        majority class down to ``majority_ratio * minority_count`` rows.

        X: Feature matrix (rows aligned with y along the first axis)
        y: Target array (1-D, exactly two distinct integer classes)
        random_state: seed for a local ``np.random.default_rng`` -- the global
            NumPy random state is deliberately left untouched, so calling this
            method cannot perturb model initialisation or splits elsewhere in the
            pipeline.
        majority_ratio: majority-to-minority ratio to retain. 1.0 gives parity;
            2.0 keeps twice as many majority rows as minority rows. If the
            majority class holds fewer rows than the target, every majority row
            is kept (no oversampling is ever performed).

        Rows are returned in their original order, so a chronologically sorted
        input stays chronologically sorted.

        Apply this to the training split ONLY. The resulting model's predicted
        probabilities are shifted towards the minority class; use
        ``correct_undersampling_bias`` before thresholding or sizing on them.
        """
        y_arr = ImbalanceHandler._validate_labels(y)
        X_arr = np.asarray(X)

        if X_arr.ndim < 1:
            raise ValueError("X must have at least one dimension whose first axis indexes samples.")
        if X_arr.shape[0] != y_arr.size:
            raise ValueError(
                f"X and y must have the same number of rows, got {X_arr.shape[0]} and {y_arr.size}.")
        if not np.isfinite(majority_ratio) or majority_ratio <= 0:
            raise ValueError(f"majority_ratio must be a positive finite number, got {majority_ratio!r}.")

        classes, counts = np.unique(y_arr, return_counts=True)
        if len(classes) != 2:
            raise NotImplementedError(
                "random_undersample currently only supports binary classification "
                f"(2 classes), found {len(classes)}.")

        # argmin and argmax both return index 0 on a tie, which would otherwise
        # make minority_class == majority_class and drop one class entirely.
        minority_idx = int(np.argmin(counts))
        majority_idx = 1 - minority_idx
        minority_class = int(classes[minority_idx])
        majority_class = int(classes[majority_idx])
        minority_count = int(counts[minority_idx])
        majority_count = int(counts[majority_idx])

        target_majority = min(majority_count, int(np.floor(minority_count * majority_ratio)))
        if target_majority < 1:
            raise ValueError(
                f"majority_ratio={majority_ratio} with {minority_count} minority sample(s) "
                "would retain zero majority samples.")

        minority_indices = np.where(y_arr == minority_class)[0]
        majority_indices = np.where(y_arr == majority_class)[0]

        rng = np.random.default_rng(random_state)
        sampled_majority_indices = rng.choice(majority_indices, size=target_majority, replace=False)

        logger.info(
            f"Undersampled majority class {majority_class} from {majority_count} to {target_majority} rows "
            f"against {minority_count} minority ({minority_class}) rows; "
            f"retention beta = {target_majority / majority_count:.6f}")

        # Sort to restore the original (e.g. chronological) row order.
        balanced_indices = np.sort(np.concatenate([minority_indices, sampled_majority_indices]))
        return X_arr[balanced_indices], y_arr[balanced_indices]

    @staticmethod
    def correct_undersampling_bias(probabilities: np.ndarray, beta: float) -> np.ndarray:
        """
        Removes the prior shift that majority-class undersampling introduces into
        predicted minority-class probabilities.

        ``beta`` is the fraction of majority-class rows retained by the
        undersampling step (``kept_majority / original_majority``); minority rows
        are assumed to be kept in full, which is what ``random_undersample`` does.
        Keeping only a fraction beta of the negatives multiplies the odds of the
        positive class by ``1 / beta``, so the true posterior is recovered by
        scaling the odds back down:

            odds_true = beta * odds_sampled
            p = beta * p_s / (beta * p_s - p_s + 1)

        The denominator equals ``1 - p_s * (1 - beta)`` and is strictly positive
        for beta in (0, 1] and p_s in [0, 1], so no zero-division guard is needed.
        beta == 1.0 (no undersampling) returns the input unchanged.

        Correcting matters whenever a probability is consumed as a probability --
        expected-value sizing, Kelly fractions, cost-benefit thresholds. It is a
        strictly monotone transform, so it does not change the ranking of samples
        and therefore does not change PR-AUC or ROC-AUC.
        """
        if not np.isfinite(beta) or not 0 < beta <= 1:
            raise ValueError(f"beta must be in (0, 1], got {beta!r}.")

        p_s = np.asarray(probabilities, dtype=float)
        if not np.all(np.isfinite(p_s)):
            raise ValueError("probabilities contain NaN or infinite values.")
        if np.any(p_s < 0) or np.any(p_s > 1):
            raise ValueError("probabilities must lie in [0, 1].")

        corrected = (beta * p_s) / (beta * p_s - p_s + 1)
        logger.info(f"Corrected {p_s.size} probabilities for undersampling beta={beta:.6f}")
        return corrected
