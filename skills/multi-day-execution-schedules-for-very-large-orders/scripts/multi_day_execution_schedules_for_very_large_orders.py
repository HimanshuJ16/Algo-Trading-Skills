"""Multi-day execution schedules for parent orders too large for one session.

Given a parent order, an average daily volume (ADV) figure and a daily
participation cap, this module produces a day-by-day target schedule and the two
quantities a trader needs in order to choose a horizon:

* **Expected market impact cost**, which *rises* as the horizon shortens, and
* **Overnight price risk on the unexecuted inventory**, which *rises* as the
  horizon lengthens.

Impact coefficients follow Almgren, Thum, Hauptmann & Li (2005), "Direct
Estimation of Equity Market Impact", *Risk* 18(7), 57-62 (ATHL). Overnight risk
follows the variance term of Almgren & Chriss (2000), "Optimal Execution of
Portfolio Transactions", *Journal of Risk* 3(2), 5-39, Eq. (5).

This module does **not** compute the Almgren-Chriss optimal trajectory. It
allocates a caller-selected heuristic profile subject to a hard participation
cap. For the closed-form optimum see `implementation-shortfall-minimization`.

Units: quantities in shares, prices and cash in the instrument's quote currency,
volatility as a decimal fraction of price per trading day, impact in basis
points of the parent order's notional.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Schedule profiles this engine understands. Unknown values raise rather than
#: silently degrading to a flat schedule.
SUPPORTED_PROFILES = ("EQUAL_DAILY", "FRONT_LOADED", "BACK_LOADED")

#: Decimal places used when reporting share quantities.
_QTY_DP = 2
#: Decimal places used when reporting basis-point figures. Kept finer than
#: share rounding because a single session's impact can be well under 1 bp.
_BPS_DP = 4
#: Reporting granularity for share quantities, derived from _QTY_DP.
_QTY_QUANTUM = 10 ** -_QTY_DP
#: Absolute share tolerance for float comparisons against the daily cap.
_QTY_TOL = 1e-9
#: Tolerance used when flooring a quantity onto the reporting quantum grid.
_UNIT_TOL = 1e-6

#: ATHL (2005) Sec. 4.2 liquidity-factor exponent applied to shares
#: outstanding / ADV. Reported there as delta ~ 1/4.
_ATHL_LIQUIDITY_EXPONENT = 0.25

#: Upper bound on the horizon, roughly ten years of US equity sessions. An order
#: needing longer is a capacity problem, not a scheduling one, and materialising
#: the schedule would burn memory proportional to the horizon.
MAX_HORIZON_SESSIONS = 2520


@dataclass
class MultiDayOrderConfig:
    """Inputs for one multi-day parent-order schedule.

    Attributes:
        symbol: Instrument identifier, for reporting only.
        total_parent_quantity: Parent order size in shares. Always positive;
            this engine schedules magnitudes and is side-agnostic.
        current_price: Reference price used to convert shares to cash.
        adv_shares: Average daily volume in shares. Must be measured over a
            window the caller records; the engine treats it as constant across
            the whole horizon (see `references/standards.md`).
        max_daily_participation_pct: Hard per-day cap as a fraction of ADV
            (0.10 = 10%). A house limit, not a regulatory one.
        schedule_profile: One of ``SUPPORTED_PROFILES``.
        volatility_daily_pct: Daily volatility as a fraction of price
            (0.02 = 2%). Drives both impact and overnight risk.
        temp_impact_coeff: ATHL eta. Fitted value 0.142 +/- 0.0062.
        perm_impact_coeff: ATHL gamma. Fitted value 0.314 +/- 0.041.
        temp_impact_exponent: ATHL beta. Fitted value 0.600 +/- 0.038. ATHL
            reject the square-root value 0.5 at the 95% level.
        shares_outstanding: ATHL Theta. Required for the permanent-impact term;
            when omitted, permanent impact is reported as ``None`` rather than
            estimated from an assumed turnover.
        target_horizon_days: Requested horizon, in whole trading sessions,
            between the minimum feasible horizon implied by the cap and
            ``MAX_HORIZON_SESSIONS``. When ``None`` the minimum is used, which
            leaves the schedule capacity-saturated and flattens every profile --
            pass a longer horizon to give a profile room.
        profile_decay: Per-day exponential rate for the loaded profiles. Larger
            values tilt the schedule harder; 0.0 reproduces ``EQUAL_DAILY``.
    """

    symbol: str
    total_parent_quantity: float
    current_price: float
    adv_shares: float                            # Average Daily Volume
    max_daily_participation_pct: float = 0.10    # e.g., 0.10 (10% ADV)
    schedule_profile: str = "EQUAL_DAILY"        # see SUPPORTED_PROFILES
    volatility_daily_pct: float = 0.02           # Daily volatility (2%)
    temp_impact_coeff: float = 0.142             # ATHL (2005) eta
    perm_impact_coeff: float = 0.314             # ATHL (2005) gamma
    temp_impact_exponent: float = 0.60           # ATHL (2005) beta
    shares_outstanding: Optional[float] = None   # ATHL (2005) Theta
    target_horizon_days: Optional[int] = None
    profile_decay: float = 0.30


@dataclass
class DailySliceSchedule:
    """One trading session's target.

    Attributes:
        day_index: 1-based session ordinal. Sessions, not calendar days -- the
            caller maps these onto an exchange calendar.
        target_quantity: Shares to execute during the session.
        participation_pct_adv: ``target_quantity`` as a percentage of ADV.
        remaining_unexecuted_qty: Shares still open once the session closes,
            i.e. the inventory carried overnight into the next session.
        expected_temp_impact_bps: ATHL temporary impact for this session,
            in basis points of the session's own notional.
    """

    day_index: int
    target_quantity: float
    participation_pct_adv: float
    remaining_unexecuted_qty: float
    expected_temp_impact_bps: float = 0.0


@dataclass
class MultiDayScheduleReport:
    """Schedule plus the impact/risk pair the horizon decision trades off.

    ``expected_perm_impact_bps`` and ``expected_total_impact_bps`` are ``None``
    when ``shares_outstanding`` was not supplied; the ATHL permanent term is not
    identified without it.
    """

    symbol: str
    total_parent_quantity: float
    adv_shares: float
    execution_horizon_days: int
    daily_participation_cap_shares: float
    daily_schedules: List[DailySliceSchedule]
    expected_temp_impact_bps: float
    expected_perm_impact_bps: Optional[float]
    overnight_volatility_risk_usd: float
    status: str                          # 'SCHEDULE_GENERATED_SUCCESS'
    audit_notes: str
    min_feasible_horizon_days: int = 0
    expected_total_impact_bps: Optional[float] = None
    overnight_volatility_risk_bps: float = 0.0


def _tolerance(magnitude: float) -> float:
    """Share-count comparison tolerance that stays meaningful for large orders.

    A fixed absolute epsilon is below float resolution once quantities reach
    tens of millions of shares, which would make cap and capacity comparisons
    fire on representation noise alone.
    """
    return max(_QTY_TOL, abs(magnitude) * 1e-12)


def _require_finite(value: float, name: str) -> float:
    """Return ``value`` as a float, raising if it is NaN or infinite."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")
    return numeric


def _profile_weights(profile: str, n_days: int, decay: float) -> List[float]:
    """Unnormalised daily weights for a schedule profile.

    ``FRONT_LOADED`` decays and ``BACK_LOADED`` grows at ``decay`` per session.
    Weights are anchored so the largest is 1.0, which keeps them finite for long
    horizons -- ``exp(0.3 * 250)`` overflows, ``exp(-0.3 * 250)`` does not.
    """
    if profile == "EQUAL_DAILY" or decay == 0.0:
        return [1.0] * n_days
    if profile == "FRONT_LOADED":
        return [math.exp(-decay * i) for i in range(n_days)]
    if profile == "BACK_LOADED":
        return [math.exp(-decay * (n_days - 1 - i)) for i in range(n_days)]
    raise ValueError(
        f"Unknown schedule_profile {profile!r}; expected one of {SUPPORTED_PROFILES}."
    )


def _water_fill(weights: Sequence[float], total: float, cap: float) -> List[float]:
    """Allocate ``total`` across ``weights`` with a per-element ceiling ``cap``.

    Solves for the scale ``lam`` such that ``q_i = min(cap, lam * w_i)`` sums to
    ``total``. Elements that hit the ceiling are frozen and the remainder is
    re-shared among the rest, preserving the *relative* shape of the uncapped
    elements. A greedy "clip then refill in index order" pass does not: it moves
    the clipped excess to whichever element comes first, which can turn a
    monotonically increasing profile into a non-monotonic one.

    Raises:
        ValueError: if ``total`` exceeds the total capacity ``len(weights) * cap``.
    """
    n = len(weights)
    capacity = n * cap
    tol = _tolerance(total)
    if total > capacity + tol:
        raise ValueError(
            f"Cannot allocate {total:,.4f} shares across {n} sessions capped at "
            f"{cap:,.4f} shares each (capacity {capacity:,.4f})."
        )

    alloc = [0.0] * n
    frozen = [False] * n
    remaining = total

    # Each pass freezes at least one element, so this terminates in <= n passes.
    for _ in range(n):
        active = [i for i in range(n) if not frozen[i]]
        if not active or remaining <= tol:
            break
        active_weights = [weights[i] for i in active]
        weight_sum = sum(active_weights)
        if weight_sum <= 0.0:
            # Weights underflowed to zero (very large profile_decay over a long
            # horizon). Share the remainder evenly rather than dividing by zero.
            active_weights = [1.0] * len(active)
            weight_sum = float(len(active))
        newly_frozen = [
            i
            for i, w in zip(active, active_weights)
            if remaining * w / weight_sum > cap + tol
        ]
        if not newly_frozen:
            for i, w in zip(active, active_weights):
                alloc[i] = remaining * w / weight_sum
            remaining = 0.0
            break
        for i in newly_frozen:
            alloc[i] = cap
            frozen[i] = True
            remaining -= cap

    return alloc


def _cap_units(cap: float) -> int:
    """Largest reportable quantity, in quanta, that does not exceed ``cap``."""
    return int(math.floor(cap / _QTY_QUANTUM + _UNIT_TOL))


def _apportion(
    alloc: Sequence[float], total: float, cap: float, prefer_late: bool
) -> List[float]:
    """Round to the reporting quantum by largest-remainder apportionment.

    Rounding each slice independently lets the schedule drift away from the
    parent quantity, which would leave the parent silently over- or
    under-executed. Handing the whole residual to one session cannot fix that in
    general: 57 sessions each allocated 99.982536 shares under a 100-share cap
    round down to a 0.14-share shortfall while every session has only 0.02 shares
    of headroom, so no single session can absorb it legally.

    Each session therefore floors to a whole quantum and the shortfall is handed
    out one quantum at a time to the sessions with the largest discarded
    fractions, skipping any already at the cap. Ties -- which arise whenever the
    exact allocations are equal, as they are on a capacity-saturated schedule --
    resolve towards the end of the horizon when ``prefer_late`` is set, so a
    non-decreasing trajectory stays non-decreasing and a non-increasing one stays
    non-increasing.
    """
    cap_u = _cap_units(cap)
    total_u = int(round(total / _QTY_QUANTUM))

    base: List[int] = []
    remainders: List[float] = []
    for qty in alloc:
        exact_u = qty / _QTY_QUANTUM
        floor_u = min(cap_u, int(math.floor(exact_u + _UNIT_TOL)))
        base.append(floor_u)
        remainders.append(exact_u - floor_u)

    shortfall = total_u - sum(base)
    if shortfall > 0:
        candidates = [i for i in range(len(base)) if base[i] < cap_u]
        candidates.sort(key=lambda i: (-remainders[i], -i if prefer_late else i))
        for i in candidates[:shortfall]:
            base[i] += 1
        shortfall -= min(shortfall, len(candidates))

    if shortfall != 0:
        logger.warning(
            "%d quanta of the parent quantity could not be allocated without "
            "breaching the daily cap.",
            shortfall,
        )

    return [round(units * _QTY_QUANTUM, _QTY_DP) for units in base]


class MultiDayExecutionSchedulerEngine:
    """Participation-capped multi-day slicer with ATHL impact and AC-2000 risk.

    The engine answers one question: for a parent order too large to complete in
    a single session, what does each candidate horizon cost in expected impact,
    and what does it expose in overnight price risk? It does not place orders and
    holds no state between calls.
    """

    def _validate(self, cfg: MultiDayOrderConfig) -> None:
        """Reject configurations that cannot produce a meaningful schedule."""
        if not str(cfg.symbol).strip():
            raise ValueError("symbol must be a non-empty identifier.")

        total_qty = _require_finite(cfg.total_parent_quantity, "total_parent_quantity")
        adv = _require_finite(cfg.adv_shares, "adv_shares")
        price = _require_finite(cfg.current_price, "current_price")
        if total_qty <= 0 or adv <= 0 or price <= 0:
            raise ValueError("Total quantity, ADV, and price must be positive numbers.")

        participation = _require_finite(
            cfg.max_daily_participation_pct, "max_daily_participation_pct"
        )
        if not 0.0 < participation <= 1.0:
            raise ValueError(
                "max_daily_participation_pct must be a fraction in (0, 1]; "
                f"got {participation!r}. Values above 1.0 would schedule more "
                "than the instrument's entire average daily volume."
            )

        volatility = _require_finite(cfg.volatility_daily_pct, "volatility_daily_pct")
        if volatility < 0:
            raise ValueError("volatility_daily_pct must be non-negative.")

        for name in ("temp_impact_coeff", "perm_impact_coeff"):
            if _require_finite(getattr(cfg, name), name) < 0:
                raise ValueError(f"{name} must be non-negative.")
        if _require_finite(cfg.temp_impact_exponent, "temp_impact_exponent") <= 0:
            raise ValueError("temp_impact_exponent must be positive.")
        if _require_finite(cfg.profile_decay, "profile_decay") < 0:
            raise ValueError("profile_decay must be non-negative.")

        if str(cfg.schedule_profile).upper() not in SUPPORTED_PROFILES:
            raise ValueError(
                f"Unknown schedule_profile {cfg.schedule_profile!r}; expected one "
                f"of {SUPPORTED_PROFILES}."
            )

        if cfg.shares_outstanding is not None:
            theta = _require_finite(cfg.shares_outstanding, "shares_outstanding")
            if theta <= 0:
                raise ValueError("shares_outstanding must be positive when supplied.")
            if theta < adv:
                raise ValueError(
                    f"shares_outstanding ({theta:,.0f}) is below ADV ({adv:,.0f}), "
                    "implying the entire float turns over more than once per day. "
                    "Check the reference data before scheduling against it."
                )

        if cfg.target_horizon_days is not None:
            horizon = _require_finite(cfg.target_horizon_days, "target_horizon_days")
            if horizon != int(horizon):
                raise ValueError(
                    "target_horizon_days must be a whole number of sessions; "
                    f"got {cfg.target_horizon_days!r}."
                )
            if int(horizon) < 1:
                raise ValueError("target_horizon_days must be at least 1 when supplied.")
            if int(horizon) > MAX_HORIZON_SESSIONS:
                raise ValueError(
                    f"target_horizon_days={int(horizon)} exceeds the "
                    f"{MAX_HORIZON_SESSIONS}-session limit."
                )

    def generate_multi_day_schedule(
        self, cfg: MultiDayOrderConfig
    ) -> MultiDayScheduleReport:
        """Build the day-by-day schedule and its impact / overnight-risk pair.

        The horizon is ``cfg.target_horizon_days`` when supplied, otherwise the
        minimum feasible under the participation cap. Daily slices follow the
        requested profile, scaled so no session exceeds the cap and so the
        slices sum exactly to the parent quantity.

        Raises:
            ValueError: on any invalid input, on an unknown profile, or when
                ``target_horizon_days`` is shorter than the cap allows.
        """
        self._validate(cfg)

        total_qty = float(cfg.total_parent_quantity)
        adv = float(cfg.adv_shares)
        price = float(cfg.current_price)
        volatility = float(cfg.volatility_daily_pct)
        profile = str(cfg.schedule_profile).upper()

        # 1. Daily cap and the shortest horizon it permits.
        daily_cap_shares = adv * float(cfg.max_daily_participation_pct)
        # Allocation works against the cap rounded down onto the reporting grid.
        # Reporting slices at _QTY_DP places while allocating against a finer cap
        # would leave quanta that cannot legally be placed anywhere.
        alloc_cap_shares = round(_cap_units(daily_cap_shares) * _QTY_QUANTUM, _QTY_DP)
        if alloc_cap_shares <= 0:
            raise ValueError(
                f"A {daily_cap_shares:,.6f} share/session cap rounds to zero at the "
                f"{_QTY_QUANTUM} share reporting granularity."
            )
        # Derive the horizon from ceil(), then step back while the shorter
        # horizon still holds the whole order. Comparing in share units rather
        # than trusting the raw ratio keeps a quantity that is an exact multiple
        # of the cap from being rounded up to a spurious extra session.
        min_horizon = max(1, int(math.ceil(total_qty / alloc_cap_shares)))
        while (
            min_horizon > 1
            and (min_horizon - 1) * alloc_cap_shares
            >= total_qty - _tolerance(total_qty)
        ):
            min_horizon -= 1

        if min_horizon > MAX_HORIZON_SESSIONS:
            raise ValueError(
                f"{total_qty:,.0f} shares at a {alloc_cap_shares:,.0f} share/session "
                f"cap needs {min_horizon:,} sessions, beyond the "
                f"{MAX_HORIZON_SESSIONS}-session limit. Check that the quantity is in "
                "shares rather than notional, or treat this as a capacity question "
                "rather than a scheduling one."
            )

        if cfg.target_horizon_days is None:
            n_days = min_horizon
        else:
            n_days = int(cfg.target_horizon_days)
            if n_days < min_horizon:
                raise ValueError(
                    f"target_horizon_days={n_days} is infeasible: {total_qty:,.0f} "
                    f"shares at a {alloc_cap_shares:,.0f} share/session cap needs at "
                    f"least {min_horizon} sessions."
                )

        # 2. Profile weights, water-filled against the cap, then reconciled so
        #    the printed slices sum exactly to the parent quantity.
        weights = _profile_weights(profile, n_days, float(cfg.profile_decay))
        raw_slices = _water_fill(weights, total_qty, alloc_cap_shares)
        daily_slices = _apportion(
            raw_slices, total_qty, alloc_cap_shares, prefer_late=(profile == "BACK_LOADED")
        )

        allocated = sum(daily_slices)
        if abs(allocated - total_qty) > 10 ** -_QTY_DP:
            raise RuntimeError(
                f"Schedule allocates {allocated:,.4f} shares against a parent of "
                f"{total_qty:,.4f}; refusing to emit an unbalanced schedule."
            )

        # 3. Per-session rows, inventory carried overnight, and the ATHL
        #    temporary impact each session's own participation rate implies.
        #    Temporary impact is a per-session quantity: it depends on how fast
        #    that session trades, which is exactly what the profile changes.
        eta = float(cfg.temp_impact_coeff)
        beta = float(cfg.temp_impact_exponent)

        daily_schedules: List[DailySliceSchedule] = []
        rem_qty = total_qty
        temp_cost_shares_bps = 0.0

        for day, qty_day in enumerate(daily_slices, start=1):
            rem_qty = round(rem_qty - qty_day, _QTY_DP)
            participation_rate = qty_day / adv
            # ATHL Sec. 4.3: K = eta * sigma * |X / (V T)|^beta, T = 1 session.
            temp_impact_frac = eta * volatility * (participation_rate ** beta)
            temp_cost_shares_bps += qty_day * temp_impact_frac * 10000.0
            daily_schedules.append(
                DailySliceSchedule(
                    day_index=day,
                    target_quantity=round(qty_day, _QTY_DP),
                    participation_pct_adv=round(participation_rate * 100.0, _QTY_DP),
                    remaining_unexecuted_qty=max(0.0, rem_qty),
                    expected_temp_impact_bps=round(temp_impact_frac * 10000.0, _BPS_DP),
                )
            )

        # Quantity-weighted temporary impact, in bps of the parent notional.
        temp_impact_bps = temp_cost_shares_bps / total_qty

        # 4. Permanent impact. ATHL: I = gamma * sigma * (X/V) * (Theta/V)^(1/4),
        #    the *full* price move; AC (2000) Eq. (8) shows the cost borne by a
        #    completed program is half of it. It depends only on total size, so
        #    unlike temporary impact it does not fall as the horizon lengthens.
        perm_impact_bps: Optional[float] = None
        total_impact_bps: Optional[float] = None
        if cfg.shares_outstanding is not None:
            liquidity_factor = (
                float(cfg.shares_outstanding) / adv
            ) ** _ATHL_LIQUIDITY_EXPONENT
            perm_move_frac = (
                float(cfg.perm_impact_coeff)
                * volatility
                * (total_qty / adv)
                * liquidity_factor
            )
            perm_impact_bps = 0.5 * perm_move_frac * 10000.0
            total_impact_bps = temp_impact_bps + perm_impact_bps
        else:
            logger.warning(
                "shares_outstanding not supplied for %s; permanent impact is not "
                "identified under the ATHL model and is reported as None.",
                cfg.symbol,
            )

        # 5. Overnight risk on the unexecuted inventory. Almgren & Chriss (2000)
        #    Eq. (5): V(x) = sigma^2 * sum_k tau * x_k^2, with tau = 1 session and
        #    sigma expressed in price units as volatility_daily_pct * price. The
        #    result is a one-standard-deviation figure, not a worst case, and
        #    assumes independent daily returns and a constant reference price.
        variance_rem = sum(
            (s.remaining_unexecuted_qty * price) ** 2 for s in daily_schedules
        )
        overnight_vol_usd = math.sqrt(variance_rem) * volatility
        parent_notional = total_qty * price
        overnight_vol_bps = overnight_vol_usd / parent_notional * 10000.0

        perm_txt = "n/a (shares_outstanding not supplied)"
        if perm_impact_bps is not None:
            perm_txt = f"{perm_impact_bps:.1f} bps"
        notes = (
            f"MULTI-DAY SCHEDULE GENERATED [{cfg.symbol}]: Total Parent Qty = "
            f"{total_qty:,.0f} shares ({total_qty / adv * 100:.1f}% ADV). "
            f"Horizon = {n_days} sessions (minimum feasible {min_horizon}). "
            f"Daily Cap = {daily_cap_shares:,.0f} shares "
            f"({cfg.max_daily_participation_pct * 100:.1f}% ADV). "
            f"Profile = {profile}. Expected Temp Impact = {temp_impact_bps:.1f} bps, "
            f"Perm Impact = {perm_txt}, Overnight Risk (1 sigma) = "
            f"${overnight_vol_usd:,.2f} ({overnight_vol_bps:.1f} bps)."
        )
        logger.info(notes)

        return MultiDayScheduleReport(
            symbol=cfg.symbol,
            total_parent_quantity=total_qty,
            adv_shares=adv,
            execution_horizon_days=n_days,
            daily_participation_cap_shares=daily_cap_shares,
            daily_schedules=daily_schedules,
            expected_temp_impact_bps=round(temp_impact_bps, _BPS_DP),
            expected_perm_impact_bps=(
                None if perm_impact_bps is None else round(perm_impact_bps, _BPS_DP)
            ),
            overnight_volatility_risk_usd=round(overnight_vol_usd, 2),
            status="SCHEDULE_GENERATED_SUCCESS",
            audit_notes=notes,
            min_feasible_horizon_days=min_horizon,
            expected_total_impact_bps=(
                None if total_impact_bps is None else round(total_impact_bps, _BPS_DP)
            ),
            overnight_volatility_risk_bps=round(overnight_vol_bps, _BPS_DP),
        )
