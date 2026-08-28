"""
scenario-based-stress-testing-custom-shocks: applies deterministic multi-factor shocks
(historical crisis replays and caller-defined hypotheticals) to a factor-mapped book and
reports the scenario P&L, the loss as a percentage of a stated capital base, and whether
a drawdown limit would have been breached.

This is a **factor-sensitivity** stress engine, not a per-symbol return replay. Every
position declares one risk factor and one sensitivity to that factor; the scenario
declares a shock per factor. Two shock types are supported, and they carry different
sign conventions because the underlying sensitivities are different quantities::

    RELATIVE_RETURN   pnl_i = V_i * beta_i * shock          # shock is a fractional return
    YIELD_BPS         pnl_i = -V_i * D_i * (bps / 10_000)   # shock is a level move in bps

``RELATIVE_RETURN`` covers price-like factors (equity spot, crude, an implied-vol level
quoted as a proportional move). ``beta_i`` is the position's return elasticity to the
factor: a beta of 1.1 means the position is expected to return 1.1x the factor's return.

``YIELD_BPS`` covers yield-like factors (interest rates, credit spreads), where the
canonical first-order sensitivity is

    dP / P  ~=  -D_mod * dy

so a **rise** in the factor is a **loss** for a long position. ``beta_to_factor`` carries
the modified duration (rates) or spread duration (credit) in years. The minus sign lives
in the engine, not in the caller's beta.

Version 1.0.0 got both of these wrong and the errors ran in the same direction -- they
turned losses into gains:

  * The absolute-change branch computed ``+V * beta * (bps / 10_000)``. A +200bp hike
    against a duration-7 bond book reported a **+14% gain** instead of an approximately
    -14% loss, even though the code comment beside it stated the correct
    ``-Duration * delta_r`` formula.
  * ``FactorShock("CREDIT_SPREAD", 3.00)`` in the 2008 scenario was commented "+300bps"
    but was flagged as a *relative* shock, so it applied +300% -- a $1M credit book
    reported a **+$3,000,000 gain** on a spread blow-out.

Both are corrected here, which changes the numbers ``is_absolute_change=True`` produces.
That is the point of the change; see SKILL.md for the migration note.

Sign and denominator conventions
--------------------------------
``current_value_usd`` is the **signed** mark-to-market of the position: a short is
negative. P&L is signed, negative is a loss.

``percentage_loss_pct`` is the scenario P&L over a **capital base**. Drawdown limits are
set against capital, not against net exposure, and for a long/short book the net sum of
position values is not a meaningful denominator -- a market-neutral book nets to near
zero and any loss over it explodes. So the base defaults to the sum of position values
only when the book is entirely long; the moment any position is short, an explicit
``capital_base_usd`` is required rather than inferred.

Scenario coverage
-----------------
A position whose factor no scenario shocks contributes exactly zero and is invisible in
the P&L. Version 1.0.0 reported such a book as a $0 loss with no breach and an empty
worst-case scenario -- a stress test answering "clear" on a book it never actually
stressed. Coverage is now reported explicitly (``unshocked_asset_ids`` per scenario,
``factors_never_shocked`` and ``status`` on the report), and a run in which *no* position
is shocked by *any* scenario raises rather than returning a vacuous all-clear. This
follows BCBS *Stress testing principles* (d450, October 2018), Principle 4: "If certain
material and relevant risks are excluded from the scenarios, their exclusion should be
explained and documented."

Calibration provenance of the predefined scenarios
--------------------------------------------------
The predefined magnitudes are **library defaults, not reconstructions of the episodes and
not regulator-set scenarios**. They are deliberately left at their 1.0.0 values so that
existing callers' numbers do not move silently; where they understate the historical
record, that is stated in ``references/standards.md`` with sources. In particular the
-35% equity shock is far milder than the -56.8% peak-to-trough decline of the 2007-2009
bear market, and the +150%/+120% vol shocks are milder than the moves that carried VIX to
its 80.86 (20 Nov 2008) and 82.69 (16 Mar 2020) closing highs. Recalibrate deliberately.

Limitations (documented, deliberate)
------------------------------------
- **First-order and linear.** Each position is a single beta or duration against a single
  factor. There is no gamma, no vega convexity, no bond convexity, and no cross-factor
  term. An options book stressed this way is wrong in the direction that matters: a long
  option gains convexly on a large move, a short option loses convexly.
- **Multiplicative shocks cannot cross zero.** A ``RELATIVE_RETURN`` shock is bounded
  below at -1.0 (-100%). The May 2020 WTI contract settled at **-$37.63 on 20 April 2020**
  (CFTC, Interim Staff Report, 24 November 2020); no percentage shock reaches a negative
  price. For instruments that can go through zero, stress the position value directly.
- **No correlation model, no liquidity, no funding.** Shocks are deterministic inputs
  applied independently per factor. Correlations going to one, the cost of liquidating
  into the shock, and margin spirals are out of scope -- see
  ``portfolio-stress-test-including-liquidity-crunch-scenarios``,
  ``tail-correlation-between-strategies-under-stress`` and
  ``correlation-aware-exposure-limits``.
- **Single-period.** One instantaneous revaluation, not a path. The reported figure is a
  scenario loss, not a realised drawdown over a path, despite the limit being expressed
  as a drawdown percentage.
- **Not a reverse stress test.** ``StressScenarioCategory.REVERSE_STRESS`` is a label a
  caller may attach to a scenario they constructed; the engine does not solve for the
  shock that would breach a limit.
- **Not a regulatory capital calculation.** No regulator-set methodology is implemented.
  See ``references/standards.md`` for what actually binds whom.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

#: Loss, as a percentage of the capital base, above which a scenario is flagged.
#: A library default, not a regulatory limit -- see references/standards.md.
DEFAULT_MAX_ALLOWED_DRAWDOWN_PCT = 20.0

#: Report status values.
STATUS_COMPLETE = "STRESS_TEST_COMPLETE"
STATUS_INCOMPLETE_COVERAGE = "STRESS_TEST_INCOMPLETE_FACTOR_COVERAGE"

_BPS_PER_UNIT = 10_000.0


def _require_finite(value: float, name: str, context: str) -> float:
    """
    Rejects NaN, +/-Inf, bools and numeric strings before they reach the arithmetic.

    Every comparison against NaN is False, so an unguarded NaN value flows through the
    P&L, clears ``loss_pct < -limit`` and lands in a report whose
    ``is_drawdown_limit_breached`` field reads ``False``. A stress test that answers
    "limit not breached" on corrupt input is worse than one that is absent, because the
    caller has been told the book survives.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context}: {name} must be a real number, got {value!r}")
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        raise ValueError(f"{context}: {name} must be finite, got {value!r}")
    return numeric


@dataclass
class ScenarioResult:
    """Legacy ScenarioResult container for backward compatibility."""
    scenario_name: str
    pnl: float


class StressScenarioCategory(str, Enum):
    HISTORICAL_CRISIS = "HISTORICAL_CRISIS"
    CUSTOM_HYPOTHETICAL = "CUSTOM_HYPOTHETICAL"
    #: A label only. The engine does not solve for the shock that breaches a limit.
    REVERSE_STRESS = "REVERSE_STRESS"


class ShockType(str, Enum):
    """
    How a shock magnitude is interpreted, and therefore which sign convention applies.

    ``RELATIVE_RETURN`` -- ``percentage_shock`` is a fractional return on a price-like
    factor (-0.35 is -35%). P&L is ``V * beta * shock``; a fall in the factor is a loss
    for a long position.

    ``YIELD_BPS`` -- ``percentage_shock`` is a level move in basis points on a yield-like
    factor (200.0 is +200bp). P&L is ``-V * D * (bps / 10_000)`` per the first-order
    duration approximation ``dP/P ~= -D_mod * dy``; a *rise* in the factor is a loss for a
    long position, and ``beta_to_factor`` carries the duration in years.
    """
    RELATIVE_RETURN = "RELATIVE_RETURN"
    YIELD_BPS = "YIELD_BPS"


@dataclass
class FactorShock:
    """
    One factor's move within a scenario.

    ``percentage_shock`` is read in the units implied by ``shock_type``: a fractional
    return for ``RELATIVE_RETURN``, basis points for ``YIELD_BPS``.

    ``is_absolute_change`` is the pre-2.0.0 spelling of ``shock_type=YIELD_BPS`` and is
    still honoured, so existing construction sites keep working. Its *result* changed in
    2.0.0: it now carries the ``-duration * dy`` sign, where 1.0.0 returned a gain on a
    rate rise. Prefer ``shock_type``.
    """
    factor_name: str
    percentage_shock: float
    is_absolute_change: bool = False
    shock_type: Optional[ShockType] = None

    def __post_init__(self) -> None:
        if self.shock_type is None:
            self.shock_type = (
                ShockType.YIELD_BPS if self.is_absolute_change else ShockType.RELATIVE_RETURN
            )
        elif self.is_absolute_change and self.shock_type is not ShockType.YIELD_BPS:
            raise ValueError(
                f"FactorShock({self.factor_name!r}): is_absolute_change=True conflicts "
                f"with shock_type={self.shock_type}"
            )
        # Keep the legacy flag consistent with the authoritative field for any caller
        # still reading it.
        self.is_absolute_change = self.shock_type is ShockType.YIELD_BPS


@dataclass
class StressScenarioDefinition:
    scenario_id: str
    scenario_name: str
    category: StressScenarioCategory
    shocks: List[FactorShock]
    description: str = ""


PREDEFINED_HISTORICAL_SCENARIOS: List[StressScenarioDefinition] = [
    StressScenarioDefinition(
        scenario_id="SCEN_2008_LEHMAN",
        scenario_name="2008 Lehman Financial Crisis",
        category=StressScenarioCategory.HISTORICAL_CRISIS,
        shocks=[
            FactorShock("EQUITY_SPOT", -0.35),
            FactorShock("IMPLIED_VOL", 1.50),   # +150% proportional rise in the vol level
            # +300bp credit spread widening. In 1.0.0 this was written as 3.00 with the
            # relative flag, i.e. +300%, and produced a gain on a spread blow-out.
            FactorShock("CREDIT_SPREAD", 300.0, shock_type=ShockType.YIELD_BPS),
        ],
        description="Global banking liquidity freeze and equity market sell-off."
    ),
    StressScenarioDefinition(
        scenario_id="SCEN_2020_COVID",
        scenario_name="March 2020 Covid Liquidity Shock",
        category=StressScenarioCategory.HISTORICAL_CRISIS,
        shocks=[
            FactorShock("EQUITY_SPOT", -0.30),
            FactorShock("CRUDE_OIL", -0.60),    # see the negative-price limitation above
            FactorShock("IMPLIED_VOL", 1.20),
        ],
        description="Rapid global market shutdown and commodity collapse."
    ),
    StressScenarioDefinition(
        scenario_id="SCEN_2022_RATES",
        scenario_name="2022 Inflationary Rate Hike",
        category=StressScenarioCategory.HISTORICAL_CRISIS,
        shocks=[
            FactorShock("INTEREST_RATE_BPS", 200.0, shock_type=ShockType.YIELD_BPS),
            FactorShock("TECH_GROWTH_SPOT", -0.25),
        ],
        description="Aggressive central bank rate hiking cycle."
    ),
]


@dataclass
class AssetPosition:
    """
    One factor exposure.

    ``current_value_usd`` is the signed mark-to-market -- negative for a short.
    ``beta_to_factor`` is a return elasticity for ``RELATIVE_RETURN`` factors and a
    duration in years for ``YIELD_BPS`` factors.

    A single instrument may appear on several rows, one per factor it is exposed to.
    """
    asset_id: str
    factor_name: str
    current_value_usd: float
    beta_to_factor: float = 1.0


@dataclass
class DetailedScenarioResult:
    scenario_id: str
    scenario_name: str
    category: StressScenarioCategory
    starting_portfolio_value_usd: float
    simulated_pnl_usd: float
    ending_portfolio_value_usd: float
    percentage_loss_pct: float
    is_drawdown_limit_breached: bool
    audit_notes: str
    #: Absolute value of the positions this scenario actually shocked.
    shocked_value_usd: float = 0.0
    #: Positions this scenario left untouched, in input order, de-duplicated.
    unshocked_asset_ids: List[str] = field(default_factory=list)
    #: Absolute value of those untouched positions.
    unshocked_value_usd: float = 0.0


@dataclass
class StressTestReport:
    total_scenarios_evaluated: int
    #: The least-favourable scenario by P&L. Named even when no scenario lost money.
    worst_case_scenario: str
    #: That scenario's signed P&L. Positive means every scenario was a gain.
    max_loss_usd: float
    max_percentage_loss_pct: float
    results: List[DetailedScenarioResult]
    audit_notes: str
    #: The denominator used for every percentage in this report.
    capital_base_usd: float = 0.0
    status: str = STATUS_COMPLETE
    #: Factors held in the book that no scenario in this run shocked at all.
    factors_never_shocked: List[str] = field(default_factory=list)
    #: Absolute value of the positions carrying those factors.
    value_never_shocked_usd: float = 0.0
    #: Scenario ids whose loss exceeded max_allowed_drawdown_pct.
    breached_scenario_ids: List[str] = field(default_factory=list)


class CustomScenarioStressTester:
    """
    Deterministic multi-factor scenario stress engine: historical crisis replays and
    caller-defined hypothetical shocks against a factor-mapped book, with a drawdown
    limit audit. See the module docstring for sign conventions and limitations.
    """

    def __init__(
        self,
        scenarios: Optional[Dict[str, float]] = None,
        max_allowed_drawdown_pct: float = DEFAULT_MAX_ALLOWED_DRAWDOWN_PCT,
    ) -> None:
        # Legacy dict support: scenario_name -> shock multiplier (e.g. {"Crash": -0.2}).
        self.scenarios = scenarios or {"Crash": -0.2, "Rally": 0.1}
        limit = _require_finite(
            max_allowed_drawdown_pct, "max_allowed_drawdown_pct", "CustomScenarioStressTester"
        )
        if limit <= 0:
            raise ValueError(
                f"CustomScenarioStressTester: max_allowed_drawdown_pct must be > 0, got {limit}"
            )
        self.max_allowed_drawdown_pct = limit

    def run_stress_test(self, current_value: float) -> List[ScenarioResult]:
        """Legacy single-factor helper retained for backward compatibility."""
        value = _require_finite(current_value, "current_value", "run_stress_test")
        return [ScenarioResult(name, value * shock) for name, shock in self.scenarios.items()]

    def run_multi_factor_stress_test(
        self,
        positions: Iterable[AssetPosition],
        custom_scenarios: Optional[Iterable[StressScenarioDefinition]] = None,
        capital_base_usd: Optional[float] = None,
    ) -> StressTestReport:
        """
        Runs every predefined scenario plus any custom scenario against ``positions``.

        ``capital_base_usd`` is the denominator for every percentage in the report. It is
        required when the book contains a short, because the net sum of position values
        is not a meaningful drawdown base for a hedged book. Left unset on a long-only
        book it defaults to the sum of position values.

        Raises ``ValueError`` on non-finite or malformed input, on duplicate scenario ids
        or duplicate factors within one scenario, on a non-positive capital base, and on a
        run in which no scenario shocks any position.
        """
        scenarios_to_run = self._resolve_scenarios(custom_scenarios)
        # Materialised up front: the book is walked once per scenario, and a generator
        # would be exhausted after the first pass and report every later scenario as a
        # $0 all-clear on a book it had already consumed.
        positions = list(positions)
        self._validate_positions(positions)
        capital_base = self._resolve_capital_base(positions, capital_base_usd)

        detailed_results: List[DetailedScenarioResult] = []
        breached_ids: List[str] = []
        shocked_factors: Set[str] = set()
        worst_pnl: Optional[float] = None
        worst_scenario_name = ""

        for scen in scenarios_to_run:
            shock_map = self._build_shock_map(scen)
            shocked_factors.update(shock_map)

            simulated_pnl = 0.0
            shocked_value = 0.0
            unshocked_value = 0.0
            unshocked_ids: List[str] = []

            for pos in positions:
                shk = shock_map.get(pos.factor_name)
                if shk is None:
                    unshocked_value += abs(pos.current_value_usd)
                    if pos.asset_id not in unshocked_ids:
                        unshocked_ids.append(pos.asset_id)
                    continue
                simulated_pnl += self._position_pnl(pos, shk)
                shocked_value += abs(pos.current_value_usd)

            # Finite inputs can still overflow through a large beta, and an infinite P&L
            # compares False against the limit in one direction -- the same way a NaN
            # would read as an all-clear.
            _require_finite(simulated_pnl, "simulated_pnl", f"scenario {scen.scenario_id!r}")

            # The breach decision is made on the unrounded loss. Deciding it on the
            # display-rounded percentage let a -20.004% loss round to -20.00 and clear a
            # 20.0% limit.
            loss_pct = (simulated_pnl / capital_base) * 100.0
            is_breached = loss_pct < -self.max_allowed_drawdown_pct

            rounded_pnl = round(simulated_pnl, 2)
            rounded_pct = round(loss_pct, 2)
            notes = (
                f"STRESS SCENARIO [{scen.scenario_name}]: PnL = ${rounded_pnl:,.2f} "
                f"({rounded_pct}% of ${capital_base:,.2f}), "
                f"Breached Limit ({self.max_allowed_drawdown_pct}%) = {is_breached}."
            )
            if unshocked_ids:
                notes += f" Unshocked positions: {len(unshocked_ids)} (${unshocked_value:,.2f})."

            detailed_results.append(DetailedScenarioResult(
                scenario_id=scen.scenario_id,
                scenario_name=scen.scenario_name,
                category=scen.category,
                starting_portfolio_value_usd=capital_base,
                simulated_pnl_usd=rounded_pnl,
                ending_portfolio_value_usd=round(capital_base + simulated_pnl, 2),
                percentage_loss_pct=rounded_pct,
                is_drawdown_limit_breached=is_breached,
                audit_notes=notes,
                shocked_value_usd=round(shocked_value, 2),
                unshocked_asset_ids=unshocked_ids,
                unshocked_value_usd=round(unshocked_value, 2),
            ))

            if is_breached:
                breached_ids.append(scen.scenario_id)
                logger.warning(notes)

            # Tracked unconditionally so an all-gain run still names its least-favourable
            # scenario instead of returning an empty string.
            if worst_pnl is None or simulated_pnl < worst_pnl:
                worst_pnl = simulated_pnl
                worst_scenario_name = scen.scenario_name

        portfolio_factors = dict.fromkeys(p.factor_name for p in positions)
        never_shocked = [f for f in portfolio_factors if f not in shocked_factors]
        never_shocked_set = set(never_shocked)
        value_never_shocked = sum(
            abs(p.current_value_usd) for p in positions if p.factor_name in never_shocked_set
        )
        if len(never_shocked) == len(portfolio_factors):
            raise ValueError(
                "run_multi_factor_stress_test: no scenario shocks any position "
                f"(portfolio factors {sorted(set(p.factor_name for p in positions))} vs "
                f"shocked factors {sorted(shocked_factors)}). A stress test that touches "
                "nothing cannot report an all-clear."
            )

        status = STATUS_INCOMPLETE_COVERAGE if never_shocked else STATUS_COMPLETE
        worst_pnl = 0.0 if worst_pnl is None else worst_pnl
        max_pct_loss = round((worst_pnl / capital_base) * 100.0, 2)

        summary_notes = (
            f"STRESS TEST SUMMARY [{status}]: Total Scenarios = {len(scenarios_to_run)}, "
            f"Capital Base = ${capital_base:,.2f}, "
            f"Worst Scenario = '{worst_scenario_name}' (${round(worst_pnl, 2):,.2f} / "
            f"{max_pct_loss}%), Breaches = {len(breached_ids)}."
        )
        if never_shocked:
            summary_notes += (
                f" UNSHOCKED FACTORS (${value_never_shocked:,.2f} of book): "
                f"{', '.join(never_shocked)}."
            )
            logger.warning(summary_notes)
        else:
            logger.info(summary_notes)

        return StressTestReport(
            total_scenarios_evaluated=len(scenarios_to_run),
            worst_case_scenario=worst_scenario_name,
            max_loss_usd=round(worst_pnl, 2),
            max_percentage_loss_pct=max_pct_loss,
            results=detailed_results,
            audit_notes=summary_notes,
            capital_base_usd=capital_base,
            status=status,
            factors_never_shocked=never_shocked,
            value_never_shocked_usd=round(value_never_shocked, 2),
            breached_scenario_ids=breached_ids,
        )

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _position_pnl(pos: AssetPosition, shk: FactorShock) -> float:
        """First-order P&L of one position under one factor shock."""
        if shk.shock_type is ShockType.YIELD_BPS:
            # dP/P ~= -D_mod * dy, with beta_to_factor carrying the duration in years.
            return -pos.current_value_usd * pos.beta_to_factor * (
                shk.percentage_shock / _BPS_PER_UNIT
            )
        return pos.current_value_usd * pos.beta_to_factor * shk.percentage_shock

    @staticmethod
    def _resolve_scenarios(
        custom_scenarios: Optional[Iterable[StressScenarioDefinition]],
    ) -> List[StressScenarioDefinition]:
        scenarios: List[StressScenarioDefinition] = list(PREDEFINED_HISTORICAL_SCENARIOS)
        if custom_scenarios:
            if isinstance(custom_scenarios, (StressScenarioDefinition, str, bytes)):
                raise ValueError(
                    "custom_scenarios must be a sequence of StressScenarioDefinition, not a "
                    f"single {type(custom_scenarios).__name__}"
                )
            scenarios.extend(custom_scenarios)

        seen: Set[str] = set()
        for scen in scenarios:
            if not isinstance(scen, StressScenarioDefinition):
                raise ValueError(f"custom_scenarios: expected StressScenarioDefinition, got {scen!r}")
            if not str(scen.scenario_id).strip():
                raise ValueError("StressScenarioDefinition: scenario_id must be non-empty")
            if scen.scenario_id in seen:
                # Two rows sharing an id make every downstream lookup by id ambiguous.
                raise ValueError(
                    f"duplicate scenario_id {scen.scenario_id!r}; ids must be unique across "
                    "predefined and custom scenarios"
                )
            seen.add(scen.scenario_id)
            if not scen.shocks:
                raise ValueError(f"scenario {scen.scenario_id!r}: shocks must not be empty")
        return scenarios

    @staticmethod
    def _build_shock_map(scen: StressScenarioDefinition) -> Dict[str, FactorShock]:
        shock_map: Dict[str, FactorShock] = {}
        for shk in scen.shocks:
            context = f"scenario {scen.scenario_id!r}"
            if not isinstance(shk, FactorShock):
                raise ValueError(f"{context}: expected FactorShock, got {shk!r}")
            factor = str(shk.factor_name).strip()
            if not factor:
                raise ValueError(f"{context}: factor_name must be non-empty")
            if factor in shock_map:
                # A dict comprehension silently kept the last of a duplicated pair, so a
                # -30% shock beside a -5% typo applied the -5%.
                raise ValueError(f"{context}: duplicate shock for factor {factor!r}")
            magnitude = _require_finite(shk.percentage_shock, "percentage_shock", context)
            if shk.shock_type is ShockType.RELATIVE_RETURN and magnitude < -1.0:
                raise ValueError(
                    f"{context}: relative shock for {factor!r} is {magnitude}; a price-like "
                    "factor cannot fall more than -1.0 (-100%). Stress the position value "
                    "directly for instruments that can trade through zero."
                )
            shock_map[factor] = shk
        return shock_map

    @staticmethod
    def _validate_positions(positions: Sequence[AssetPosition]) -> None:
        if not positions:
            raise ValueError("run_multi_factor_stress_test: positions must not be empty")
        for pos in positions:
            if not isinstance(pos, AssetPosition):
                raise ValueError(f"positions: expected AssetPosition, got {pos!r}")
            context = f"position {pos.asset_id!r}"
            if not str(pos.asset_id).strip():
                raise ValueError("positions: asset_id must be non-empty")
            if not str(pos.factor_name).strip():
                raise ValueError(f"{context}: factor_name must be non-empty")
            _require_finite(pos.current_value_usd, "current_value_usd", context)
            _require_finite(pos.beta_to_factor, "beta_to_factor", context)

    @staticmethod
    def _resolve_capital_base(
        positions: Sequence[AssetPosition], capital_base_usd: Optional[float]
    ) -> float:
        if capital_base_usd is not None:
            base = _require_finite(capital_base_usd, "capital_base_usd", "run_multi_factor_stress_test")
            if base <= 0:
                raise ValueError(f"capital_base_usd must be > 0, got {base}")
            return base

        if any(p.current_value_usd < 0 for p in positions):
            raise ValueError(
                "run_multi_factor_stress_test: the book contains short positions, so the net "
                "sum of position values is not a meaningful drawdown denominator (a hedged "
                "book nets to near zero and any loss over it explodes). Pass an explicit "
                "capital_base_usd."
            )

        base = sum(p.current_value_usd for p in positions)
        if base <= 0:
            raise ValueError(f"Portfolio value must be > 0, got {base}")
        return base
