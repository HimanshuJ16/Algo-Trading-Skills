"""
stress-testing-against-historical-crash-scenarios: replays a portfolio's **current**
position vector through per-symbol historical crash return shocks and reports the
stressed P&L, the loss against a stated NAV, and whether a stressed-loss gate would
have been breached.

This is a **per-symbol return replay**. Each scenario carries a map of
``symbol -> cumulative return``; each position is revalued as
``quantity * price * shock``. For a factor-sensitivity engine (one beta or duration per
position, shocks declared per factor) use ``scenario-based-stress-testing-custom-shocks``
instead; for the cost of actually liquidating into the shock use
``portfolio-stress-test-including-liquidity-crunch-scenarios``.

Sign and denominator conventions
--------------------------------
``quantity`` is **signed**: a short is negative. ``stressed_pnl_usd`` is therefore signed
and a negative number is a loss. ``portfolio_nav`` is the capital base and every
percentage in the report is taken over it.

``worst_loss_pct`` is a **loss magnitude and is never negative**. When the worst scenario
across the library is a *gain* -- the normal outcome for a net-short book under a crash
replay -- it is ``0.0``, because there is no loss to gate on.

Version 1.0.0 computed ``worst_loss_pct = abs(worst.stressed_pnl_pct)``. On a book that
profits in every scenario that reported the profit as a loss of the same size and fired
the gate: a 1,000-share SPY short priced at $100 against a $100,000 NAV gains $34,000 in
a -34% replay, and 1.0.0 reported "Worst-case scenario 'COVID' produces 34.00% loss
($34,000.00) ... Blocking new entries." The gate blocked trading because the book made
money. If you are migrating from 1.0.0 and worked around this by inverting the sign on
short quantities, remove the workaround and pass the signed quantity.

Coverage is reported, never inferred
------------------------------------
Two silent zeroes in 1.0.0 produced confident all-clears on books that were never
stressed:

  * A position held but absent from ``prices`` was skipped with ``continue`` and
    contributed exactly $0. A partially priced book returned an understated loss with
    nothing in the report to say so.
  * A symbol absent from a scenario and from its ``DEFAULT`` entry fell back to a
    hard-coded ``-0.30`` -- a magnitude with no source, applied as though it were the
    historical record.

Both are now explicit. ``unpriced_symbols`` and ``unshocked_symbols`` are reported per
scenario and on the report; ``fallback_symbols`` names every symbol priced off the
scenario's ``DEFAULT`` rather than off a symbol-specific historical return; ``status``
carries ``STRESS_TEST_INCOMPLETE_COVERAGE`` when any of these is non-empty; and a run in
which no position could be priced at all raises rather than returning a vacuous $0 loss.
This follows BCBS *Stress testing principles* (d450, October 2018), Principle 4: "If
certain material and relevant risks are excluded from the scenarios, their exclusion
should be explained and documented."

Non-finite input is rejected
----------------------------
Every comparison against NaN is False, so an unguarded NaN price flowed through the P&L
in 1.0.0 and cleared ``worst_loss_pct >= max_stressed_loss_pct``, producing a report whose
``threshold_breached`` field read ``False`` and whose ``breach_reason`` was ``None``. A
risk gate that answers "not breached" on corrupt input is worse than one that is absent.

Limitations (documented, deliberate)
------------------------------------
- **Single period, instantaneous revaluation.** One shock applied to the current position
  vector. There is no path, no rebalancing, no margin call and no forced liquidation, so
  the estimate omits precisely the amplification that makes real crashes worse than their
  headline return. See ``portfolio-stress-test-including-liquidity-crunch-scenarios``.
- **No correlation model.** Shocks are a vector you supply and are applied independently
  per symbol. Diversification survives the scenario only to the extent your own shock
  vector says it does. See ``correlation-aware-exposure-limits`` and
  ``tail-correlation-between-strategies-under-stress``.
- **Linear in position value.** A shock is a proportional move in the underlying. An
  option's P&L is not proportional to its underlying's return, so an options book stressed
  this way is wrong in the direction that matters. Aggregate the Greeks with
  ``options-greeks-real-time-portfolio-aggregation`` and shock those.
- **Bounded at -100%.** A return shock cannot take a price through zero; the engine
  rejects shocks below -1.0. Instruments that can settle negative must be shocked by
  position value directly.
- **Not a regulatory calculation.** No regulator-set methodology is implemented. See
  ``references/standards.md`` for what actually binds whom.
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

#: Key inside ``CrashScenario.asset_returns`` holding the shock applied to any symbol the
#: scenario does not name explicitly. Symbols priced off it are reported in
#: ``fallback_symbols`` -- a fallback is a modelling assumption, not a historical return.
FALLBACK_SYMBOL_KEY = "DEFAULT"

#: Stressed loss, as a fraction of NAV, at or above which the gate is flagged. A library
#: default, not a regulatory limit -- see references/standards.md.
DEFAULT_MAX_STRESSED_LOSS_PCT = 0.15

#: Report status values.
STATUS_COMPLETE = "STRESS_TEST_COMPLETE"
STATUS_INCOMPLETE_COVERAGE = "STRESS_TEST_INCOMPLETE_COVERAGE"

#: Quantities below this in absolute value are treated as flat.
_FLAT_QUANTITY_EPS = 1e-9


def _require_finite(value: object, name: str, context: str) -> float:
    """
    Rejects NaN, +/-Inf, bools and non-numeric input before it reaches the arithmetic.

    Anything exposing ``__float__`` is accepted, so quantities and prices arriving as
    ``numpy`` scalars or ``Decimal`` from the data layer work without conversion at the
    call site. ``bool`` is excluded explicitly because it is a subclass of ``int``:
    ``True`` would otherwise silently become a quantity of 1. Strings are excluded because
    a numeric string is a parsing bug upstream, not a quantity.
    """
    if isinstance(value, (bool, str, bytes)) or not hasattr(value, "__float__"):
        raise ValueError(f"{context}: {name} must be a real number, got {value!r}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{context}: {name} must be a real number, got {value!r}") from exc
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        raise ValueError(f"{context}: {name} must be finite, got {value!r}")
    return numeric


@dataclass
class CrashScenario:
    """
    A historical crash episode expressed as per-symbol cumulative return shocks.

    ``asset_returns`` maps ``symbol -> cumulative return`` over the scenario window
    (``-0.34`` = -34%), plus an optional ``DEFAULT`` entry applied to symbols the scenario
    does not name. A symbol matched by neither is reported unshocked rather than assigned
    an invented shock.

    ``window_start``/``window_end``/``basis`` record where the numbers came from. They are
    documentation, not inputs to the arithmetic, and they exist because a shock magnitude
    is meaningless without the window and the price basis it was measured over: an asset's
    return across the equity index's peak-to-trough window is not the same number as that
    asset's own worst move inside the episode.
    """
    name: str
    description: str
    asset_returns: Dict[str, float]
    window_start: str = ""
    window_end: str = ""
    basis: str = ""
    calibration_note: str = ""


@dataclass
class ScenarioResult:
    """Outcome of replaying the current book through one scenario."""
    scenario_name: str
    stressed_pnl_usd: float
    stressed_pnl_pct: float
    per_asset_impact: Dict[str, float]
    #: Gross absolute position value that actually received a shock.
    shocked_value_usd: float = 0.0
    #: Held, non-flat symbols the scenario named neither directly nor via DEFAULT.
    unshocked_symbols: List[str] = field(default_factory=list)
    #: Held, non-flat symbols priced off the scenario's DEFAULT rather than off a
    #: symbol-specific historical return.
    fallback_symbols: List[str] = field(default_factory=list)


@dataclass
class StressTestReport:
    """
    Full replay across the scenario library.

    ``worst_loss_pct`` and ``worst_loss_usd`` are **loss magnitudes**: both are ``0.0``
    when the worst scenario in the library is a gain. ``worst_pnl_pct`` and
    ``worst_pnl_usd`` carry the same outcome signed, so a gain is still auditable.
    """
    results: List[ScenarioResult]
    worst_scenario: str
    worst_loss_pct: float
    worst_loss_usd: float
    threshold_breached: bool
    breach_reason: Optional[str]
    #: Signed P&L of the worst (most adverse) scenario. Negative is a loss.
    worst_pnl_usd: float = 0.0
    worst_pnl_pct: float = 0.0
    status: str = STATUS_COMPLETE
    #: Held, non-flat symbols with no entry in ``prices`` -- excluded from every scenario.
    unpriced_symbols: List[str] = field(default_factory=list)
    #: Union across scenarios of symbols no shock reached.
    unshocked_symbols: List[str] = field(default_factory=list)
    #: Union across scenarios of symbols priced off a scenario DEFAULT.
    fallback_symbols: List[str] = field(default_factory=list)


# Built-in historical crash scenarios.
#
# These magnitudes are **library defaults for the broad-market proxies named below**, not
# reconstructions of the episodes and not regulator-set scenarios. They are deliberately
# left at their 1.0.0 values so existing callers' numbers do not move silently; where they
# sit against the historical record is set out with sources in references/standards.md.
# Recalibrate them from your own point-in-time data before relying on them.
#
# 1.0.0 also shipped per-single-name shocks (AAPL, MSFT, AMZN, TSLA, NVDA, META) in every
# scenario. Two of them could not have existed: the 2008 scenario assigned TSLA -80.0% and
# META -50.0%, but Tesla did not list until 29 June 2010 and Facebook/Meta not until
# 18 May 2012. They have been removed rather than re-estimated. A library cannot ship
# correct single-name crash returns for a universe it does not know -- doing so is the
# survivorship-bias trap this skill warns about, because the names that survived to be
# hard-coded are exactly the ones that did not go to -100%. Supply single-name shocks
# yourself, from point-in-time data that includes the names that died.
BUILTIN_SCENARIOS: List[CrashScenario] = [
    CrashScenario(
        name="2020_COVID_CRASH",
        description="COVID-19 pandemic sell-off, broad-market proxies",
        window_start="2020-02-19",
        window_end="2020-03-23",
        basis="close-to-close over the S&P 500's peak-to-trough window",
        calibration_note=(
            "The S&P 500 fell from a 3386.15 close on 2020-02-19 to 2237.40 on "
            "2020-03-23, -33.9%. Non-equity legs are window returns over those same "
            "dates and understate their own worst moves inside the episode: gold fell "
            "about 12% between 9 and 19 March 2020 in the dash for cash, against the "
            "-3.8% carried here."
        ),
        asset_returns={
            "SPY": -0.3393,
            "QQQ": -0.2833,
            "IWM": -0.4131,
            "EEM": -0.3120,
            "TLT": 0.1560,
            "GLD": -0.0380,
            FALLBACK_SYMBOL_KEY: -0.30,
        },
    ),
    CrashScenario(
        name="2008_GFC",
        description="Global Financial Crisis bear market, broad-market proxies",
        window_start="2007-10-09",
        window_end="2009-03-09",
        basis="close-to-close over the S&P 500's peak-to-trough window",
        calibration_note=(
            "1.0.0 labelled this scenario 'Sep-Nov 2008' but carried an SPY shock of "
            "-51.9%, which matches no such window: the S&P 500 closed at 752.44 on "
            "2008-11-20 against roughly 1280 in early September. The window recorded "
            "here is the bear market the magnitudes are closer to -- 1565.15 on "
            "2007-10-09 to 676.53 on 2009-03-09, -56.8%. The shipped shocks are milder "
            "than that decline and were not independently re-derived per symbol."
        ),
        asset_returns={
            "SPY": -0.5190,
            "QQQ": -0.4950,
            "IWM": -0.5530,
            "EEM": -0.6240,
            "TLT": 0.2830,
            "GLD": -0.0720,
            FALLBACK_SYMBOL_KEY: -0.50,
        },
    ),
    CrashScenario(
        name="2015_FLASH_CRASH",
        description="24 August 2015 China-driven flash crash, broad-market proxies",
        window_start="2015-08-24",
        window_end="2015-08-24",
        basis="single-day intraday trough against the prior close -- NOT peak-to-trough",
        calibration_note=(
            "The S&P 500 fell to an intraday 1867.01, -5.3% against the 21 August close, "
            "and finished -3.9%. These index-level magnitudes badly understate what the "
            "episode did to ETFs: many traded far below net asset value in the first "
            "hour -- SPLV printed -45.8% intraday and closed -5.3% -- and 1,237 "
            "circuit-breaker halts fired that day, 85% of them in exchange-traded "
            "products. If your book holds ETFs, shock them at the dislocation, not at "
            "the index. Note this scenario's basis differs from the other two."
        ),
        asset_returns={
            "SPY": -0.0530,
            "QQQ": -0.0650,
            "IWM": -0.0780,
            "EEM": -0.0710,
            "TLT": 0.0200,
            "GLD": 0.0150,
            FALLBACK_SYMBOL_KEY: -0.06,
        },
    ),
]


class HistoricalStressTester:
    """
    Replays the current position vector through a library of historical crash scenarios
    and flags a stressed-loss gate.

    The gate compares a **loss magnitude** against ``max_stressed_loss_pct`` and fires at
    or above it: a stressed loss of exactly the threshold is a breach. A scenario gain can
    never fire it.
    """

    def __init__(
        self,
        max_stressed_loss_pct: float = DEFAULT_MAX_STRESSED_LOSS_PCT,
        scenarios: Optional[List[CrashScenario]] = None,
    ) -> None:
        threshold = _require_finite(
            max_stressed_loss_pct, "max_stressed_loss_pct", "HistoricalStressTester"
        )
        if threshold <= 0.0:
            raise ValueError(
                f"HistoricalStressTester: max_stressed_loss_pct must be > 0, got {threshold!r}"
            )
        self.max_stressed_loss_pct: float = threshold

        library = list(BUILTIN_SCENARIOS if scenarios is None else scenarios)
        if not library:
            raise ValueError("HistoricalStressTester: scenario library must not be empty")

        seen: Dict[str, None] = {}
        for scenario in library:
            if not isinstance(scenario, CrashScenario):
                raise ValueError(
                    f"HistoricalStressTester: expected CrashScenario, got {type(scenario).__name__}"
                )
            if not isinstance(scenario.name, str) or not scenario.name.strip():
                raise ValueError(
                    "HistoricalStressTester: scenario name must be a non-empty string"
                )
            if scenario.name in seen:
                raise ValueError(
                    f"HistoricalStressTester: duplicate scenario name {scenario.name!r}; "
                    "the worst-case result would be ambiguous"
                )
            seen[scenario.name] = None
            if not scenario.asset_returns:
                raise ValueError(
                    f"HistoricalStressTester: scenario {scenario.name!r} declares no shocks"
                )
            for symbol, shock in scenario.asset_returns.items():
                value = _require_finite(
                    shock, f"shock for {symbol!r}", f"scenario {scenario.name!r}"
                )
                if value < -1.0:
                    raise ValueError(
                        f"scenario {scenario.name!r}: shock for {symbol!r} is {value!r}; a "
                        "return shock cannot take a price below zero. Shock the position "
                        "value directly."
                    )
        self.scenarios: List[CrashScenario] = library

    def _get_scenario_return(
        self, scenario: CrashScenario, symbol: str
    ) -> Optional[float]:
        """
        Returns the scenario's shock for ``symbol``, or ``None`` if the scenario names
        neither the symbol nor a ``DEFAULT`` fallback.

        ``None`` means "this scenario has nothing to say about this symbol" and the caller
        reports it as unshocked. 1.0.0 returned a hard-coded ``-0.30`` here instead, which
        put an unsourced magnitude into a report labelled as a historical replay.
        """
        if symbol in scenario.asset_returns:
            return scenario.asset_returns[symbol]
        return scenario.asset_returns.get(FALLBACK_SYMBOL_KEY)

    def _price_book(
        self, positions: Dict[str, float], prices: Dict[str, float]
    ) -> "tuple[Dict[str, float], List[str]]":
        """
        Validates the book and returns ``(symbol -> signed position value, unpriced)`` for
        every held, non-flat symbol. Flat positions are skipped; unpriced ones are
        collected for reporting rather than silently dropped.
        """
        if not isinstance(positions, dict) or not isinstance(prices, dict):
            raise ValueError("run_stress_test: positions and prices must both be dicts")
        if not positions:
            raise ValueError(
                "run_stress_test: positions is empty. A stress test over no positions "
                "returns a $0 loss and no breach, which reads as an all-clear on a book "
                "that was never examined. Skip the call when the book is genuinely flat."
            )

        values: Dict[str, float] = {}
        unpriced: List[str] = []
        for symbol, quantity in positions.items():
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError(
                    f"run_stress_test: position key must be a non-empty string, got {symbol!r}"
                )
            if symbol == FALLBACK_SYMBOL_KEY:
                raise ValueError(
                    f"run_stress_test: {FALLBACK_SYMBOL_KEY!r} is the scenario fallback key "
                    "and cannot also be a position symbol; its shock would be ambiguous"
                )
            qty = _require_finite(quantity, "quantity", f"position {symbol!r}")
            if abs(qty) < _FLAT_QUANTITY_EPS:
                continue
            if symbol not in prices:
                unpriced.append(symbol)
                continue
            price = _require_finite(prices[symbol], "price", f"position {symbol!r}")
            values[symbol] = qty * price

        if unpriced and not values:
            raise ValueError(
                "run_stress_test: none of the held positions could be priced "
                f"({', '.join(sorted(unpriced))}). Every scenario would report a $0 loss "
                "on a book that was never stressed."
            )
        return values, sorted(unpriced)

    def run_stress_test(
        self,
        positions: Dict[str, float],
        prices: Dict[str, float],
        portfolio_nav: float,
    ) -> StressTestReport:
        """
        Replays ``positions`` (signed quantities) at ``prices`` through every scenario.

        ``portfolio_nav`` is the capital base for every reported percentage. Raises
        ``ValueError`` on a non-positive or non-finite NAV, on non-finite quantities or
        prices, on an empty book, and on a book in which nothing could be priced.
        """
        nav = _require_finite(portfolio_nav, "portfolio_nav", "run_stress_test")
        if nav <= 0:
            raise ValueError(
                f"run_stress_test: portfolio_nav must be > 0, got {portfolio_nav!r}"
            )

        position_values, unpriced = self._price_book(positions, prices)

        results: List[ScenarioResult] = []
        all_unshocked: Dict[str, None] = {}
        all_fallback: Dict[str, None] = {}

        for scenario in self.scenarios:
            per_asset_impact: Dict[str, float] = {}
            unshocked: List[str] = []
            fallback: List[str] = []
            total_pnl = 0.0
            shocked_value = 0.0

            for symbol, position_value in position_values.items():
                shock = self._get_scenario_return(scenario, symbol)
                if shock is None:
                    unshocked.append(symbol)
                    all_unshocked[symbol] = None
                    continue
                if symbol not in scenario.asset_returns:
                    fallback.append(symbol)
                    all_fallback[symbol] = None
                impact = position_value * shock
                per_asset_impact[symbol] = impact
                shocked_value += abs(position_value)
                total_pnl += impact

            # Finite inputs can still overflow: a position value near the float ceiling
            # sends the P&L to +/-inf, and an infinite loss formats as "inf% loss ($inf)"
            # in the breach reason. Fail loudly rather than emit a corrupt report.
            total_pnl = _require_finite(
                total_pnl, "stressed P&L", f"scenario {scenario.name!r}"
            )

            results.append(ScenarioResult(
                scenario_name=scenario.name,
                stressed_pnl_usd=total_pnl,
                stressed_pnl_pct=total_pnl / nav,
                per_asset_impact=per_asset_impact,
                shocked_value_usd=shocked_value,
                unshocked_symbols=sorted(unshocked),
                fallback_symbols=sorted(fallback),
            ))

        # Most adverse scenario = minimum SIGNED P&L. Ranking on a magnitude here is what
        # let 1.0.0 rank a gain as a loss.
        worst = min(results, key=lambda r: r.stressed_pnl_pct)

        # A loss magnitude, floored at zero: when the worst outcome across the library is
        # a gain there is no loss to gate on, and reporting abs(gain) as a loss fires the
        # gate on a book that made money.
        worst_loss_pct = max(0.0, -worst.stressed_pnl_pct)
        worst_loss_usd = max(0.0, -worst.stressed_pnl_usd)
        breached = worst_loss_pct >= self.max_stressed_loss_pct

        unshocked_symbols = sorted(all_unshocked)
        fallback_symbols = sorted(all_fallback)
        status = (
            STATUS_INCOMPLETE_COVERAGE
            if (unpriced or unshocked_symbols or fallback_symbols)
            else STATUS_COMPLETE
        )
        if unpriced:
            logger.warning(
                "STRESS TEST COVERAGE: %d held position(s) have no price and were excluded "
                "from every scenario: %s. The reported loss understates the book.",
                len(unpriced), ", ".join(unpriced),
            )
        if unshocked_symbols:
            logger.warning(
                "STRESS TEST COVERAGE: %d held position(s) are named by no scenario and have "
                "no %s fallback: %s. They contributed $0.",
                len(unshocked_symbols), FALLBACK_SYMBOL_KEY, ", ".join(unshocked_symbols),
            )
        if fallback_symbols:
            logger.warning(
                "STRESS TEST COVERAGE: %d held position(s) were priced off a scenario %s "
                "fallback, not a symbol-specific historical return: %s.",
                len(fallback_symbols), FALLBACK_SYMBOL_KEY, ", ".join(fallback_symbols),
            )

        breach_reason: Optional[str] = None
        if breached:
            breach_reason = (
                f"STRESS TEST BREACH: Worst-case scenario '{worst.scenario_name}' produces "
                f"{worst_loss_pct:.2%} loss (${worst_loss_usd:,.2f}), exceeding "
                f"threshold {self.max_stressed_loss_pct:.2%}. Blocking new entries."
            )
            logger.warning(breach_reason)

        return StressTestReport(
            results=results,
            worst_scenario=worst.scenario_name,
            worst_loss_pct=worst_loss_pct,
            worst_loss_usd=worst_loss_usd,
            threshold_breached=breached,
            breach_reason=breach_reason,
            worst_pnl_usd=worst.stressed_pnl_usd,
            worst_pnl_pct=worst.stressed_pnl_pct,
            status=status,
            unpriced_symbols=unpriced,
            unshocked_symbols=unshocked_symbols,
            fallback_symbols=fallback_symbols,
        )
