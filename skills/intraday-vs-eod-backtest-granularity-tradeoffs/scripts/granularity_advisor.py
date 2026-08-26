"""
intraday-vs-eod-backtest-granularity-tradeoffs: advisor for choosing backtest bar
resolution, auditing in-bar execution-path ambiguity, and sizing the resulting
dataset before it is ingested.

Two questions this module answers, and they pull in opposite directions:

1. **Is the selected resolution fine enough to simulate this strategy honestly?**
   An OHLC bar records four prices and no path. Given a bar with a High above the
   take-profit and a Low below the stop-loss, the bar itself cannot say which came
   first, so the simulator has to *assume* an answer -- and the assumption decides
   whether the trade books a win or a loss. This is the dominant fidelity risk for
   any strategy with an intraday stop.

2. **What does that resolution cost to store and process?**

        points  = trading_days_per_year x history_years x records_per_day x universe
        bytes   = points x bytes_per_record / compression_ratio

Reading order for the ambiguity model, because it is the part most often stated
wrongly:

- The ambiguity is a property of **bars**, not of *daily* bars. Freqtrade's
  backtesting documentation puts it plainly: backtesting "lacks some detailed
  information about what happens within a candle", and with four data points per
  candle you cannot know whether the High preceded the Low. Moving from daily bars
  to 1-minute bars shrinks the window in which the ordering is unknown; it does not
  close it. A stop placed a few ticks away is inside the range of a 1-minute bar as
  surely as it is inside the range of a daily one.
  https://www.freqtrade.io/en/stable/backtesting/
- Because the ambiguity cannot be removed at bar granularity, mainstream engines
  resolve it by *convention*, and the convention is the whole result. backtesting.py
  gives the stop-loss priority when both levels fall inside one bar -- its maintainer
  describes the framework as taking "an adversarial, rather than an optimistic
  stance". Freqtrade likewise evaluates stoploss before ROI within a candle and warns
  this yields more stop exits in backtest than in dry/live. Conventions are not
  uniform, and platforms expose intra-bar modes (TradeStation's "Use Look-Inside-Bar
  back-testing", Freqtrade's ``--timeframe-detail``) precisely because an OHLC-only
  default has to resolve such bars by assumption. Read your engine's; do not assume
  it leans pessimistic, because the optimistic direction inflates every ambiguous
  trade and nothing in the output says so.
  https://github.com/kernc/backtesting.py/discussions/242
  https://help.tradestation.com/10_00/eng/tsportfolio/strategy_groups/strategy_group_settings.htm
- Therefore this module treats an **undeclared or optimistic** in-bar fill assumption
  on a stop-loss strategy as the defect, and a declared pessimistic one as a
  conservative *bound* rather than an unbiased estimate. Only genuine intra-bar data
  (tick / L2, or the detail-timeframe replay that Freqtrade exposes as
  ``--timeframe-detail``) resolves the ordering rather than assuming it.

Session and calendar constants (verified 2026-08):

- The NYSE Core Trading Session runs "9:30 a.m. to 4:00 p.m. ET" = **390 minutes**,
  which is where ``DEFAULT_SESSION_MINUTES_PER_DAY`` comes from. It covers regular
  hours only: extended-hours data, half-days (NYSE scheduled three 1:00 p.m. closes
  in 2026), and auction prints are not in that count.
  https://www.nyse.com/markets/hours-calendars
- ``DEFAULT_TRADING_DAYS_PER_YEAR = 252`` is the usual planning approximation, not a
  fixed quantity. Applying the published NYSE 2026 holiday list to the 2026 weekday
  calendar gives **251** trading days. Other venues differ far more: futures and FX
  sessions run close to 23 hours a day, and crypto venues trade continuously. Pass
  ``session_minutes_per_day`` / ``trading_days_per_year`` for anything that is not a
  US equity regular session; the defaults will otherwise understate a futures or
  crypto dataset several-fold.

Limitations (read before quoting a number out of this module):

- **The storage estimate is a planning figure, not a measurement.** It multiplies a
  record count by an assumed bytes-per-record. That product is exactly the practice
  ``historical-tick-data-storage-and-compaction`` warns against when it is presented
  as a compression baseline: "'60 bytes per tick' times the tick count is not a
  measurement of anything". Use this to decide whether a dataset is gigabytes or
  terabytes; measure the real archive before signing a storage contract.
- **``DEFAULT_TICKS_PER_SYMBOL_PER_DAY = 100_000`` is a heuristic and nothing more.**
  No venue or vendor publishes it. Real per-symbol message rates span orders of
  magnitude between a mega-cap on a volatile day and an illiquid listing, and L2
  depth updates outnumber trade prints by a wide and instrument-specific factor.
  Supply a measured value from your own feed whenever you have one.
- **Data volume is not run time.** ``data_volume_ratio_vs_recommended`` reports how
  many more *records* the selected dataset holds than the recommended one. It is a
  fact about the data, not a claimed wall-clock multiplier -- actual slowdown depends
  on the engine, the I/O path, and whether the strategy is vectorized. See
  ``vectorized-vs-event-driven-backtest-tradeoffs``.
- **Tick data resolves ordering, not fill realism.** Knowing the true path removes
  the High-vs-Low ambiguity; it does not model queue position, spread capture, or
  market impact. See ``execution-realistic-simulation``.
- **No strategy code is inspected.** The advice is derived entirely from the declared
  profile. A profile that misstates its holding period gets advice for the strategy
  it claims to be.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

#: Supported data resolutions.
GRANULARITIES: Tuple[str, ...] = ("TICK_L2", "INTRADAY_1MIN", "INTRADAY_5MIN", "DAILY_EOD")

#: Coarse-to-fine ordering. Used to decide whether a selection is under-resolved for
#: the strategy (rank below the recommendation) or wastefully over-resolved (rank two
#: or more steps above it).
GRANULARITY_RESOLUTION_RANK: Dict[str, int] = {
    "DAILY_EOD": 0,
    "INTRADAY_5MIN": 1,
    "INTRADAY_1MIN": 2,
    "TICK_L2": 3,
}

#: Bar length in minutes for the aggregated intraday resolutions. TICK_L2 is absent
#: deliberately: it is not a bar, which is exactly why it can resolve in-bar ordering.
BAR_GRANULARITY_MINUTES: Dict[str, int] = {"INTRADAY_1MIN": 1, "INTRADAY_5MIN": 5}

#: Supported strategy holding-period classes.
HOLDING_PERIODS: Tuple[str, ...] = (
    "INTRADAY_MINUTES", "INTRADAY_HOURS", "SWING_DAYS", "POSITIONAL_MONTHS",
)

#: How the backtester breaks a tie when stop-loss and take-profit both fall inside one
#: bar. 'PESSIMISTIC' = stop assumed first (backtesting.py / Freqtrade behaviour);
#: 'OPTIMISTIC' = target assumed first; 'UNSPECIFIED' = the caller has not checked,
#: which is treated as unsafe because the engine default is then unknown.
INTRABAR_FILL_ASSUMPTIONS: Tuple[str, ...] = ("PESSIMISTIC", "OPTIMISTIC", "UNSPECIFIED")

#: Per-instrument round trips per day at which tick/L2 replay becomes the sensible
#: default for an intraday strategy. An engineering choice, not a published standard.
HIGH_FREQUENCY_TRADES_PER_DAY: float = 50.0

#: NYSE/Nasdaq Core Trading Session, 09:30-16:00 ET. Regular hours only.
DEFAULT_SESSION_MINUTES_PER_DAY: int = 390

#: Conventional US equity planning approximation; the true 2026 NYSE count is 251.
DEFAULT_TRADING_DAYS_PER_YEAR: int = 252

#: Planning heuristic only -- see the module Limitations note.
DEFAULT_TICKS_PER_SYMBOL_PER_DAY: int = 100_000

#: Uncompressed on-disk width assumed per stored record, by resolution. Narrower for
#: ticks (price/size/side/timestamp) than for a daily bar (OHLCV plus adjustments).
DEFAULT_BYTES_PER_RECORD: Dict[str, int] = {
    "TICK_L2": 32, "INTRADAY_1MIN": 40, "INTRADAY_5MIN": 40, "DAILY_EOD": 48,
}

#: Storage figures are reported in GiB (2**30 bytes), not decimal GB.
BYTES_PER_GIB: int = 1024 ** 3

#: Report status values, most severe first.
STATUS_OHLC_SEQUENCE_BIAS = "OHLC_SEQUENCE_BIAS_WARNING"
STATUS_INSUFFICIENT_RESOLUTION = "INSUFFICIENT_RESOLUTION_WARNING"
STATUS_IN_BAR_PATH_AMBIGUITY = "IN_BAR_PATH_AMBIGUITY_WARNING"
STATUS_COMPUTE_OVERHEAD = "COMPUTE_OVERHEAD_WARNING"
STATUS_APPROVED = "GRANULARITY_APPROVED"


def _normalize(value: str, allowed: Tuple[str, ...], label: str) -> str:
    """Upper-cases and validates an enumerated string field.

    An unrecognized value is rejected rather than defaulted. The previous behaviour --
    falling through to DAILY_EOD -- silently sized a tick dataset as a daily one *and*
    bypassed the stop-loss ambiguity audit, so a misspelled resolution came back
    approved.
    """
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    normalized = value.strip().upper()
    if normalized not in allowed:
        raise ValueError(f"{label} must be one of {allowed}, got {value!r}")
    return normalized


def _require_positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an int, got {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{label} must be >= 1, got {value}")
    return value


def _require_finite_float(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be numeric, got {type(value).__name__}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite, got {value!r}")
    return numeric


def _require_positive_float(value: float, label: str) -> float:
    numeric = _require_finite_float(value, label)
    if numeric <= 0.0:
        raise ValueError(f"{label} must be > 0, got {numeric}")
    return numeric


def _require_non_negative_float(value: float, label: str) -> float:
    numeric = _require_finite_float(value, label)
    if numeric < 0.0:
        raise ValueError(f"{label} must be >= 0, got {numeric}")
    return numeric


@dataclass
class BacktestStrategyProfile:
    """Declared characteristics of the strategy whose backtest is being sized.

    ``trade_frequency_per_day`` is round trips per day **for a single instrument**,
    not for the portfolio. The distinction matters: 25 trades/day spread over a
    500-name universe is one trade per name every 20 days, which is a positional
    strategy, whereas 25 trades/day in one name is a scalper. Reading a portfolio
    count as a per-instrument rate is what pushes a monthly-hold strategy onto
    minute bars.

    The venue-calendar fields default to a US equity regular session. Override them
    for futures, FX, or crypto, where a trading day holds several times as many
    minutes and the year holds more days.
    """

    strategy_name: str
    holding_period: str                     # one of HOLDING_PERIODS
    trade_frequency_per_day: float          # round trips per day, per instrument
    has_intraday_stop_loss: bool            # strategy relies on intraday stop / target
    universe_size: int                      # instruments in the backtest universe
    history_years: float                    # calendar years of history
    selected_data_granularity: str          # one of GRANULARITIES
    intrabar_fill_assumption: str = "UNSPECIFIED"   # one of INTRABAR_FILL_ASSUMPTIONS
    session_minutes_per_day: int = DEFAULT_SESSION_MINUTES_PER_DAY
    trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR
    ticks_per_symbol_per_day: int = DEFAULT_TICKS_PER_SYMBOL_PER_DAY
    compression_ratio: float = 1.0          # 1.0 = uncompressed; measure, do not guess


@dataclass
class BacktestGranularityReport:
    """Outcome of the granularity audit.

    ``has_ohlc_sequence_bias`` is True whenever the strategy depends on intraday
    stop/target ordering **and** the selected data is bars of any length, because bars
    of any length leave that ordering unknown. It stays True on an approved config
    that declares a pessimistic tie-break: the convention bounds the error, it does
    not remove it. Only ``TICK_L2`` clears the flag.
    """

    strategy_name: str
    recommended_granularity: str
    estimated_data_points_count: int
    estimated_storage_size_gb: float        # GiB (2**30 bytes), see BYTES_PER_GIB
    has_ohlc_sequence_bias: bool
    status: str
    audit_notes: str
    selected_granularity: str = ""
    recommended_data_points_count: int = 0
    recommended_storage_size_gb: float = 0.0
    data_volume_ratio_vs_recommended: float = 1.0   # records, not run time
    intrabar_fill_assumption: str = "UNSPECIFIED"
    profile_warnings: List[str] = field(default_factory=list)


class BacktestGranularityAdvisorEngine:
    """
    Recommends a backtest data resolution for a declared strategy profile, audits the
    in-bar execution-path ambiguity the selected resolution leaves unresolved, and
    estimates the dataset footprint at both the selected and the recommended
    resolution.
    """

    def __init__(self, bytes_per_record: Optional[Mapping[str, int]] = None) -> None:
        """
        Args:
            bytes_per_record: Optional per-granularity override of the assumed
                uncompressed record width. Supply measured widths from your own
                schema; the defaults are planning figures.
        """
        merged: Dict[str, int] = dict(DEFAULT_BYTES_PER_RECORD)
        if bytes_per_record is not None:
            for granularity, width in bytes_per_record.items():
                key = _normalize(granularity, GRANULARITIES, "bytes_per_record key")
                merged[key] = _require_positive_int(width, f"bytes_per_record[{key}]")
        self.bytes_per_record: Dict[str, int] = merged

    def records_per_symbol_per_day(
        self,
        granularity: str,
        session_minutes_per_day: int = DEFAULT_SESSION_MINUTES_PER_DAY,
        ticks_per_symbol_per_day: int = DEFAULT_TICKS_PER_SYMBOL_PER_DAY,
    ) -> int:
        """Records stored per symbol per trading day at the given resolution.

        Bar counts are derived from the session length rather than hard-coded, so a
        futures or crypto session produces the right count instead of a US-equity one.
        A partial trailing bar is counted (ceiling), matching how bar builders emit a
        final stub bar on an early close.
        """
        granularity = _normalize(granularity, GRANULARITIES, "granularity")
        _require_positive_int(session_minutes_per_day, "session_minutes_per_day")
        if session_minutes_per_day > 1440:
            raise ValueError(
                f"session_minutes_per_day must be <= 1440 (one calendar day), "
                f"got {session_minutes_per_day}")

        if granularity == "TICK_L2":
            return _require_positive_int(ticks_per_symbol_per_day, "ticks_per_symbol_per_day")
        if granularity == "DAILY_EOD":
            return 1
        return math.ceil(session_minutes_per_day / BAR_GRANULARITY_MINUTES[granularity])

    def estimate_data_footprint(
        self,
        granularity: str,
        universe_size: int,
        history_years: float,
        session_minutes_per_day: int = DEFAULT_SESSION_MINUTES_PER_DAY,
        trading_days_per_year: int = DEFAULT_TRADING_DAYS_PER_YEAR,
        ticks_per_symbol_per_day: int = DEFAULT_TICKS_PER_SYMBOL_PER_DAY,
        compression_ratio: float = 1.0,
    ) -> Tuple[int, float]:
        """Estimates total record count and on-disk size for a backtest dataset.

        Returns:
            ``(record_count, size_gib)`` where ``size_gib`` uses 2**30 bytes and is
            rounded to 3 decimals.

        The size is ``records x assumed_bytes_per_record / compression_ratio``. It is a
        planning estimate: the bytes-per-record term is assumed, not measured, so treat
        the output as an order of magnitude and measure the real archive before
        committing storage. ``compression_ratio`` defaults to 1.0 (uncompressed); pass
        a ratio you have actually measured on your own encoder.
        """
        granularity = _normalize(granularity, GRANULARITIES, "granularity")
        _require_positive_int(universe_size, "universe_size")
        _require_positive_float(history_years, "history_years")
        _require_positive_int(trading_days_per_year, "trading_days_per_year")
        if trading_days_per_year > 366:
            raise ValueError(
                f"trading_days_per_year must be <= 366, got {trading_days_per_year}")
        if _require_positive_float(compression_ratio, "compression_ratio") < 1.0:
            raise ValueError(
                f"compression_ratio must be >= 1.0 (1.0 = uncompressed), "
                f"got {compression_ratio}")

        records_per_day = self.records_per_symbol_per_day(
            granularity, session_minutes_per_day, ticks_per_symbol_per_day)
        total_days = trading_days_per_year * history_years
        total_points = int(total_days * records_per_day * universe_size)
        raw_bytes = total_points * self.bytes_per_record[granularity]
        storage_gib = round(raw_bytes / compression_ratio / BYTES_PER_GIB, 3)
        return total_points, storage_gib

    def recommend_granularity(self, profile: BacktestStrategyProfile) -> str:
        """Minimum resolution that can simulate the declared strategy honestly.

        Holding period gates the decision and trade frequency only escalates *within*
        an intraday holding period. Letting a portfolio-level trade count drive the
        recommendation on its own is how a months-long positional strategy ends up
        sized for minute bars.

        A swing or positional strategy that still carries an intraday stop needs
        intraday data to know whether that stop was touched, so it is floored at
        5-minute bars rather than daily.
        """
        holding_period = _normalize(profile.holding_period, HOLDING_PERIODS, "holding_period")
        frequency = _require_non_negative_float(
            profile.trade_frequency_per_day, "trade_frequency_per_day")

        if holding_period in ("INTRADAY_MINUTES", "INTRADAY_HOURS"):
            return "TICK_L2" if frequency >= HIGH_FREQUENCY_TRADES_PER_DAY else "INTRADAY_1MIN"
        return "INTRADAY_5MIN" if profile.has_intraday_stop_loss else "DAILY_EOD"

    def _validate_profile(self, profile: BacktestStrategyProfile) -> Tuple[str, str, str]:
        if not isinstance(profile.strategy_name, str) or not profile.strategy_name.strip():
            raise ValueError("strategy_name must be a non-empty string")
        if not isinstance(profile.has_intraday_stop_loss, bool):
            raise TypeError("has_intraday_stop_loss must be a bool")
        holding_period = _normalize(profile.holding_period, HOLDING_PERIODS, "holding_period")
        selected = _normalize(
            profile.selected_data_granularity, GRANULARITIES, "selected_data_granularity")
        fill_assumption = _normalize(
            profile.intrabar_fill_assumption, INTRABAR_FILL_ASSUMPTIONS,
            "intrabar_fill_assumption")
        _require_non_negative_float(
            profile.trade_frequency_per_day, "trade_frequency_per_day")
        return holding_period, selected, fill_assumption

    def _profile_consistency_warnings(
        self, profile: BacktestStrategyProfile, holding_period: str,
    ) -> List[str]:
        warnings: List[str] = []
        if (holding_period in ("SWING_DAYS", "POSITIONAL_MONTHS")
                and profile.trade_frequency_per_day >= 20.0):
            warnings.append(
                f"trade_frequency_per_day={profile.trade_frequency_per_day:g} is a "
                f"per-instrument rate and contradicts holding_period='{holding_period}'. "
                f"If this is a portfolio-wide count, divide by universe_size "
                f"({profile.universe_size}) before passing it."
            )
        if (profile.session_minutes_per_day == DEFAULT_SESSION_MINUTES_PER_DAY
                and profile.trading_days_per_year > 300):
            warnings.append(
                f"trading_days_per_year={profile.trading_days_per_year} with the default "
                f"{DEFAULT_SESSION_MINUTES_PER_DAY}-minute US equity session is "
                f"inconsistent; a continuously traded venue also has a longer day."
            )
        return warnings

    def advise_backtest_granularity(
        self,
        profile: BacktestStrategyProfile,
    ) -> BacktestGranularityReport:
        """Audits a strategy/resolution pairing and sizes the resulting dataset.

        Raises:
            ValueError / TypeError: on an unrecognized or out-of-range profile field.
                Rejecting is deliberate -- an unrecognized resolution previously came
                back ``GRANULARITY_APPROVED``.
        """
        holding_period, selected, fill_assumption = self._validate_profile(profile)
        recommended = self.recommend_granularity(profile)

        footprint_kwargs = dict(
            universe_size=profile.universe_size,
            history_years=profile.history_years,
            session_minutes_per_day=profile.session_minutes_per_day,
            trading_days_per_year=profile.trading_days_per_year,
            ticks_per_symbol_per_day=profile.ticks_per_symbol_per_day,
            compression_ratio=profile.compression_ratio,
        )
        selected_points, selected_gib = self.estimate_data_footprint(selected, **footprint_kwargs)
        recommended_points, recommended_gib = self.estimate_data_footprint(
            recommended, **footprint_kwargs)
        volume_ratio = round(selected_points / recommended_points, 3) if recommended_points else 0.0

        # In-bar ordering is unknown on bars of *any* length. Only tick/L2 carries the
        # path that decides whether the stop or the target was reached first.
        has_bias = profile.has_intraday_stop_loss and selected != "TICK_L2"
        selected_rank = GRANULARITY_RESOLUTION_RANK[selected]
        recommended_rank = GRANULARITY_RESOLUTION_RANK[recommended]
        warnings = self._profile_consistency_warnings(profile, holding_period)

        if has_bias and selected == "DAILY_EOD":
            status = STATUS_OHLC_SEQUENCE_BIAS
            notes = (
                f"OHLC SEQUENCE BIAS [{profile.strategy_name}]: strategy uses an intraday "
                f"stop-loss but selected 'DAILY_EOD'. A daily bar's High and Low carry no "
                f"ordering, so a bar spanning both stop and target resolves entirely on the "
                f"engine's tie-break convention. Minimum resolution: '{recommended}'; only "
                f"'TICK_L2' resolves the ordering rather than assuming it."
            )
        elif selected_rank < recommended_rank:
            status = STATUS_INSUFFICIENT_RESOLUTION
            notes = (
                f"INSUFFICIENT RESOLUTION [{profile.strategy_name}]: selected '{selected}' is "
                f"coarser than the '{recommended}' this profile requires "
                f"(holding_period='{holding_period}'). Entries and exits that fall inside one "
                f"bar cannot be simulated from that bar's OHLC."
            )
        elif has_bias and fill_assumption != "PESSIMISTIC":
            status = STATUS_IN_BAR_PATH_AMBIGUITY
            notes = (
                f"IN-BAR PATH AMBIGUITY [{profile.strategy_name}]: '{selected}' bars narrow the "
                f"unknown-ordering window but do not close it, and "
                f"intrabar_fill_assumption='{fill_assumption}'. Read the engine's tie-break out "
                f"of its documentation (backtesting.py and Freqtrade apply the stop first; do "
                f"not assume yours does), or replay tick/detail-timeframe data."
            )
        elif selected_rank - recommended_rank >= 2:
            status = STATUS_COMPUTE_OVERHEAD
            notes = (
                f"COMPUTE OVERHEAD [{profile.strategy_name}]: '{selected}' holds "
                f"{selected_points:,} records ({selected_gib:.3f} GiB) against {recommended_points:,} "
                f"({recommended_gib:.3f} GiB) for the recommended '{recommended}' -- "
                f"{volume_ratio:,.0f}x the records for a '{holding_period}' strategy. Record count, "
                f"not measured run time."
            )
        else:
            status = STATUS_APPROVED
            notes = (
                f"GRANULARITY APPROVED [{profile.strategy_name}]: '{selected}' matches the "
                f"'{recommended}' minimum for this profile. Dataset = {selected_points:,} records "
                f"({selected_gib:.3f} GiB over {profile.history_years:g} years, estimated from an "
                f"assumed {self.bytes_per_record[selected]} bytes/record)."
            )
            if has_bias:
                notes += (
                    " In-bar ordering remains unresolved on bars; the declared PESSIMISTIC "
                    "tie-break makes the result a conservative bound, not an unbiased estimate."
                )

        if status == STATUS_APPROVED:
            logger.info(notes)
        else:
            logger.warning(notes)
        for warning in warnings:
            logger.warning("PROFILE CONSISTENCY [%s]: %s", profile.strategy_name, warning)

        return BacktestGranularityReport(
            strategy_name=profile.strategy_name,
            recommended_granularity=recommended,
            estimated_data_points_count=selected_points,
            estimated_storage_size_gb=selected_gib,
            has_ohlc_sequence_bias=has_bias,
            status=status,
            audit_notes=notes,
            selected_granularity=selected,
            recommended_data_points_count=recommended_points,
            recommended_storage_size_gb=recommended_gib,
            data_volume_ratio_vs_recommended=volume_ratio,
            intrabar_fill_assumption=fill_assumption,
            profile_warnings=warnings,
        )
