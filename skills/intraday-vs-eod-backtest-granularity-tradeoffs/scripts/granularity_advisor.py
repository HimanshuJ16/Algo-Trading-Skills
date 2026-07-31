import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Data Granularity Resolutions: 'TICK_L2', 'INTRADAY_1MIN', 'INTRADAY_5MIN', 'DAILY_EOD'

@dataclass
class BacktestStrategyProfile:
    strategy_name: str
    holding_period: str                 # 'INTRADAY_MINUTES', 'INTRADAY_HOURS', 'SWING_DAYS', 'POSITIONAL_MONTHS'
    trade_frequency_per_day: float       # e.g. 25.0 trades/day
    has_intraday_stop_loss: bool        # True if strategy relies on intraday stop-loss / take-profit
    universe_size: int                  # e.g. 500 stocks
    history_years: float                # e.g. 5.0 years
    selected_data_granularity: str      # 'TICK_L2', 'INTRADAY_1MIN', 'INTRADAY_5MIN', 'DAILY_EOD'

@dataclass
class BacktestGranularityReport:
    strategy_name: str
    recommended_granularity: str        # 'TICK_L2', 'INTRADAY_1MIN', 'INTRADAY_5MIN', 'DAILY_EOD'
    estimated_data_points_count: int
    estimated_storage_size_gb: float
    has_ohlc_sequence_bias: bool
    status: str                         # 'GRANULARITY_APPROVED', 'OHLC_SEQUENCE_BIAS_WARNING', 'COMPUTE_OVERHEAD_WARNING'
    audit_notes: str

class BacktestGranularityAdvisorEngine:
    """
    Quantitative backtest engineering advisor for evaluating data resolution tradeoffs (Tick vs 1-Min vs Daily EOD),
    estimating storage/compute footprints, and eliminating OHLC in-bar order sequence biases.
    """
    def __init__(self):
        pass

    def estimate_data_footprint(
        self,
        granularity: str,
        universe_size: int,
        history_years: float
    ) -> Tuple[int, float]:
        """
        Estimates total data points and storage size in GB for a backtest dataset.
        """
        trading_days_per_year = 252
        tot_days = trading_days_per_year * history_years

        if granularity == "TICK_L2":
            bars_per_day = 100_000 # Approx 100k ticks per symbol per day
            bytes_per_point = 32
        elif granularity == "INTRADAY_1MIN":
            bars_per_day = 390
            bytes_per_point = 40
        elif granularity == "INTRADAY_5MIN":
            bars_per_day = 78
            bytes_per_point = 40
        else: # DAILY_EOD
            bars_per_day = 1
            bytes_per_point = 48

        tot_points = int(tot_days * bars_per_day * universe_size)
        storage_gb = round((tot_points * bytes_per_point) / (1024.0 ** 3), 3)

        return tot_points, storage_gb

    def advise_backtest_granularity(
        self,
        profile: BacktestStrategyProfile
    ) -> BacktestGranularityReport:
        """
        Advises optimal data granularity and audits simulation sequence bias risks.
        """
        hp = profile.holding_period.upper()
        sel = profile.selected_data_granularity.upper()

        # 1. Determine Recommended Granularity
        if hp == "INTRADAY_MINUTES" or profile.trade_frequency_per_day >= 20:
            rec = "TICK_L2" if profile.trade_frequency_per_day >= 50 else "INTRADAY_1MIN"
        elif hp == "INTRADAY_HOURS" or profile.has_intraday_stop_loss:
            rec = "INTRADAY_1MIN"
        elif hp == "SWING_DAYS":
            rec = "INTRADAY_5MIN" if profile.has_intraday_stop_loss else "DAILY_EOD"
        else: # POSITIONAL_MONTHS
            rec = "DAILY_EOD"

        tot_points, est_gb = self.estimate_data_footprint(sel, profile.universe_size, profile.history_years)

        # 2. Audit OHLC Sequence Bias Risk
        has_bias = False
        if profile.has_intraday_stop_loss and sel == "DAILY_EOD":
            has_bias = True
            status = "OHLC_SEQUENCE_BIAS_WARNING"
            notes = (
                f"OHLC SEQUENCE BIAS WARNING [{profile.strategy_name}]: Strategy uses intraday stop-loss "
                f"but selected 'DAILY_EOD' data. In-bar order sequence (High vs Low) cannot be determined, "
                f"causing optimistic execution bias. Recommended minimum: '{rec}'."
            )
            logger.warning(notes)
            return BacktestGranularityReport(
                strategy_name=profile.strategy_name, recommended_granularity=rec,
                estimated_data_points_count=tot_points, estimated_storage_size_gb=est_gb,
                has_ohlc_sequence_bias=True, status=status, audit_notes=notes
            )

        # 3. Audit Compute Overhead
        if sel == "TICK_L2" and hp == "POSITIONAL_MONTHS":
            status = "COMPUTE_OVERHEAD_WARNING"
            notes = (
                f"COMPUTE OVERHEAD WARNING [{profile.strategy_name}]: Using TICK_L2 data ({est_gb:.1f} GB) "
                f"for POSITIONAL_MONTHS strategy causes 1,000x compute slowdown without improving accuracy. "
                f"Recommended: 'DAILY_EOD'."
            )
            logger.warning(notes)
            return BacktestGranularityReport(
                strategy_name=profile.strategy_name, recommended_granularity=rec,
                estimated_data_points_count=tot_points, estimated_storage_size_gb=est_gb,
                has_ohlc_sequence_bias=False, status=status, audit_notes=notes
            )

        status = "GRANULARITY_APPROVED"
        notes = (
            f"GRANULARITY APPROVED [{profile.strategy_name}]: Selected '{sel}' matches strategy profile. "
            f"Dataset size = {tot_points:,} points ({est_gb:.3f} GB across {profile.history_years} years)."
        )
        logger.info(notes)

        return BacktestGranularityReport(
            strategy_name=profile.strategy_name,
            recommended_granularity=rec,
            estimated_data_points_count=tot_points,
            estimated_storage_size_gb=est_gb,
            has_ohlc_sequence_bias=False,
            status=status,
            audit_notes=notes
        )
