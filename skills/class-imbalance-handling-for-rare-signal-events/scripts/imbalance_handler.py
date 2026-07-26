import numpy as np
import logging
from typing import Tuple, Dict

logger = logging.getLogger(__name__)

class ImbalanceHandler:
    """
    Utility to handle highly imbalanced datasets for rare financial events.
    Supports calculating class weights (for cost-sensitive learning) and 
    random undersampling (for reducing majority class dominance).
    """

    @staticmethod
    def compute_class_weights(y: np.ndarray) -> Dict[int, float]:
        """
        Calculates heuristic class weights using the scikit-learn standard formula:
        n_samples / (n_classes * np.bincount(y))
        
        y must be an array of integers (0, 1, 2, ...).
        """
        if len(y) == 0:
            raise ValueError("Target array y is empty.")
            
        classes = np.unique(y)
        n_classes = len(classes)
        n_samples = len(y)
        
        weights = {}
        for c in classes:
            class_count = np.sum(y == c)
            if class_count == 0:
                continue
                
            weight = n_samples / (n_classes * class_count)
            weights[c] = weight
            
        logger.info(f"Computed class weights: {weights}")
        return weights

    @staticmethod
    def random_undersample(X: np.ndarray, y: np.ndarray, random_state: int = 42) -> Tuple[np.ndarray, np.ndarray]:
        """
        Balances a binary classification dataset by randomly undersampling the majority class 
        until it matches the minority class count.
        
        X: Feature matrix (2D numpy array)
        y: Target array (1D numpy array, binary 0 and 1)
        """
        if len(X) != len(y):
            raise ValueError("X and y must have the same number of rows.")
            
        np.random.seed(random_state)
        
        classes, counts = np.unique(y, return_counts=True)
        if len(classes) != 2:
            raise NotImplementedError("random_undersample currently only supports binary classification (2 classes).")
            
        minority_class = classes[np.argmin(counts)]
        majority_class = classes[np.argmax(counts)]
        minority_count = min(counts)
        
        logger.info(f"Undersampling majority class ({majority_class}) down to {minority_count} samples.")
        
        minority_indices = np.where(y == minority_class)[0]
        majority_indices = np.where(y == majority_class)[0]
        
        # Randomly choose exactly 'minority_count' indices from the majority class
        sampled_majority_indices = np.random.choice(majority_indices, size=minority_count, replace=False)
        
        # Combine and sort indices to maintain general chronological order (optional, but good for finance)
        balanced_indices = np.concatenate([minority_indices, sampled_majority_indices])
        balanced_indices = np.sort(balanced_indices)
        
        return X[balanced_indices], y[balanced_indices]
