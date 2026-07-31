import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class MultiCurrencyPosition:
    symbol: str
    native_currency: str
    quantity: float
    current_price_native: float
    fx_rate_to_base: float = 1.0        # 1.0 if native_currency == base_currency

    @property
    def value_base(self) -> float:
        return self.quantity * self.current_price_native * self.fx_rate_to_base

@dataclass
class VarConfig:
    confidence_level: float = 0.95       # 0.95 or 0.99
    holding_period_days: int = 1
    base_currency: str = "USD"

@dataclass
class MultiCurrencyVarReport:
    base_currency: str
    total_portfolio_value_base: float
    confidence_level: float
    parametric_var_base: float
    historical_var_base: float
    expected_shortfall_cvar_base: float
    currency_risk_breakdown: Dict[str, float]
    status: str                         # 'VAR_CALCULATION_SUCCESS'
    audit_notes: str

class MultiCurrencyVarAggregatorEngine:
    """
    Multi-currency Value at Risk (VaR) and Expected Shortfall (CVaR) aggregation engine,
    accounting for joint asset-FX return covariance and per-currency risk decomposition.
    """
    def __init__(self):
        pass

    @staticmethod
    def _get_z_score(confidence: float) -> float:
        """Returns standard normal Z-score for given confidence level."""
        if math.isclose(confidence, 0.95):
            return 1.6449
        elif math.isclose(confidence, 0.99):
            return 2.3263
        elif math.isclose(confidence, 0.90):
            return 1.2816
        else:
            # Approximate via inverse error function formula
            return math.sqrt(2) * math.erfinv(2 * confidence - 1)

    def calculate_multi_currency_var(
        self,
        config: VarConfig,
        positions: List[MultiCurrencyPosition],
        native_symbol_returns: Dict[str, List[float]],
        fx_returns_to_base: Dict[str, List[float]]
    ) -> MultiCurrencyVarReport:
        """
        Synthesizes joint base returns (compounding asset + FX returns), computes Parametric & Historical VaR,
        and derives Expected Shortfall (CVaR).
        """
        if not positions:
            raise ValueError("Position list cannot be empty.")

        total_value_base = sum(p.value_base for p in positions)
        if total_value_base <= 0.0:
            raise ValueError("Total portfolio base value must be positive.")

        # 1. Synthesize Joint Base Return Series for Each Position
        # R_base = (1 + R_native) * (1 + R_fx) - 1
        num_periods = 0
        joint_returns_dict: Dict[str, List[float]] = {}
        weights_dict: Dict[str, float] = {}

        for p in positions:
            symbol = p.symbol
            weights_dict[symbol] = p.value_base / total_value_base

            r_native = native_symbol_returns.get(symbol, [])
            r_fx = fx_returns_to_base.get(p.native_currency, [0.0] * len(r_native))

            if not r_native:
                raise ValueError(f"Missing historical return series for symbol '{symbol}'.")

            if num_periods == 0:
                num_periods = len(r_native)
            elif len(r_native) != num_periods:
                raise ValueError(f"Mismatched return series length for symbol '{symbol}'. Expected {num_periods}, got {len(r_native)}.")

            joint_returns = [(1.0 + rn) * (1.0 + rfx) - 1.0 for rn, rfx in zip(r_native, r_fx)]
            joint_returns_dict[symbol] = joint_returns

        # 2. Portfolio Base Return Series Synthesis
        portfolio_base_returns: List[float] = [0.0] * num_periods
        for t in range(num_periods):
            port_r_t = sum(weights_dict[p.symbol] * joint_returns_dict[p.symbol][t] for p in positions)
            portfolio_base_returns[t] = port_r_t

        # 3. Parametric VaR (Variance-Covariance)
        mean_port_r = sum(portfolio_base_returns) / num_periods
        var_port_r = sum((x - mean_port_r) ** 2 for x in portfolio_base_returns) / (num_periods - 1) if num_periods > 1 else 0.0
        std_port_r = math.sqrt(var_port_r)

        z_score = self._get_z_score(config.confidence_level)
        holding_factor = math.sqrt(config.holding_period_days)
        parametric_var_base = z_score * std_port_r * holding_factor * total_value_base

        # 4. Historical Simulation VaR & Expected Shortfall (CVaR)
        # Loss series in USD (positive number = loss)
        losses_base = [-r * total_value_base * holding_factor for r in portfolio_base_returns]
        sorted_losses = sorted(losses_base, reverse=True)

        alpha_index = int(math.floor((1.0 - config.confidence_level) * num_periods))
        alpha_index = max(0, min(alpha_index, num_periods - 1))
        historical_var_base = sorted_losses[alpha_index]

        # Expected Shortfall (CVaR) = average of worst losses beyond VaR
        tail_losses = sorted_losses[:max(1, alpha_index + 1)]
        expected_shortfall_base = sum(tail_losses) / len(tail_losses)

        # 5. Currency Breakdown Sizing
        currency_breakdown: Dict[str, float] = {}
        for p in positions:
            ccy = p.native_currency
            currency_breakdown[ccy] = currency_breakdown.get(ccy, 0.0) + p.value_base

        notes = (
            f"MULTI-CURRENCY VAR CALCULATED [{config.base_currency}]: Total Value = ${total_value_base:,.2f}, "
            f"{config.confidence_level * 100:.0f}% Parametric VaR = ${parametric_var_base:,.2f}, "
            f"Historical VaR = ${historical_var_base:,.2f}, CVaR = ${expected_shortfall_base:,.2f}."
        )
        logger.info(notes)

        return MultiCurrencyVarReport(
            base_currency=config.base_currency,
            total_portfolio_value_base=round(total_value_base, 2),
            confidence_level=config.confidence_level,
            parametric_var_base=round(parametric_var_base, 2),
            historical_var_base=round(historical_var_base, 2),
            expected_shortfall_cvar_base=round(expected_shortfall_base, 2),
            currency_risk_breakdown=currency_breakdown,
            status="VAR_CALCULATION_SUCCESS",
            audit_notes=notes
        )
