import random
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class BrokerVenueConfig:
    broker_name: str
    supports_native_iceberg: bool       # e.g. True for IBKR/Binance, False for REST broker
    native_parameter_name: Optional[str] = None # 'displaySize', 'icebergQty', 'DisplayQty'
    client_network_latency_ms: float = 25.0

@dataclass
class IcebergOrderRequest:
    symbol: str
    side: str                           # 'BUY' or 'SELL'
    total_quantity: int                 # e.g. 10,000 shares
    target_display_quantity: int        # e.g. 500 shares
    limit_price: float
    slice_randomization_pct: float = 0.20 # 20% variance for synthetic slices

@dataclass
class IcebergChildSlice:
    slice_id: str
    slice_quantity: int
    limit_price: float
    status: str                         # 'SUBMITTED', 'FILLED'

@dataclass
class IcebergExecutionReport:
    symbol: str
    broker_name: str
    execution_mode: str                 # 'NATIVE_ICEBERG', 'SYNTHETIC_SIMULATION'
    total_quantity: int
    target_display_quantity: int
    native_display_parameter_sent: Optional[str]
    total_slices_count: int
    synthetic_child_slices: List[IcebergChildSlice]
    total_refill_latency_penalty_ms: float
    status: str                         # 'ROUTED_NATIVE_EXCHANGE', 'INITIALIZED_SYNTHETIC_SIMULATION'
    audit_notes: str

class IcebergExecutionRouterEngine:
    """
    Execution routing engine for evaluating Native Exchange Iceberg orders vs Synthetic Client-Side Iceberg simulations,
    managing display slice randomization, and tracking refill latency.
    """
    def __init__(self):
        pass

    def generate_randomized_synthetic_slice_size(
        self,
        base_display_qty: int,
        remaining_total_qty: int,
        randomization_pct: float = 0.20
    ) -> int:
        """
        Generates randomized child slice quantity (e.g. 500 +- 20% -> 400 to 600 shares) to hide synthetic iceberg patterns.
        """
        if remaining_total_qty <= base_display_qty:
            return remaining_total_qty

        min_qty = max(1, int(base_display_qty * (1.0 - randomization_pct)))
        max_qty = int(base_display_qty * (1.0 + randomization_pct))
        rand_qty = random.randint(min_qty, max_qty)
        return min(remaining_total_qty, rand_qty)

    def route_iceberg_order(
        self,
        req: IcebergOrderRequest,
        venue: BrokerVenueConfig
    ) -> IcebergExecutionReport:
        """
        Routes order to Native Exchange Iceberg or initializes Synthetic Simulation manager.
        """
        if req.total_quantity <= 0 or req.target_display_quantity <= 0:
            raise ValueError("Total quantity and display quantity must be > 0.")

        if req.target_display_quantity >= req.total_quantity:
            # Not an iceberg order; standard limit order
            exec_mode = "STANDARD_LIMIT_ORDER"
            notes = f"ICEBERG ROUTE [{req.symbol}]: Display quantity >= total quantity. Routing as standard limit order."
            return IcebergExecutionReport(
                symbol=req.symbol, broker_name=venue.broker_name, execution_mode=exec_mode,
                total_quantity=req.total_quantity, target_display_quantity=req.total_quantity,
                native_display_parameter_sent=None, total_slices_count=1, synthetic_child_slices=[],
                total_refill_latency_penalty_ms=0.0, status="ROUTED_STANDARD_LIMIT", audit_notes=notes
            )

        if venue.supports_native_iceberg:
            # MODE A: NATIVE ICEBERG ON EXCHANGE MATCHING ENGINE
            exec_mode = "NATIVE_ICEBERG"
            param_name = venue.native_parameter_name or "displaySize"
            param_str = f"{param_name}={req.target_display_quantity}"
            estimated_slices = (req.total_quantity + req.target_display_quantity - 1) // req.target_display_quantity

            notes = (
                f"NATIVE ICEBERG ROUTED [{req.symbol} on {venue.broker_name}]: Sent single parent order "
                f"(Total = {req.total_quantity:,}, Display = {req.target_display_quantity:,}, {param_str}). "
                f"Matching engine refills automatically with 0ms client network latency."
            )
            logger.info(notes)

            return IcebergExecutionReport(
                symbol=req.symbol,
                broker_name=venue.broker_name,
                execution_mode=exec_mode,
                total_quantity=req.total_quantity,
                target_display_quantity=req.target_display_quantity,
                native_display_parameter_sent=param_str,
                total_slices_count=estimated_slices,
                synthetic_child_slices=[],
                total_refill_latency_penalty_ms=0.0, # 0ms native refill latency
                status="ROUTED_NATIVE_EXCHANGE",
                audit_notes=notes
            )
        else:
            # MODE B: SYNTHETIC CLIENT-SIDE ICEBERG SIMULATION
            exec_mode = "SYNTHETIC_SIMULATION"
            remaining = req.total_quantity
            slices: List[IcebergChildSlice] = []
            slice_count = 0

            # Simulate slicing parent into randomized child orders
            while remaining > 0:
                slice_count += 1
                c_qty = self.generate_randomized_synthetic_slice_size(
                    req.target_display_quantity, remaining, req.slice_randomization_pct
                )
                slices.append(IcebergChildSlice(
                    slice_id=f"SYNTH_{req.symbol}_{slice_count:03d}",
                    slice_quantity=c_qty,
                    limit_price=req.limit_price,
                    status="FILLED"
                ))
                remaining -= c_qty

            # Total network refill latency penalty = (slice_count - 1) * client_network_latency_ms
            total_latency_ms = round((slice_count - 1) * venue.client_network_latency_ms, 2)

            notes = (
                f"SYNTHETIC ICEBERG INITIALIZED [{req.symbol} on {venue.broker_name}]: Venue lacks native iceberg support. "
                f"Generated {slice_count} randomized child slices (Target Display = {req.target_display_quantity:,} ± {req.slice_randomization_pct*100:.0f}%). "
                f"Total client refill network latency penalty = {total_latency_ms:.1f}ms."
            )
            logger.warning(notes)

            return IcebergExecutionReport(
                symbol=req.symbol,
                broker_name=venue.broker_name,
                execution_mode=exec_mode,
                total_quantity=req.total_quantity,
                target_display_quantity=req.target_display_quantity,
                native_display_parameter_sent=None,
                total_slices_count=slice_count,
                synthetic_child_slices=slices,
                total_refill_latency_penalty_ms=total_latency_ms,
                status="INITIALIZED_SYNTHETIC_SIMULATION",
                audit_notes=notes
            )
