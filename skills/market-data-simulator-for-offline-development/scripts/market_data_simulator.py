"""
market-data-simulator-for-offline-development: synthetic tick and top-of-book
generator for offline strategy development.

What this module actually does
------------------------------
Given a ``SimulationConfig`` it produces a deterministic stream of ``SimulatedTick``
records:

    1. Walk a mid-price with the **exact** (not Euler-discretised) solution of
       Geometric Brownian Motion:
       ``S_i = S_{i-1} * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z_i)``.
       The walk is carried at full float precision; quantisation is applied to the
       emitted value only, never to the state fed into the next step.
    2. Quantise the emitted mid onto the ``price_tick_size`` grid and derive a
       top-of-book quote around it, widening to the enclosing tick boundaries so the
       quoted spread is never *narrower* than requested and never narrower than one
       tick.
    3. Synthesise top-of-book depth from a second, independent random stream.
    4. Return a ``SimulationReport`` carrying path statistics and the **realised**
       annualised volatility, so the calibration can be checked rather than assumed.

Two clocks, and why it matters
------------------------------
``drift_mu`` and ``volatility_sigma`` are **annualised**. Converting them to a
per-tick step requires knowing how many seconds are in a "year" for the instrument,
and there is no single answer:

  * ``TRADING_SECONDS_PER_YEAR_US_EQUITY_RTH`` (5,896,800 s) -- 252 trading days x
    6.5 h of NYSE/Nasdaq core session. Correct for cash equities whose sigma is
    quoted in business time.
  * ``SECONDS_PER_YEAR_CONTINUOUS`` (31,536,000 s) -- 365 x 24 h. Correct for crypto
    and other continuously traded instruments.

They differ by a factor of 5.35, i.e. sqrt(5.35) ~= 2.31 in volatility terms.
Simulating ``BTC-USD`` on the equity clock produces a path that is ~2.31x as volatile
per elapsed wall-clock second as the sigma that was asked for. ``seconds_per_year`` is
therefore an explicit config field, and the report carries **two** realised
volatilities so the choice is visible rather than implicit:

  * ``realized_annualized_volatility`` -- annualised on the clock the run used. This
    is a self-consistency check on the discretisation and should always land within
    sampling error of ``configured_annualized_volatility``. It cannot tell you the
    clock was wrong, because the clock is an input to both sides.
  * ``realized_wall_clock_annualized_volatility`` -- the same path annualised on the
    365x24h calendar. **This is the number that exposes a wrong clock.** On the equity
    clock it comes back at ~2.31x the configured sigma, which is correct for an
    instrument that only trades 6.5 h a day and wrong for one that trades 24/7.

Determinism
-----------
Every run is a pure function of its ``SimulationConfig``. The engine holds no state,
seeds no global RNG, and reads no wall clock:

  * Two ``random.Random`` instances are derived from ``random_seed`` -- one for the
    price path, one for depth -- so a change to the depth model cannot perturb the
    price path of an existing regression fixture.
  * ``start_timestamp_epoch`` defaults to a fixed epoch, not ``time.time()``.
  * Tick timestamps are computed in **integer nanoseconds** from the tick index, so
    they neither accumulate float error nor collide. Float epoch seconds resolve to
    only ~238 ns at a 2020 epoch, so ``timestamp_epoch`` is a convenience view;
    ``timestamp_nanos`` is the authoritative timestamp.

Because nothing is shared between calls, one engine instance may be used
concurrently from multiple threads.

Deliberate limitations
----------------------
- **``last_price`` is the mid, not a trade print.** Nothing here models trade arrival
  or aggressor side. Filling a backtest at ``last_price`` against this feed pays zero
  spread. Fill at ``bid_price`` / ``ask_price``.
- **GBM only.** Constant volatility, independent Gaussian increments: no jumps, no
  volatility clustering, no intraday seasonality, no autocorrelation, no fat tails.
  Real returns have all of these. For GARCH and block-bootstrap paths see
  ``synthetic-data-generation-for-backtest-augmentation``; to test against real
  microstructure use recorded ticks and
  ``market-data-replay-harness-for-integration-testing``.
- **Top of book only.** One price level per side. No depth ladder, no queue, no order
  flow, and therefore no basis for queue-position or market-impact research.
- **Depth is uniform noise**, uncorrelated with price, spread or time of day. It is a
  plumbing placeholder that exercises field wiring, not a liquidity model.
- **No session structure.** No open/close, no auctions, no halts, no gaps, no
  holidays. Ticks arrive on an exactly regular grid, which no real feed does.
- **Never use it to validate a strategy's edge.** A GBM path has no predictable
  structure by construction, so a strategy that "works" on it has found noise.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 252 trading days x 6.5 h NYSE/Nasdaq core session (09:30-16:00 ET) x 3600 s.
#: Use for cash equities whose sigma is quoted in business time.
TRADING_SECONDS_PER_YEAR_US_EQUITY_RTH: float = 252.0 * 6.5 * 3600.0  # 5_896_800.0

#: 365 x 24 h. Use for crypto and any other continuously traded instrument.
SECONDS_PER_YEAR_CONTINUOUS: float = 365.0 * 24.0 * 3600.0  # 31_536_000.0

#: 2020-01-01T00:00:00Z. A fixed default, so an unspecified start time cannot make an
#: otherwise-seeded run irreproducible.
DEFAULT_START_TIMESTAMP_EPOCH: float = 1_577_836_800.0

#: Mixed into the seed for the depth stream so price and depth draw from independent
#: sequences. Fractional bits of the golden ratio; no significance beyond being an
#: arbitrary fixed constant.
_DEPTH_STREAM_OFFSET: int = 0x9E3779B97F4A7C15

#: A half-spread at or above this fraction of the mid drives the bid to zero or below.
_MAX_SPREAD_BPS_EXCLUSIVE: float = 20_000.0

#: Cap on derived price decimals, well inside double precision.
_MAX_PRICE_DECIMALS: int = 12


def _require_finite(value: float, name: str) -> float:
    """Reject non-numeric, NaN and +/-inf values before they reach the price path."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number, got {type(value).__name__}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return numeric


def decimals_for_tick(tick_size: float) -> int:
    """Decimal places needed to express a price on a ``tick_size`` grid.

    Used to strip binary-float artefacts (``0.1 + 0.2 -> 0.30000000000000004``) from
    emitted prices without moving them off the grid.

    Derived from the decimal exponent of the tick itself, not from its magnitude: a
    $0.25 futures tick needs **two** decimals, not one, even though it is larger than
    $0.1. Rounding a 0.25-grid price to one decimal would move it off the grid.
    """
    tick = _require_finite(tick_size, "tick_size")
    if tick <= 0.0:
        raise ValueError(f"tick_size must be strictly positive, got {tick_size!r}.")
    exponent = Decimal(repr(tick)).normalize().as_tuple().exponent
    if not isinstance(exponent, int):  # 'n', 'N' or 'F' for NaN/Inf, already excluded
        raise ValueError(f"tick_size must be a finite decimal, got {tick_size!r}.")
    decimals = max(0, -exponent)
    if decimals > _MAX_PRICE_DECIMALS:
        raise ValueError(
            f"tick_size {tick_size!r} needs {decimals} decimal places, above the "
            f"{_MAX_PRICE_DECIMALS}-decimal limit. Quantising to fewer decimals would "
            "silently snap prices onto a coarser grid than the one requested. Pass a "
            "tick size that is an exact decimal (a value such as 1/3, or an arithmetic "
            "result like 3 * 1e-8, is not)."
        )
    return decimals


@dataclass
class SimulationConfig:
    """Complete, self-contained description of one simulation run.

    Every field that affects the output lives here: two runs with equal configs
    produce identical tick streams, in any process, on any machine.
    """

    symbol: str                          # e.g. 'AAPL', 'BTC-USD'
    initial_price: float                 # Starting mid-price, strictly positive
    drift_mu: float = 0.05               # Annualised drift mu (5%)
    volatility_sigma: float = 0.20       # Annualised volatility sigma (20%)
    time_step_sec: float = 1.0           # Wall-clock seconds between ticks
    num_steps: int = 1000                # Number of ticks to generate
    spread_bps: float = 10.0             # Quoted spread, bps of mid (10 bps = 0.10%)
    start_timestamp_epoch: float = DEFAULT_START_TIMESTAMP_EPOCH
    random_seed: Optional[int] = 42      # None => irreproducible run, flagged on report

    #: Minimum price increment of the venue being simulated. Must match the
    #: instrument: 0.01 for most NMS stocks >= $1.00 and 0.0001 below $1.00
    #: (17 CFR 242.612), 0.25 for CME ES futures, 1e-8 for small-denomination crypto.
    #: See references/standards.md.
    price_tick_size: float = 0.01

    #: Seconds per year used to convert annualised mu/sigma into a per-tick step. Set
    #: to SECONDS_PER_YEAR_CONTINUOUS for 24/7 instruments; the default is the US
    #: equity regular-session clock.
    seconds_per_year: float = TRADING_SECONDS_PER_YEAR_US_EQUITY_RTH

    #: Uniform bounds for synthesised top-of-book depth. See the module docstring:
    #: this is a placeholder, not a liquidity model.
    min_depth: float = 10.0
    max_depth: float = 500.0
    depth_decimals: int = 2

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Validate every field. Runs on construction and again before generation."""
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string.")

        self.initial_price = _require_finite(self.initial_price, "initial_price")
        if self.initial_price <= 0.0:
            raise ValueError(
                f"initial_price must be strictly positive, got {self.initial_price!r}. "
                "GBM is undefined at or below zero: exp() cannot lift a non-positive "
                "price."
            )

        self.drift_mu = _require_finite(self.drift_mu, "drift_mu")

        self.volatility_sigma = _require_finite(
            self.volatility_sigma, "volatility_sigma")
        if self.volatility_sigma < 0.0:
            raise ValueError(
                f"volatility_sigma must be non-negative, got "
                f"{self.volatility_sigma!r}. A negative sigma only mirrors the "
                "Gaussian draw; it is a configuration error, not a valid input."
            )

        if isinstance(self.num_steps, bool) or not isinstance(self.num_steps, int):
            raise TypeError(
                f"num_steps must be an int, got {type(self.num_steps).__name__}.")
        if self.num_steps <= 0:
            raise ValueError(
                f"num_steps must be strictly positive, got {self.num_steps!r}.")

        self.time_step_sec = _require_finite(self.time_step_sec, "time_step_sec")
        if self.time_step_sec <= 0.0:
            raise ValueError(
                f"time_step_sec must be strictly positive, got "
                f"{self.time_step_sec!r}."
            )
        if round(self.time_step_sec * 1e9) < 1:
            raise ValueError(
                f"time_step_sec={self.time_step_sec!r} is below one nanosecond; tick "
                "timestamps are held as integer nanoseconds and would all collide."
            )

        self.spread_bps = _require_finite(self.spread_bps, "spread_bps")
        if self.spread_bps < 0.0:
            raise ValueError(
                f"spread_bps must be non-negative, got {self.spread_bps!r}.")
        if self.spread_bps >= _MAX_SPREAD_BPS_EXCLUSIVE:
            raise ValueError(
                f"spread_bps must be < {_MAX_SPREAD_BPS_EXCLUSIVE:,.0f} (a 200% "
                f"spread), got {self.spread_bps!r}: the half-spread would drive the "
                "bid to zero or below."
            )

        self.start_timestamp_epoch = _require_finite(
            self.start_timestamp_epoch, "start_timestamp_epoch")

        self.price_tick_size = _require_finite(self.price_tick_size, "price_tick_size")
        if self.price_tick_size <= 0.0:
            raise ValueError(
                f"price_tick_size must be strictly positive, got "
                f"{self.price_tick_size!r}."
            )

        # Called for its validation: it rejects a tick that cannot be represented
        # exactly at the emitted precision.
        decimals_for_tick(self.price_tick_size)
        if self.price_tick_size > self.initial_price:
            raise ValueError(
                f"price_tick_size ({self.price_tick_size!r}) exceeds initial_price "
                f"({self.initial_price!r}): every emitted price would be quantised to "
                "zero or to a grid point a whole tick away from the true mid. Set a "
                "tick size that matches the instrument."
            )
        if self.initial_price < 100.0 * self.price_tick_size:
            logger.warning(
                "SIMULATION CONFIG [%s]: initial_price %r is only %.1f ticks of "
                "price_tick_size %r, so quantisation error exceeds 0.5%% of the "
                "price. Check the tick size matches the instrument.",
                self.symbol, self.initial_price,
                self.initial_price / self.price_tick_size, self.price_tick_size,
            )

        self.seconds_per_year = _require_finite(self.seconds_per_year, "seconds_per_year")
        if self.seconds_per_year <= 0.0:
            raise ValueError(
                f"seconds_per_year must be strictly positive, got "
                f"{self.seconds_per_year!r}."
            )

        self.min_depth = _require_finite(self.min_depth, "min_depth")
        self.max_depth = _require_finite(self.max_depth, "max_depth")
        if self.min_depth < 0.0:
            raise ValueError(f"min_depth must be non-negative, got {self.min_depth!r}.")
        if self.max_depth < self.min_depth:
            raise ValueError(
                f"max_depth ({self.max_depth!r}) must be >= min_depth "
                f"({self.min_depth!r})."
            )

        if isinstance(self.depth_decimals, bool) or not isinstance(
                self.depth_decimals, int):
            raise TypeError(
                f"depth_decimals must be an int, got "
                f"{type(self.depth_decimals).__name__}."
            )
        if not 0 <= self.depth_decimals <= _MAX_PRICE_DECIMALS:
            raise ValueError(
                f"depth_decimals must be in [0, {_MAX_PRICE_DECIMALS}], got "
                f"{self.depth_decimals!r}."
            )

        if self.random_seed is not None and (
            isinstance(self.random_seed, bool)
            or not isinstance(self.random_seed, int)
        ):
            raise TypeError(
                "random_seed must be an int or None, got "
                f"{type(self.random_seed).__name__}."
            )


@dataclass
class SimulatedTick:
    """One synthetic top-of-book update.

    ``last_price`` is the **mid**, not a trade print -- see the module docstring.
    ``timestamp_nanos`` is authoritative; ``timestamp_epoch`` is a lossy float view.
    """

    sequence_id: int
    symbol: str
    timestamp_epoch: float
    bid_price: float
    ask_price: float
    last_price: float
    bid_volume: float
    ask_volume: float
    #: Same value as ``last_price``, named for what it actually is.
    mid_price: float = 0.0
    #: Exact integer-nanosecond timestamp: ``start + sequence_id * time_step_sec``.
    timestamp_nanos: int = 0
    #: Realised quoted spread, ``(ask - bid) / mid`` in bps. Equals or exceeds the
    #: configured ``spread_bps`` -- quotes are widened to the grid, never narrowed.
    quoted_spread_bps: float = 0.0
    #: True when the requested spread was narrower than one tick and the quote was
    #: widened to the venue's minimum increment.
    tick_constrained: bool = False
    #: Exact GBM log-return that produced this tick's mid, before quantisation.
    log_return: float = 0.0


@dataclass
class SimulationReport:
    """Summary of one run. Price statistics cover **emitted ticks only**.

    ``initial_price`` seeds the walk and is never emitted as a tick, so it is
    deliberately excluded from ``min_price`` / ``max_price``.
    """

    symbol: str
    total_ticks_generated: int
    initial_price: float
    final_price: float
    min_price: float
    max_price: float
    total_simulated_duration_sec: float
    random_seed: Optional[int]
    ticks: List[SimulatedTick]
    status: str                          # 'SIMULATION_COMPLETED'
    audit_notes: str
    #: False when ``random_seed`` is None: the run cannot be reproduced.
    deterministic: bool = True
    #: True when ticks were retained in ``ticks``; False when streamed and discarded.
    ticks_retained: bool = True
    seconds_per_year: float = TRADING_SECONDS_PER_YEAR_US_EQUITY_RTH
    price_tick_size: float = 0.01
    configured_annualized_volatility: float = 0.0
    #: Sample volatility of the realised log-return path, annualised on the clock the
    #: run used. None below two ticks. A self-consistency check on the discretisation:
    #: it should match ``configured_annualized_volatility`` within sampling error, and
    #: it cannot detect a wrong clock, because the clock is an input to both sides.
    realized_annualized_volatility: Optional[float] = None
    #: The same path annualised on the 365x24h calendar. **This** is the number that
    #: exposes a wrong clock: it equals the configured sigma only when the run used
    #: the continuous clock. None below two ticks.
    realized_wall_clock_annualized_volatility: Optional[float] = None
    mean_quoted_spread_bps: float = 0.0
    #: Ticks whose quote was widened to one tick because the requested spread was
    #: narrower than the venue increment.
    tick_constrained_quote_count: int = 0


class MarketDataSimulatorEngine:
    """Deterministic synthetic market-data generator.

    Stateless: every run is a pure function of its ``SimulationConfig``. No global RNG
    is seeded, no wall clock is read, and instances are safe to share across threads.
    """

    def __init__(self) -> None:
        pass

    @staticmethod
    def _build_streams(seed: Optional[int]) -> Tuple[random.Random, random.Random]:
        """Independent price and depth streams derived from a single seed.

        Separating them means a change to the depth model leaves the price path of an
        existing regression fixture untouched.
        """
        if seed is None:
            return random.Random(), random.Random()
        return random.Random(seed), random.Random(seed ^ _DEPTH_STREAM_OFFSET)

    def iter_synthetic_ticks(
        self, config: SimulationConfig
    ) -> Iterator[SimulatedTick]:
        """Stream synthetic ticks without materialising them.

        Use this for long runs: ``generate_synthetic_tick_stream`` retains every tick
        in memory by default, which a multi-million-tick session will not survive.

        Validation happens here, not on first ``next()``, so an invalid config fails
        where it was passed rather than wherever the iterator is first consumed.
        """
        config.validate()
        return self._iter_ticks(config)

    def _iter_ticks(self, config: SimulationConfig) -> Iterator[SimulatedTick]:
        price_rng, depth_rng = self._build_streams(config.random_seed)

        dt = config.time_step_sec / config.seconds_per_year
        drift_term = (config.drift_mu - 0.5 * config.volatility_sigma ** 2) * dt
        vol_term = config.volatility_sigma * math.sqrt(dt)

        tick_size = config.price_tick_size
        price_decimals = decimals_for_tick(tick_size)
        half_spread_fraction = config.spread_bps / (2.0 * 10_000.0)

        start_nanos = round(config.start_timestamp_epoch * 1e9)
        step_nanos = round(config.time_step_sec * 1e9)

        price = config.initial_price

        for i in range(1, config.num_steps + 1):
            z = price_rng.gauss(0.0, 1.0)
            log_return = drift_term + vol_term * z
            # Exact solution of dS = mu*S*dt + sigma*S*dW over one step. Carried at
            # full precision: quantising the state here would bias the walk and, on a
            # sub-tick instrument, snap it permanently to zero.
            price *= math.exp(log_return)
            if not math.isfinite(price) or price <= 0.0:
                raise ArithmeticError(
                    f"GBM path left the representable range at step {i} "
                    f"(price={price!r}). Check drift_mu, volatility_sigma, "
                    "time_step_sec and seconds_per_year: the per-step exponent has "
                    "overflowed."
                )

            mid_ticks_raw = price / tick_size
            mid = round(round(mid_ticks_raw) * tick_size, price_decimals)

            # Widen to the enclosing tick boundaries, never narrow: a simulator that
            # rounded the quote inward would understate transaction costs.
            half_spread_ticks = half_spread_fraction * mid_ticks_raw
            bid_ticks = math.floor(mid_ticks_raw - half_spread_ticks)
            ask_ticks = math.ceil(mid_ticks_raw + half_spread_ticks)
            if ask_ticks <= bid_ticks:
                ask_ticks = bid_ticks + 1

            bid = round(bid_ticks * tick_size, price_decimals)
            ask = round(ask_ticks * tick_size, price_decimals)
            if bid <= 0.0:
                raise ArithmeticError(
                    f"Bid went non-positive at step {i} (mid={mid!r}, bid={bid!r}, "
                    f"tick={tick_size!r}). The requested half-spread rounds the bid "
                    "to or below the bottom of the tick grid: lower spread_bps, "
                    "raise initial_price, use a finer price_tick_size, or shorten "
                    "the run."
                )

            timestamp_nanos = start_nanos + i * step_nanos

            yield SimulatedTick(
                sequence_id=i,
                symbol=config.symbol,
                timestamp_epoch=timestamp_nanos / 1e9,
                bid_price=bid,
                ask_price=ask,
                last_price=mid,
                bid_volume=round(
                    depth_rng.uniform(config.min_depth, config.max_depth),
                    config.depth_decimals),
                ask_volume=round(
                    depth_rng.uniform(config.min_depth, config.max_depth),
                    config.depth_decimals),
                mid_price=mid,
                timestamp_nanos=timestamp_nanos,
                quoted_spread_bps=(ask - bid) / mid * 10_000.0,
                tick_constrained=(2.0 * half_spread_ticks) < 1.0,
                log_return=log_return,
            )

    def generate_synthetic_tick_stream(
        self,
        config: SimulationConfig,
        retain_ticks: bool = True,
    ) -> SimulationReport:
        """Generate a complete run and summarise it.

        Pass ``retain_ticks=False`` to compute the report over a long run without
        holding every tick in memory: ``SimulationReport.ticks`` is then empty and
        ``ticks_retained`` is False.
        """
        config.validate()

        ticks: List[SimulatedTick] = []
        count = 0
        min_price = math.inf
        max_price = -math.inf
        final_price = config.initial_price
        sum_log_return = 0.0
        sum_log_return_sq = 0.0
        sum_spread_bps = 0.0
        constrained_count = 0

        for tick in self.iter_synthetic_ticks(config):
            count += 1
            if retain_ticks:
                ticks.append(tick)
            mid = tick.mid_price
            if mid < min_price:
                min_price = mid
            if mid > max_price:
                max_price = mid
            final_price = mid
            sum_spread_bps += tick.quoted_spread_bps
            if tick.tick_constrained:
                constrained_count += 1
            # Volatility is measured on the unquantised walk: quantisation is a
            # display convention of the venue, not a property of the process.
            sum_log_return += tick.log_return
            sum_log_return_sq += tick.log_return * tick.log_return

        realized_vol = self._annualized_sample_volatility(
            count, sum_log_return, sum_log_return_sq,
            config.seconds_per_year / config.time_step_sec,
        )
        realized_wall_clock_vol = self._annualized_sample_volatility(
            count, sum_log_return, sum_log_return_sq,
            SECONDS_PER_YEAR_CONTINUOUS / config.time_step_sec,
        )

        duration = config.num_steps * config.time_step_sec
        notes = self._build_audit_notes(
            config, count, duration, final_price, min_price, max_price,
            realized_vol, realized_wall_clock_vol, constrained_count,
        )
        if config.random_seed is None:
            logger.warning(
                "MARKET DATA SIMULATION [%s]: random_seed is None -- this run cannot "
                "be reproduced and must not be used as a regression fixture.",
                config.symbol,
            )
        logger.info(notes)

        return SimulationReport(
            symbol=config.symbol,
            total_ticks_generated=count,
            initial_price=config.initial_price,
            final_price=final_price,
            min_price=min_price,
            max_price=max_price,
            total_simulated_duration_sec=duration,
            random_seed=config.random_seed,
            ticks=ticks,
            status="SIMULATION_COMPLETED",
            audit_notes=notes,
            deterministic=config.random_seed is not None,
            ticks_retained=retain_ticks,
            seconds_per_year=config.seconds_per_year,
            price_tick_size=config.price_tick_size,
            configured_annualized_volatility=config.volatility_sigma,
            realized_annualized_volatility=realized_vol,
            realized_wall_clock_annualized_volatility=realized_wall_clock_vol,
            mean_quoted_spread_bps=sum_spread_bps / count if count else 0.0,
            tick_constrained_quote_count=constrained_count,
        )

    @staticmethod
    def _annualized_sample_volatility(
        count: int,
        sum_log_return: float,
        sum_log_return_sq: float,
        steps_per_year: float,
    ) -> Optional[float]:
        """Annualised sample standard deviation of the realised log-returns.

        Returns None below two observations, where the sample variance is undefined.
        The sum-of-squares form is adequate here because the log-returns are small and
        near-zero-mean by construction; it is not a general-purpose variance routine.
        """
        if count < 2:
            return None
        mean = sum_log_return / count
        variance = (sum_log_return_sq - count * mean * mean) / (count - 1)
        if variance <= 0.0:
            return 0.0
        return math.sqrt(variance) * math.sqrt(steps_per_year)

    @staticmethod
    def _build_audit_notes(
        config: SimulationConfig,
        count: int,
        duration: float,
        final_price: float,
        min_price: float,
        max_price: float,
        realized_vol: Optional[float],
        realized_wall_clock_vol: Optional[float],
        constrained_count: int,
    ) -> str:
        realized_txt = (
            f"{realized_vol:.4f}" if realized_vol is not None else "n/a (<2 ticks)"
        )
        wall_txt = (
            f"{realized_wall_clock_vol:.4f}"
            if realized_wall_clock_vol is not None else "n/a (<2 ticks)"
        )
        seed_txt = (
            str(config.random_seed) if config.random_seed is not None
            else "None (NOT REPRODUCIBLE)"
        )
        return (
            f"MARKET DATA SIMULATION COMPLETE [{config.symbol}]: generated {count:,} "
            f"ticks over {duration:,.6g}s simulated time on a "
            f"{config.seconds_per_year:,.0f}s/yr clock. Mid: start={config.initial_price:g}, "
            f"end={final_price:g}, min={min_price:g}, max={max_price:g}, "
            f"tick_size={config.price_tick_size:g}. Volatility: "
            f"configured={config.volatility_sigma:.4f}, realized={realized_txt}, """
            f"realized_wall_clock={wall_txt}. "
            f"Tick-constrained quotes: {constrained_count:,}/{count:,}. Seed={seed_txt}."
        )
