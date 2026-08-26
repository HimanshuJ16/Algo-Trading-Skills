"""
implementation-shortfall-minimization: Almgren-Chriss (2000) optimal execution
trajectories and the four-component Perold (1988) Implementation Shortfall
decomposition.

Two independent capabilities live here:

1. **Pre-trade scheduling** -- ``calculate_almgren_chriss_trajectory`` returns the
   share count to work in each of ``N`` intervals, following the closed-form
   Almgren-Chriss solution.
2. **Post-trade measurement** -- ``evaluate_implementation_shortfall`` decomposes a
   completed (or partially completed) parent order against the decision price.

Almgren-Chriss trajectory
-------------------------
Almgren, R. and Chriss, N. (2000), "Optimal Execution of Portfolio Transactions",
*Journal of Risk* 3(2), 5-39. The holdings trajectory (their Eq. 17) is

    x_j = X * sinh(kappa * (T - t_j)) / sinh(kappa * T),   j = 0..N

and the trade list is the drop in holdings across each interval,
``n_j = x_{j-1} - x_j`` (their Eq. 18 in closed form). ``kappa`` is *not* a free
parameter and is *not* ``sqrt(lambda)``: it is fixed by the difference equation
their Eq. (16) yields, whose characteristic root satisfies
``(2/tau^2)(cosh(kappa*tau) - 1) = kappa_tilde^2``, i.e.

    kappa         = arccosh(1 + kappa_tilde^2 * tau^2 / 2) / tau
    kappa_tilde^2 = lambda * sigma^2 / eta_tilde
    eta_tilde     = eta - gamma * tau / 2

with

    lambda  risk aversion (units 1/currency; 0 => risk-neutral => TWAP)
    sigma   volatility in price units per sqrt(time)
    eta     temporary market-impact coefficient (price per unit trading rate)
    gamma   permanent market-impact coefficient (price per unit trading rate)
    tau     interval length, T = N * tau

sigma and tau must use the *same* time unit: with ``tau = 1`` that unit is one
interval, so sigma is per-sqrt-interval; with ``tau = 300`` (five-minute bins
measured in seconds) sigma must be per-sqrt-second. Only the product
``kappa * tau`` shapes the schedule, so a mismatched pair silently rescales
urgency rather than failing.

Their Eq. (19), ``kappa ~ sqrt(lambda * sigma^2 / eta)``, is the ``tau -> 0``
*approximation*; this module uses the exact discrete root instead, so the schedule
is the optimum for the interval grid actually being traded rather than for its
continuous-time limit.

**The defaults are dimensionless placeholders, not a calibration.** With
``sigma = 1``, ``eta = 1``, ``gamma = 0``, ``tau = 1`` the schedule reduces to a
pure function of ``risk_aversion_lambda`` and describes no particular instrument.
A schedule intended for a real order must be given sigma and eta estimated for
that instrument, in consistent units; otherwise ``risk_aversion_lambda`` is only
an abstract urgency dial and the output must not be presented as an
impact-vs-risk optimum for the name being traded.

Because ``n_j > 0`` for every ``j`` when ``X > 0`` (ibid., Sec. 3), an
Almgren-Chriss sell program never buys and a buy program never sells. That
property is preserved here by rounding the *holdings* trajectory to whole shares
under a monotonicity constraint and differencing it, rather than rounding each
slice independently -- independent slice rounding can emit a negative slice, which
is a reversing trade the model never prescribes.

Implementation Shortfall
------------------------
Perold, A. F. (1988), "The Implementation Shortfall: Paper vs. Reality",
*Journal of Portfolio Management* 14(3), 4-9. IS is the return difference between
the paper portfolio (the whole order filled instantly at the decision price
``P_0``) and the implemented portfolio. For a buy of ``Q`` shares of which ``Q_f``
filled at quantity-weighted average ``P_e``, marked at horizon price ``P_end``:

    IS = Q*(P_end - P_0) - [ Q_f*(P_end - P_e) - fees ]
       = Q_f*(P_e - P_0) + (Q - Q_f)*(P_end - P_0) + fees
         ^^ execution cost   ^^ opportunity cost      ^^ explicit fees

Sells are the mirror image. **Cost sign convention: positive is money lost**, for
buys and sells alike, so a buy filled above ``P_0`` and a sell filled below it
both report a positive cost.

When ``arrival_price`` is supplied the executed-leg term is split exactly:

    execution cost = Q_f*(P_a - P_0)  +  Q_f*(P_e - P_a)
                     ^^ delay cost       ^^ market impact / trading cost

Delay + impact sum to the execution cost by construction, so the four-component
total is unchanged. Opportunity cost stays measured from ``P_0``; some vendors
instead measure it from the arrival price, which moves cost between the delay and
opportunity buckets. Compare bucket-by-bucket figures across systems only after
confirming both use the same boundary.

Basis points are quoted on the **intended notional** ``total_order_qty * P_0`` --
the decision-time value of the order the PM asked for, not the value of what
actually filled. On a partial fill the two differ, and the intended notional is
the denominator that keeps a badly underfilled order from looking cheap. The bps
figure is derived from the *unrounded* shortfall: rounding to cents first zeroes
the whole measurement on low-priced instruments (one share of a 0.0001-priced
asset moving 10% is a real 1,000 bps but rounds to $0.00 and thence to 0.00 bps).
Currency figures are still rounded to cents and still sum exactly to the reported
total.

Deliberate limitations
----------------------
- **"Market impact" is not causal.** Even with an arrival price, the impact term is
  whatever the price did between arrival and completion. It contains market drift
  and news alongside the order's own footprint, and no post-trade arithmetic can
  separate them -- the counterfactual price path had the order not traded is
  unobservable. Treat the term as attribution, not as a measured impact
  coefficient, and never feed it back as ``eta``.
- **No market/beta adjustment.** No component is decontaminated of index movement.
- **Single parent order, single currency.** No aggregation across orders, venues or
  days; all prices and fees must already be in one currency.
- **The horizon price is an input, not a market truth.** Opportunity cost is
  linear in ``final_market_price``; choosing a different horizon changes it. Fix
  the horizon convention (e.g. order-cancel time, or close) before comparing
  orders.
- **Executed-leg-only attribution** (delay vs impact, materiality, driver naming)
  is owned by ``execution-slippage-attribution-timing-vs-sizing``; this module
  reports the four-component total.
"""
import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Default number of trading intervals when the caller does not specify one.
DEFAULT_INTERVALS = 5

#: Basis points per unit (1 = 10,000 bps).
BPS_PER_UNIT = 10_000.0

STATUS_SUCCESS = "IS_EVALUATION_SUCCESS"
STATUS_NO_FILLS = "IS_EVALUATION_NO_FILLS"


@dataclass
class ExecutedTradeFill:
    fill_id: str                        # Unique per fill; duplicates are rejected, not summed
    quantity: int                       # Shares in this fill, > 0
    fill_price: float                   # Execution price, > 0 and finite
    explicit_fee_usd: float             # Commission/fees; may be negative for a maker rebate
    timestamp_nanos: int                # Informational; IS is order-independent


@dataclass
class ImplementationShortfallReport:
    symbol: str
    side: str                           # 'BUY' or 'SELL'
    total_order_qty: int                # Intended (parent) quantity -- the bps denominator
    executed_qty: int
    unfilled_qty: int
    decision_price_p0: float
    volume_weighted_executed_price: Optional[float]  # None when nothing executed
    final_market_price: float
    execution_cost_usd: float           # Q_f*(P_e - P_0), cost-signed. Delay + impact + drift.
    opportunity_cost_usd: float         # (Q - Q_f)*(P_end - P_0), cost-signed
    explicit_fees_usd: float
    total_implementation_shortfall_usd: float
    total_implementation_shortfall_bps: float
    status: str
    audit_notes: str
    fill_ratio: float = 0.0
    arrival_price: Optional[float] = None
    delay_cost_usd: Optional[float] = None          # Q_f*(P_a - P_0); None without arrival price
    market_impact_cost_usd: Optional[float] = None  # Q_f*(P_e - P_a); None without arrival price


def _round_cents(value: float) -> float:
    """
    Rounds a signed currency amount to cents, normalising ``-0.0`` to ``0.0`` so a
    zero-cost sell does not render as ``-$0.00`` in a report.
    """
    return round(value, 2) + 0.0


def _require_positive_finite(value: float, name: str) -> float:
    """Rejects non-numeric, non-finite and non-positive prices/parameters."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be numeric, got {type(value).__name__}.")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}.")
    if value <= 0.0:
        raise ValueError(f"{name} must be > 0, got {value}.")
    return value


def _require_positive_int(value: int, name: str) -> int:
    """Rejects bools, non-integers and non-positive share/interval counts."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}.")
    return value


def _sinh_ratio(numer_arg: float, denom_arg: float) -> float:
    """
    ``sinh(a) / sinh(b)`` for ``0 <= a <= b`` with ``b > 0``, without overflow.

    The direct form overflows for ``b`` beyond ~710 (``math.sinh(5000)`` raises
    ``OverflowError``) even though the ratio itself is bounded by 1. Rewriting as

        sinh(a)/sinh(b) = exp(a - b) * (1 - exp(-2a)) / (1 - exp(-2b))

    keeps every exponent non-positive, so the expression underflows smoothly to 0
    instead of raising. ``expm1`` preserves accuracy as ``a, b -> 0``.
    """
    if numer_arg <= 0.0:
        return 0.0
    return (
        math.exp(numer_arg - denom_arg)
        * (-math.expm1(-2.0 * numer_arg))
        / (-math.expm1(-2.0 * denom_arg))
    )


def almgren_chriss_kappa(
    risk_aversion_lambda: float,
    volatility_per_sqrt_time: float = 1.0,
    temporary_impact_eta: float = 1.0,
    permanent_impact_gamma: float = 0.0,
    interval_length: float = 1.0,
) -> float:
    """
    Exact discrete Almgren-Chriss decay rate ``kappa`` (2000, Eq. 16 and following).

        eta_tilde     = eta - gamma * tau / 2
        kappa_tilde^2 = lambda * sigma^2 / eta_tilde
        kappa         = arccosh(1 + kappa_tilde^2 * tau^2 / 2) / tau

    Returns 0.0 for ``lambda == 0`` (risk-neutral), which yields a linear TWAP
    trajectory. ``lambda < 0`` is rejected rather than silently floored to 0:
    negative risk aversion is not a slower schedule, it is an ill-posed problem,
    and a silent fallback would return a TWAP schedule for nonsense input.

    :raises ValueError: if ``eta_tilde <= 0`` (permanent impact over one interval
        reaching temporary impact leaves Eq. 16 without a real decay root), or if
        the parameters overflow the arccosh argument.
    """
    if isinstance(risk_aversion_lambda, bool) or not isinstance(risk_aversion_lambda, (int, float)):
        raise TypeError(
            f"risk_aversion_lambda must be numeric, got {type(risk_aversion_lambda).__name__}."
        )
    risk_aversion_lambda = float(risk_aversion_lambda)
    if not math.isfinite(risk_aversion_lambda):
        raise ValueError(f"risk_aversion_lambda must be finite, got {risk_aversion_lambda}.")
    if risk_aversion_lambda < 0.0:
        raise ValueError(
            f"risk_aversion_lambda must be >= 0, got {risk_aversion_lambda}. Negative risk "
            "aversion has no Almgren-Chriss solution; use 0.0 for a risk-neutral (TWAP) schedule."
        )

    sigma = _require_positive_finite(volatility_per_sqrt_time, "volatility_per_sqrt_time")
    eta = _require_positive_finite(temporary_impact_eta, "temporary_impact_eta")
    tau = _require_positive_finite(interval_length, "interval_length")

    if isinstance(permanent_impact_gamma, bool) or not isinstance(permanent_impact_gamma, (int, float)):
        raise TypeError(
            f"permanent_impact_gamma must be numeric, got {type(permanent_impact_gamma).__name__}."
        )
    gamma = float(permanent_impact_gamma)
    if not math.isfinite(gamma) or gamma < 0.0:
        raise ValueError(f"permanent_impact_gamma must be finite and >= 0, got {permanent_impact_gamma}.")

    if risk_aversion_lambda == 0.0:
        return 0.0

    eta_tilde = eta - gamma * tau / 2.0
    if eta_tilde <= 0.0:
        raise ValueError(
            f"eta_tilde = eta - gamma*tau/2 = {eta_tilde} must be > 0 "
            f"(eta={eta}, gamma={gamma}, tau={tau}). Almgren-Chriss (2000) Eq. 16 has no real "
            "decay root once permanent impact over one interval reaches temporary impact; "
            "shorten the interval or re-estimate the impact coefficients."
        )

    kappa_tilde_sq = risk_aversion_lambda * sigma * sigma / eta_tilde
    acosh_arg = 1.0 + kappa_tilde_sq * tau * tau / 2.0
    if not math.isfinite(acosh_arg):
        raise ValueError(
            "Almgren-Chriss parameters overflow the arccosh argument "
            f"(lambda={risk_aversion_lambda}, sigma={sigma}, eta_tilde={eta_tilde}, tau={tau}). "
            "Re-scale the parameters into consistent units."
        )
    return math.acosh(acosh_arg) / tau


class ImplementationShortfallEngine:
    """
    Almgren-Chriss optimal trajectory planning and four-component Perold (1988)
    Implementation Shortfall decomposition (delay, market impact, opportunity cost,
    explicit fees) in USD and basis points.

    Stateless: no instance holds order state between calls, so one engine may be
    reused across orders.
    """

    def calculate_almgren_chriss_trajectory(
        self,
        total_qty: int,
        n_intervals: int = DEFAULT_INTERVALS,
        risk_aversion_lambda: float = 1e-4,
        *,
        volatility_per_sqrt_time: float = 1.0,
        temporary_impact_eta: float = 1.0,
        permanent_impact_gamma: float = 0.0,
        interval_length: float = 1.0,
    ) -> List[int]:
        """
        Whole-share Almgren-Chriss trade list: shares to work in each of
        ``n_intervals`` intervals, summing exactly to ``total_qty``.

        Every element is >= 0, because the holdings trajectory (Eq. 17) is rounded
        under a monotone non-increasing constraint and then differenced. Rounding
        each slice independently and plugging the residual into the last interval
        can emit a negative final slice (``total_qty=32, n_intervals=5, lambda=4``
        produced ``[28, 4, 1, 0, -1]``), a reversing trade Almgren-Chriss never
        prescribes.

        ``volatility_per_sqrt_time``, ``temporary_impact_eta``,
        ``permanent_impact_gamma`` and ``interval_length`` are keyword-only and
        default to dimensionless placeholders (1, 1, 0, 1). See the module
        docstring: the defaults reduce kappa to a function of
        ``risk_aversion_lambda`` alone and are **not** a calibration for any
        instrument, and sigma must use the same time unit as ``interval_length``.

        Whole-share rounding can swap two adjacent slices by at most one share when
        the real-valued schedule is nearly flat (e.g. 7 shares over 3 intervals at
        a near-zero lambda gives ``[2, 3, 2]``). The invariants that matter hold
        regardless: every slice is >= 0 and they sum to ``total_qty`` exactly.

        :raises TypeError: non-integer ``total_qty``/``n_intervals``, or non-numeric
            model parameters.
        :raises ValueError: non-positive quantity or interval count, negative
            ``risk_aversion_lambda``, non-positive ``sigma``/``eta``/``tau``, or
            ``eta - gamma*tau/2 <= 0``.
        """
        total_qty = _require_positive_int(total_qty, "total_qty")
        n_intervals = _require_positive_int(n_intervals, "n_intervals")

        kappa = almgren_chriss_kappa(
            risk_aversion_lambda,
            volatility_per_sqrt_time=volatility_per_sqrt_time,
            temporary_impact_eta=temporary_impact_eta,
            permanent_impact_gamma=permanent_impact_gamma,
            interval_length=interval_length,
        )

        tau = float(interval_length)
        horizon = kappa * n_intervals * tau

        # Holdings remaining after each interval: x_0 = total_qty down to x_N = 0.
        remaining: List[int] = [total_qty]
        for j in range(1, n_intervals):
            if kappa > 0.0:
                fraction = _sinh_ratio(kappa * (n_intervals - j) * tau, horizon)
            else:
                # Risk-neutral limit of Eq. 17: linear (TWAP) holdings decay.
                fraction = float(n_intervals - j) / float(n_intervals)
            held = int(round(total_qty * fraction))
            # Monotone non-increasing and bounded, so every differenced slice is >= 0.
            remaining.append(max(0, min(held, remaining[-1])))
        remaining.append(0)

        shares = [remaining[j] - remaining[j + 1] for j in range(n_intervals)]

        # x_0 - x_N telescopes to total_qty exactly; assert rather than plug a residual.
        if sum(shares) != total_qty:  # pragma: no cover - defensive
            raise AssertionError(f"Trajectory sums to {sum(shares)}, expected {total_qty}.")

        logger.debug(
            "Almgren-Chriss trajectory: qty=%d intervals=%d lambda=%s kappa=%.6f schedule=%s",
            total_qty, n_intervals, risk_aversion_lambda, kappa, shares,
        )
        return shares

    def evaluate_implementation_shortfall(
        self,
        symbol: str,
        side: str,
        total_order_qty: int,
        decision_price_p0: float,
        executed_fills: Sequence[ExecutedTradeFill],
        final_market_price: float,
        *,
        arrival_price: Optional[float] = None,
    ) -> ImplementationShortfallReport:
        """
        Four-component Perold (1988) Implementation Shortfall for one parent order.

        Costs are signed so that **positive means money lost**, for buys and sells
        alike. Basis points are quoted on the intended notional
        ``total_order_qty * decision_price_p0``.

        :param executed_fills: fills against this parent order. May be empty (a
            fully unfilled order, reported with status ``IS_EVALUATION_NO_FILLS``
            and 100% opportunity cost). Fill order does not affect the result.
        :param final_market_price: horizon price used to mark the unexecuted
            quantity. Must be a valid price whenever anything is unfilled.
        :param arrival_price: optional price when the order reached the venue. When
            supplied, the executed-leg cost is split exactly into ``delay_cost_usd``
            and ``market_impact_cost_usd``; when omitted both are ``None`` and only
            the combined ``execution_cost_usd`` is reported. It is never inferred.

        :raises TypeError: non-integer quantities, non-numeric prices or fees.
        :raises ValueError: invalid side, non-positive quantity or price, non-finite
            fee, duplicate ``fill_id``, or executed quantity exceeding
            ``total_order_qty``.
        """
        total_order_qty = _require_positive_int(total_order_qty, "total_order_qty")
        decision_price_p0 = _require_positive_finite(decision_price_p0, "decision_price_p0")

        if not isinstance(side, str):
            raise TypeError(f"side must be a str, got {type(side).__name__}.")
        side_clean = side.strip().upper()
        if side_clean not in {"BUY", "SELL"}:
            raise ValueError(f"Invalid side '{side}'. Must be 'BUY' or 'SELL'.")
        # +1 for BUY, -1 for SELL: makes every cost term positive-is-loss.
        side_sign = 1.0 if side_clean == "BUY" else -1.0

        fills = list(executed_fills) if executed_fills is not None else []
        seen_ids = set()
        for index, fill in enumerate(fills):
            context = f"executed_fills[{index}]"
            if not isinstance(fill, ExecutedTradeFill):
                raise TypeError(f"{context} must be an ExecutedTradeFill, got {type(fill).__name__}.")
            _require_positive_int(fill.quantity, f"{context}.quantity")
            _require_positive_finite(fill.fill_price, f"{context}.fill_price")
            fee = fill.explicit_fee_usd
            if isinstance(fee, bool) or not isinstance(fee, (int, float)):
                raise TypeError(f"{context}.explicit_fee_usd must be numeric, got {type(fee).__name__}.")
            if not math.isfinite(float(fee)):
                # A non-finite fee makes the whole shortfall NaN while the report still
                # reads IS_EVALUATION_SUCCESS. Reject it at the boundary instead.
                raise ValueError(f"{context}.explicit_fee_usd must be finite, got {fee}.")
            if fill.fill_id in seen_ids:
                raise ValueError(
                    f"{context}: duplicate fill_id '{fill.fill_id}'. Replayed or double-booked "
                    "fills silently double-count executed quantity and understate opportunity cost."
                )
            seen_ids.add(fill.fill_id)

        executed_qty = sum(f.quantity for f in fills)
        if executed_qty > total_order_qty:
            raise ValueError(
                f"Executed quantity {executed_qty:,} exceeds total_order_qty {total_order_qty:,}. "
                "Implementation Shortfall is undefined against a paper portfolio smaller than the "
                "real one; an over-execution is an order-control incident (see "
                "`order-placement-idempotency`), not a TCA result."
            )
        unfilled_qty = total_order_qty - executed_qty

        if unfilled_qty > 0:
            final_market_price = _require_positive_finite(final_market_price, "final_market_price")
        else:
            # Unused in the arithmetic when nothing is unfilled, but it still must be a
            # real number rather than a NaN carried through to a report or dashboard.
            if isinstance(final_market_price, bool) or not isinstance(final_market_price, (int, float)):
                raise TypeError(
                    f"final_market_price must be numeric, got {type(final_market_price).__name__}."
                )
            final_market_price = float(final_market_price)
            if not math.isfinite(final_market_price):
                raise ValueError(f"final_market_price must be finite, got {final_market_price}.")

        if arrival_price is not None:
            arrival_price = _require_positive_finite(arrival_price, "arrival_price")

        total_exec_notional = sum(f.quantity * f.fill_price for f in fills)
        vwap_exec_price: Optional[float] = (
            round(total_exec_notional / float(executed_qty), 6) if executed_qty > 0 else None
        )

        # Raw (unrounded) components. Currency figures are rounded for reporting, but
        # the bps figure is derived from the raw total -- see the module docstring.
        raw_fees = sum(float(f.explicit_fee_usd) for f in fills)
        # 1. Execution cost on filled shares: delay + trading cost + market drift.
        raw_execution_cost = side_sign * sum(
            f.quantity * (f.fill_price - decision_price_p0) for f in fills
        )
        # 2. Opportunity cost on the unexecuted quantity.
        raw_opportunity_cost = side_sign * unfilled_qty * (final_market_price - decision_price_p0)
        raw_total = raw_execution_cost + raw_opportunity_cost + raw_fees

        total_explicit_fees = _round_cents(raw_fees)
        execution_cost = _round_cents(raw_execution_cost)
        opportunity_cost = _round_cents(raw_opportunity_cost)

        # 1a. Optional exact split of the executed leg at the arrival price.
        delay_cost: Optional[float] = None
        market_impact_cost: Optional[float] = None
        if arrival_price is not None:
            delay_cost = _round_cents(side_sign * executed_qty * (arrival_price - decision_price_p0))
            market_impact_cost = _round_cents(
                side_sign * sum(f.quantity * (f.fill_price - arrival_price) for f in fills)
            )

        # 3. Totals. The reported USD total is the sum of the reported components, so
        # the decomposition adds up exactly on the report; bps comes from the raw total.
        total_is_usd = _round_cents(execution_cost + opportunity_cost + total_explicit_fees)
        intended_notional = total_order_qty * decision_price_p0
        total_is_bps = round((raw_total / intended_notional) * BPS_PER_UNIT, 2) + 0.0
        fill_ratio = round(executed_qty / float(total_order_qty), 6)

        status = STATUS_SUCCESS if executed_qty > 0 else STATUS_NO_FILLS
        vwap_text = f"${vwap_exec_price:,.4f}" if vwap_exec_price is not None else "n/a (no fills)"
        split_text = (
            f" Delay = ${delay_cost:,.2f}, Market Impact = ${market_impact_cost:,.2f}."
            if delay_cost is not None
            else " Delay/impact split unavailable (no arrival_price supplied)."
        )
        notes = (
            f"IMPLEMENTATION SHORTFALL REPORT [{symbol} - {side_clean}]: "
            f"Decision Price P0 = ${decision_price_p0:,.4f}, Exec VWAP = {vwap_text}. "
            f"Executed {executed_qty:,}/{total_order_qty:,} shares ({unfilled_qty:,} unfilled, "
            f"fill ratio {fill_ratio:.2%}). "
            f"Execution Cost = ${execution_cost:,.2f}, Opportunity Cost = ${opportunity_cost:,.2f}, "
            f"Fees = ${total_explicit_fees:,.2f}.{split_text} "
            f"Total IS = ${total_is_usd:,.2f} ({total_is_bps:.2f} bps of intended notional). "
            "Positive = cost."
        )
        logger.info(notes)

        return ImplementationShortfallReport(
            symbol=symbol,
            side=side_clean,
            total_order_qty=total_order_qty,
            executed_qty=executed_qty,
            unfilled_qty=unfilled_qty,
            decision_price_p0=decision_price_p0,
            volume_weighted_executed_price=vwap_exec_price,
            final_market_price=final_market_price,
            execution_cost_usd=execution_cost,
            opportunity_cost_usd=opportunity_cost,
            explicit_fees_usd=total_explicit_fees,
            total_implementation_shortfall_usd=total_is_usd,
            total_implementation_shortfall_bps=total_is_bps,
            status=status,
            audit_notes=notes,
            fill_ratio=fill_ratio,
            arrival_price=arrival_price,
            delay_cost_usd=delay_cost,
            market_impact_cost_usd=market_impact_cost,
        )
