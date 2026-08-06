import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class StrategyPerformanceAttributionVsMarketBetaConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True

class StrategyPerformanceAttributionVsMarketBeta:
    """Legacy class retained for 100% backward compatibility."""
    def __init__(self, config: StrategyPerformanceAttributionVsMarketBetaConfig):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled

@dataclass
class FactorAttributionBreakdown:
    factor_name: str
    beta_exposure: float                   # Sensitivity / Loading
    factor_annual_return_pct: float
    return_contribution_pct: float         # Beta * Factor Return
    t_statistic: float
    is_statistically_significant: bool     # |t| >= 1.96

@dataclass
class PerformanceAttributionReport:
    strategy_id: str
    total_realized_annual_return_pct: float
    risk_free_rate_pct: float
    annualized_jensens_alpha_pct: float    # True idiosyncratic alpha
    alpha_t_statistic: float
    is_true_alpha_significant: bool        # |t| >= 1.96
    r_squared: float                       # Variance explained by systematic factors
    market_beta: float
    factor_breakdown: List[FactorAttributionBreakdown]
    alpha_percentage_of_total_return: float# (Alpha / Total Return) * 100
    audit_notes: str

class StrategyPerformanceAttributionEngine:
    """
    Production-grade Performance Attribution Engine performing CAPM and Fama-French multi-factor regressions
    to decompose strategy returns into systematic Market Beta, Style Risk Premia (SMB, HML), and True Alpha.
    """
    def __init__(self, periods_per_year: int = 252):
        self.periods_per_year = periods_per_year

    def _run_ols_regression(
        self,
        y: np.ndarray,
        X: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Runs OLS regression y = X * beta + residual.
        Returns coefficients, t-statistics, and R-squared.
        """
        n, k = X.shape
        # Coefficients beta = (X^T X)^-1 X^T y
        beta, residuals, rank, s = np.linalg.lstsq(X, y, rcond=None)

        pred = X @ beta
        res = y - pred

        ss_tot = np.sum((y - np.mean(y)) ** 2)
        ss_res = np.sum(res ** 2)
        r_sq = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        # Standard errors
        df_e = max(1, n - k)
        sigma_sq = ss_res / float(df_e)
        cov_beta = sigma_sq * np.linalg.pinv(X.T @ X)
        se_beta = np.sqrt(np.maximum(1e-8, np.diag(cov_beta)))
        t_stats = beta / se_beta

        return beta, t_stats, float(r_sq)

    def analyze_attribution(
        self,
        strategy_id: str,
        strategy_returns: pd.Series,
        market_returns: pd.Series,
        smb_returns: Optional[pd.Series] = None,
        hml_returns: Optional[pd.Series] = None,
        risk_free_rate_annual_pct: float = 2.0
    ) -> PerformanceAttributionReport:
        """
        Decomposes strategy returns using CAPM or Fama-French 3-Factor model.
        y = R_strategy - R_rf_daily
        X = [1, MKT_excess, SMB, HML]
        """
        df = pd.DataFrame({
            "strat": strategy_returns,
            "mkt": market_returns
        }).dropna()

        rf_daily = (risk_free_rate_annual_pct / 100.0) / self.periods_per_year
        y = df["strat"].values - rf_daily
        mkt_excess = df["mkt"].values - rf_daily

        X_list = [np.ones(len(y)), mkt_excess]
        factor_names = ["Market (MKT)"]
        factor_series = [df["mkt"]]

        if smb_returns is not None:
            df["smb"] = smb_returns
            df = df.dropna()
            y = df["strat"].values - rf_daily
            mkt_excess = df["mkt"].values - rf_daily
            X_list = [np.ones(len(y)), mkt_excess, df["smb"].values]
            factor_names.append("Size (SMB)")
            factor_series.append(df["smb"])

        if hml_returns is not None:
            df["hml"] = hml_returns
            df = df.dropna()
            y = df["strat"].values - rf_daily
            mkt_excess = df["mkt"].values - rf_daily
            X_list = [np.ones(len(y)), mkt_excess]
            if "smb" in df.columns:
                X_list.append(df["smb"].values)
            X_list.append(df["hml"].values)
            factor_names.append("Value (HML)")
            factor_series.append(df["hml"])

        X = np.column_stack(X_list)
        beta, t_stats, r_sq = self._run_ols_regression(y, X)

        daily_alpha = beta[0]
        alpha_t = t_stats[0]
        annualized_alpha_pct = (daily_alpha * self.periods_per_year) * 100.0

        mkt_beta = float(beta[1])

        # Factor Attribution Breakdown
        factor_breakdown: List[FactorAttributionBreakdown] = []
        for idx in range(1, len(beta)):
            fname = factor_names[idx - 1]
            b_val = float(beta[idx])
            t_val = float(t_stats[idx])
            f_ret = factor_series[idx - 1]
            ann_f_ret_pct = float(f_ret.mean() * self.periods_per_year * 100.0)
            contrib_pct = b_val * ann_f_ret_pct

            factor_breakdown.append(FactorAttributionBreakdown(
                factor_name=fname,
                beta_exposure=round(b_val, 4),
                factor_annual_return_pct=round(ann_f_ret_pct, 2),
                return_contribution_pct=round(contrib_pct, 2),
                t_statistic=round(t_val, 2),
                is_statistically_significant=abs(t_val) >= 1.96
            ))

        total_realized_ann_ret_pct = float(strategy_returns.mean() * self.periods_per_year * 100.0)
        alpha_pct_of_total = (annualized_alpha_pct / total_realized_ann_ret_pct * 100.0) if abs(total_realized_ann_ret_pct) > 1e-4 else 0.0

        notes = (
            f"PERFORMANCE ATTRIBUTION [{strategy_id}]: Total Return = {total_realized_ann_ret_pct:.2f}%, "
            f"Jensen's Alpha = {annualized_alpha_pct:+.2f}% (t={alpha_t:.2f}), Market Beta = {mkt_beta:.2f}, R2 = {r_sq:.2f}."
        )

        logger.info(notes)

        return PerformanceAttributionReport(
            strategy_id=strategy_id,
            total_realized_annual_return_pct=round(total_realized_ann_ret_pct, 2),
            risk_free_rate_pct=risk_free_rate_annual_pct,
            annualized_jensens_alpha_pct=round(annualized_alpha_pct, 2),
            alpha_t_statistic=round(alpha_t, 2),
            is_true_alpha_significant=abs(alpha_t) >= 1.96,
            r_squared=round(r_sq, 4),
            market_beta=round(mkt_beta, 4),
            factor_breakdown=factor_breakdown,
            alpha_percentage_of_total_return=round(alpha_pct_of_total, 2),
            audit_notes=notes
        )
