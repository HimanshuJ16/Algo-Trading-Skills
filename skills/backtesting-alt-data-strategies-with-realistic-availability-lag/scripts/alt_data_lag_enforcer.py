import pandas as pd
from typing import Optional

class AltDataLagEnforcer:
    """
    Enforces Point-in-Time (PIT) causality for alternative data during backtesting.
    Prevents lookahead bias by ensuring data is only visible to the backtest AFTER
    its realistic publication lag has elapsed.
    """
    def __init__(
        self,
        df: pd.DataFrame,
        event_date_col: str = "event_date",
        publication_date_col: Optional[str] = None,
        default_lag_days: int = 3,
    ):
        """
        :param df: The alternative dataset.
        :param event_date_col: Column representing when the real-world event occurred.
        :param publication_date_col: Column representing when the data was published.
        :param default_lag_days: Fallback lag applied if publication_date_col is missing.
        """
        self.df = df.copy()
        self.event_date_col = event_date_col
        self.publication_date_col = publication_date_col
        self.default_lag_days = default_lag_days

        # Ensure datetime format
        self.df[self.event_date_col] = pd.to_datetime(self.df[self.event_date_col])

        # Resolve the effective publication date
        self._resolve_publication_dates()

    def _resolve_publication_dates(self):
        """
        Calculates the exact timestamp when this row of data becomes "known" to the trading model.
        """
        if self.publication_date_col and self.publication_date_col in self.df.columns:
            self.df[self.publication_date_col] = pd.to_datetime(self.df[self.publication_date_col])
            # If publication date is missing for specific rows, fallback to event_date + default lag
            fallback_dates = self.df[self.event_date_col] + pd.Timedelta(days=self.default_lag_days)
            self.df["_effective_pub_date"] = self.df[self.publication_date_col].fillna(fallback_dates)
        else:
            # Entirely rely on the default lag
            self.df["_effective_pub_date"] = self.df[self.event_date_col] + pd.Timedelta(days=self.default_lag_days)

    def get_point_in_time_data(self, as_of_date: str | pd.Timestamp) -> pd.DataFrame:
        """
        Filters the dataset to return ONLY the rows that were publicly available
        on or before the `as_of_date`.
        
        :param as_of_date: The current simulated trading date in the backtest loop.
        :return: DataFrame containing only causally safe data.
        """
        as_of = pd.to_datetime(as_of_date)
        
        # Point-in-Time filter: Effective publication date must be <= as_of_date
        pit_df = self.df[self.df["_effective_pub_date"] <= as_of].copy()
        
        # Sort by event date to maintain chronological order
        pit_df.sort_values(by=self.event_date_col, inplace=True)
        
        # Clean up internal column before returning
        pit_df.drop(columns=["_effective_pub_date"], inplace=True)
        
        return pit_df
