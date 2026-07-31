import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class MultiDayOrderConfig:
    symbol: str
    total_parent_quantity: float
    current_price: float
    adv_shares: float                    # Average Daily Volume
    max_daily_participation_pct: float = 0.10   # e.g., 0.10 (10% ADV)
    schedule_profile: str = "EQUAL_DAILY"        # 'EQUAL_DAILY', 'FRONT_LOADED', 'BACK_LOADED'
    volatility_daily_pct: float = 0.02           # Daily volatility (2%)
    temp_impact_coeff: float = 0.10              # Almgren-Chriss temp impact coefficient gamma
    perm_impact_coeff: float = 0.05              # Almgren-Chriss perm impact coefficient gamma_p

@dataclass
class DailySliceSchedule:
    day_index: int                       # 1, 2, ..., N
    target_quantity: float
    participation_pct_adv: float
    remaining_unexecuted_qty: float

@dataclass
class MultiDayScheduleReport:
    symbol: str
    total_parent_quantity: float
    adv_shares: float
    execution_horizon_days: int
    daily_participation_cap_shares: float
    daily_schedules: List[DailySliceSchedule]
    expected_temp_impact_bps: float
    expected_perm_impact_bps: float
    overnight_volatility_risk_usd: float
    status: str                         # 'SCHEDULE_GENERATED_SUCCESS'
    audit_notes: str

class MultiDayExecutionSchedulerEngine:
    """
    Almgren-Chriss institutional order execution scheduler for large parent orders (>10% ADV),
    calculating multi-day horizon slicing, ADV participation caps, and overnight volatility risk tradeoffs.
    """
    def __init__(self):
        pass

    def generate_multi_day_schedule(self, cfg: MultiDayOrderConfig) -> MultiDayScheduleReport:
        """
        Calculates required execution horizon N_days based on ADV participation limits and
        allocates daily slicing trajectories ('EQUAL_DAILY', 'FRONT_LOADED', 'BACK_LOADED').
        """
        if cfg.total_parent_quantity <= 0 or cfg.adv_shares <= 0 or cfg.current_price <= 0:
            raise ValueError("Total quantity, ADV, and price must be positive numbers.")

        # 1. Daily Limit & Execution Horizon Calculation
        daily_cap_shares = cfg.adv_shares * cfg.max_daily_participation_pct
        if daily_cap_shares <= 0:
            raise ValueError("Max daily participation cap shares must be positive.")

        n_days = max(1, int(math.ceil(cfg.total_parent_quantity / daily_cap_shares)))

        # 2. Daily Trajectory Allocation
        raw_weights: List[float] = []
        profile_upper = cfg.schedule_profile.upper()

        if profile_upper == "EQUAL_DAILY":
            raw_weights = [1.0] * n_days
        elif profile_upper == "FRONT_LOADED":
            # Exponentially decaying weights
            raw_weights = [math.exp(-0.3 * i) for i in range(n_days)]
        elif profile_upper == "BACK_LOADED":
            # Exponentially increasing weights
            raw_weights = [math.exp(0.3 * i) for i in range(n_days)]
        else:
            raw_weights = [1.0] * n_days

        weight_sum = sum(raw_weights)
        daily_slices: List[float] = [(w / weight_sum) * cfg.total_parent_quantity for w in raw_weights]

        # Enforce strict daily cap ceiling per day
        excess_qty = 0.0
        for i in range(n_days):
            if daily_slices[i] > daily_cap_shares:
                excess_qty += (daily_slices[i] - daily_cap_shares)
                daily_slices[i] = daily_cap_shares

        # Distribute excess evenly if cap overflowed
        if excess_qty > 0 and n_days > 1:
            for i in range(n_days):
                if daily_slices[i] < daily_cap_shares:
                    add_qty = min(excess_qty, daily_cap_shares - daily_slices[i])
                    daily_slices[i] += add_qty
                    excess_qty -= add_qty

        # 3. Construct Daily Slice Schedules & Track Inventory
        daily_schedules: List[DailySliceSchedule] = []
        rem_qty = cfg.total_parent_quantity

        for day in range(1, n_days + 1):
            qty_day = daily_slices[day - 1]
            rem_qty -= qty_day
            part_pct = (qty_day / cfg.adv_shares) * 100.0
            daily_schedules.append(DailySliceSchedule(
                day_index=day,
                target_quantity=round(qty_day, 2),
                participation_pct_adv=round(part_pct, 2),
                remaining_unexecuted_qty=round(max(0.0, rem_qty), 2)
            ))

        # 4. Impact & Overnight Volatility Risk Metrics
        avg_daily_qty = cfg.total_parent_quantity / n_days
        participation_ratio = avg_daily_qty / cfg.adv_shares

        # Almgren-Chriss Impact estimates (bps)
        temp_impact_bps = cfg.temp_impact_coeff * math.sqrt(participation_ratio) * 10000.0
        perm_impact_bps = cfg.perm_impact_coeff * participation_ratio * 10000.0

        # Overnight volatility risk = sqrt(sum(rem_qty_d^2)) * daily_vol_pct * price
        variance_rem = sum((s.remaining_unexecuted_qty * cfg.current_price) ** 2 for s in daily_schedules)
        overnight_vol_usd = math.sqrt(variance_rem) * cfg.volatility_daily_pct

        notes = (
            f"MULTI-DAY SCHEDULE GENERATED [{cfg.symbol}]: Total Parent Qty = {cfg.total_parent_quantity:,.0f} shares "
            f"({cfg.total_parent_quantity / cfg.adv_shares * 100:.1f}% ADV). Horizon = {n_days} days. "
            f"Daily Cap = {daily_cap_shares:,.0f} shares ({cfg.max_daily_participation_pct * 100:.1f}% ADV). "
            f"Expected Temp Impact = {temp_impact_bps:.1f} bps, Overnight Risk = ${overnight_vol_usd:,.2f}."
        )
        logger.info(notes)

        return MultiDayScheduleReport(
            symbol=cfg.symbol,
            total_parent_quantity=cfg.total_parent_quantity,
            adv_shares=cfg.adv_shares,
            execution_horizon_days=n_days,
            daily_participation_cap_shares=daily_cap_shares,
            daily_schedules=daily_schedules,
            expected_temp_impact_bps=round(temp_impact_bps, 2),
            expected_perm_impact_bps=round(perm_impact_bps, 2),
            overnight_volatility_risk_usd=round(overnight_vol_usd, 2),
            status="SCHEDULE_GENERATED_SUCCESS",
            audit_notes=notes
        )
