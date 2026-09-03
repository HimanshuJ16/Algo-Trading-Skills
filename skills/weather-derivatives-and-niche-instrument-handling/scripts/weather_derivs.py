"""
weather-derivatives-and-niche-instrument-handling: degree-day index accumulation,
CME weather futures/option settlement, capped OTC weather swap payoffs, and burn
analysis (historical simulation) with linear climate detrending.

Primary sources
---------------
- CME Rulebook Chapter 403, *CME Degree Days Index Futures* (US contracts), and the
  CME US weather product specifications: **USD 20 per index point**, base
  65 degrees Fahrenheit.
- CME European Monthly/Seasonal Weather HDD and CAT contract specifications:
  **EUR 20 per index point**, base 18 degrees Celsius for HDD; CAT accumulates the
  daily average temperature in Celsius with no base subtraction.
- CME Rulebook Chapter 411, *CME Pacific Rim CAT Index Futures* (Tokyo):
  **JPY 2,500 per index point**, Celsius.
- Jewson & Brix, *Weather Derivative Valuation*, Cambridge University Press, 2005 --
  Chapter 3 (burn analysis) and the "Trend models" appendix (detrending).

Units and currency convention (every number in this module obeys it)
--------------------------------------------------------------------
There is no universal "$20 multiplier". ``tick_value`` is denominated in the
contract's own ``currency`` and is **currency units per index point**. Degree-day
indexes are unitless point counts; a CAT index is a sum of daily mean temperatures
in degrees Celsius and may legitimately be negative. Temperatures carry an explicit
``TemperatureUnit`` -- this module never infers one.

Settlement authority
--------------------
The official CME settlement index is calculated and reported by Speedwell Settlement
Services Ltd from National Weather Service / Japan Meteorological Agency station
observations, on the second Exchange Business Day after the contract month. An index
computed here from raw station data is an *estimate* for pricing and risk; settle
cash against the reported index, never against a local recomputation.
"""
import logging
import math
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class TemperatureUnit(Enum):
    FAHRENHEIT = "FAHRENHEIT"
    CELSIUS = "CELSIUS"


class WeatherIndexType(Enum):
    HDD = "HDD"     # Heating Degree Days: max(0, base - T_mean), accumulated
    CDD = "CDD"     # Cooling Degree Days: max(0, T_mean - base), accumulated
    CAT = "CAT"     # Cumulative Average Temperature: sum of T_mean, no base


class InstrumentType(Enum):
    FUTURES = "FUTURES"
    CALL_OPTION = "CALL_OPTION"
    PUT_OPTION = "PUT_OPTION"
    CAPPED_SWAP = "CAPPED_SWAP"


class WeatherDerivativeError(Exception):
    """Base exception for Weather Derivatives Engine errors."""
    pass


@dataclass(frozen=True)
class ContractSpec:
    """
    A verified exchange contract specification. Build contracts from one of the
    ``CME_CONTRACT_SPECS`` entries rather than retyping a multiplier by hand -- the
    multiplier and its currency differ per venue and are the single most common
    source of order-of-magnitude sizing errors in this asset class.
    """
    name: str
    tick_value: float
    currency: str
    temperature_unit: TemperatureUnit
    index_types: Tuple[WeatherIndexType, ...]
    base_temperature: Optional[float] = None
    source: str = ""


#: Verified CME weather contract specifications. ``tick_value`` is per index point in
#: the stated ``currency`` -- note that only the US contracts are USD 20.
CME_CONTRACT_SPECS: Dict[str, ContractSpec] = {
    "CME_US_DEGREE_DAY": ContractSpec(
        name="CME US Degree Days Index Futures/Options (HDD, CDD)",
        tick_value=20.0,
        currency="USD",
        temperature_unit=TemperatureUnit.FAHRENHEIT,
        index_types=(WeatherIndexType.HDD, WeatherIndexType.CDD),
        base_temperature=65.0,
        source="CME Rulebook Ch. 403 / CME US weather product specifications",
    ),
    "CME_EUROPEAN_HDD": ContractSpec(
        name="CME European Monthly/Seasonal Weather HDD Index Futures",
        tick_value=20.0,
        currency="EUR",
        temperature_unit=TemperatureUnit.CELSIUS,
        index_types=(WeatherIndexType.HDD,),
        base_temperature=18.0,
        source="CME European weather HDD contract specifications",
    ),
    "CME_EUROPEAN_CAT": ContractSpec(
        name="CME European Monthly/Seasonal Weather CAT Index Futures",
        tick_value=20.0,
        currency="EUR",
        temperature_unit=TemperatureUnit.CELSIUS,
        index_types=(WeatherIndexType.CAT,),
        base_temperature=None,
        source="CME European Monthly Weather CAT contract specifications",
    ),
    "CME_PACIFIC_RIM_CAT": ContractSpec(
        name="CME Pacific Rim CAT Index Futures (Tokyo)",
        tick_value=2500.0,
        currency="JPY",
        temperature_unit=TemperatureUnit.CELSIUS,
        index_types=(WeatherIndexType.CAT,),
        base_temperature=None,
        source="CME Rulebook Ch. 411, CME Pacific Rim CAT Index Futures",
    ),
}


@dataclass
class WeatherDerivativeContract:
    """
    A weather futures, option, or capped OTC swap position.

    ``tick_value`` is in ``currency`` units per index point and must be supplied
    explicitly -- there is deliberately no default, because the correct value is
    USD 20 (US), EUR 20 (Europe), or JPY 2,500 (Pacific Rim) depending on venue.
    Use :meth:`from_spec` to take it from a verified specification.

    ``entry_index_price`` is the index level at which a FUTURES position was opened.
    Futures P&L is ``(final_index - entry_index_price) * tick_value * quantity``;
    without it only :meth:`WeatherDerivativesEngine.final_settlement_value` -- the
    contract's cash value at settlement -- is defined.

    Cap semantics: ``max_payout`` caps a gain, ``max_loss`` floors a loss, and both
    are non-negative magnitudes. If ``max_payout`` is set and ``max_loss`` is None
    the cap is symmetric, matching the standard OTC construction
    ``sign(P) * min(|P|, C_cap)``.
    """
    contract_id: str
    symbol: str
    location: str
    index_type: WeatherIndexType
    instrument_type: InstrumentType
    tick_value: float
    currency: str = "USD"
    strike_index: float = 0.0
    entry_index_price: Optional[float] = None
    quantity: float = 1.0
    max_payout: Optional[float] = None
    max_loss: Optional[float] = None
    start_date: datetime.date = field(default_factory=datetime.date.today)
    end_date: datetime.date = field(default_factory=datetime.date.today)

    def __post_init__(self) -> None:
        if not isinstance(self.index_type, WeatherIndexType):
            raise WeatherDerivativeError(f"index_type must be a WeatherIndexType, got {self.index_type!r}")
        if not isinstance(self.instrument_type, InstrumentType):
            raise WeatherDerivativeError(
                f"instrument_type must be an InstrumentType, got {self.instrument_type!r}"
            )
        if not math.isfinite(self.tick_value) or self.tick_value <= 0.0:
            raise WeatherDerivativeError(f"tick_value must be finite and positive, got {self.tick_value!r}")
        if not math.isfinite(self.quantity) or self.quantity == 0.0:
            raise WeatherDerivativeError(f"quantity must be finite and non-zero, got {self.quantity!r}")
        if not math.isfinite(self.strike_index):
            raise WeatherDerivativeError(f"strike_index must be finite, got {self.strike_index!r}")
        for limit_name in ("max_payout", "max_loss"):
            limit = getattr(self, limit_name)
            if limit is not None and (not math.isfinite(limit) or limit < 0.0):
                raise WeatherDerivativeError(
                    f"{limit_name} must be a non-negative finite magnitude, got {limit!r}"
                )
        if self.entry_index_price is not None and not math.isfinite(self.entry_index_price):
            raise WeatherDerivativeError(f"entry_index_price must be finite, got {self.entry_index_price!r}")
        if self.end_date < self.start_date:
            raise WeatherDerivativeError(f"end_date {self.end_date} precedes start_date {self.start_date}")

    @classmethod
    def from_spec(cls, spec_key: str, **kwargs) -> "WeatherDerivativeContract":
        """
        Build a contract from a verified :data:`CME_CONTRACT_SPECS` entry, taking
        ``tick_value`` and ``currency`` from the specification so neither can be
        mistyped. ``index_type`` must be one the specification actually lists.
        """
        try:
            spec = CME_CONTRACT_SPECS[spec_key]
        except KeyError:
            raise WeatherDerivativeError(
                f"Unknown contract spec {spec_key!r}. Known: {sorted(CME_CONTRACT_SPECS)}"
            ) from None
        index_type = kwargs.get("index_type")
        if index_type is not None and index_type not in spec.index_types:
            raise WeatherDerivativeError(
                f"{spec.name} does not list index type {index_type}; "
                f"it lists {[i.value for i in spec.index_types]}"
            )
        kwargs.setdefault("tick_value", spec.tick_value)
        kwargs.setdefault("currency", spec.currency)
        return cls(**kwargs)


@dataclass
class BurnAnalysisResult:
    contract_id: str
    currency: str
    historical_seasons_analyzed: int
    mean_index_value: float
    std_dev_index: float
    expected_payoff: float
    std_dev_payoff: float
    best_historical_payoff: float
    worst_historical_payoff: float
    payoff_5th_percentile: float


@dataclass
class SettlementPayoff:
    contract_id: str
    currency: str
    accumulated_index: float
    gross_payoff: float
    total_payoff: float
    is_capped: bool
    rationale: str


class WeatherDerivativesEngine:
    """
    Weather derivatives engine for CME degree-day / CAT contracts and capped OTC
    weather swaps: accumulates indexes from daily station temperatures, computes
    settlement payoffs at the contract's own multiplier and currency, and values
    contracts by burn analysis over detrended historical seasons.
    """

    def __init__(self) -> None:
        logger.info("Initialized Weather Derivatives & Niche Instrument Engine")

    # ------------------------------------------------------------------ indexes

    def calculate_monthly_index(
        self,
        daily_min_max_temps: Sequence[Tuple[float, float]],
        index_type: WeatherIndexType,
        temperature_unit: TemperatureUnit,
        base_temperature: Optional[float] = None,
    ) -> float:
        """
        Accumulate an HDD, CDD, or CAT index from daily ``(T_min, T_max)`` pairs.

        The daily average temperature is the mean of the day's maximum and minimum
        on a midnight-to-midnight basis. ``base_temperature`` is required for HDD and
        CDD and must be expressed in ``temperature_unit`` (65 F for CME US contracts,
        18 C for CME European HDD); it is rejected for CAT, which sums the daily mean
        directly and may therefore return a negative total.

        Missing or non-finite observations raise rather than being silently absorbed:
        ``max(0.0, float('nan'))`` evaluates to ``0.0`` in Python, so an unguarded NaN
        would understate a degree-day index without raising anything.

        The result is an estimate for pricing and risk. Cash settlement uses the index
        reported by Speedwell Settlement Services Ltd, whose published methodology
        governs rounding and the treatment of missing station observations.
        """
        if not isinstance(index_type, WeatherIndexType):
            raise WeatherDerivativeError(f"index_type must be a WeatherIndexType, got {index_type!r}")
        if not isinstance(temperature_unit, TemperatureUnit):
            raise WeatherDerivativeError(
                f"temperature_unit must be a TemperatureUnit, got {temperature_unit!r}. "
                "Degree-day base temperatures are unit-specific and are never inferred."
            )
        if not daily_min_max_temps:
            raise WeatherDerivativeError("Temperature list cannot be empty.")

        if index_type is WeatherIndexType.CAT:
            if base_temperature is not None:
                raise WeatherDerivativeError(
                    "CAT accumulates the daily mean temperature directly; base_temperature does not apply."
                )
            if temperature_unit is not TemperatureUnit.CELSIUS:
                raise WeatherDerivativeError("CME CAT indexes are defined in degrees Celsius.")
        else:
            if base_temperature is None:
                raise WeatherDerivativeError(
                    f"{index_type.value} requires an explicit base_temperature in "
                    f"{temperature_unit.value} (65.0 F for CME US contracts, "
                    "18.0 C for CME European HDD)."
                )
            if not math.isfinite(base_temperature):
                raise WeatherDerivativeError(f"base_temperature must be finite, got {base_temperature!r}")

        total_index = 0.0
        for day, pair in enumerate(daily_min_max_temps):
            t_min, t_max = pair
            if not (math.isfinite(t_min) and math.isfinite(t_max)):
                raise WeatherDerivativeError(
                    f"Non-finite temperature at day index {day}: (T_min={t_min!r}, T_max={t_max!r}). "
                    "Repair or explicitly infill the station series before accumulating."
                )
            if t_min > t_max:
                raise WeatherDerivativeError(
                    f"T_min {t_min} exceeds T_max {t_max} at day index {day} -- station record is corrupt."
                )

            t_mean = (t_max + t_min) / 2.0
            if index_type is WeatherIndexType.HDD:
                daily_val = max(0.0, base_temperature - t_mean)
            elif index_type is WeatherIndexType.CDD:
                daily_val = max(0.0, t_mean - base_temperature)
            else:
                daily_val = t_mean
            total_index += daily_val

        logger.info(
            "Accumulated %s index = %.2f points over %d days (%s)",
            index_type.value, total_index, len(daily_min_max_temps), temperature_unit.value,
        )
        return round(total_index, 2)

    # --------------------------------------------------------------- settlement

    @staticmethod
    def _validate_index(contract: WeatherDerivativeContract, accumulated_index: float) -> None:
        if not math.isfinite(accumulated_index):
            raise WeatherDerivativeError(f"accumulated_index must be finite, got {accumulated_index!r}")
        # HDD and CDD are sums of non-negative daily values. A CAT index is a sum of
        # Celsius means and is legitimately negative over a cold accumulation period.
        if contract.index_type is not WeatherIndexType.CAT and accumulated_index < 0.0:
            raise WeatherDerivativeError(
                f"{contract.index_type.value} index cannot be negative, got {accumulated_index}."
            )

    def final_settlement_value(
        self, contract: WeatherDerivativeContract, accumulated_index: float
    ) -> float:
        """
        Cash value of the contract at final settlement:
        ``index * tick_value * quantity``, in the contract's currency.

        This is the contract's *value*, not a position's profit. A futures position's
        P&L is measured from its entry index price -- see
        :meth:`calculate_settlement_payoff`.
        """
        self._validate_index(contract, accumulated_index)
        return round(accumulated_index * contract.tick_value * contract.quantity, 2)

    def calculate_settlement_payoff(
        self, contract: WeatherDerivativeContract, accumulated_index: float
    ) -> SettlementPayoff:
        """
        Settlement payoff (position P&L) in the contract's currency.

        - FUTURES: ``(index - entry_index_price) * tick_value * quantity``. The entry
          price is mandatory -- ``index * tick_value`` is the settlement *value* of
          the contract, and using it as P&L overstates the result by the entire entry
          notional.
        - CALL_OPTION: ``max(0, index - strike) * tick_value * quantity``, intrinsic
          value at expiry, before the premium paid.
        - PUT_OPTION: ``max(0, strike - index) * tick_value * quantity``.
        - CAPPED_SWAP: ``(index - strike) * tick_value * quantity``, then capped.
        """
        self._validate_index(contract, accumulated_index)

        scale = contract.tick_value * contract.quantity
        itype = contract.instrument_type

        if itype is InstrumentType.FUTURES:
            if contract.entry_index_price is None:
                raise WeatherDerivativeError(
                    f"FUTURES contract {contract.contract_id} has no entry_index_price; futures P&L is "
                    "(final_index - entry_index_price) * tick_value * quantity. Call "
                    "final_settlement_value() instead if you want the contract's cash settlement value."
                )
            gross = (accumulated_index - contract.entry_index_price) * scale
        elif itype is InstrumentType.CALL_OPTION:
            gross = max(0.0, accumulated_index - contract.strike_index) * scale
        elif itype is InstrumentType.PUT_OPTION:
            gross = max(0.0, contract.strike_index - accumulated_index) * scale
        elif itype is InstrumentType.CAPPED_SWAP:
            gross = (accumulated_index - contract.strike_index) * scale
        else:  # pragma: no cover - unreachable, instrument_type is validated
            raise WeatherDerivativeError(f"Unsupported instrument type {itype!r}")

        # Cap gains at max_payout and floor losses at -max_loss. When only max_payout
        # is given the cap is symmetric: sign(P) * min(|P|, C_cap).
        upper = contract.max_payout
        lower = contract.max_loss if contract.max_loss is not None else contract.max_payout

        final = gross
        is_capped = False
        if upper is not None and final > upper:
            final, is_capped = upper, True
        elif lower is not None and final < -lower:
            final, is_capped = -lower, True

        rationale = (
            f"Settlement [{contract.symbol}]: Index={accumulated_index:.2f}, "
            f"Strike={contract.strike_index:.2f}, Gross={gross:,.2f} {contract.currency}, "
            f"Final={final:,.2f} {contract.currency} "
            f"({'CAPPED' if is_capped else 'uncapped'})"
        )
        logger.info(rationale)

        return SettlementPayoff(
            contract_id=contract.contract_id,
            currency=contract.currency,
            accumulated_index=round(accumulated_index, 2),
            gross_payoff=round(gross, 2),
            total_payoff=round(final, 2),
            is_capped=is_capped,
            rationale=rationale,
        )

    # ------------------------------------------------------------- burn analysis

    @staticmethod
    def detrend_historical_indexes(
        historical_season_indexes: Sequence[float],
        target_season: Optional[int] = None,
    ) -> List[float]:
        """
        Remove a linear climate trend from a chronologically ordered index history,
        re-centring every season on the fitted level of ``target_season``.

        Ordinary least squares on ``index_j = a + b * j``; the adjusted series is
        ``index_j + (fit(target) - fit(j)) = index_j + b * (target - j)``, which
        preserves the residual dispersion while shifting the mean to the target
        season's climate. ``target_season`` defaults to the last (most recent)
        observation, index ``n - 1``; pass ``n`` to project one season forward. This
        is the standard trend correction in Jewson & Brix, *Weather Derivative
        Valuation* (2005), Chapter 3 and the "Trend models" appendix.

        Undetrended burn analysis overstates winter HDD and understates summer CDD,
        because the sample mean sits at the midpoint of a warming record rather than
        at the contract season.
        """
        n = len(historical_season_indexes)
        if n < 3:
            raise WeatherDerivativeError(
                f"Detrending needs at least 3 seasons to estimate a trend, got {n}."
            )
        for j, value in enumerate(historical_season_indexes):
            if not math.isfinite(value):
                raise WeatherDerivativeError(f"Non-finite historical index at season {j}: {value!r}")

        target = float(n - 1 if target_season is None else target_season)
        mean_x = (n - 1) / 2.0
        mean_y = sum(historical_season_indexes) / n
        sxx = sum((j - mean_x) ** 2 for j in range(n))
        sxy = sum((j - mean_x) * (y - mean_y) for j, y in enumerate(historical_season_indexes))
        slope = sxy / sxx  # sxx > 0 whenever n >= 2

        adjusted = [y + slope * (target - j) for j, y in enumerate(historical_season_indexes)]
        logger.info(
            "Detrended %d seasons to season %.0f: slope=%.4f points/season, mean %.2f -> %.2f",
            n, target, slope, mean_y, sum(adjusted) / n,
        )
        return adjusted

    @staticmethod
    def _percentile(sorted_values: Sequence[float], pct: float) -> float:
        """Linear-interpolated percentile of an ascending sequence (``pct`` in [0, 100])."""
        if not sorted_values:
            raise WeatherDerivativeError("Cannot take a percentile of an empty sample.")
        if len(sorted_values) == 1:
            return sorted_values[0]
        rank = (pct / 100.0) * (len(sorted_values) - 1)
        low = math.floor(rank)
        high = math.ceil(rank)
        if low == high:
            return sorted_values[int(rank)]
        return sorted_values[low] + (rank - low) * (sorted_values[high] - sorted_values[low])

    def run_burn_analysis(
        self,
        contract: WeatherDerivativeContract,
        historical_season_indexes: Sequence[float],
        discount_factor: float = 1.0,
    ) -> BurnAnalysisResult:
        """
        Burn analysis: replay the contract against each historical season's index and
        summarise the resulting payoff distribution.

        ``historical_season_indexes`` must already be **detrended** -- run
        :meth:`detrend_historical_indexes` first. Burn analysis is an unbiased fair
        value only if every historical season is a draw from the *contract season's*
        climate.

        ``discount_factor`` discounts the expected payoff from settlement back to
        today and defaults to 1.0 (undiscounted), which is adequate only for
        short-dated contracts. The best, worst, and 5th-percentile payoffs are
        reported undiscounted, as risk figures rather than values.

        Burn analysis carries no distributional model: with ~30 seasons the tail is
        estimated from one or two observations, so ``payoff_5th_percentile`` is a weak
        lower bound on a capped swap's downside, not a reliable tail measure.
        """
        n = len(historical_season_indexes)
        if n == 0:
            raise WeatherDerivativeError("Historical season indexes list cannot be empty.")
        if n < 2:
            raise WeatherDerivativeError(
                "Burn analysis needs at least 2 seasons for a dispersion estimate; got 1. "
                "20-30 seasons is the market convention."
            )
        if not math.isfinite(discount_factor) or discount_factor <= 0.0:
            raise WeatherDerivativeError(
                f"discount_factor must be finite and positive, got {discount_factor!r}"
            )

        mean_index = sum(historical_season_indexes) / n
        var_index = sum((x - mean_index) ** 2 for x in historical_season_indexes) / (n - 1)
        std_index = math.sqrt(var_index)

        payoffs = [
            self.calculate_settlement_payoff(contract, idx).total_payoff
            for idx in historical_season_indexes
        ]

        mean_payoff = sum(payoffs) / n
        var_payoff = sum((p - mean_payoff) ** 2 for p in payoffs) / (n - 1)
        std_payoff = math.sqrt(var_payoff)
        ordered = sorted(payoffs)

        expected_payoff = mean_payoff * discount_factor
        logger.info(
            "Burn analysis [%s]: %d seasons, MeanIndex=%.1f, ExpectedPayoff=%.2f %s, "
            "StdDevPayoff=%.2f, Worst=%.2f, Best=%.2f",
            contract.symbol, n, mean_index, expected_payoff, contract.currency,
            std_payoff, ordered[0], ordered[-1],
        )

        return BurnAnalysisResult(
            contract_id=contract.contract_id,
            currency=contract.currency,
            historical_seasons_analyzed=n,
            mean_index_value=round(mean_index, 2),
            std_dev_index=round(std_index, 2),
            expected_payoff=round(expected_payoff, 2),
            std_dev_payoff=round(std_payoff, 2),
            best_historical_payoff=round(ordered[-1], 2),
            worst_historical_payoff=round(ordered[0], 2),
            payoff_5th_percentile=round(self._percentile(ordered, 5.0), 2),
        )
