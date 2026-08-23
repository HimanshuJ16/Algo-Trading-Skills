"""Pre-trade audit engine for Uniswap v2 style constant-product AMM swaps.

Quotes a swap against a constant-product pool, measures price impact, derives the
``amountOutMin`` floor and the absolute deadline that the router will enforce, and
gates the trade on a price-impact ceiling before any transaction is signed.

Canonical arithmetic (UniswapV2Library.getAmountOut):

    amountInWithFee = amountIn * 997
    amountOut       = (amountInWithFee * reserveOut)
                      / (reserveIn * 1000 + amountInWithFee)

with ``require(amountIn > 0)`` and ``require(reserveIn > 0 && reserveOut > 0)``.
``fee_bps`` generalises the hard-coded 997/1000: canonical Uniswap v2 is 30 bps,
PancakeSwap v2 is 25 bps (9975/10000).

Price impact follows Uniswap's own ``computePriceImpact`` in ``@uniswap/sdk-core``:

    priceImpact = (midPrice * amountIn - amountOut) / (midPrice * amountIn)
                = 1 - executionPrice / midPrice

This measure is **fee-inclusive** — ``amountOut`` is already net of the LP fee, so
an infinitesimal trade reports an impact equal to the pool fee rather than zero.
That is the canonical definition and is preserved here; ``reserve_shift_impact_pct``
is reported alongside it as the fee-excluded component (the impact the same trade
would incur in an identical pool charging no fee), because a price-impact ceiling
set without accounting for the fee tier behaves very differently on a 1 bp pool
than on a 100 bp pool.

Scope and limitations (documented, deliberate):

- **Uniswap v2 style constant-product pools only** — Uniswap v2, Sushiswap,
  PancakeSwap v2 and other x*y=k forks. Uniswap v3/v4 concentrated liquidity is
  NOT supported: v3 behaves like x*y=k only between adjacent initialised ticks
  (on *virtual* reserves), and liquidity changes when a swap crosses a tick, so
  applying this formula to a v3 pool's token balances yields materially wrong
  output. Use the v3 quoter for v3 pools.
- **This is a pre-trade estimator, not an executor.** ``execute_swap`` applies the
  quote to an in-memory pool model; it signs nothing and broadcasts nothing. The
  on-chain router recomputes the output against live reserves at inclusion time,
  so the quote is a prediction and ``amountOutMin`` is the only real guarantee.
- **Float reserves cannot reproduce on-chain results exactly.** The contract uses
  uint256 floor division. Use :func:`get_amount_out_integer` on raw base units
  (wei) when deriving an ``amountOutMin`` that must survive an on-chain
  comparison; a float-derived floor can be unattainable by a few base units.
- **Fee-on-transfer and rebasing tokens are not modelled.** For those the amount
  received by the pair differs from the amount sent, which is why the router
  exposes separate ``...SupportingFeeOnTransferTokens`` entry points.
- Single-hop only: multi-hop routing, split routes, and gas costs are out of scope.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Basis points denominator used by the canonical integer formula.
BPS_DENOMINATOR = 10_000

#: Refuse a slippage tolerance above this fraction. A tolerance is a fraction
#: (0.005 == 0.50%), and the ``_pct`` suffix invites passing 0.5 meaning "0.5%",
#: which would authorise a 50% worse fill and hand a sandwich bot the difference.
MAX_PERMITTED_SLIPPAGE_FRACTION = 0.10

#: Same guard for the price-impact ceiling.
MAX_PERMITTED_IMPACT_FRACTION = 0.50


@dataclass
class AmmPoolState:
    pool_id: str
    token_in_symbol: str
    token_out_symbol: str
    reserve_in: float                    # Reserve x (e.g. 1000.0 ETH)
    reserve_out: float                   # Reserve y (e.g. 3000000.0 USDC)
    fee_pct: float = 0.003               # Fraction, not percent. 0.003 == 0.30% (Uniswap v2)


@dataclass
class UniswapSwapRequest:
    swap_id: str
    token_in_symbol: str
    token_out_symbol: str
    amount_in: float
    max_slippage_pct: float              # Fraction: 0.005 == 0.50%
    max_price_impact_pct: float          # Fraction: 0.05 == 5.0%
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
    price_impact_pct: float              # Fee-inclusive, per Uniswap computePriceImpact
    deadline_timestamp_sec: float
    is_executed: bool
    rejection_reason: Optional[str]
    reserve_shift_impact_pct: float = 0.0   # Fee-excluded component of the impact
    realized_amount_out: Optional[float] = None
    rejection_reasons: Tuple[str, ...] = field(default_factory=tuple)


def get_amount_out_integer(
    amount_in: int,
    reserve_in: int,
    reserve_out: int,
    fee_bps: int = 30,
) -> int:
    """
    Exact uint256 port of ``UniswapV2Library.getAmountOut``, in raw base units (wei).

    Mirrors the contract exactly, including floor division and the contract's own
    preconditions, so an ``amountOutMin`` derived from this value cannot be
    unattainable by a rounding step. ``fee_bps`` generalises the hard-coded
    997/1000: 30 for Uniswap v2, 25 for PancakeSwap v2.
    """
    for name, value in (("amount_in", amount_in), ("reserve_in", reserve_in),
                        ("reserve_out", reserve_out), ("fee_bps", fee_bps)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an int in base units, got {type(value).__name__}.")
    if amount_in <= 0:
        raise ValueError("INSUFFICIENT_INPUT_AMOUNT: amount_in must be > 0.")
    if reserve_in <= 0 or reserve_out <= 0:
        raise ValueError("INSUFFICIENT_LIQUIDITY: reserves must be > 0.")
    if not 0 <= fee_bps < BPS_DENOMINATOR:
        raise ValueError(f"fee_bps must be in [0, {BPS_DENOMINATOR}), got {fee_bps}.")

    amount_in_with_fee = amount_in * (BPS_DENOMINATOR - fee_bps)
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * BPS_DENOMINATOR + amount_in_with_fee
    return numerator // denominator


class UniswapDexIntegrationEngine:
    """
    Pre-trade audit engine for Uniswap v2 style constant-product pools: quotes swap
    output via x*y=k, measures price impact, derives the amountOutMin floor and the
    router deadline, and gates execution on a price-impact ceiling.
    """

    def __init__(self) -> None:
        self.pools: Dict[str, AmmPoolState] = {}

    def register_pool(self, pool: AmmPoolState) -> AmmPoolState:
        """Registers (or replaces) a pool after validating its reserves and fee."""
        if not isinstance(pool.pool_id, str) or not pool.pool_id.strip():
            raise ValueError("pool_id must be a non-empty string.")
        if pool.reserve_in <= 0 or pool.reserve_out <= 0:
            raise ValueError(
                f"INSUFFICIENT_LIQUIDITY: pool {pool.pool_id} reserves must be positive, "
                f"got in={pool.reserve_in}, out={pool.reserve_out}."
            )
        if not 0.0 <= pool.fee_pct < 1.0:
            raise ValueError(
                f"fee_pct for pool {pool.pool_id} must be a fraction in [0, 1) "
                f"(0.003 == 0.30%), got {pool.fee_pct}."
            )
        if pool.token_in_symbol == pool.token_out_symbol:
            raise ValueError(f"Pool {pool.pool_id} cannot swap {pool.token_in_symbol} for itself.")
        self.pools[pool.pool_id] = pool
        return pool

    def calculate_swap_output(
        self,
        pool: AmmPoolState,
        amount_in: float
    ) -> Tuple[float, float, float, float]:
        """
        Returns ``(amount_out, spot_price, execution_price, price_impact_pct)`` for a
        constant-product swap:

            amount_out = (y * gamma * dx) / (x + gamma * dx),  gamma = 1 - fee

        ``price_impact_pct`` is ``(1 - execution_price / spot_price) * 100`` — Uniswap's
        canonical fee-inclusive ``computePriceImpact``. Values are returned unrounded:
        rounding prices to a fixed number of decimals silently destroys pairs quoted at
        inverted scale (a true 0.000333 spot price rounds to 0.0003, a 10% error), and
        rounding amounts to 6 decimals is meaningless for 18-decimal tokens. Round at
        the display boundary instead.
        """
        if pool.reserve_in <= 0 or pool.reserve_out <= 0:
            raise ValueError("Pool reserves must be positive.")
        if not isinstance(amount_in, (int, float)) or isinstance(amount_in, bool):
            raise TypeError(f"amount_in must be numeric, got {type(amount_in).__name__}.")
        if not math.isfinite(amount_in):
            # inf produced a NaN output whose impact clamped to 0.0, passing the
            # impact gate and writing NaN into reserve_out — poisoning every
            # subsequent quote against this pool.
            raise ValueError(f"amount_in must be a finite number, got {amount_in}.")
        if amount_in <= 0:
            # Mirrors require(amountIn > 0, 'INSUFFICIENT_INPUT_AMOUNT'). A negative
            # amount previously produced a negative output and moved both reserves the
            # wrong way, silently inventing liquidity in the pool model.
            raise ValueError(
                f"INSUFFICIENT_INPUT_AMOUNT: amount_in must be > 0, got {amount_in}."
            )

        spot_price = pool.reserve_out / pool.reserve_in
        gamma = 1.0 - pool.fee_pct
        amount_in_with_fee = amount_in * gamma

        amount_out = (pool.reserve_out * amount_in_with_fee) / (pool.reserve_in + amount_in_with_fee)
        execution_price = amount_out / amount_in
        price_impact_pct = max(0.0, (1.0 - (execution_price / spot_price)) * 100.0)

        return amount_out, spot_price, execution_price, price_impact_pct

    def calculate_reserve_shift_impact_pct(self, pool: AmmPoolState, amount_in: float) -> float:
        """
        Fee-excluded price impact: the impact this trade would incur in an identical
        pool charging no fee, i.e. ``dx / (x + dx) * 100``.

        Reported so that a price-impact ceiling can be reasoned about independently of
        the pool's fee tier. On a 1% fee tier the canonical fee-inclusive impact starts
        at 1% for a dust trade, which consumes most of a 5% ceiling before size is even
        considered.
        """
        if amount_in <= 0:
            raise ValueError(f"INSUFFICIENT_INPUT_AMOUNT: amount_in must be > 0, got {amount_in}.")
        return (amount_in / (pool.reserve_in + amount_in)) * 100.0

    def execute_swap(
        self,
        pool_id: str,
        req: UniswapSwapRequest,
        realized_amount_out: Optional[float] = None,
    ) -> UniswapSwapExecutionReport:
        """
        Audits a swap request and, if it passes every gate, applies it to the in-memory
        pool model. Signs and broadcasts nothing.

        Gates, all evaluated so the report lists every failure rather than only the first:

        1. Token orientation must match the pool. A request naming the pool's tokens in
           reverse was previously computed against the pool's own orientation while the
           report echoed the request's symbols — a wrong-direction trade that reads as
           correct.
        2. Slippage tolerance and impact ceiling must be fractions within the permitted
           caps (see ``MAX_PERMITTED_SLIPPAGE_FRACTION``).
        3. ``deadline_seconds`` must be positive: the router enforces
           ``require(deadline >= block.timestamp, 'EXPIRED')``.
        4. Price impact must not exceed ``max_price_impact_pct``.
        5. If ``realized_amount_out`` is supplied (the output actually quoted or received
           at inclusion time), it must be >= ``min_amount_out`` — the router's
           ``require(amounts[last] >= amountOutMin, 'INSUFFICIENT_OUTPUT_AMOUNT')``.
           Without this the floor was computed and then never checked against anything.
        """
        if pool_id not in self.pools:
            raise ValueError(f"Pool {pool_id} not registered.")

        pool = self.pools[pool_id]
        reasons: list = []

        if (req.token_in_symbol, req.token_out_symbol) != (pool.token_in_symbol, pool.token_out_symbol):
            raise ValueError(
                f"TOKEN_ORIENTATION_MISMATCH: request swaps {req.token_in_symbol}->"
                f"{req.token_out_symbol} but pool {pool_id} is oriented "
                f"{pool.token_in_symbol}->{pool.token_out_symbol}. Register the reversed "
                "pool explicitly rather than relying on the engine to infer direction."
            )

        if not 0.0 <= req.max_slippage_pct <= MAX_PERMITTED_SLIPPAGE_FRACTION:
            raise ValueError(
                f"max_slippage_pct must be a FRACTION in [0, {MAX_PERMITTED_SLIPPAGE_FRACTION}] "
                f"(0.005 == 0.50%), got {req.max_slippage_pct}. Passing 0.5 to mean '0.5%' "
                "would authorise a 50% worse fill."
            )
        if not 0.0 <= req.max_price_impact_pct <= MAX_PERMITTED_IMPACT_FRACTION:
            raise ValueError(
                f"max_price_impact_pct must be a FRACTION in [0, {MAX_PERMITTED_IMPACT_FRACTION}] "
                f"(0.05 == 5.0%), got {req.max_price_impact_pct}."
            )
        if req.deadline_seconds <= 0:
            raise ValueError(
                f"deadline_seconds must be > 0, got {req.deadline_seconds}. The router "
                "reverts with 'EXPIRED' unless deadline >= block.timestamp."
            )

        amount_out, spot_p, exec_p, price_impact = self.calculate_swap_output(pool, req.amount_in)
        reserve_shift = self.calculate_reserve_shift_impact_pct(pool, req.amount_in)

        min_out = amount_out * (1.0 - req.max_slippage_pct)
        deadline = req.current_timestamp_sec + req.deadline_seconds

        if price_impact > (req.max_price_impact_pct * 100.0):
            reasons.append(
                f"HIGH PRICE IMPACT: {price_impact:.2f}% > Max Allowed "
                f"{req.max_price_impact_pct * 100.0:.2f}%"
            )

        if realized_amount_out is not None and realized_amount_out < min_out:
            reasons.append(
                f"INSUFFICIENT_OUTPUT_AMOUNT: realized {realized_amount_out} < "
                f"min_amount_out {min_out}"
            )

        is_ok = not reasons
        if is_ok:
            pool.reserve_in += req.amount_in
            pool.reserve_out -= amount_out
            logger.info(
                "DEX SWAP PASSED [%s]: In=%s %s -> Out=%s %s (impact=%.4f%%, "
                "reserve-shift=%.4f%%, minOut=%s, deadline=%s)",
                req.swap_id, req.amount_in, req.token_in_symbol, amount_out,
                req.token_out_symbol, price_impact, reserve_shift, min_out, deadline,
            )
        else:
            logger.error("DEX SWAP REJECTED [%s]: %s", req.swap_id, "; ".join(reasons))

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
            rejection_reason=reasons[0] if reasons else None,
            reserve_shift_impact_pct=reserve_shift,
            realized_amount_out=realized_amount_out,
            rejection_reasons=tuple(reasons),
        )
