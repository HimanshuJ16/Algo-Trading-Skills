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

@dataclass
class CloudPricing:
    # Hourly cost per vCPU
    cpu_hourly_rate: float
    # Hourly cost per GB of RAM
    ram_hourly_rate: float
    # Monthly cost per GB of storage (will scale to hours for short runs, or assume persistent)
    storage_monthly_rate_per_gb: float

class BacktestCostBudgeter:
    def __init__(self, pricing: CloudPricing, max_budget: float):
        self.pricing = pricing
        self.max_budget = max_budget

    def estimate_costs(self, job: BacktestJobSpec) -> dict:
        total_units = job.instruments * job.parameter_combinations
        
        total_cpu_hours = total_units * job.cpu_hours_per_unit
        
        # CPU Cost
        cpu_cost = total_cpu_hours * self.pricing.cpu_hourly_rate
        
        # RAM Cost (assuming memory is allocated for the duration of the CPU hours)
        ram_cost = total_cpu_hours * job.memory_gb_required * self.pricing.ram_hourly_rate
        
        # Storage Cost (assuming storage persists for at least 1 month as standard billing cycle)
        total_storage_gb = total_units * job.storage_gb_per_unit
        storage_cost = total_storage_gb * self.pricing.storage_monthly_rate_per_gb
        
        total_cost = cpu_cost + ram_cost + storage_cost
        
        return {
            "total_units": total_units,
            "cpu_cost": cpu_cost,
            "ram_cost": ram_cost,
            "storage_cost": storage_cost,
            "total_cost": total_cost,
            "is_over_budget": total_cost > self.max_budget
        }
