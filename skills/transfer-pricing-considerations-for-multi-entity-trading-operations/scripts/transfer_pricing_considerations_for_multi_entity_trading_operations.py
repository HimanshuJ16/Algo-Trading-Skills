"""
Intercompany pricing arithmetic for a trading group spread across legal entities.

Scope note: this engine computes arm's length *settlement amounts* from a
methodology, a cost base, and benchmark inputs that the caller supplies. It is
not a benchmarking database and it invents no markups, no Berry ratio range and
no allocation keys. Arm's length ranges come from a benchmarking study of
comparable independent parties (OECD TPG 2022 Chapter III); the two published
figures that are fixed rather than benchmarked -- the OECD 5% simplified markup
for low value-adding intra-group services (TPG 2022 para. 7.61) and the US
Services Cost Method at cost with no markup (Treas. Reg. s.1.482-9(b)) -- both
exclude research, scientific and financial-transaction services, which is most
of what a quant trading group charges between entities (TPG 2022 para. 7.47;
Treas. Reg. s.1.482-9(b)(4)).

The profit split implemented here is a *contribution* analysis (TPG 2022 para.
2.150) unless routine returns are supplied, in which case it becomes a
*residual* analysis (para. 2.152). DEMPE (TPG 2022 Chapter VI) is a functional
analysis framework for deciding which entity is entitled to intangible return;
the OECD publishes no numeric DEMPE score and no formula turning one into a
profit share. The weights here are a caller-supplied allocation key that must
be justified by evidence of relative value creation and be measurable with
reasonable reliability (paras. 2.166, 2.170, 2.171).

Output is decision support for a tax adviser preparing transfer pricing
documentation, not a filing position and not a tax opinion.
"""
import logging
import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# --- Profit split basis ---------------------------------------------------
# OECD TPG 2022 para. 2.150 (contribution analysis) divides the whole of the
# relevant profits by reference to relative contributions. Para. 2.152
# (residual analysis) is a two-step approach: benchmarkable contributions are
# first rewarded with a routine return, and only the residual is divided. They
# are different analyses and produce different numbers; labelling one as the
# other misdescribes the method in the Local File.
SPLIT_BASIS_CONTRIBUTION = "CONTRIBUTION_ANALYSIS"
SPLIT_BASIS_RESIDUAL = "RESIDUAL_ANALYSIS"

# The five DEMPE functions of TPG 2022 Chapter VI.
DEMPE_DIMENSIONS: Tuple[str, ...] = (
    "development",
    "enhancement",
    "maintenance",
    "protection",
    "exploitation",
)

_CENTS = Decimal("0.01")
_HUNDRED = Decimal("100")


class TPMethodology(Enum):
    COST_PLUS = "COST_PLUS"               # Cost Plus Method (cost base + benchmarked markup %)
    CUP = "CUP"                           # Comparable Uncontrolled Price Method
    TNMM = "TNMM"                         # Transactional Net Margin Method (net cost-plus PLI here)
    PROFIT_SPLIT = "PROFIT_SPLIT"         # Transactional Profit Split (use calculate_profit_split)


class EntityType(Enum):
    IP_OWNER = "IP_OWNER"                      # Algo strategy & quant research IP holder
    INVESTMENT_MANAGER = "INVESTMENT_MANAGER"  # Regulated investment adviser / manager
    EXECUTION_HUB = "EXECUTION_HUB"            # Low-latency DMA / broker execution entity
    CAPITAL_PROVIDER = "CAPITAL_PROVIDER"      # Offshore fund / balance sheet entity


class TransferPricingError(Exception):
    """Base exception for transfer pricing calculation errors."""
    pass


def _money(value: float) -> float:
    """
    Rounds a monetary amount to cents using ROUND_HALF_UP.

    Python's built-in round() is round-half-even, so round(0.125, 2) is 0.12 --
    an intercompany invoice figure that will not tie out against the ledger
    posting or the Local File prepared under conventional half-up rounding.
    """
    return float(Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP))


def _money_mul(amount: float, multiplier: float) -> float:
    """
    Multiplies a money amount by a factor in exact decimal arithmetic, then
    rounds half-up to cents.

    Rounding a binary float product is not sufficient: 1.50 * 0.15 evaluates to
    0.22499999999999998 in float, which rounds to 0.22 even under half-up, while
    the exact decimal product 0.2250 rounds to 0.23.
    """
    product = Decimal(str(amount)) * Decimal(str(multiplier))
    return float(product.quantize(_CENTS, rounding=ROUND_HALF_UP))


def _markup_amount(base_cost: float, markup_pct: float) -> float:
    """Computes base_cost * markup_pct / 100 in exact decimal arithmetic."""
    product = (Decimal(str(base_cost)) * Decimal(str(markup_pct))) / _HUNDRED
    return float(product.quantize(_CENTS, rounding=ROUND_HALF_UP))


def _require_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TransferPricingError(
            f"{field_name} must be a number, got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise TransferPricingError(f"{field_name} must be finite, got {number}")
    return number


def _require_non_negative(value: object, field_name: str) -> float:
    number = _require_number(value, field_name)
    if number < 0:
        raise TransferPricingError(f"{field_name} must be non-negative, got {number}")
    return number


@dataclass
class LegalEntity:
    entity_id: str
    name: str
    jurisdiction: str
    entity_type: EntityType
    tax_rate_pct: float  # Headline corporate income tax rate (%), carried for context only


@dataclass
class IntercompanyTransaction:
    """
    One controlled service transaction between two registered group entities.

    `base_cost_usd` is the cost base the markup is applied to under COST_PLUS
    and TNMM. Which costs belong in it is a transfer pricing decision, not an
    arithmetic one: pass-through costs are excluded from the marked-up pool
    under the OECD simplified approach (TPG 2022 paras. 7.34, 7.61), while the
    IRAS routine-support-services concession requires *all* costs relating to
    the service to be in the base. Supply the base you can defend.

    `cogs_usd` and `operating_expenses_usd` describe the *provider's* own P&L
    and exist only for the Berry ratio. They are optional and are never
    inferred: the Berry ratio is gross profit over operating expenses (TPG 2022
    para. 2.106), and the OECD warns it is very sensitive to whether a cost is
    classified as an operating expense (para. 2.107). Assuming that split
    silently degrades the ratio into the cost-plus markup factor.
    """
    transaction_id: str
    provider_entity_id: str
    recipient_entity_id: str
    service_description: str
    base_cost_usd: float
    tp_method: TPMethodology
    markup_pct: float = 10.0             # Benchmarked markup; no default is arm's length per se
    benchmark_cup_rate_usd: float = 0.0  # Third-party unit price for the CUP method
    volume_units: float = 1.0
    cogs_usd: Optional[float] = None
    operating_expenses_usd: Optional[float] = None


@dataclass
class DEMPEContribution:
    """
    One entity's relative contribution across the five DEMPE functions.

    Each weight is a relative score in [0, 1] describing how much of that
    function this entity performs and controls. DEMPE itself (TPG 2022 Chapter
    VI) is a functional analysis used to decide entitlement to intangible
    return -- legal ownership alone confers no right to retain it (para. 6.42).
    The OECD publishes no numeric DEMPE score, so these weights are an
    allocation key the taxpayer must justify, not a regulator-supplied formula.
    """
    entity_id: str
    development_weight: float
    enhancement_weight: float
    maintenance_weight: float
    protection_weight: float
    exploitation_weight: float

    def __post_init__(self) -> None:
        if not self.entity_id or not str(self.entity_id).strip():
            raise TransferPricingError("entity_id must be a non-empty string")
        for dimension in DEMPE_DIMENSIONS:
            field_name = f"{dimension}_weight"
            weight = _require_number(getattr(self, field_name), field_name)
            if not 0.0 <= weight <= 1.0:
                raise TransferPricingError(
                    f"{field_name} must be within [0.0, 1.0], got {weight}. Weights are "
                    "relative contribution scores, not percentages or currency amounts."
                )
            setattr(self, field_name, weight)

    def weighted_score(self, dimension_weights: Optional[Dict[str, float]] = None) -> float:
        """
        Combines the five function scores into a single allocation key.

        With no `dimension_weights` the five functions are weighted equally.
        That is a modelling default, not an OECD rule: the Guidelines allow a
        key built from a weighting of multiple factors (para. 2.170) but require
        it to reflect the key contributions to value (para. 2.166) and to be
        determinable with reasonable reliability (para. 2.171). Development and
        exploitation of a trading algorithm are rarely worth the same, so an
        equal weighting needs the same evidential support as any other.

        `dimension_weights`, when supplied, must already be normalised to sum
        to 1; `TransferPricingEngine.calculate_profit_split` normalises it.
        """
        if dimension_weights is None:
            return sum(
                getattr(self, f"{d}_weight") for d in DEMPE_DIMENSIONS
            ) / float(len(DEMPE_DIMENSIONS))
        return sum(
            getattr(self, f"{d}_weight") * dimension_weights[d] for d in DEMPE_DIMENSIONS
        )

    @property
    def total_dempe_score(self) -> float:
        """Equal-weighted composite score. See `weighted_score` for the caveat."""
        return self.weighted_score()


@dataclass
class TransferPricingSettlement:
    transaction_id: str
    provider_name: str
    recipient_name: str
    methodology: TPMethodology
    base_cost_usd: float
    arm_length_fee_usd: float
    intercompany_markup_usd: float
    # None when the provider's COGS / operating expense split was not supplied.
    # A Berry ratio computed from an assumed split is not a Berry ratio.
    berry_ratio: Optional[float]
    profit_level_indicator: str
    warnings: Tuple[str, ...] = field(default_factory=tuple)


@dataclass
class ProfitSplitAllocation:
    total_global_pnl_usd: float
    entity_allocations_usd: Dict[str, float]
    dempe_percentages: Dict[str, float]
    split_basis: str = SPLIT_BASIS_CONTRIBUTION
    routine_returns_usd: Dict[str, float] = field(default_factory=dict)
    # The amount actually divided by the allocation key: the whole of the
    # combined profit under a contribution analysis, or the residual left
    # after routine returns under a residual analysis. Read `split_basis`
    # before describing this figure as a residual.
    amount_split_by_key_usd: float = 0.0
    warnings: Tuple[str, ...] = field(default_factory=tuple)


class TransferPricingEngine:
    """
    Computes intercompany settlement amounts and profit split allocations for a
    multi-entity trading group.

    Markups, CUP benchmark prices and allocation keys are caller-supplied. The
    engine holds no benchmarking database and will not substitute a default for
    a missing benchmark, because no single default is arm's length across
    service lines and jurisdictions.
    """

    def __init__(self) -> None:
        self.entities: Dict[str, LegalEntity] = {}
        logger.info("Initialized transfer pricing engine")

    def register_entity(self, entity: LegalEntity) -> None:
        """Registers a group legal entity, replacing any entity with the same id."""
        if not isinstance(entity, LegalEntity):
            raise TransferPricingError(
                f"entity must be a LegalEntity, got {type(entity).__name__}"
            )
        if not entity.entity_id or not str(entity.entity_id).strip():
            raise TransferPricingError("entity_id must be a non-empty string")
        _require_number(entity.tax_rate_pct, "entity.tax_rate_pct")
        self.entities[entity.entity_id] = entity
        logger.info(
            "Registered entity %s [%s] as %s",
            entity.name, entity.jurisdiction, entity.entity_type.value,
        )

    @staticmethod
    def calculate_cost_plus_fee(base_cost: float, markup_pct: float) -> Tuple[float, float]:
        """
        Cost Plus fee: base cost * (1 + markup %). Returns (fee, markup amount).

        A markup below -100% would invert the fee, and a negative fee is not a
        service charge; it is rejected rather than returned. A markup of exactly
        0% is permitted and is meaningful -- it is the US Services Cost Method
        charge (Treas. Reg. s.1.482-9(b)) and an IRAS strict pass-through
        cost-pooling charge.
        """
        base = _require_non_negative(base_cost, "base_cost")
        markup = _require_number(markup_pct, "markup_pct")
        if markup < -100.0:
            raise TransferPricingError(
                f"markup_pct {markup} is below -100% and would produce a negative fee. "
                "Express the markup as a percentage (10.0 for 10%), not a fraction."
            )
        markup_usd = _markup_amount(base, markup)
        return _money(base + markup_usd), markup_usd

    @staticmethod
    def calculate_cup_fee(volume_units: float, benchmark_unit_price: float) -> float:
        """CUP fee: units transacted * the third-party benchmark unit price."""
        units = _require_non_negative(volume_units, "volume_units")
        price = _require_non_negative(benchmark_unit_price, "benchmark_unit_price")
        return _money_mul(units, price)

    @staticmethod
    def calculate_berry_ratio(gross_profit_usd: float, operating_expenses_usd: float) -> float:
        """
        Berry ratio: gross profit / operating expenses (OECD TPG 2022 para. 2.106),
        where gross profit is net sales less cost of goods sold, excluding
        interest and extraneous income.

        The OECD publishes no benchmark range for this ratio. An arm's length
        range comes from a benchmarking study of comparable independent service
        providers; para. 2.107 warns the ratio is very sensitive to whether a
        cost is classified as an operating expense, and that it is an
        appropriate indicator only where the value of the function performed is
        proportional to operating expenses and is not materially affected by the
        value of the product handled.
        """
        gross_profit = _require_number(gross_profit_usd, "gross_profit_usd")
        opex = _require_number(operating_expenses_usd, "operating_expenses_usd")
        if opex <= 0:
            raise TransferPricingError(
                f"operating_expenses_usd must be positive to form a Berry ratio, got {opex}"
            )
        return gross_profit / opex

    def _resolve_parties(
        self, tx: IntercompanyTransaction
    ) -> Tuple[LegalEntity, LegalEntity]:
        missing = [
            eid for eid in (tx.provider_entity_id, tx.recipient_entity_id)
            if eid not in self.entities
        ]
        if missing:
            raise TransferPricingError(
                f"Entities not registered: {missing}. Register every party before pricing "
                "a controlled transaction."
            )
        if tx.provider_entity_id == tx.recipient_entity_id:
            raise TransferPricingError(
                f"provider and recipient are the same entity ({tx.provider_entity_id}); "
                "an entity cannot transact with itself at arm's length."
            )
        return self.entities[tx.provider_entity_id], self.entities[tx.recipient_entity_id]

    def _berry_ratio_for(
        self, tx: IntercompanyTransaction, arm_length_fee: float
    ) -> Tuple[Optional[float], List[str]]:
        """
        Computes the provider's Berry ratio when -- and only when -- the caller
        has supplied the COGS / operating expense split it depends on.
        """
        warnings: List[str] = []
        if tx.operating_expenses_usd is None:
            warnings.append(
                "Berry ratio not computed: provider operating_expenses_usd was not supplied. "
                "The ratio is gross profit over operating expenses (TPG 2022 para. 2.106) and "
                "is very sensitive to the COGS/opex classification (para. 2.107), so it is "
                "not inferred from the cost base."
            )
            return None, warnings
        opex = _require_number(tx.operating_expenses_usd, "operating_expenses_usd")
        if opex <= 0:
            warnings.append(
                f"Berry ratio not computed: operating_expenses_usd is {opex}, which cannot "
                "form a ratio."
            )
            return None, warnings
        if tx.cogs_usd is None:
            warnings.append(
                "cogs_usd not supplied; the Berry ratio was computed on the assumption that "
                "the provider has no cost of goods sold, so gross profit equals the fee."
            )
        cogs = _require_non_negative(
            tx.cogs_usd if tx.cogs_usd is not None else 0.0, "cogs_usd"
        )
        gross_profit = _money(arm_length_fee - cogs)
        return self.calculate_berry_ratio(gross_profit, opex), warnings

    def process_intercompany_transaction(
        self, tx: IntercompanyTransaction
    ) -> TransferPricingSettlement:
        """
        Prices one controlled transaction and returns the settlement to post to
        the intercompany ledger.

        PROFIT_SPLIT is rejected here by design: a profit split is a two-sided
        analysis of combined profits, not a per-invoice charge. Use
        `calculate_profit_split`.
        """
        if not isinstance(tx, IntercompanyTransaction):
            raise TransferPricingError(
                f"tx must be an IntercompanyTransaction, got {type(tx).__name__}"
            )
        provider, recipient = self._resolve_parties(tx)
        base_cost = _require_non_negative(tx.base_cost_usd, "base_cost_usd")
        warnings: List[str] = []

        if tx.tp_method is TPMethodology.COST_PLUS:
            arm_length_fee, markup_usd = self.calculate_cost_plus_fee(base_cost, tx.markup_pct)
            pli = "GROSS_COST_PLUS_MARKUP"
        elif tx.tp_method is TPMethodology.TNMM:
            # TNMM tests a *net* profit indicator, so the markup here is a net
            # cost-plus PLI (full cost mark-up), not the gross-margin Cost Plus
            # Method of TPG 2022 Chapter II Part II. The arithmetic coincides;
            # the cost base and the comparable set do not, and the Local File
            # has to say which indicator was actually tested.
            arm_length_fee, markup_usd = self.calculate_cost_plus_fee(base_cost, tx.markup_pct)
            pli = "NET_COST_PLUS_MARKUP"
            warnings.append(
                "TNMM applied using a net cost-plus PLI on base_cost_usd. Confirm the cost "
                "base is the provider's full operating cost rather than a gross-margin cost "
                "of sales, and that the comparable set was screened on the same indicator."
            )
        elif tx.tp_method is TPMethodology.CUP:
            arm_length_fee = self.calculate_cup_fee(tx.volume_units, tx.benchmark_cup_rate_usd)
            markup_usd = _money(arm_length_fee - base_cost)
            pli = "COMPARABLE_UNCONTROLLED_PRICE"
            if markup_usd < 0:
                below_cost = (
                    f"CUP fee ${arm_length_fee:,.2f} is below the provider's cost base "
                    f"${base_cost:,.2f}. A provider that bears cost and risk while pricing "
                    "below cost needs a documented commercial rationale; a tax authority "
                    "will otherwise treat the shortfall as a non-arm's-length subsidy."
                )
                warnings.append(below_cost)
                logger.warning("TP settlement %s: %s", tx.transaction_id, below_cost)
        elif tx.tp_method is TPMethodology.PROFIT_SPLIT:
            raise TransferPricingError(
                "PROFIT_SPLIT is a two-sided analysis of combined profits and cannot be "
                "priced as a single invoice; use calculate_profit_split()."
            )
        else:
            raise TransferPricingError(f"Unsupported transfer pricing method: {tx.tp_method}")

        berry_ratio, berry_warnings = self._berry_ratio_for(tx, arm_length_fee)
        warnings.extend(berry_warnings)

        if provider.jurisdiction == recipient.jurisdiction:
            warnings.append(
                f"Provider and recipient are both in {provider.jurisdiction}; this is a "
                "domestic controlled transaction. Domestic transfer pricing rules may still "
                "apply, but cross-border documentation and treaty analysis do not."
            )

        logger.info(
            "TP settlement %s: %s -> %s | method=%s fee=%.2f markup=%.2f berry=%s",
            tx.transaction_id, provider.name, recipient.name, tx.tp_method.value,
            arm_length_fee, markup_usd,
            f"{berry_ratio:.4f}" if berry_ratio is not None else "n/a",
        )
        # The full list is returned as structured output; only the genuine risk
        # flags above are logged at WARNING, so a clean settlement stays quiet.
        for warning in warnings:
            logger.info("TP settlement %s note: %s", tx.transaction_id, warning)

        return TransferPricingSettlement(
            transaction_id=tx.transaction_id,
            provider_name=provider.name,
            recipient_name=recipient.name,
            methodology=tx.tp_method,
            base_cost_usd=base_cost,
            arm_length_fee_usd=arm_length_fee,
            intercompany_markup_usd=markup_usd,
            berry_ratio=berry_ratio,
            profit_level_indicator=pli,
            warnings=tuple(warnings),
        )

    def calculate_profit_split(
        self,
        total_global_pnl_usd: float,
        dempe_contributions: List[DEMPEContribution],
        routine_returns_usd: Optional[Dict[str, float]] = None,
        dimension_weights: Optional[Dict[str, float]] = None,
    ) -> ProfitSplitAllocation:
        """
        Splits combined trading profits across entities using DEMPE-based
        allocation keys.

        With `routine_returns_usd` omitted this is a contribution analysis (TPG
        2022 para. 2.150): the whole of the relevant profits is divided by the
        keys. Supply `routine_returns_usd` -- benchmarked routine rewards for
        the contributions that *can* be priced with a one-sided method -- to run
        a residual analysis (para. 2.152) instead: routine returns are allocated
        first and only the residual is divided by the keys.

        `dimension_weights` overrides the equal weighting of the five DEMPE
        functions; it is normalised to sum to 1 before use.
        """
        if not dempe_contributions:
            raise TransferPricingError("dempe_contributions list cannot be empty.")
        entity_ids = [d.entity_id for d in dempe_contributions]
        duplicates = {e for e in entity_ids if entity_ids.count(e) > 1}
        if duplicates:
            raise TransferPricingError(
                f"Duplicate entity ids in dempe_contributions: {sorted(duplicates)}"
            )
        total_pnl = _money(_require_number(total_global_pnl_usd, "total_global_pnl_usd"))
        warnings: List[str] = []

        normalised_dims = self._normalise_dimension_weights(dimension_weights)
        scores = {d.entity_id: d.weighted_score(normalised_dims) for d in dempe_contributions}
        total_score = sum(scores.values())
        if total_score <= 0:
            raise TransferPricingError(
                "Total DEMPE score across entities must be greater than zero; every "
                "contribution weight is zero, so there is no allocation key."
            )

        routine = self._validate_routine_returns(routine_returns_usd, set(entity_ids))
        if routine is None:
            split_basis = SPLIT_BASIS_CONTRIBUTION
            routine_map: Dict[str, float] = {}
            splittable = total_pnl
        else:
            split_basis = SPLIT_BASIS_RESIDUAL
            routine_map = routine
            total_routine = _money(sum(routine_map.values()))
            splittable = _money(total_pnl - total_routine)
            if splittable < 0:
                warnings.append(
                    f"Routine returns of ${total_routine:,.2f} exceed combined profits of "
                    f"${total_pnl:,.2f}; the residual is negative. Under a residual analysis "
                    "a system loss is normally borne by the entities that control the "
                    "economically significant risks, which is not necessarily the split the "
                    "DEMPE key applied here produces."
                )

        if total_pnl < 0:
            warnings.append(
                "Combined profits are negative. Allocating a loss in proportion to DEMPE "
                "contribution assigns the largest loss to the largest IP contributor, which "
                "is a risk-assumption conclusion this key was not built to support. Confirm "
                "which entity contractually assumes and controls the downside risk."
            )

        allocations_usd: Dict[str, float] = {}
        dempe_percentages: Dict[str, float] = {}
        for contribution in dempe_contributions:
            share = scores[contribution.entity_id] / total_score
            allocations_usd[contribution.entity_id] = _money(
                routine_map.get(contribution.entity_id, 0.0) + _money_mul(splittable, share)
            )
            dempe_percentages[contribution.entity_id] = share * 100.0

        # Each share is rounded to cents independently, so with three or more
        # entities the rounded allocations need not sum back to the combined
        # profit. A profit split whose legs do not tie to the profit being split
        # will not post as a balanced intercompany journal, so the sub-cent
        # residue is assigned to the largest allocation (ties broken by entity
        # id, so the result is deterministic and reproducible for the file).
        residue = _money(total_pnl - _money(sum(allocations_usd.values())))
        if residue != 0.0:
            absorbing_id = max(
                allocations_usd,
                key=lambda eid: (abs(allocations_usd[eid]), eid),
            )
            allocations_usd[absorbing_id] = _money(
                allocations_usd[absorbing_id] + residue
            )
            logger.debug(
                "Profit split rounding residue of %.2f assigned to %s",
                residue, absorbing_id,
            )

        logger.info(
            "Profit split (%s) of $%.2f across %d entities; amount split by key $%.2f",
            split_basis, total_pnl, len(dempe_contributions), splittable,
        )
        for warning in warnings:
            logger.warning("Profit split: %s", warning)

        return ProfitSplitAllocation(
            total_global_pnl_usd=total_pnl,
            entity_allocations_usd=allocations_usd,
            dempe_percentages=dempe_percentages,
            split_basis=split_basis,
            routine_returns_usd=routine_map,
            amount_split_by_key_usd=splittable,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _normalise_dimension_weights(
        dimension_weights: Optional[Dict[str, float]]
    ) -> Optional[Dict[str, float]]:
        if dimension_weights is None:
            return None
        unknown = set(dimension_weights) - set(DEMPE_DIMENSIONS)
        if unknown:
            raise TransferPricingError(
                f"Unknown DEMPE dimensions {sorted(unknown)}; expected {list(DEMPE_DIMENSIONS)}"
            )
        missing = set(DEMPE_DIMENSIONS) - set(dimension_weights)
        if missing:
            raise TransferPricingError(
                f"dimension_weights is missing {sorted(missing)}; supply a weight for every "
                "DEMPE function, using 0.0 for one that creates no value here."
            )
        values = {
            d: _require_non_negative(dimension_weights[d], f"dimension_weights[{d!r}]")
            for d in DEMPE_DIMENSIONS
        }
        total = sum(values.values())
        if total <= 0:
            raise TransferPricingError("dimension_weights must sum to more than zero.")
        return {d: v / total for d, v in values.items()}

    @staticmethod
    def _validate_routine_returns(
        routine_returns_usd: Optional[Dict[str, float]], known_ids: Set[str]
    ) -> Optional[Dict[str, float]]:
        if routine_returns_usd is None:
            return None
        unknown = set(routine_returns_usd) - known_ids
        if unknown:
            raise TransferPricingError(
                f"routine_returns_usd names entities with no DEMPE contribution: "
                f"{sorted(unknown)}"
            )
        return {
            eid: _money(_require_number(value, f"routine_returns_usd[{eid!r}]"))
            for eid, value in routine_returns_usd.items()
        }
