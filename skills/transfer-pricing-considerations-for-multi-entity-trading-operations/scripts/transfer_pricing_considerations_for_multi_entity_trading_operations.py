import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class TPMethodology(Enum):
    COST_PLUS = "COST_PLUS"               # Cost Plus Method (Direct Costs + Benchmark Markup %)
    CUP = "CUP"                           # Comparable Uncontrolled Price Method
    TNMM = "TNMM"                         # Transactional Net Margin Method (Berry Ratio / Net Margin)
    PROFIT_SPLIT = "PROFIT_SPLIT"         # Residual Profit Split Method (DEMPE Analysis)


class EntityType(Enum):
    IP_OWNER = "IP_OWNER"                 # Algo Strategy & Quant Research IP Holder
    INVESTMENT_MANAGER = "INVESTMENT_MANAGER" # Regulated Investment Advisor
    EXECUTION_HUB = "EXECUTION_HUB"       # Low-Latency DMA / Broker Execution Entity
    CAPITAL_PROVIDER = "CAPITAL_PROVIDER" # Offshore Fund / Balance Sheet Entity


class TransferPricingError(Exception):
    """Base exception for transfer pricing calculation errors."""
    pass


@dataclass
class LegalEntity:
    entity_id: str
    name: str
    jurisdiction: str
    entity_type: EntityType
    tax_rate_pct: float  # Corporate income tax rate (%)


@dataclass
class IntercompanyTransaction:
    transaction_id: str
    provider_entity_id: str
    recipient_entity_id: str
    service_description: str
    base_cost_usd: float
    tp_method: TPMethodology
    markup_pct: float = 10.0           # E.g. 10% Cost-Plus markup
    benchmark_cup_rate_usd: float = 0.0 # Benchmark third-party price for CUP method
    volume_units: float = 1.0


@dataclass
class DEMPEContribution:
    entity_id: str
    development_weight: float   # Weight 0-1 for Development of IP
    enhancement_weight: float   # Weight 0-1 for Enhancement
    maintenance_weight: float   # Weight 0-1 for Maintenance
    protection_weight: float    # Weight 0-1 for Legal Protection
    exploitation_weight: float  # Weight 0-1 for Commercial Exploitation

    @property
    def total_dempe_score(self) -> float:
        """Composite DEMPE score (equal weighting across 5 OECD dimensions)."""
        return (
            self.development_weight
            + self.enhancement_weight
            + self.maintenance_weight
            + self.protection_weight
            + self.exploitation_weight
        ) / 5.0


@dataclass
class TransferPricingSettlement:
    transaction_id: str
    provider_name: str
    recipient_name: str
    methodology: TPMethodology
    base_cost_usd: float
    arm_length_fee_usd: float
    intercompany_markup_usd: float
    berry_ratio: float


@dataclass
class ProfitSplitAllocation:
    total_global_pnl_usd: float
    entity_allocations_usd: Dict[str, float]
    dempe_percentages: Dict[str, float]


class TransferPricingEngine:
    """
    Institutional OECD Transfer Pricing Engine for Multi-Entity Trading Operations.
    
    Calculates arm's length intercompany fees, cost-plus markups, CUP benchmarks,
    DEMPE residual profit split allocations, Berry Ratios, and OECD BEPS compliance logs.
    """

    def __init__(self):
        self.entities: Dict[str, LegalEntity] = {}
        logger.info("Initialized Institutional Transfer Pricing Engine")

    def register_entity(self, entity: LegalEntity) -> None:
        """Registers a group legal entity."""
        self.entities[entity.entity_id] = entity
        logger.info(f"Registered entity: {entity.name} [{entity.jurisdiction}]")

    @staticmethod
    def calculate_cost_plus_fee(base_cost: float, markup_pct: float) -> Tuple[float, float]:
        """Calculates arm's length fee under Cost-Plus Method: Base Cost * (1 + Markup %)."""
        if base_cost < 0:
            raise TransferPricingError(f"Base cost cannot be negative: {base_cost}")
        markup_usd = base_cost * (markup_pct / 100.0)
        arm_length_fee = base_cost + markup_usd
        return arm_length_fee, markup_usd

    @staticmethod
    def calculate_cup_fee(volume_units: float, benchmark_unit_price: float) -> float:
        """Calculates arm's length fee under CUP Method: Volume * Third-Party Price."""
        if volume_units < 0 or benchmark_unit_price < 0:
            raise TransferPricingError("Volume and benchmark price must be non-negative.")
        return volume_units * benchmark_unit_price

    @staticmethod
    def calculate_berry_ratio(gross_profit_usd: float, operating_expenses_usd: float) -> float:
        """
        Calculates Berry Ratio (Gross Profit / Operating Expenses).
        OECD benchmark standard for financial service entities: 1.05 - 1.25.
        """
        if operating_expenses_usd <= 0:
            raise TransferPricingError(f"Operating expenses must be positive: {operating_expenses_usd}")
        return gross_profit_usd / operating_expenses_usd

    def process_intercompany_transaction(self, tx: IntercompanyTransaction) -> TransferPricingSettlement:
        """Processes an intercompany transaction to compute arm's length settlement fee."""
        if tx.provider_entity_id not in self.entities or tx.recipient_entity_id not in self.entities:
            raise TransferPricingError(f"Entities not registered: Provider={tx.provider_entity_id}, Recipient={tx.recipient_entity_id}")

        provider = self.entities[tx.provider_entity_id]
        recipient = self.entities[tx.recipient_entity_id]

        if tx.tp_method == TPMethodology.COST_PLUS:
            arm_length_fee, markup_usd = self.calculate_cost_plus_fee(tx.base_cost_usd, tx.markup_pct)
        elif tx.tp_method == TPMethodology.CUP:
            arm_length_fee = self.calculate_cup_fee(tx.volume_units, tx.benchmark_cup_rate_usd)
            markup_usd = arm_length_fee - tx.base_cost_usd
        elif tx.tp_method == TPMethodology.TNMM:
            # TNMM based on standard 10% net cost-plus fallback
            arm_length_fee, markup_usd = self.calculate_cost_plus_fee(tx.base_cost_usd, tx.markup_pct)
        else:
            raise TransferPricingError(f"Use calculate_profit_split() for method: {tx.tp_method}")

        gross_profit = arm_length_fee
        berry_ratio = self.calculate_berry_ratio(gross_profit, tx.base_cost_usd)

        logger.info(
            f"TP Settlement [{tx.transaction_id}]: {provider.name} -> {recipient.name} | "
            f"Fee=${arm_length_fee:,.2f} (Markup=${markup_usd:,.2f}, Berry Ratio={berry_ratio:.2f})"
        )

        return TransferPricingSettlement(
            transaction_id=tx.transaction_id,
            provider_name=provider.name,
            recipient_name=recipient.name,
            methodology=tx.tp_method,
            base_cost_usd=tx.base_cost_usd,
            arm_length_fee_usd=arm_length_fee,
            intercompany_markup_usd=markup_usd,
            berry_ratio=berry_ratio,
        )

    def calculate_profit_split(
        self, total_global_pnl_usd: float, dempe_contributions: List[DEMPEContribution]
    ) -> ProfitSplitAllocation:
        """
        Splits global trading PnL across legal entities based on OECD DEMPE contribution weights.
        """
        if not dempe_contributions:
            raise TransferPricingError("DEMPE contributions list cannot be empty.")

        total_dempe_sum = sum(d.total_dempe_score for d in dempe_contributions)
        if total_dempe_sum <= 0:
            raise TransferPricingError("Total DEMPE score across entities must be greater than zero.")

        allocations_usd = {}
        dempe_percentages = {}

        for d in dempe_contributions:
            pct = d.total_dempe_score / total_dempe_sum
            allocations_usd[d.entity_id] = total_global_pnl_usd * pct
            dempe_percentages[d.entity_id] = pct * 100.0

        logger.info(f"Calculated Profit Split for Global PnL ${total_global_pnl_usd:,.2f} across {len(dempe_contributions)} entities.")
        return ProfitSplitAllocation(
            total_global_pnl_usd=total_global_pnl_usd,
            entity_allocations_usd=allocations_usd,
            dempe_percentages=dempe_percentages,
        )

