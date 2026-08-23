"""
custodial-vs-non-custodial-tradeoff-assessment: decision support for choosing
between custodial CEX execution, off-exchange settlement, and non-custodial
on-chain execution.

Two-stage design. HARD CONSTRAINTS run first and DISQUALIFY architectures
outright — a key-sovereignty mandate, a counterparty-exposure budget, a latency
floor, and a qualified-custodian requirement are pass/fail conditions, not
things to trade off against a convenient cost score. Only surviving
architectures are then RANKED by a weighted heuristic score.

That ordering is the whole point of the module. A mandate saying "we tolerate
zero exchange counterparty exposure" must make a custodial exchange
*ineligible*; scoring it down and letting a strong latency and fee score carry
it back to the top is how a fund ends up with its balance sheet inside the next
FTX.

Honesty boundary: the composite score is an ORDINAL heuristic for comparing
surviving candidates under explicitly declared weights. It is not a calibrated
probability, a risk model, or a substitute for counterparty due diligence. The
residual-exposure and cost figures in ``DEFAULT_ARCHITECTURE_PROFILES`` are
ILLUSTRATIVE PLACEHOLDERS flagged by ``is_illustrative_default``; replace them
with figures from your own due diligence before relying on any output.
"""
from dataclasses import dataclass, replace
import logging
import math
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --- Key control models -----------------------------------------------------
# Who can move the assets. A key-sovereignty mandate is satisfied only by SELF.
KEY_CONTROL_EXCHANGE = "EXCHANGE"                    # venue holds the keys
KEY_CONTROL_SHARED_CUSTODIAN = "SHARED_OR_CUSTODIAN" # MPC co-control / third-party custodian
KEY_CONTROL_SELF = "SELF"                            # client sole signer
VALID_KEY_CONTROLS = (KEY_CONTROL_EXCHANGE, KEY_CONTROL_SHARED_CUSTODIAN, KEY_CONTROL_SELF)

# --- Regulatory constraints -------------------------------------------------
# Jurisdiction-specific; the CALLER asserts which applies. See references/standards.md.
REGULATORY_NONE = "NONE"
REGULATORY_QUALIFIED_CUSTODIAN_REQUIRED = "QUALIFIED_CUSTODIAN_REQUIRED"
VALID_REGULATORY_CONSTRAINTS = (REGULATORY_NONE, REGULATORY_QUALIFIED_CUSTODIAN_REQUIRED)

# Firm-policy default (NOT a regulatory threshold) above which off-exchange
# settlement must at least be evaluated before settling on a custodial venue.
DEFAULT_OFF_EXCHANGE_EVALUATION_THRESHOLD_USD = 10_000_000.0


class CustodyAssessmentError(Exception):
    """Base class for assessment failures."""


class NoEligibleArchitectureError(CustodyAssessmentError):
    """Raised when every candidate architecture fails a hard constraint.

    Fail closed: returning the least-bad disqualified option would present a
    mandate breach as a recommendation. The requirements must be relaxed, or a
    architecture profile added, by a human.
    """


def _validate_number(
    name: str,
    value: float,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
    allow_zero: bool = True,
) -> float:
    """Reject non-numeric, NaN and infinite values, then bound-check.

    NaN is the case that matters here: every ``<=`` comparison against NaN is
    False, so an unvalidated NaN latency budget silently skips every latency
    constraint and yields a confident-looking recommendation from garbage.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value!r}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}, got {value!r}")
    if value == 0 and not allow_zero:
        raise ValueError(f"{name} must be non-zero, got {value!r}")
    return value


@dataclass
class StrategyRequirements:
    """What the strategy and its mandate require.

    ``max_counterparty_risk_pct`` is a TOLERANCE BUDGET: the maximum share of
    deployed capital the mandate permits to sit exposed to a single custodial
    counterparty, in percent. 0.0 means zero tolerance and disqualifies every
    architecture carrying residual exposure — it does not mean "no constraint".

    ``requires_key_sovereignty`` is satisfied only by sole client signing
    control. MPC co-control with a venue, and third-party qualified custody,
    are NOT self-sovereignty; a firm that accepts those should set this False
    and express its limit through ``max_counterparty_risk_pct`` instead.
    """

    strategy_name: str
    required_latency_ms: float          # end-to-end budget for order -> executable state
    monthly_volume_usd: float
    max_counterparty_risk_pct: float    # tolerance budget, percent of deployed capital
    gas_sensitivity_score: float        # 0.0 (insensitive) to 1.0 (highly sensitive)
    requires_key_sovereignty: bool
    regulatory_constraint: str = REGULATORY_NONE

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_name, str) or not self.strategy_name.strip():
            raise ValueError(f"strategy_name must be a non-empty string, got {self.strategy_name!r}")
        self.required_latency_ms = _validate_number(
            "required_latency_ms", self.required_latency_ms, minimum=0.0, allow_zero=False
        )
        self.monthly_volume_usd = _validate_number(
            "monthly_volume_usd", self.monthly_volume_usd, minimum=0.0
        )
        self.max_counterparty_risk_pct = _validate_number(
            "max_counterparty_risk_pct", self.max_counterparty_risk_pct, minimum=0.0, maximum=100.0
        )
        self.gas_sensitivity_score = _validate_number(
            "gas_sensitivity_score", self.gas_sensitivity_score, minimum=0.0, maximum=1.0
        )
        if not isinstance(self.requires_key_sovereignty, bool):
            raise ValueError(
                f"requires_key_sovereignty must be a bool, got {self.requires_key_sovereignty!r}"
            )
        if self.regulatory_constraint not in VALID_REGULATORY_CONSTRAINTS:
            raise ValueError(
                f"regulatory_constraint must be one of {VALID_REGULATORY_CONSTRAINTS}, "
                f"got {self.regulatory_constraint!r}"
            )


@dataclass
class CustodyArchitectureProfile:
    """Declared characteristics of one candidate architecture.

    ``min_achievable_latency_ms`` is a FLOOR, not a typical value: the fastest
    an order can reach an executable state on this architecture. For on-chain
    execution the floor is set by block inclusion, not network round-trip.

    ``residual_counterparty_exposure_pct`` is the share of deployed capital
    still exposed to a custodial counterparty after the architecture's controls
    apply. Off-exchange settlement reduces this but does not zero it — collateral
    sits outside the venue while unsettled P&L on open positions remains exposed,
    and custodian/trust-structure risk is substituted for venue risk.
    """

    architecture_name: str
    key_control: str
    min_achievable_latency_ms: float
    residual_counterparty_exposure_pct: float
    gas_cost_burden: float               # 0..100, scaled by the strategy's gas sensitivity
    provides_qualified_custodian: bool
    tradeoff_summary: str
    is_illustrative_default: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.architecture_name, str) or not self.architecture_name.strip():
            raise ValueError(
                f"architecture_name must be a non-empty string, got {self.architecture_name!r}"
            )
        if self.key_control not in VALID_KEY_CONTROLS:
            raise ValueError(
                f"key_control must be one of {VALID_KEY_CONTROLS}, got {self.key_control!r}"
            )
        self.min_achievable_latency_ms = _validate_number(
            "min_achievable_latency_ms", self.min_achievable_latency_ms,
            minimum=0.0, allow_zero=False,
        )
        self.residual_counterparty_exposure_pct = _validate_number(
            "residual_counterparty_exposure_pct", self.residual_counterparty_exposure_pct,
            minimum=0.0, maximum=100.0,
        )
        self.gas_cost_burden = _validate_number(
            "gas_cost_burden", self.gas_cost_burden, minimum=0.0, maximum=100.0
        )
        if not isinstance(self.provides_qualified_custodian, bool):
            raise ValueError(
                f"provides_qualified_custodian must be a bool, got "
                f"{self.provides_qualified_custodian!r}"
            )


@dataclass
class CustodyArchitectureScore:
    architecture_name: str
    is_eligible: bool
    disqualification_reasons: List[str]
    latency_headroom_score: float        # 0.0 to 100.0
    custody_control_score: float         # 0.0 to 100.0
    cost_efficiency_score: float         # 0.0 to 100.0
    composite_suitability_score: float   # 0.0 to 100.0; 0.0 when ineligible
    residual_counterparty_exposure_pct: float
    tradeoff_summary: str


@dataclass
class CustodyTradeoffReport:
    strategy_name: str
    recommended_architecture: str
    recommended_score: float
    rankings: List[CustodyArchitectureScore]          # eligible only, best first
    disqualified: List[CustodyArchitectureScore]      # with reasons
    key_risk_mitigations: List[str]
    advisories: List[str]
    uses_illustrative_defaults: bool
    weights: Dict[str, float]


# Structural facts behind the default floors, verified and cited in
# references/standards.md:
#   - On-chain execution cannot settle faster than block inclusion. Ethereum
#     mainnet slot time is 12 seconds, so 12_000 ms is the floor for L1 DEX
#     execution regardless of client-side network latency. Override this for
#     an L2 or a faster chain.
#   - Off-exchange settlement (Fireblocks Off-Exchange collateral vaults,
#     Copper ClearLoop delegation) still executes on the venue's matching
#     engine, so its latency floor tracks the venue's, not a chain's.
# The exposure and cost figures below are ILLUSTRATIVE and must be replaced
# with firm-specific due diligence figures.
DEFAULT_ARCHITECTURE_PROFILES: Tuple[CustodyArchitectureProfile, ...] = (
    CustodyArchitectureProfile(
        architecture_name="CUSTODIAL_CEX",
        key_control=KEY_CONTROL_EXCHANGE,
        min_achievable_latency_ms=1.0,
        residual_counterparty_exposure_pct=100.0,
        gas_cost_burden=0.0,
        provides_qualified_custodian=False,
        tradeoff_summary=(
            "Lowest execution latency and deepest liquidity, but the venue holds the "
            "keys: the full traded balance is exposed to venue insolvency, fraud and "
            "withdrawal suspension."
        ),
        is_illustrative_default=True,
    ),
    CustodyArchitectureProfile(
        architecture_name="HYBRID_OFF_EXCHANGE_SETTLEMENT",
        key_control=KEY_CONTROL_SHARED_CUSTODIAN,
        min_achievable_latency_ms=1.0,
        residual_counterparty_exposure_pct=15.0,
        gas_cost_burden=5.0,
        provides_qualified_custodian=True,
        tradeoff_summary=(
            "Collateral held in a co-controlled MPC vault or bankruptcy-remote trust "
            "and mirrored to the venue, so venue insolvency does not reach principal. "
            "Residual exposure remains on unsettled P&L for open positions, and "
            "custodian and trust-structure risk is substituted for venue risk."
        ),
        is_illustrative_default=True,
    ),
    CustodyArchitectureProfile(
        architecture_name="NON_CUSTODIAL_DEX",
        key_control=KEY_CONTROL_SELF,
        min_achievable_latency_ms=12_000.0,
        residual_counterparty_exposure_pct=0.0,
        gas_cost_burden=70.0,
        provides_qualified_custodian=False,
        tradeoff_summary=(
            "Sole signing control and no custodial counterparty, but execution is "
            "bounded by block inclusion, priced in volatile gas, and exposed to MEV "
            "and to smart-contract and bridge risk that custodial models do not carry."
        ),
        is_illustrative_default=True,
    ),
)

DEFAULT_WEIGHTS: Dict[str, float] = {"latency": 0.40, "custody": 0.35, "cost": 0.25}


class CustodialTradeoffAssessorEngine:
    """
    Decision support for institutional custody and execution architecture choice.

    Hard constraints are applied before scoring; see the module docstring. The
    composite score ranks only the survivors and is ordinal, not calibrated.

    Args:
        profiles: candidate architectures. Defaults to
            ``DEFAULT_ARCHITECTURE_PROFILES``, whose risk and cost figures are
            illustrative placeholders — supply your own for production use.
        weights: composite weights keyed 'latency', 'custody', 'cost'. Must be
            non-negative and sum to 1.0. Exposed so the trade-off actually
            applied is auditable rather than buried in the implementation.
        off_exchange_evaluation_threshold_usd: monthly volume above which a
            custodial-venue recommendation raises an advisory to evaluate
            off-exchange settlement. A firm-policy default, not a regulatory
            threshold.
    """

    def __init__(
        self,
        profiles: Optional[Sequence[CustodyArchitectureProfile]] = None,
        weights: Optional[Dict[str, float]] = None,
        off_exchange_evaluation_threshold_usd: float = DEFAULT_OFF_EXCHANGE_EVALUATION_THRESHOLD_USD,
    ) -> None:
        chosen = tuple(profiles) if profiles is not None else DEFAULT_ARCHITECTURE_PROFILES
        if not chosen:
            raise ValueError("profiles must contain at least one architecture")
        for p in chosen:
            if not isinstance(p, CustodyArchitectureProfile):
                raise TypeError(
                    f"profiles must contain CustodyArchitectureProfile, got {type(p).__name__}"
                )
        names = [p.architecture_name for p in chosen]
        if len(set(names)) != len(names):
            raise ValueError(f"architecture names must be unique, got {names}")
        # Copy so a caller mutating its profile objects cannot silently change
        # the constraints a past assessment was made under.
        self.profiles: Tuple[CustodyArchitectureProfile, ...] = tuple(replace(p) for p in chosen)

        self.weights = dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS)
        if set(self.weights) != set(DEFAULT_WEIGHTS):
            raise ValueError(
                f"weights must have exactly the keys {sorted(DEFAULT_WEIGHTS)}, "
                f"got {sorted(self.weights)}"
            )
        for key, value in self.weights.items():
            _validate_number(f"weights[{key}]", value, minimum=0.0, maximum=1.0)
        total = sum(self.weights.values())
        if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"weights must sum to 1.0, got {total}")

        self.off_exchange_evaluation_threshold_usd = _validate_number(
            "off_exchange_evaluation_threshold_usd",
            off_exchange_evaluation_threshold_usd,
            minimum=0.0,
        )

    # ------------------------------------------------------------------
    # Hard constraints
    # ------------------------------------------------------------------
    def _disqualifications(
        self, req: StrategyRequirements, profile: CustodyArchitectureProfile
    ) -> List[str]:
        """Pass/fail conditions. Any non-empty result makes the architecture ineligible."""
        reasons: List[str] = []

        if req.requires_key_sovereignty and profile.key_control != KEY_CONTROL_SELF:
            reasons.append(
                f"Mandate requires sole key control; this architecture's key control is "
                f"'{profile.key_control}'."
            )

        if profile.residual_counterparty_exposure_pct > req.max_counterparty_risk_pct:
            reasons.append(
                f"Residual counterparty exposure {profile.residual_counterparty_exposure_pct:.1f}% "
                f"exceeds the mandate tolerance of {req.max_counterparty_risk_pct:.1f}%."
            )

        if profile.min_achievable_latency_ms > req.required_latency_ms:
            reasons.append(
                f"Latency floor {profile.min_achievable_latency_ms:,.0f} ms exceeds the "
                f"{req.required_latency_ms:,.0f} ms budget."
            )

        if (
            req.regulatory_constraint == REGULATORY_QUALIFIED_CUSTODIAN_REQUIRED
            and not profile.provides_qualified_custodian
        ):
            reasons.append(
                "A qualified custodian is required for this entity and this architecture "
                "does not provide one."
            )

        return reasons

    # ------------------------------------------------------------------
    # Scoring (survivors only)
    # ------------------------------------------------------------------
    def _score(
        self, req: StrategyRequirements, profile: CustodyArchitectureProfile
    ) -> Tuple[float, float, float, float]:
        # Latency HEADROOM, not raw speed: an architecture sitting exactly on the
        # budget scores 0 because it has no margin for a bad tail, while one an
        # order of magnitude faster than needed scores 90.
        latency = 100.0 * (1.0 - profile.min_achievable_latency_ms / req.required_latency_ms)
        latency = min(100.0, max(0.0, latency))

        custody = 100.0 - profile.residual_counterparty_exposure_pct

        cost = 100.0 - req.gas_sensitivity_score * profile.gas_cost_burden
        cost = min(100.0, max(0.0, cost))

        composite = (
            self.weights["latency"] * latency
            + self.weights["custody"] * custody
            + self.weights["cost"] * cost
        )
        return round(latency, 2), round(custody, 2), round(cost, 2), round(composite, 2)

    def _mitigations(
        self, req: StrategyRequirements, profile: CustodyArchitectureProfile
    ) -> List[str]:
        mitigations: List[str] = []
        if profile.key_control == KEY_CONTROL_EXCHANGE:
            mitigations.append(
                "Cap the balance held at the venue and sweep the remainder to custody; "
                "the venue balance is the loss given venue default."
            )
            mitigations.append(
                "Enforce withdrawal address whitelisting and trade-only API keys with "
                "withdrawal permission disabled."
            )
        elif profile.key_control == KEY_CONTROL_SHARED_CUSTODIAN:
            mitigations.append(
                "Confirm the legal structure that makes collateral bankruptcy-remote, and "
                "who holds it, in writing — this substitutes custodian and trust-structure "
                "risk for venue risk rather than removing counterparty risk."
            )
            mitigations.append(
                "Size open positions against the unsettled P&L still exposed to the venue "
                "between settlement cycles."
            )
        else:
            mitigations.append(
                "Route through a private mempool (e.g. Flashbots Protect) to reduce "
                "exposure to sandwich and frontrunning bots. This mitigates rather than "
                "eliminates MEV: inclusion is not guaranteed and the relay is trusted."
            )
            mitigations.append(
                "Budget for gas volatility and failed-transaction cost, and audit every "
                "contract and bridge in the execution path."
            )
            mitigations.append(
                "Self-custody moves the failure mode to key management: require MPC or "
                "multi-signature signing with a tested recovery procedure."
            )
        return mitigations

    # ------------------------------------------------------------------
    # Assessment
    # ------------------------------------------------------------------
    def evaluate_strategy_custody(self, req: StrategyRequirements) -> CustodyTradeoffReport:
        """Apply hard constraints, then rank the eligible architectures.

        Raises:
            TypeError: ``req`` is not a ``StrategyRequirements``.
            NoEligibleArchitectureError: every candidate failed a hard constraint.
        """
        if not isinstance(req, StrategyRequirements):
            raise TypeError(
                f"req must be a StrategyRequirements, got {type(req).__name__}"
            )

        eligible: List[CustodyArchitectureScore] = []
        disqualified: List[CustodyArchitectureScore] = []

        for profile in self.profiles:
            reasons = self._disqualifications(req, profile)
            if reasons:
                disqualified.append(
                    CustodyArchitectureScore(
                        architecture_name=profile.architecture_name,
                        is_eligible=False,
                        disqualification_reasons=reasons,
                        latency_headroom_score=0.0,
                        custody_control_score=0.0,
                        cost_efficiency_score=0.0,
                        composite_suitability_score=0.0,
                        residual_counterparty_exposure_pct=profile.residual_counterparty_exposure_pct,
                        tradeoff_summary=profile.tradeoff_summary,
                    )
                )
                continue

            latency, custody, cost, composite = self._score(req, profile)
            eligible.append(
                CustodyArchitectureScore(
                    architecture_name=profile.architecture_name,
                    is_eligible=True,
                    disqualification_reasons=[],
                    latency_headroom_score=latency,
                    custody_control_score=custody,
                    cost_efficiency_score=cost,
                    composite_suitability_score=composite,
                    residual_counterparty_exposure_pct=profile.residual_counterparty_exposure_pct,
                    tradeoff_summary=profile.tradeoff_summary,
                )
            )

        disqualified.sort(key=lambda s: s.architecture_name)

        if not eligible:
            detail = "; ".join(
                f"{s.architecture_name}: {' '.join(s.disqualification_reasons)}"
                for s in disqualified
            )
            logger.error(
                f"CUSTODY EVALUATION [{req.strategy_name}]: no eligible architecture. {detail}"
            )
            raise NoEligibleArchitectureError(
                f"No candidate architecture satisfies the requirements for "
                f"'{req.strategy_name}'. {detail}"
            )

        # Deterministic ordering: best composite first, then lower residual
        # counterparty exposure, then name. Without an explicit tiebreak, ties
        # resolve by declaration order, which quietly favours whichever profile
        # happens to be listed first.
        eligible.sort(
            key=lambda s: (
                -s.composite_suitability_score,
                s.residual_counterparty_exposure_pct,
                s.architecture_name,
            )
        )
        top = eligible[0]
        top_profile = next(p for p in self.profiles if p.architecture_name == top.architecture_name)

        advisories: List[str] = []
        if (
            top_profile.key_control == KEY_CONTROL_EXCHANGE
            and req.monthly_volume_usd >= self.off_exchange_evaluation_threshold_usd
        ):
            advisories.append(
                f"Monthly volume ${req.monthly_volume_usd:,.0f} is at or above the "
                f"${self.off_exchange_evaluation_threshold_usd:,.0f} review threshold: "
                f"evaluate off-exchange settlement before committing balances to a "
                f"custodial venue."
            )
        if len(eligible) == 1:
            advisories.append(
                f"Only one architecture is eligible; the ranking carries no comparative "
                f"information. Review the {len(disqualified)} disqualification reason(s) "
                f"before treating this as a chosen trade-off."
            )
        uses_defaults = any(p.is_illustrative_default for p in self.profiles)
        if uses_defaults:
            advisories.append(
                "Assessment used illustrative default risk and cost figures. Replace them "
                "with firm-specific due diligence before relying on this recommendation."
            )
            logger.warning(
                f"CUSTODY EVALUATION [{req.strategy_name}]: using illustrative default "
                f"architecture profiles; figures are placeholders, not due diligence."
            )

        logger.info(
            f"CUSTODY EVALUATION [{req.strategy_name}]: Recommended -> "
            f"{top.architecture_name} (Score: {top.composite_suitability_score}; "
            f"{len(eligible)} eligible, {len(disqualified)} disqualified)"
        )

        return CustodyTradeoffReport(
            strategy_name=req.strategy_name,
            recommended_architecture=top.architecture_name,
            recommended_score=top.composite_suitability_score,
            rankings=eligible,
            disqualified=disqualified,
            key_risk_mitigations=self._mitigations(req, top_profile),
            advisories=advisories,
            uses_illustrative_defaults=uses_defaults,
            weights=dict(self.weights),
        )
