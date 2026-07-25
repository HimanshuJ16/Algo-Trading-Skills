from dataclasses import dataclass
from typing import Optional

@dataclass
class BacktestJobSpec:
    instruments: int
    parameter_combinations: int
    # Per single combination-instrument pair or baseline unit
    cpu_hours_per_unit: float
    memory_gb_required: float
    storage_gb_per_unit: float
    storage_retention_days: float = 30.0  # Allow fractional month storage costs

@dataclass
class CloudPricing:
    # Hourly cost per vCPU
    cpu_hourly_rate: float
    # Hourly cost per GB of RAM
    ram_hourly_rate: float
    # Monthly cost per GB of storage (will scale to hours for short runs, or assume persistent)
    storage_monthly_rate_per_gb: float
    # Multiplier for spot/preemptible instances (e.g. 0.3 means a 70% discount)
    spot_discount_multiplier: float = 0.3

class BacktestCostBudgeter:
    def __init__(self, pricing: CloudPricing, max_budget: float):
        self.pricing = pricing
        self.max_budget = max_budget

    def estimate_costs(self, job: BacktestJobSpec, use_spot_instances: bool = False) -> dict:
        total_units = job.instruments * job.parameter_combinations
        
        total_cpu_hours = total_units * job.cpu_hours_per_unit
        
        compute_multiplier = self.pricing.spot_discount_multiplier if use_spot_instances else 1.0

        # CPU Cost
        cpu_cost = total_cpu_hours * self.pricing.cpu_hourly_rate * compute_multiplier
        
        # RAM Cost (assuming memory is allocated for the duration of the CPU hours)
        ram_cost = total_cpu_hours * job.memory_gb_required * self.pricing.ram_hourly_rate * compute_multiplier
        
        # Storage Cost based on actual retention days (assuming 30 days = 1 month)
        total_storage_gb = total_units * job.storage_gb_per_unit
        retention_months = job.storage_retention_days / 30.0
        storage_cost = total_storage_gb * self.pricing.storage_monthly_rate_per_gb * retention_months
        
        total_cost = cpu_cost + ram_cost + storage_cost
        
        return {
            "total_units": total_units,
            "cpu_cost": cpu_cost,
            "ram_cost": ram_cost,
            "storage_cost": storage_cost,
            "total_cost": total_cost,
            "use_spot_instances": use_spot_instances,
            "is_over_budget": total_cost > self.max_budget
        }
