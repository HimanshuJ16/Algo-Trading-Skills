"""Order book microstructure signal research: OFI, weighted mid-price, and forward-return IC.

This module answers one question: *does a top-of-book microstructure signal computed
from this tick series carry forward-predictive information, or does it only look like
it does?* Four things about that question are easy to get wrong, and the module is
built around them.

**Order Flow Imbalance has a published definition, and the depletion branches are the
half that gets dropped.** Cont, Kukanov and Stoikov (2014) define the contribution of
the n-th top-of-book event as::

    e_n = 1{P_bid_n >= P_bid_n-1} q_bid_n  -  1{P_bid_n <= P_bid_n-1} q_bid_n-1
        - 1{P_ask_n <= P_ask_n-1} q_ask_n  +  1{P_ask_n >= P_ask_n-1} q_ask_n-1

A prior revision of this module assigned ``0`` when the best bid *fell* and when the
best ask *rose*, instead of the published ``-q_bid_n-1`` and ``+q_ask_n-1``. Those two
branches are precisely the queue-depletion events -- the bid being consumed or pulled,
the ask being consumed or pulled -- so a book whose bid collapsed registered an
imbalance of zero. The error was silent: OFI still moved with the book most of the
time, because the price-unchanged branch (which was correct) is the most frequent one.

**"OFI" in the literature is a sum, and this module's per-tick value is not it.**
``e_n`` is a single event's contribution; ``OFI_k`` is the sum of ``e_n`` over an
interval, and it is ``OFI_k`` -- not ``e_n`` -- that Cont et al. regress against price
changes. Set ``ofi_window_ticks`` above 1 to aggregate. The default of 1 leaves the
signal as the raw per-event contribution, which is a legitimate object of study but is
not the published variable.

**The weighted mid-price is not Stoikov's micro-price, and its deviation from the mid
is not independent of the depth imbalance.** What this module computes is the weighted
mid ``P_w = (q_bid * P_ask + q_ask * P_bid) / (q_bid + q_ask)``. Stoikov (2018) defines
the *micro-price* as a martingale limit of expected future mid-prices and shows the
weighted mid is the noisier, non-martingale approximation to it. Separately, and
exactly::

    P_w - mid == (VOI / 2) * spread

so ``micro_price_dev`` is a deterministic rescaling of ``voi`` by the spread. On a
constant-spread instrument -- most large-tick names sit at one tick nearly always --
``ic_micro_price_dev_return`` and ``ic_voi_forward_return`` are *arithmetically the same
number*, and reading them as two corroborating signals double-counts one signal.

**Overlapping forward returns inflate significance.** A k-tick forward return sampled at
every tick shares k-1 ticks with its neighbour, so N observations carry roughly N/k
independent ones. Grinold's IR = IC * sqrt(breadth) counts *independent* decisions; the
naive t-statistic over N counts them all. This module reports the t-statistic on the
non-overlapping effective sample ``floor(N/k)`` and refuses to certify a signal when
that sample is too small to mean anything.

Every threshold in this module is an engineering choice by this skill. No regulator,
exchange or standards body publishes an IC floor, a hit-ratio floor, or a sample-size
gate for microstructure signal research -- see ``references/standards.md``.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

# --- Audit statuses --------------------------------------------------------
STATUS_PREDICTIVE = "PREDICTIVE_ALPHA_FOUND"
STATUS_WEAK = "WEAK_SIGNAL"
STATUS_INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES"

# --- Findings recorded on the report --------------------------------------
FINDING_INSUFFICIENT_EFFECTIVE_SAMPLE = "INSUFFICIENT_EFFECTIVE_SAMPLE"
FINDING_IC_BELOW_FLOOR = "IC_BELOW_FLOOR"
FINDING_IC_SIGN_INVERTED = "IC_SIGN_INVERTED"
FINDING_HIT_RATIO_BELOW_FLOOR = "HIT_RATIO_BELOW_FLOOR"
FINDING_NO_DIRECTIONAL_PREDICTIONS = "NO_DIRECTIONAL_PREDICTIONS"
FINDING_ZERO_VARIANCE_SIGNAL = "ZERO_VARIANCE_SIGNAL"
FINDING_DEGENERATE_DEPTH = "DEGENERATE_DEPTH_TICKS"
FINDING_CONSTANT_SPREAD_COLLINEARITY = "CONSTANT_SPREAD_COLLINEARITY"

# --- Engineering thresholds (this skill's rules, not published standards) --
# Signed, not absolute: the Cont/Kukanov/Stoikov model predicts a *positive* coefficient
# on OFI, so a large negative IC is evidence of a sign or alignment error, not of alpha
# to be traded inverted. It is reported as its own finding rather than approved.
MIN_IC_FOR_ALPHA = 0.05
MIN_HIT_RATIO_PCT = 53.0

# An approval requires this many *non-overlapping* observations. Below it the IC point
# estimate is dominated by sampling noise: with 9 independent observations the two-sided
# confidence interval around an IC of 0.05 comfortably spans zero in both directions.
MIN_EFFECTIVE_OBSERVATIONS = 30

# A price or quantity beyond these magnitudes is a unit error, not a market. Bounding
# them also keeps the sums inside ``_pearson_correlation`` far from float overflow.
MAX_PLAUSIBLE_PRICE = 1e12
MAX_PLAUSIBLE_QTY = 1e15


class MicrostructureInputError(ValueError):
    """Raised when a tick series cannot support a meaningful microstructure audit.

    Subclasses ``ValueError`` so callers written against the previous bare
    ``raise ValueError`` on a short series keep working unchanged.
    """


class MicrostructureConfigError(ValueError):
    """Raised when engine configuration cannot produce a meaningful verdict."""


@dataclass
class OrderBookTick:
    """One top-of-book observation. ``bid_price``/``ask_price`` are absolute prices."""

    timestamp_ns: int
    symbol: str
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float


@dataclass
class MicrostructureFeatures:
    """Per-tick microstructure features, at full precision.

    Values are deliberately **not** rounded. A microstructure signal lives at the scale
    of a tick divided by the queue size; rounding a mid-price to four decimals discards
    the entire signal on a five-decimal FX cross, and rounding OFI to two discards it
    entirely on an instrument quoted in fractional units. Round at the point of display,
    never in the data structure.
    """

    timestamp_ns: int
    symbol: str
    mid_price: float
    micro_price: float
    micro_price_dev: float              # weighted mid - mid == (voi / 2) * spread
    voi: float                          # depth volume imbalance, [-1.0, +1.0]
    ofi: float                          # Cont-Kukanov-Stoikov e_n for this tick
    spread: float = 0.0                 # ask - bid
    ofi_window: float = 0.0             # rolling sum of e_n over ofi_window_ticks
    is_event_observed: bool = True      # False at index 0: e_0 has no predecessor
    is_window_complete: bool = True     # False until ofi_window_ticks events accumulate


@dataclass
class MicrostructureSignalReport:
    """Result of a forward-return efficacy audit over one tick series."""

    symbol: str
    total_ticks_analyzed: int
    ic_ofi_forward_return: float        # Pearson IC, aggregated OFI vs forward return
    ic_micro_price_dev_return: float    # Pearson IC, weighted-mid deviation vs return
    hit_ratio_pct: float                # % correct direction, over directional calls only
    status: str
    audit_notes: str
    ic_voi_forward_return: float = 0.0
    ic_ofi_t_stat: float = 0.0          # on the non-overlapping effective sample
    observations: int = 0               # (signal, forward return) pairs formed
    effective_observations: int = 0     # observations // forward_horizon_ticks
    directional_predictions: int = 0    # hit-ratio denominator
    flat_or_neutral_ticks: int = 0      # excluded: zero signal or zero forward return
    degenerate_depth_ticks: int = 0     # ticks with zero total top-of-book depth
    forward_horizon_ticks: int = 0
    ofi_window_ticks: int = 1
    findings: List[str] = field(default_factory=list)


class OrderBookMicrostructureSignalResearchEngine:
    """Computes top-of-book microstructure signals and audits their forward-return IC.

    ``forward_horizon_ticks`` is the number of ticks ahead the mid-price return is
    measured over. ``ofi_window_ticks`` is how many consecutive event contributions
    ``e_n`` are summed into the signal actually tested: 1 leaves the raw per-event
    contribution, higher values reproduce the interval-summed ``OFI_k`` of
    Cont/Kukanov/Stoikov (2014).
    """

    def __init__(self, forward_horizon_ticks: int = 5, ofi_window_ticks: int = 1) -> None:
        if not isinstance(forward_horizon_ticks, int) or isinstance(forward_horizon_ticks, bool):
            raise MicrostructureConfigError("forward_horizon_ticks must be an int.")
        if forward_horizon_ticks < 1:
            # 0 makes every "forward" return identically zero; a negative value makes it
            # a *backward* return paired with a contemporaneous signal, which manufactures
            # correlation out of look-ahead and reports it as predictive power.
            raise MicrostructureConfigError(
                f"forward_horizon_ticks must be >= 1, got {forward_horizon_ticks}."
            )
        if not isinstance(ofi_window_ticks, int) or isinstance(ofi_window_ticks, bool):
            raise MicrostructureConfigError("ofi_window_ticks must be an int.")
        if ofi_window_ticks < 1:
            raise MicrostructureConfigError(
                f"ofi_window_ticks must be >= 1, got {ofi_window_ticks}."
            )
        self.forward_horizon_ticks = forward_horizon_ticks
        self.ofi_window_ticks = ofi_window_ticks

    # -- validation --------------------------------------------------------

    def _validate(self, ticks: Sequence[OrderBookTick]) -> None:
        """Reject tick series that would produce a confidently wrong report.

        Each rejected class is one that does not raise on its own but silently corrupts
        the verdict, so repairing rather than rejecting would hide the corruption.
        """
        symbol = ticks[0].symbol
        prev_ts: Optional[int] = None
        for i, t in enumerate(ticks):
            if t.symbol != symbol:
                # The report is labelled with ticks[0].symbol. Interleaving two books
                # produces price "deltas" between unrelated instruments.
                raise MicrostructureInputError(
                    f"tick {i}: symbol {t.symbol!r} differs from series symbol {symbol!r}; "
                    "audit one instrument at a time."
                )
            for name, value in (
                ("bid_price", t.bid_price), ("ask_price", t.ask_price),
                ("bid_qty", t.bid_qty), ("ask_qty", t.ask_qty),
            ):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise MicrostructureInputError(f"tick {i}: {name} must be numeric.")
                if not math.isfinite(float(value)):
                    # NaN compares False against every threshold, so a corrupted series
                    # propagates a NaN IC and lands on WEAK_SIGNAL as if it were measured.
                    raise MicrostructureInputError(f"tick {i}: {name} is not finite ({value}).")
            if t.bid_qty < 0 or t.ask_qty < 0:
                raise MicrostructureInputError(f"tick {i}: negative queue size.")
            if t.bid_qty > MAX_PLAUSIBLE_QTY or t.ask_qty > MAX_PLAUSIBLE_QTY:
                raise MicrostructureInputError(f"tick {i}: queue size beyond plausible range.")
            if abs(t.bid_price) > MAX_PLAUSIBLE_PRICE or abs(t.ask_price) > MAX_PLAUSIBLE_PRICE:
                raise MicrostructureInputError(f"tick {i}: price beyond plausible range.")
            if t.bid_price <= 0 or t.ask_price <= 0:
                # Simple returns are undefined through zero and sign-flip through a
                # negative denominator. Negatively-priced instruments do exist (CME WTI
                # settled below zero on 2020-04-20); they need a log or absolute-change
                # return convention, which is outside this module's scope.
                raise MicrostructureInputError(
                    f"tick {i}: non-positive price; simple mid-price returns are undefined."
                )
            if t.ask_price < t.bid_price:
                # A crossed top of book on a single venue means stale or mismatched
                # sides. It also flips the sign of micro_price_dev relative to voi,
                # so the signal silently inverts on exactly those ticks.
                raise MicrostructureInputError(
                    f"tick {i}: crossed book (bid {t.bid_price} > ask {t.ask_price}); "
                    "clean consolidated/NBBO feeds before auditing."
                )
            if isinstance(t.timestamp_ns, bool) or not isinstance(t.timestamp_ns, int):
                raise MicrostructureInputError(f"tick {i}: timestamp_ns must be an int.")
            if prev_ts is not None and t.timestamp_ns < prev_ts:
                # e_n is defined by comparing observation n against n-1. Out-of-order
                # ticks make those deltas describe events that never happened.
                raise MicrostructureInputError(
                    f"tick {i}: timestamp {t.timestamp_ns} precedes {prev_ts}; "
                    "sort the series before auditing."
                )
            prev_ts = t.timestamp_ns

    # -- feature extraction ------------------------------------------------

    def extract_features(
        self, ticks: List[OrderBookTick]
    ) -> List[MicrostructureFeatures]:
        """Extract per-tick OFI, VOI, weighted mid-price and its deviation from the mid.

        Returns one feature row per input tick. Row 0 carries ``is_event_observed=False``
        and ``ofi=0.0``: ``e_0`` has no predecessor to difference against, so its OFI is
        undefined rather than zero, and treating that placeholder as an observation puts
        a fabricated data point into the research sample.
        """
        features: List[MicrostructureFeatures] = []
        if not ticks:
            return features
        self._validate(ticks)

        prev_bid_p = ticks[0].bid_price
        prev_bid_q = ticks[0].bid_qty
        prev_ask_p = ticks[0].ask_price
        prev_ask_q = ticks[0].ask_qty

        window: List[float] = []
        running = 0.0

        for idx, tick in enumerate(ticks):
            total_depth = tick.bid_qty + tick.ask_qty
            mid = (tick.bid_price + tick.ask_price) / 2.0
            spread = tick.ask_price - tick.bid_price
            if total_depth <= 0.0:
                # Empty top of book: no imbalance is defined. Fall back to the mid and
                # record the tick, rather than reporting a confident zero imbalance.
                micro = mid
                voi = 0.0
            else:
                micro = (tick.bid_qty * tick.ask_price + tick.ask_qty * tick.bid_price) / total_depth
                voi = (tick.bid_qty - tick.ask_qty) / total_depth

            # Cont, Kukanov & Stoikov (2014), section 2.1. Both queue-depletion branches
            # -- bid price down, ask price up -- carry the *previous* queue size, which
            # is the size that was removed by a market order or a cancellation.
            if idx == 0:
                delta_b = 0.0
                delta_a = 0.0
            else:
                if tick.bid_price > prev_bid_p:
                    delta_b = tick.bid_qty                    # price-improving limit buy
                elif tick.bid_price == prev_bid_p:
                    delta_b = tick.bid_qty - prev_bid_q       # add / market sell / cancel
                else:
                    delta_b = -prev_bid_q                     # bid queue removed entirely

                if tick.ask_price < prev_ask_p:
                    delta_a = -tick.ask_qty                   # price-improving limit sell
                elif tick.ask_price == prev_ask_p:
                    delta_a = prev_ask_q - tick.ask_qty       # add / market buy / cancel
                else:
                    delta_a = prev_ask_q                      # ask queue removed entirely

            ofi = delta_b + delta_a

            # Rolling sum of the last ofi_window_ticks *observed* events (index 0 is not
            # an event, so it never enters the window).
            if idx > 0:
                window.append(ofi)
                running += ofi
                if len(window) > self.ofi_window_ticks:
                    running -= window.pop(0)
            window_complete = len(window) == self.ofi_window_ticks

            features.append(MicrostructureFeatures(
                timestamp_ns=tick.timestamp_ns,
                symbol=tick.symbol,
                mid_price=mid,
                micro_price=micro,
                micro_price_dev=micro - mid,
                voi=voi,
                ofi=ofi,
                spread=spread,
                ofi_window=running if window_complete else 0.0,
                is_event_observed=idx > 0,
                is_window_complete=window_complete,
            ))

            prev_bid_p = tick.bid_price
            prev_bid_q = tick.bid_qty
            prev_ask_p = tick.ask_price
            prev_ask_q = tick.ask_qty

        return features

    # -- efficacy audit ----------------------------------------------------

    def evaluate_signal_efficacy(
        self, ticks: List[OrderBookTick]
    ) -> MicrostructureSignalReport:
        """Measure forward-return IC, t-statistic and hit ratio for the OFI signal.

        Forward returns are simple mid-to-mid returns over ``forward_horizon_ticks``,
        computed at full precision on both endpoints. They overlap by construction, so
        the reported t-statistic uses the non-overlapping effective sample size and an
        approval additionally requires that sample to reach
        ``MIN_EFFECTIVE_OBSERVATIONS``.
        """
        k = self.forward_horizon_ticks
        if len(ticks) <= k + 5:
            raise MicrostructureInputError(
                f"Need at least {k + 6} ticks for efficacy research, got {len(ticks)}."
            )

        features = self.extract_features(ticks)
        symbol = ticks[0].symbol
        n_ticks = len(ticks)

        # Index 0 has no observed event, and the first ofi_window_ticks events are needed
        # before the rolling window is full. A partial window is a different random
        # variable from a full one and must not be mixed into the same sample.
        start = max(1, self.ofi_window_ticks)
        end = n_ticks - 1 - k               # inclusive

        ofi_vals: List[float] = []
        dev_vals: List[float] = []
        voi_vals: List[float] = []
        fwd_returns: List[float] = []
        spreads: List[float] = []
        hits = 0
        directional = 0
        flat = 0
        degenerate = sum(1 for t in ticks if (t.bid_qty + t.ask_qty) <= 0.0)

        for i in range(start, end + 1):
            f = features[i]
            fwd_tick = ticks[i + k]
            cur_mid = f.mid_price
            fwd_mid = (fwd_tick.bid_price + fwd_tick.ask_price) / 2.0
            ret = (fwd_mid - cur_mid) / cur_mid   # cur_mid > 0 guaranteed by _validate

            signal = f.ofi_window
            ofi_vals.append(signal)
            dev_vals.append(f.micro_price_dev)
            voi_vals.append(f.voi)
            fwd_returns.append(ret)
            spreads.append(f.spread)

            # A zero signal is not a directional call, and a zero forward return is not a
            # directional outcome. Counting either as a "hit" -- as a prior revision did
            # for the zero/zero pair -- inflates the ratio with ticks where nothing was
            # predicted and nothing happened.
            if signal != 0.0 and ret != 0.0:
                directional += 1
                if (signal > 0.0) == (ret > 0.0):
                    hits += 1
            else:
                flat += 1

        observations = len(ofi_vals)
        if observations < 2:
            raise MicrostructureInputError(
                f"Only {observations} usable observation(s) after excluding the undefined "
                f"first event, the {self.ofi_window_ticks}-tick warm-up and the {k}-tick "
                "forward horizon; supply a longer series."
            )

        effective_observations = max(1, observations // k)

        ic_ofi = self._pearson_correlation(ofi_vals, fwd_returns)
        ic_dev = self._pearson_correlation(dev_vals, fwd_returns)
        ic_voi = self._pearson_correlation(voi_vals, fwd_returns)
        t_stat = self._ic_t_statistic(ic_ofi, effective_observations)

        hit_ratio_pct = (hits / float(directional)) * 100.0 if directional else 0.0

        findings: List[str] = []
        if effective_observations < MIN_EFFECTIVE_OBSERVATIONS:
            findings.append(FINDING_INSUFFICIENT_EFFECTIVE_SAMPLE)
        if ic_ofi < MIN_IC_FOR_ALPHA:
            findings.append(FINDING_IC_BELOW_FLOOR)
        if ic_ofi <= -MIN_IC_FOR_ALPHA:
            # CKS predicts a positive coefficient. A materially negative IC points at a
            # sign convention, side-mapping or timestamp alignment error before it points
            # at a contrarian edge.
            findings.append(FINDING_IC_SIGN_INVERTED)
        if directional == 0:
            findings.append(FINDING_NO_DIRECTIONAL_PREDICTIONS)
        elif hit_ratio_pct < MIN_HIT_RATIO_PCT:
            findings.append(FINDING_HIT_RATIO_BELOW_FLOOR)
        if self._is_constant(ofi_vals) or self._is_constant(fwd_returns):
            # Pearson is undefined here and reported as 0.0; say so rather than let a
            # degenerate series read as a measured absence of correlation.
            findings.append(FINDING_ZERO_VARIANCE_SIGNAL)
        if degenerate:
            findings.append(FINDING_DEGENERATE_DEPTH)
        if self._is_effectively_constant(spreads):
            # micro_price_dev == (voi / 2) * spread exactly, so a constant spread makes
            # ic_micro_price_dev_return and ic_voi_forward_return the same number.
            findings.append(FINDING_CONSTANT_SPREAD_COLLINEARITY)

        approved = (
            ic_ofi >= MIN_IC_FOR_ALPHA
            and directional > 0
            and hit_ratio_pct >= MIN_HIT_RATIO_PCT
            and effective_observations >= MIN_EFFECTIVE_OBSERVATIONS
        )
        if approved:
            status = STATUS_PREDICTIVE
        elif effective_observations < MIN_EFFECTIVE_OBSERVATIONS:
            # Not enough independent observations to say either way. This is not the same
            # verdict as "measured and found weak", and conflating them lets a 9-sample
            # research run read as a rejection it did not earn.
            status = STATUS_INSUFFICIENT_SAMPLES
        else:
            status = STATUS_WEAK

        notes = (
            f"MICROSTRUCTURE SIGNAL AUDIT [{symbol} - {status}]: Ticks = {n_ticks}, "
            f"Observations = {observations} (effective non-overlapping = "
            f"{effective_observations}), Forward Horizon = {k} ticks, OFI Window = "
            f"{self.ofi_window_ticks} ticks. IC(OFI, Ret) = {ic_ofi:+.4f} "
            f"(t = {t_stat:+.2f} on the effective sample), IC(MicroDev, Ret) = "
            f"{ic_dev:+.4f}, IC(VOI, Ret) = {ic_voi:+.4f}, Hit Ratio = "
            f"{hit_ratio_pct:.2f}% over {directional} directional call(s)."
        )
        if findings:
            notes += f" Findings: {', '.join(findings)}."

        if status == STATUS_PREDICTIVE:
            logger.info(notes)
        else:
            logger.warning(notes)

        return MicrostructureSignalReport(
            symbol=symbol,
            total_ticks_analyzed=n_ticks,
            ic_ofi_forward_return=round(ic_ofi, 4),
            ic_micro_price_dev_return=round(ic_dev, 4),
            ic_voi_forward_return=round(ic_voi, 4),
            ic_ofi_t_stat=round(t_stat, 4),
            hit_ratio_pct=round(hit_ratio_pct, 2),
            observations=observations,
            effective_observations=effective_observations,
            directional_predictions=directional,
            flat_or_neutral_ticks=flat,
            degenerate_depth_ticks=degenerate,
            forward_horizon_ticks=k,
            ofi_window_ticks=self.ofi_window_ticks,
            status=status,
            findings=findings,
            audit_notes=notes,
        )

    # -- statistics --------------------------------------------------------

    @staticmethod
    def _is_constant(values: Sequence[float]) -> bool:
        """Exact constancy -- the same criterion Pearson's zero-denominator guard uses."""
        return len(values) < 2 or all(v == values[0] for v in values)

    @staticmethod
    def _is_effectively_constant(values: Sequence[float]) -> bool:
        """Constancy up to float representation error.

        A one-tick spread computed as ``ask - bid`` is not bit-identical across price
        levels: at 100.00/100.01 it is 0.009999999999990905 and at 100.02/100.03 it is
        0.010000000000005116. An exact comparison would miss the collinearity this flag
        exists to warn about on precisely the instruments where it always applies.
        """
        if len(values) < 2:
            return True
        first = values[0]
        return all(math.isclose(v, first, rel_tol=1e-9, abs_tol=0.0) for v in values)

    @staticmethod
    def _ic_t_statistic(ic: float, effective_n: int) -> float:
        """Two-sided t-statistic for a Pearson correlation on ``effective_n`` samples.

        ``effective_n`` must be the *non-overlapping* count. Using the raw observation
        count instead inflates the statistic by roughly sqrt(k) at horizon k, which is
        how an overlapping-return study reports significance it does not have.

        This is a conservative approximation, not a HAC estimator: it discards the
        information in the overlapping samples rather than modelling their
        autocorrelation the way Newey-West or Hansen-Hodrick standard errors would.
        """
        if effective_n < 3:
            return 0.0
        denom = 1.0 - ic * ic
        if denom <= 0.0:
            return 0.0
        return ic * math.sqrt((effective_n - 2) / denom)

    @staticmethod
    def _pearson_correlation(x: Sequence[float], y: Sequence[float]) -> float:
        """Two-pass Pearson correlation. Returns 0.0 when either series has no variance.

        A zero here means *undefined*, not *uncorrelated*; callers see that case flagged
        as ``ZERO_VARIANCE_SIGNAL`` on the report.
        """
        n = len(x)
        if n != len(y):
            raise MicrostructureInputError("correlation inputs must be equal length.")
        if n < 2:
            return 0.0
        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
        var_x = sum((x[i] - mean_x) ** 2 for i in range(n))
        var_y = sum((y[i] - mean_y) ** 2 for i in range(n))

        denom = math.sqrt(var_x * var_y)
        if denom <= 0.0:
            return 0.0
        # Guard the float boundary: exactly-collinear series can produce |rho| a hair
        # above 1, which would make the t-statistic's (1 - rho^2) negative.
        return max(-1.0, min(1.0, cov / denom))
