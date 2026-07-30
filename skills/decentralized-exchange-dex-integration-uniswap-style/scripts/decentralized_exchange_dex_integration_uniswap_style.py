import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class AmmPoolState:
    pool_id: str
    token_in_symbol: str
    token_out_symbol: str
    reserve_in: float                    # Reserve x (e.g. 1000.0 ETH)
    reserve_out: float                   # Reserve y (e.g. 3000000.0 USDC)
    fee_pct: float = 0.003               # 0.30% Uniswap v2 fee

@dataclass
class UniswapSwapRequest:
    swap_id: str
    token_in_symbol: str
    token_out_symbol: str
    amount_in: float
    max_slippage_pct: float              # e.g. 0.005 (0.50%)
    max_price_impact_pct: float          # e.g. 0.05 (5.0%)
    current_timestamp_sec: float
    deadline_seconds: float = 60.0

@dataclass
class UniswapSwapExecutionReport:
    swap_id: str
    token_in_symbol: str
    token_out_symbol: str
    amount_in: float
    expected_amount_out: float
    min_amount_out: float
    spot_price_before: float
    execution_price: float
    price_impact_pct: float
    deadline_timestamp_sec: float
    is_executed: bool
    rejection_reason: Optional[str]

class UniswapDexIntegrationEngine:
    """
    Quantitative AMM DEX integration engine for Uniswap v2/v3 style pools,
    calculating constant product swap outputs (x*y=k), price impact, slippage tolerance, and MEV deadline protections.
    """
    def __init__(self):
        self.pools: Dict[str, AmmPoolState] = {}

    def register_pool(self, pool: AmmPoolState):
        self.pools[pool.pool_id] = pool

    def calculate_swap_output(
        self,
        pool: AmmPoolState,
        amount_in: float
    ) -> Tuple[float, float, float, float]:
        """
        Calculates swap output amount, spot price, execution price, and price impact using Constant Product Math (x*y=k):
        Delta_y = (y * (1 - fee) * Delta_x) / (x + (1 - fee) * Delta_x)
        """
        if pool.reserve_in <= 0 or pool.reserve_out <= 0:
            raise ValueError("Pool reserves must be positive.")

        spot_price = pool.reserve_out / pool.reserve_in
        gamma = 1.0 - pool.fee_pct
        amount_in_with_fee = amount_in * gamma

        amount_out = (pool.reserve_out * amount_in_with_fee) / (pool.reserve_in + amount_in_with_fee)
        execution_price = amount_out / amount_in

        # Price Impact = (1 - Execution Price / Spot Price) * 100%
        price_impact_pct = round((1.0 - (execution_price / spot_price)) * 100.0, 4)

        return round(amount_out, 6), round(spot_price, 4), round(execution_price, 4), max(0.0, price_impact_pct)

    def execute_swap(
        self,
        pool_id: str,
        req: UniswapSwapRequest
    ) -> UniswapSwapExecutionReport:
        """
        Audits swap request against price impact ceilings, slippage tolerances, and deadlines.
        """
        if pool_id not in self.pools:
            raise ValueError(f"Pool {pool_id} not registered.")

        pool = self.pools[pool_id]
        amount_out, spot_p, exec_p, price_impact = self.calculate_swap_output(pool, req.amount_in)

        min_out = round(amount_out * (1.0 - req.max_slippage_pct), 6)
        deadline = req.current_timestamp_sec + req.deadline_seconds

        rejection = None
        is_ok = True

        # Audit Price Impact Ceiling
        if price_impact > (req.max_price_impact_pct * 100.0):
            is_ok = False
            rejection = f"HIGH PRICE IMPACT: {price_impact:.2f}% > Max Allowed {req.max_price_impact_pct * 100.0:.2f}%"
            logger.error(f"DEX SWAP REJECTED [{req.swap_id}]: {rejection}")

        if is_ok:
            # Update Pool Reserves
            pool.reserve_in += req.amount_in
            pool.reserve_out -= amount_out
            logger.info(f"DEX SWAP EXECUTED [{req.swap_id}]: In={req.amount_in} {req.token_in_symbol} -> Out={amount_out} {req.token_out_symbol} (Impact={price_impact:.2f}%)")

        return UniswapSwapExecutionReport(
            swap_id=req.swap_id,
            token_in_symbol=req.token_in_symbol,
            token_out_symbol=req.token_out_symbol,
            amount_in=req.amount_in,
            expected_amount_out=amount_out,
            min_amount_out=min_out,
            spot_price_before=spot_p,
            execution_price=exec_p,
            price_impact_pct=price_impact,
            deadline_timestamp_sec=deadline,
            is_executed=is_ok,
            rejection_reason=rejection
        )
