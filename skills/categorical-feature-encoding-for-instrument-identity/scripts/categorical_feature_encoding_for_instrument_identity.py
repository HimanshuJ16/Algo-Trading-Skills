import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class PointInTimeTargetEncoder:
    """
    Encodes instrument symbols using an expanding window smoothed target average.
    Strictly prevents lookahead bias by only using strictly historical (T-1) targets.
    """
    def __init__(self, smoothing_weight: float = 10.0):
        """
        smoothing_weight: The number of observations needed for the local mean 
                          to have 50% weight against the global mean.
        """
        self.smoothing_weight = smoothing_weight

    def fit_transform(self, df: pd.DataFrame, time_col: str, symbol_col: str, target_col: str) -> pd.DataFrame:
        """
        df must be sorted by time_col before being passed here.
        Returns a copy of the dataframe with a new column `{symbol_col}_encoded`.
        """
        if not df[time_col].is_monotonic_increasing:
            logger.warning("Data is not sorted by time. Sorting to prevent lookahead bias.")
            df = df.sort_values(time_col)
            
        df_out = df.copy()
        
        # 1. Global expanding stats strictly by time
        daily_global = df.groupby(time_col, as_index=False)[target_col].agg(
            day_sum='sum', day_count='count'
        )
        daily_global = daily_global.sort_values(time_col)
        daily_global['global_sum'] = daily_global['day_sum'].shift(1).expanding().sum().fillna(0)
        daily_global['global_count'] = daily_global['day_count'].shift(1).expanding().sum().fillna(0)
        daily_global = daily_global.drop(columns=['day_sum', 'day_count'])
        
        # Merge back to df_out
        df_out = df_out.merge(daily_global, on=time_col, how='left')
        
        # 2. Local (per-symbol) expanding stats strictly by time
        daily_local = df.groupby([symbol_col, time_col], as_index=False)[target_col].agg(
            day_sum='sum', day_count='count'
        )
        daily_local = daily_local.sort_values([symbol_col, time_col])
        daily_local['local_sum'] = daily_local.groupby(symbol_col)['day_sum'].transform(lambda x: x.shift(1).expanding().sum()).fillna(0)
        daily_local['local_count'] = daily_local.groupby(symbol_col)['day_count'].transform(lambda x: x.shift(1).expanding().sum()).fillna(0)
        daily_local = daily_local.drop(columns=['day_sum', 'day_count'])
        
        # Merge back
        df_out = df_out.merge(daily_local, on=[symbol_col, time_col], how='left')
        df_out['local_sum'] = df_out['local_sum'].fillna(0)
        df_out['local_count'] = df_out['local_count'].fillna(0)
        
        # 3. Calculate means (handle div by zero)
        df_out['global_mean'] = np.where(df_out['global_count'] > 0, 
                                         df_out['global_sum'] / df_out['global_count'], 
                                         0.0)
                                         
        df_out['local_mean'] = np.where(df_out['local_count'] > 0, 
                                        df_out['local_sum'] / df_out['local_count'], 
                                        df_out['global_mean']) # If 0 local obs, local mean = global mean
        
        # 4. Apply Smoothing Formula
        encoded_col = f"{symbol_col}_encoded"
        
        numerator = (df_out['local_count'] * df_out['local_mean']) + (self.smoothing_weight * df_out['global_mean'])
        denominator = df_out['local_count'] + self.smoothing_weight
        
        df_out[encoded_col] = numerator / denominator
        
        # Cleanup temp columns
        cols_to_drop = ['global_sum', 'global_count', 'local_sum', 'local_count', 'global_mean', 'local_mean']
        df_out = df_out.drop(columns=cols_to_drop)
        
        return df_out
