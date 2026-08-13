"""
backtest-infrastructure-cost-budgeting: Pre-flight cost estimation for
large-scale backtest sweeps.

Which billing model this fits
-----------------------------
Costs are modelled as **separately metered vCPU-hours and GB-hours**. That is
how serverless container platforms bill: AWS Fargate charges "based on the
vCPU, memory, Operating Systems, CPU Architecture, and storage resources used",
with CPU and memory as separate line items. Google Cloud Run bills the same
shape.

It is **not** how EC2 or Compute Engine bill. There you rent a whole instance
at one bundled hourly rate covering both vCPU and RAM, whether or not you use
it. To budget an instance-based fleet with this tool, put the full instance
hourly rate in ``cpu_hourly_rate``, set ``ram_hourly_rate=0.0``, and express
``cpu_hours_per_unit`` as instance-hours per unit — otherwise you double-count
memory that is already inside the instance price.

Bias direction
--------------
A budget guard that under-estimates is worse than one that over-estimates, so
the defaults here refuse to flatter the number:

* Non-finite and negative inputs are rejected rather than silently producing a
  meaningless total that compares False against the budget.
* Spot savings are not free. AWS states "it is always possible that your Spot
  Instance might be interrupted"; interrupted work has to be redone. Model that
  with ``spot_interruption_overhead``.
* Short units are not free either. Fargate bills "per second with a 1-minute
  minimum", so a sweep of thousands of few-second units costs far more than
  raw CPU-seconds suggest. Model that with ``minimum_billable_seconds``.

All rates are caller-supplied. This module hard-codes no cloud prices, because
published prices change and vary by region, architecture, and commitment.
"""
from dataclasses import dataclass
from typing import Any, Dict
import logging
import math

logger = logging.getLogger(__name__)

__all__ = [
    "CostBudgetError",
    "BacktestJobSpec",
    "CloudPricing",
    "BacktestCostBudgeter",
    "SECONDS_PER_HOUR",
    "DAYS_PER_BILLING_MONTH",
]

SECONDS_PER_HOUR = 3600.0
# Storage is quoted per GB-month. Providers prorate against actual calendar
# months (28-31 days); this 30-day convention is an approximation that
# over-states a 365-day retention by ~1.4% (365/30 = 12.17 "months").
DAYS_PER_BILLING_MONTH = 30.0


class CostBudgetError(ValueError):
    """Raised when a job spec or pricing table cannot produce a meaningful estimate."""


def _check_number(
    value: Any,
    label: str,
    *,
    minimum: float = 0.0,
    maximum: float = math.inf,
    allow_equal_min: bool = True,
) -> float:
    """Validates a numeric input, rejecting non-finite values.

    NaN is the dangerous case: ``float('nan') > budget`` is False, so a single
    NaN input silently reports the job as within budget — the precise failure a
    budget guard exists to prevent.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CostBudgetError(f"{label} must be a real number, got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise CostBudgetError(
            f"{label} is {value!r}; non-finite inputs make the budget comparison "
            "meaningless (NaN compares False against any budget)"
        )
    if allow_equal_min:
        if numeric < minimum:
            raise CostBudgetError(f"{label} must be >= {minimum}, got {numeric}")
    elif numeric <= minimum:
        raise CostBudgetError(f"{label} must be > {minimum}, got {numeric}")
    if numeric > maximum:
        raise CostBudgetError(f"{label} must be <= {maximum}, got {numeric}")
    return numeric


@dataclass
class BacktestJobSpec:
    """Dimensions of a planned sweep.

    Attributes:
        instruments: Number of instruments in the sweep.
        parameter_combinations: Number of parameter sets per instrument.
        cpu_hours_per_unit: Compute hours for one instrument/parameter unit. For
            instance-based billing, express this as instance-hours per unit.
        memory_gb_required: GB held concurrently by one unit while it runs.
        storage_gb_per_unit: Output storage produced per unit.
        storage_retention_days: How long that output is kept.
        parallel_overhead_factor: Multiplier on compute for coordination cost,
            contention, and scheduler waste. 1.0 means perfectly linear scaling,
            which real sweeps rarely achieve. 1.2 budgets 20% overhead.
        egress_gb_per_unit: Data transferred out per unit, if any.
    """

    instruments: float
    parameter_combinations: float
    cpu_hours_per_unit: float
    memory_gb_required: float
    storage_gb_per_unit: float
    storage_retention_days: float = 30.0
    parallel_overhead_factor: float = 1.0
    egress_gb_per_unit: float = 0.0

    def __post_init__(self) -> None:
        self.instruments = _check_number(self.instruments, "instruments")
        self.parameter_combinations = _check_number(
            self.parameter_combinations, "parameter_combinations"
        )
        self.cpu_hours_per_unit = _check_number(self.cpu_hours_per_unit, "cpu_hours_per_unit")
        self.memory_gb_required = _check_number(self.memory_gb_required, "memory_gb_required")
        self.storage_gb_per_unit = _check_number(self.storage_gb_per_unit, "storage_gb_per_unit")
        self.storage_retention_days = _check_number(
            self.storage_retention_days, "storage_retention_days"
        )
        self.parallel_overhead_factor = _check_number(
            self.parallel_overhead_factor,
            "parallel_overhead_factor",
            minimum=1.0,
        )
        self.egress_gb_per_unit = _check_number(self.egress_gb_per_unit, "egress_gb_per_unit")


@dataclass
class CloudPricing:
    """Caller-supplied rates. No prices are hard-coded in this module.

    Attributes:
        cpu_hourly_rate: Cost per vCPU-hour (or per instance-hour, for bundled
            instance billing — see the module docstring).
        ram_hourly_rate: Cost per GB-hour. Set 0.0 for bundled instance billing.
        storage_monthly_rate_per_gb: Cost per GB-month.
        spot_discount_multiplier: Fraction of on-demand price paid on spot;
            0.3 means a 70% discount. Must be in (0, 1].
        spot_interruption_overhead: Extra compute fraction expected from
            redoing work lost to interruptions. 0.15 budgets 15% rework. AWS
            does not guarantee spot availability, so 0.0 assumes perfect
            checkpointing and is optimistic for long-running units.
        minimum_billable_seconds: Billing quantum per unit. Fargate bills per
            second with a 1-minute minimum, so 60.0 is appropriate there; 0.0
            disables the floor.
        egress_rate_per_gb: Cost per GB of data transferred out.
    """

    cpu_hourly_rate: float
    ram_hourly_rate: float
    storage_monthly_rate_per_gb: float
    spot_discount_multiplier: float = 0.3
    spot_interruption_overhead: float = 0.0
    minimum_billable_seconds: float = 0.0
    egress_rate_per_gb: float = 0.0

    def __post_init__(self) -> None:
        self.cpu_hourly_rate = _check_number(self.cpu_hourly_rate, "cpu_hourly_rate")
        self.ram_hourly_rate = _check_number(self.ram_hourly_rate, "ram_hourly_rate")
        self.storage_monthly_rate_per_gb = _check_number(
            self.storage_monthly_rate_per_gb, "storage_monthly_rate_per_gb"
        )
        # A "discount" above 1.0 would silently inflate the bill; below or equal
        # to 0 would make compute free.
        self.spot_discount_multiplier = _check_number(
            self.spot_discount_multiplier,
            "spot_discount_multiplier",
            minimum=0.0,
            maximum=1.0,
            allow_equal_min=False,
        )
        self.spot_interruption_overhead = _check_number(
            self.spot_interruption_overhead, "spot_interruption_overhead"
        )
        self.minimum_billable_seconds = _check_number(
            self.minimum_billable_seconds, "minimum_billable_seconds"
        )
        self.egress_rate_per_gb = _check_number(self.egress_rate_per_gb, "egress_rate_per_gb")


class BacktestCostBudgeter:
    """Estimates sweep cost and flags budget breaches before the job is launched."""

    def __init__(self, pricing: CloudPricing, max_budget: float) -> None:
        if not isinstance(pricing, CloudPricing):
            raise CostBudgetError(
                f"pricing must be a CloudPricing, got {type(pricing).__name__}"
            )
        self.pricing = pricing
        self.max_budget = _check_number(max_budget, "max_budget")

    def estimate_costs(
        self, job: BacktestJobSpec, use_spot_instances: bool = False
    ) -> Dict[str, Any]:
        """Estimates total cost for a sweep.

        Args:
            job: Sweep dimensions and per-unit resource profile.
            use_spot_instances: Apply the spot discount and interruption overhead.

        Returns:
            A dict with ``total_units``, ``cpu_cost``, ``ram_cost``,
            ``storage_cost``, ``egress_cost``, ``total_cost``,
            ``billable_cpu_hours``, ``use_spot_instances``, ``max_budget``,
            ``budget_headroom``, and ``is_over_budget``.

            ``is_over_budget`` is True only when cost strictly exceeds the
            budget; a total exactly equal to the budget is treated as within it.

        Raises:
            CostBudgetError: If ``job`` is not a BacktestJobSpec.
        """
        if not isinstance(job, BacktestJobSpec):
            raise CostBudgetError(
                f"job must be a BacktestJobSpec, got {type(job).__name__}"
            )

        total_units = job.instruments * job.parameter_combinations

        # Apply the billing quantum per unit, not to the aggregate: each unit is
        # a separately billed task, so a sweep of many short units pays the
        # minimum many times over. This includes units with zero measured
        # runtime — launching a task still incurs the minimum — which biases the
        # estimate high, the safe direction for a budget guard. Set
        # minimum_billable_seconds=0.0 when modelling storage-only costs.
        minimum_hours = self.pricing.minimum_billable_seconds / SECONDS_PER_HOUR
        billable_hours_per_unit = max(job.cpu_hours_per_unit, minimum_hours)
        if minimum_hours > job.cpu_hours_per_unit > 0:
            logger.info(
                "Per-unit runtime %.6f h is below the %.0f s billing minimum; "
                "billing %.6f h per unit instead.",
                job.cpu_hours_per_unit, self.pricing.minimum_billable_seconds,
                billable_hours_per_unit,
            )

        billable_cpu_hours = (
            total_units * billable_hours_per_unit * job.parallel_overhead_factor
        )

        if use_spot_instances:
            # Pay a lower rate, but expect to buy more hours because interrupted
            # work is redone.
            compute_multiplier = self.pricing.spot_discount_multiplier * (
                1.0 + self.pricing.spot_interruption_overhead
            )
        else:
            compute_multiplier = 1.0

        cpu_cost = billable_cpu_hours * self.pricing.cpu_hourly_rate * compute_multiplier
        ram_cost = (
            billable_cpu_hours
            * job.memory_gb_required
            * self.pricing.ram_hourly_rate
            * compute_multiplier
        )

        total_storage_gb = total_units * job.storage_gb_per_unit
        retention_months = job.storage_retention_days / DAYS_PER_BILLING_MONTH
        storage_cost = (
            total_storage_gb * self.pricing.storage_monthly_rate_per_gb * retention_months
        )

        egress_cost = (
            total_units * job.egress_gb_per_unit * self.pricing.egress_rate_per_gb
        )

        total_cost = cpu_cost + ram_cost + storage_cost + egress_cost
        is_over_budget = total_cost > self.max_budget

        if is_over_budget:
            logger.warning(
                "Estimated cost %.2f exceeds budget %.2f by %.2f (%d units).",
                total_cost, self.max_budget, total_cost - self.max_budget, int(total_units),
            )

        return {
            "total_units": total_units,
            "billable_cpu_hours": billable_cpu_hours,
            "cpu_cost": cpu_cost,
            "ram_cost": ram_cost,
            "storage_cost": storage_cost,
            "egress_cost": egress_cost,
            "total_cost": total_cost,
            "use_spot_instances": use_spot_instances,
            "max_budget": self.max_budget,
            "budget_headroom": self.max_budget - total_cost,
            "is_over_budget": is_over_budget,
        }
