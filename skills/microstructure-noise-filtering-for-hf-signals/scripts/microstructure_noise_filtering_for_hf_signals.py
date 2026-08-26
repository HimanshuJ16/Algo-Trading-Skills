"""
microstructure-noise-filtering-for-hf-signals: quote-midpoint noise reduction for
high-frequency signal construction.

Three estimators over a top-of-book quote stream:

1. ``KALMAN`` -- the *local level model* (random walk plus noise) of Durbin & Koopman:

       y_t   = mu_t + eps_t,     eps_t ~ N(0, R)     (observation: the quote midpoint)
       mu_t  = mu_{t-1} + eta_t, eta_t ~ N(0, Q)     (state: the latent efficient price)

   with the scalar filtering recursion ``K_t = P_t^- / (P_t^- + R)``. Q is the latent
   price variance per tick and R the observation (noise) variance. In this model the
   *signal-to-noise ratio* has a canonical definition, ``q = Q / R``, and the gain
   converges to the closed form derived in ``steady_state_kalman_gain``.

2. ``WEIGHTED_MID`` (alias ``MICRO_PRICE``, kept for backwards compatibility) -- the
   imbalance-weighted mid ``W = w * P_ask + (1 - w) * P_bid`` with
   ``w = V_bid / (V_bid + V_ask)``.

   **This is the weighted mid-price, not Stoikov's micro-price.** Stoikov (2018)
   defines the micro-price as the martingale limit of expected future mid-prices under
   a Markov chain on (imbalance, spread), and shows it is a *different* and generally
   *less noisy* estimator than the weighted mid. The weighted mid is a fair-value/bias
   correction, **not a variance reducer**: because it swings with queue imbalance, its
   dispersion normally *exceeds* the midpoint's. Do not judge it by
   ``noise_reduction_pct``.

3. ``EMA`` -- exponential smoothing ``P~_t = a*P_t + (1-a)*P~_{t-1}``, ``a = 2/(N+1)``.

Scope and limitations (documented, deliberate):

- **This filters the quote midpoint; it does not remove bid-ask bounce.** Bid-ask
  bounce is the negative first-order autocovariance Roll (1984) identifies in
  *transaction* prices oscillating between the bid and the ask, and it is absent from
  midpoint data by construction. What the midpoint does carry is quote flicker, price
  discreteness (tick size) and transient depth imbalance -- that is what these filters
  address. ``RawTick.last_price`` is carried for downstream use and is deliberately
  **not** consumed by any filter here; a trade-price series would need bounce-aware
  handling this module does not implement.
- **Uniform time steps are assumed.** Q is applied once per tick, so the Kalman filter
  treats every inter-tick interval as equal. Real tick streams are irregularly spaced,
  so under bursty arrivals the effective smoothing varies with message rate. Scaling Q
  by the elapsed interval is out of scope here.
- **Causal, one-sided filters.** Every estimator is causal and therefore lags. Lowering
  R (or raising Q) reduces lag at the cost of admitting more noise; no setting removes
  both.
- **No noise-variance estimation.** R is supplied by the caller, not estimated from the
  data. Zhang, Mykland & Ait-Sahalia (2005) is the standard reference for estimating
  noise variance from high-frequency returns.

References
----------
- Durbin, J. & Koopman, S.J., *Time Series Analysis by State Space Methods*
  (local level model / random walk plus noise).
- Roll, R. (1984), "A Simple Implicit Measure of the Effective Bid-Ask Spread in an
  Efficient Market", *Journal of Finance* 39(4), 1127-1139.
- Stoikov, S. (2018), "The micro-price: a high-frequency estimator of future prices",
  *Quantitative Finance* 18(12), 1959-1966.
- Zhang, L., Mykland, P.A. & Ait-Sahalia, Y. (2005), "A Tale of Two Time Scales:
  Determining Integrated Volatility With Noisy High-Frequency Data", *Journal of the
  American Statistical Association* 100(472), 1394-1411.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Sequence

logger = logging.getLogger(__name__)

#: Canonical filter identifiers. ``MICRO_PRICE`` is retained as a backwards-compatible
#: alias for ``WEIGHTED_MID`` -- the formula is, and always was, the weighted mid-price.
FILTER_KALMAN = "KALMAN"
FILTER_WEIGHTED_MID = "WEIGHTED_MID"
FILTER_MICRO_PRICE = "MICRO_PRICE"
FILTER_EMA = "EMA"

SUPPORTED_FILTERS = (
    FILTER_KALMAN,
    FILTER_WEIGHTED_MID,
    FILTER_MICRO_PRICE,
    FILTER_EMA,
)

#: Status when the filtered series has strictly lower dispersion than the raw midpoint.
STATUS_SUCCESS = "NOISE_FILTERING_SUCCESS"

#: Status when it does not. Expected and correct for ``WEIGHTED_MID``, which is a
#: fair-value estimator rather than a smoother; a mistuning signal for KALMAN / EMA.
STATUS_NO_REDUCTION = "NOISE_FILTERING_NO_REDUCTION"


def _require_finite(value: float, name: str) -> float:
    """
    Reject NaN/Inf at the boundary.

    A single NaN silently poisons every subsequent Kalman/EMA state, and because
    ``nan > 0`` is False the resulting report still shows a plausible-looking
    ``0.00%`` reduction rather than an error.
    """
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    return numeric


def steady_state_kalman_gain(process_noise_q: float, obs_noise_r: float) -> float:
    """
    Closed-form steady-state gain of the scalar local level model.

    At the fixed point the recursion ``P^- = P + Q``, ``P = (1 - K) P^-``,
    ``K = P^- / (P^- + R)`` reduces to ``R*K^2 + Q*K - Q = 0``, whose positive root is

        K* = 0.5 * (sqrt(q^2 + 4q) - q),    q = Q / R

    where ``q`` is the local level model's signal-to-noise ratio. K* is the tuning
    quantity that matters: the weight the filter places on each new observation once
    the transient from the initial error covariance has decayed.

    Raises:
        ValueError: if Q or R is non-finite, Q is negative, or R is not > 0.
    """
    _require_finite(process_noise_q, "kalman_process_noise_q")
    _require_finite(obs_noise_r, "kalman_obs_noise_r")
    if process_noise_q < 0.0:
        raise ValueError(f"kalman_process_noise_q must be >= 0, got {process_noise_q}.")
    if obs_noise_r <= 0.0:
        raise ValueError(f"kalman_obs_noise_r must be > 0, got {obs_noise_r}.")
    q = process_noise_q / obs_noise_r
    return 0.5 * (math.sqrt(q * q + 4.0 * q) - q)


def kalman_effective_span(process_noise_q: float, obs_noise_r: float) -> float:
    """
    EMA span whose smoothing matches the filter's steady state: ``N = 2/K* - 1``.

    In steady state the local level filter *is* an EMA with ``alpha = K*``, so this
    restates a (Q, R) pair in the units practitioners actually tune in -- "roughly an
    N-tick average". Returns ``inf`` when Q == 0, where the filter freezes on its
    initial estimate.
    """
    gain = steady_state_kalman_gain(process_noise_q, obs_noise_r)
    if gain <= 0.0:
        return math.inf
    return (2.0 / gain) - 1.0


@dataclass
class FilterConfig:
    filter_type: str = FILTER_KALMAN      # One of SUPPORTED_FILTERS
    kalman_process_noise_q: float = 1e-5  # Q: latent efficient-price variance per tick
    kalman_obs_noise_r: float = 1e-2      # R: observation (microstructure noise) variance
    ema_span_n: int = 10                  # Span N for EMA smoothing, alpha = 2/(N+1)
    symbol: str = "AAPL"
    price_precision: int = 4              # Decimal places for reported prices. Raise for
                                          # FX (5) and crypto (8); 4 truncates both.


@dataclass
class RawTick:
    timestamp_epoch: float
    bid_price: float
    ask_price: float
    last_price: float   # Carried for downstream use; NOT consumed by any filter here.
    bid_volume: float
    ask_volume: float


@dataclass
class FilteredTick:
    timestamp_epoch: float
    raw_mid_price: float
    filtered_price: float
    estimated_noise: float   # raw_mid - filtered. For KALMAN/EMA this is the filter
                             # residual; for WEIGHTED_MID it is the *imbalance
                             # adjustment*, which is signal, not noise.


@dataclass
class MicrostructureFilterReport:
    symbol: str
    filter_type: str
    total_ticks_processed: int
    raw_price_std_dev: float
    filtered_price_std_dev: float
    noise_reduction_pct: float          # (1 - sigma_filtered / sigma_raw) * 100.
                                        # A STANDARD DEVIATION reduction, not variance.
    signal_to_noise_ratio: float        # sigma_filtered / sigma_residual. An amplitude
                                        # dispersion ratio -- NOT a power SNR, and not
                                        # the local level model's q (see kalman_snr_q).
    filtered_ticks: List[FilteredTick]
    status: str                         # STATUS_SUCCESS | STATUS_NO_REDUCTION
    audit_notes: str
    noise_variance_reduction_pct: float = 0.0  # (1 - sigma_f^2 / sigma_r^2) * 100
    dispersion_reduced: bool = False
    kalman_snr_q: float = 0.0           # q = Q/R, the local level model's canonical SNR
    kalman_steady_state_gain: float = 0.0
    kalman_effective_span: float = 0.0


def _validate_config(config: FilterConfig) -> None:
    if config.filter_type not in SUPPORTED_FILTERS:
        raise ValueError(
            f"Unsupported filter type {config.filter_type!r}. "
            f"Expected one of {', '.join(SUPPORTED_FILTERS)}."
        )
    if not isinstance(config.price_precision, int) or config.price_precision < 0:
        raise ValueError(
            f"price_precision must be a non-negative int, got {config.price_precision!r}."
        )
    if config.filter_type == FILTER_KALMAN:
        # Enforces Q >= 0 and R > 0. A negative R inverts the gain (R = -1 against an
        # initial P of 1.0 yields a gain near 1e5) and the filter diverges instead of
        # smoothing; Q = R = 0 divides by zero.
        steady_state_kalman_gain(
            config.kalman_process_noise_q, config.kalman_obs_noise_r
        )
    if config.filter_type == FILTER_EMA:
        # N <= -1 divides by zero; N = 0 gives alpha = 2.0, an amplifying oscillator
        # that diverges from the data rather than smoothing it.
        if not isinstance(config.ema_span_n, int) or config.ema_span_n < 1:
            raise ValueError(
                f"ema_span_n must be an int >= 1, got {config.ema_span_n!r}."
            )


def _validate_ticks(ticks: Sequence[RawTick]) -> None:
    """
    Validate every field the filters actually consume.

    ``last_price`` is deliberately **not** validated: no filter here reads it, and
    quote-only feeds commonly leave it unset or zero between trades, so rejecting on it
    would drop otherwise-usable quotes. Callers that use it downstream must validate it
    themselves.
    """
    previous_ts = -math.inf
    for i, t in enumerate(ticks):
        ts = _require_finite(t.timestamp_epoch, f"ticks[{i}].timestamp_epoch")
        bid = _require_finite(t.bid_price, f"ticks[{i}].bid_price")
        ask = _require_finite(t.ask_price, f"ticks[{i}].ask_price")
        bid_vol = _require_finite(t.bid_volume, f"ticks[{i}].bid_volume")
        ask_vol = _require_finite(t.ask_volume, f"ticks[{i}].ask_volume")

        if bid <= 0.0 or ask <= 0.0:
            raise ValueError(
                f"ticks[{i}]: prices must be positive, got bid={bid}, ask={ask}."
            )
        if bid > ask:
            raise ValueError(
                f"ticks[{i}]: crossed book, bid={bid} > ask={ask}. A crossed quote's "
                "midpoint has no economic meaning and the weighted mid inverts its "
                "sign; repair or drop the tick upstream."
            )
        if bid_vol < 0.0 or ask_vol < 0.0:
            # A negative volume can pass a naive `total > 0` guard and drive the
            # weighted mid *outside* [bid, ask]: V_bid=10, V_ask=-5 on a
            # 100.00/100.20 book yields 100.40.
            raise ValueError(
                f"ticks[{i}]: volumes must be non-negative, got "
                f"bid_volume={bid_vol}, ask_volume={ask_vol}."
            )
        if ts < previous_ts:
            raise ValueError(
                f"ticks[{i}]: timestamp {ts} precedes the previous tick's {previous_ts}. "
                "These filters are sequential and order-dependent; sort the stream "
                "before filtering."
            )
        previous_ts = ts


def _std_dev(data: Sequence[float]) -> float:
    """Sample standard deviation with the (n-1) denominator. Returns 0.0 for n <= 1."""
    n = len(data)
    if n <= 1:
        return 0.0
    mean = sum(data) / n
    variance = sum((x - mean) ** 2 for x in data) / (n - 1)
    return math.sqrt(variance)


class MicrostructureNoiseFilterEngine:
    """
    Quote-midpoint noise filtering for high-frequency signal construction: local level
    Kalman filtering, imbalance-weighted mid-price, and exponential smoothing.

    Stateless -- each :meth:`filter_tick_stream` call is independent and deterministic
    given its arguments.
    """

    def filter_tick_stream(
        self,
        config: FilterConfig,
        ticks: Sequence[RawTick],
    ) -> MicrostructureFilterReport:
        """
        Run the configured estimator over a top-of-book quote stream and report the
        dispersion change it produced.

        Args:
            config: Filter selection and parameters.
            ticks: Quote ticks in non-decreasing timestamp order.

        Returns:
            A :class:`MicrostructureFilterReport`. ``status`` is
            ``STATUS_NO_REDUCTION`` when the filtered series is not less dispersed than
            the raw midpoint -- the expected outcome for ``WEIGHTED_MID``, and a sign
            that Q/R or the EMA span is mistuned for ``KALMAN`` / ``EMA``.

        Raises:
            ValueError: on an empty stream, an unsupported filter type, invalid filter
                parameters, or any non-finite, non-positive-price, crossed,
                negative-volume, or out-of-order tick.
        """
        if not ticks:
            raise ValueError("Raw tick stream cannot be empty.")

        _validate_config(config)
        _validate_ticks(ticks)

        precision = config.price_precision
        is_weighted_mid = config.filter_type in (FILTER_WEIGHTED_MID, FILTER_MICRO_PRICE)

        filtered_ticks: List[FilteredTick] = []
        raw_mids: List[float] = []
        filtered_prices: List[float] = []

        # Kalman initialisation. p_est = 1.0 is a deliberately diffuse prior: the first
        # gain is ~1.0, so the filter adopts the first observation and then tightens
        # towards steady_state_kalman_gain(Q, R).
        x_est = (ticks[0].bid_price + ticks[0].ask_price) / 2.0
        p_est = 1.0

        ema_alpha = 2.0 / (config.ema_span_n + 1.0)
        ema_val = x_est

        for t in ticks:
            raw_mid = round((t.bid_price + t.ask_price) / 2.0, precision)
            raw_mids.append(raw_mid)

            if config.filter_type == FILTER_KALMAN:
                # Time update (predict), then measurement update (correct).
                p_predict = p_est + config.kalman_process_noise_q
                kalman_gain = p_predict / (p_predict + config.kalman_obs_noise_r)
                x_est = x_est + kalman_gain * (raw_mid - x_est)
                p_est = (1.0 - kalman_gain) * p_predict
                filt_price = round(x_est, precision)

            elif is_weighted_mid:
                # W = w*P_ask + (1-w)*P_bid with w = V_bid/(V_bid+V_ask), rearranged as
                # (V_ask*P_bid + V_bid*P_ask)/(V_bid+V_ask). Volumes are validated
                # non-negative, so W is guaranteed to lie within [bid, ask].
                total_vol = t.bid_volume + t.ask_volume
                if total_vol > 0.0:
                    weighted = (
                        t.ask_volume * t.bid_price + t.bid_volume * t.ask_price
                    ) / total_vol
                    filt_price = round(weighted, precision)
                else:
                    # Both queues empty: imbalance is undefined, fall back to the mid.
                    filt_price = raw_mid

            else:  # FILTER_EMA
                ema_val = ema_alpha * raw_mid + (1.0 - ema_alpha) * ema_val
                filt_price = round(ema_val, precision)

            filtered_prices.append(filt_price)

            filtered_ticks.append(
                FilteredTick(
                    timestamp_epoch=t.timestamp_epoch,
                    raw_mid_price=raw_mid,
                    filtered_price=filt_price,
                    estimated_noise=round(raw_mid - filt_price, precision),
                )
            )

        raw_std = _std_dev(raw_mids)
        filt_std = _std_dev(filtered_prices)
        residual_std = _std_dev([f.estimated_noise for f in filtered_ticks])

        if raw_std > 0.0:
            ratio = filt_std / raw_std
            noise_reduction_pct = round((1.0 - ratio) * 100.0, 2)
            variance_reduction_pct = round((1.0 - ratio * ratio) * 100.0, 2)
        else:
            # A constant midpoint has no dispersion to reduce.
            noise_reduction_pct = 0.0
            variance_reduction_pct = 0.0

        snr = round(filt_std / residual_std, 2) if residual_std > 0.0 else math.inf

        if config.filter_type == FILTER_KALMAN:
            q_ratio = config.kalman_process_noise_q / config.kalman_obs_noise_r
            ss_gain = steady_state_kalman_gain(
                config.kalman_process_noise_q, config.kalman_obs_noise_r
            )
            eff_span = kalman_effective_span(
                config.kalman_process_noise_q, config.kalman_obs_noise_r
            )
        else:
            q_ratio = 0.0
            ss_gain = 0.0
            eff_span = 0.0

        dispersion_reduced = filt_std < raw_std
        status = STATUS_SUCCESS if dispersion_reduced else STATUS_NO_REDUCTION

        notes = (
            f"MIDPOINT FILTER [{config.symbol} - {config.filter_type}]: "
            f"Processed {len(ticks):,} ticks. "
            f"Raw StdDev = {raw_std:.{precision}f}, "
            f"Filtered StdDev = {filt_std:.{precision}f}. "
            f"StdDev reduction = {noise_reduction_pct:+.2f}% "
            f"(variance {variance_reduction_pct:+.2f}%), "
            f"filtered/residual dispersion = {snr:.2f}x."
        )
        if is_weighted_mid:
            notes += (
                " NOTE: WEIGHTED_MID is a fair-value estimator, not a smoother -- its "
                "dispersion normally exceeds the midpoint's. Judge it by predictive "
                "power, not by dispersion reduction."
            )
        logger.info(notes)

        return MicrostructureFilterReport(
            symbol=config.symbol,
            filter_type=config.filter_type,
            total_ticks_processed=len(ticks),
            raw_price_std_dev=round(raw_std, precision),
            filtered_price_std_dev=round(filt_std, precision),
            noise_reduction_pct=noise_reduction_pct,
            signal_to_noise_ratio=snr,
            filtered_ticks=filtered_ticks,
            status=status,
            audit_notes=notes,
            noise_variance_reduction_pct=variance_reduction_pct,
            dispersion_reduced=dispersion_reduced,
            kalman_snr_q=q_ratio,
            kalman_steady_state_gain=ss_gain,
            kalman_effective_span=eff_span,
        )
