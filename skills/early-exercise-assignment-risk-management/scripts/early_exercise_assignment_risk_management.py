"""Early exercise / assignment risk screening for short American option positions.

Screens short American-style option positions for the two economically distinct
early-exercise drivers, and emits a close/roll directive with an auditable
justification:

1. **Ex-dividend capture on short calls.** A long call holder who exercises on
   the last cum-dividend session captures the dividend (exercise settles T+1,
   and since the T+1 transition of 28 May 2024 the ex-date and the record date
   are the same day, so a trade date one session before the ex-date makes the
   holder a holder of record). The short call writer is assigned, becomes short
   the stock over the ex-date, and owes the dividend.
2. **Carry-driven exercise on deep in-the-money short puts.** Exercising a put
   converts the position to cash at the strike, which then earns interest for
   the remaining life of the option.

Exercise economics implemented here
-----------------------------------
For a call held into an ex-dividend date of amount ``D``, with ``tau`` years to
expiry and continuously compounded rate ``r``, early exercise immediately
before the ex-date is optimal exactly when::

    D > TV_ex   where   TV_ex = p_ex + K * (1 - exp(-r * tau))

``TV_ex`` is the call's time value evaluated at the *ex-dividend* underlying
price, and ``p_ex`` is the value of the same-strike, same-expiry put (Merton,
"Theory of Rational Option Pricing", 1973; the equivalent ``D > K(1 - B(t,T)) +
p`` form is the standard textbook statement). This engine applies that test
whenever ``same_strike_put_price`` is supplied.

When no put price is supplied the engine falls back to the common desk screen
``D > extrinsic``, where ``extrinsic`` is the *cum-dividend* extrinsic value of
the call itself. That screen is deliberately **conservative, not exact**: by
put-call parity the cum-dividend extrinsic equals ``TV_ex - PV(D)``, so the
screen fires whenever ``TV_ex < D + PV(D)`` -- a strict superset of the exact
condition ``TV_ex < D``. It over-flags and does not under-flag under this
model, which is the correct direction for a writer, but it must not be read as
a statement that exercise is certain.

What this engine deliberately does NOT claim
--------------------------------------------
- It does not output a probability of assignment. Even when every rational
  holder exercises, an individual short position is assigned only if the OCC's
  allocation to its clearing member reaches it, and the member then allocates
  by FIFO, random selection, or another FINRA-approved equally-random method
  (FINRA Rule 2360(b)(23)(C); Regulatory Notice 11-35). The chance that *this*
  account is assigned depends on open interest and the broker's allocation
  method, neither of which is an input here. The output is an ordinal risk
  score, explicitly not a probability.
- It does not model the ex-date price drop, borrow cost, hard-to-borrow recall
  risk, pin risk at expiry, or the wildcard option in cash-settled American
  index options.
- It has no view of broker cutoff times. FINRA Rule 2360(b)(23)(A) fixes 5:30
  p.m. ET on expiration day as the final exercise decision deadline for
  *expiring* options, and members may set an earlier deadline (FINRA
  Information Notice, 3 Feb 2021). For early exercise on a non-expiration day
  the operative deadline is the clearing member's own cutoff. Treat the day
  counts here as calendar inputs you supply, not as a schedule the engine
  knows.

Input convention that materially changes the answer
---------------------------------------------------
``option_market_price`` should be the price at which the *long* holder could
realistically exit -- the **bid**, not the mid and not the last trade. A
rational holder exercises only when exercising beats selling; feeding the mid
overstates extrinsic value and therefore understates assignment risk.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# --- Engineering defaults (tunable; none of these are regulatory constants) ---

# Absolute extrinsic floor below which an ITM short option is treated as
# "trading at parity" and therefore exercisable at any moment.
DEFAULT_MIN_EXTRINSIC_USD = 0.05
# Relative floor, applied alongside the absolute one, so the parity test does
# not become meaningless on high-strike underlyings (5 bp of strike: $0.05 on a
# $100 strike, $2.50 on a $5,000 strike).
DEFAULT_MIN_EXTRINSIC_FRACTION_OF_STRIKE = 0.0005
# Days-to-ex-dividend inside which the exercise decision is actually being made
# by holders (the last cum-dividend session).
DEFAULT_EX_DIV_DECISION_DAYS = 1.0
# Wider pre-warning window, so a desk sees the risk before the decision day.
DEFAULT_EX_DIV_WARNING_DAYS = 3.0
# Days per year used to convert days_to_expiry into the tau of the interest
# term. Calendar-day basis, matching how days_to_expiry is normally supplied.
DAYS_PER_YEAR = 365.0

VALID_OPTION_TYPES = frozenset({"CALL", "PUT"})
VALID_EXERCISE_STYLES = frozenset({"AMERICAN", "EUROPEAN"})

# Risk levels, ordered from benign to severe.
RISK_LEVELS = ("LOW_RISK", "ELEVATED_ASSIGNMENT_RISK", "HIGH_ASSIGNMENT_RISK",
               "CRITICAL_ASSIGNMENT_RISK")
_RISK_RANK = {level: rank for rank, level in enumerate(RISK_LEVELS)}


class EarlyExerciseRiskError(ValueError):
    """Raised on an invalid position, engine configuration, or market input."""


def _require_finite(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise EarlyExerciseRiskError(f"{name} must be finite, got {value!r}.")
    return number


def _require_non_negative(value: float, name: str) -> float:
    number = _require_finite(value, name)
    if number < 0.0:
        raise EarlyExerciseRiskError(f"{name} must be >= 0, got {number!r}.")
    return number


def _require_positive(value: float, name: str) -> float:
    number = _require_finite(value, name)
    if number <= 0.0:
        raise EarlyExerciseRiskError(f"{name} must be > 0, got {number!r}.")
    return number


@dataclass
class ShortOptionPosition:
    """A single short option position to screen.

    Args:
        position_id: Stable internal identifier, echoed into the report.
        symbol: Underlying symbol, used in logs and summaries.
        option_type: ``'CALL'`` or ``'PUT'`` (case-insensitive). Any other value
            is rejected rather than silently treated as a put.
        exercise_style: ``'AMERICAN'`` or ``'EUROPEAN'`` (case-insensitive).
            Rejected if unrecognised -- a typo must not silently downgrade an
            American position to "no early exercise possible". Note that
            exercise style is independent of settlement method: OEX (S&P 100) is
            American-style *and* cash-settled, while XEO on the same index is
            European-style.
        strike: Strike price per share, in the option's currency.
        option_market_price: Per-share option premium. Use the **bid** -- see
            the module docstring.
        underlying_price: Current underlying price per share.
        contracts_qty: Number of contracts short, as a positive count.
        days_to_expiry: Calendar days remaining to expiration.
        upcoming_dividend_usd: Next declared dividend per share, or 0.0 if none.
        days_to_ex_div: Calendar days to the ex-dividend date. Left at the
            sentinel default (+inf) when no dividend is scheduled.
        contract_multiplier: Shares per contract (100 for standard US equity
            options).
        same_strike_put_price: Optional per-share price of the same-strike,
            same-expiry put. When supplied, the exact Merton early-exercise test
            replaces the conservative extrinsic screen for calls.
        same_strike_call_price: Optional per-share price of the same-strike,
            same-expiry call, used to report the carry edge on short puts.
    """

    position_id: str
    symbol: str
    option_type: str
    exercise_style: str
    strike: float
    option_market_price: float
    underlying_price: float
    contracts_qty: int
    days_to_expiry: float
    upcoming_dividend_usd: float = 0.0
    days_to_ex_div: float = float("inf")
    contract_multiplier: int = 100
    same_strike_put_price: Optional[float] = None
    same_strike_call_price: Optional[float] = None

    def __post_init__(self) -> None:
        if not str(self.position_id).strip():
            raise EarlyExerciseRiskError("position_id must be a non-empty string.")
        if not str(self.symbol).strip():
            raise EarlyExerciseRiskError("symbol must be a non-empty string.")

        self.option_type = str(self.option_type).strip().upper()
        if self.option_type not in VALID_OPTION_TYPES:
            raise EarlyExerciseRiskError(
                f"option_type must be one of {sorted(VALID_OPTION_TYPES)}, "
                f"got {self.option_type!r}.")

        self.exercise_style = str(self.exercise_style).strip().upper()
        if self.exercise_style not in VALID_EXERCISE_STYLES:
            raise EarlyExerciseRiskError(
                f"exercise_style must be one of {sorted(VALID_EXERCISE_STYLES)}, "
                f"got {self.exercise_style!r}.")

        self.strike = _require_positive(self.strike, "strike")
        self.underlying_price = _require_positive(
            self.underlying_price, "underlying_price")
        self.option_market_price = _require_non_negative(
            self.option_market_price, "option_market_price")
        self.days_to_expiry = _require_non_negative(
            self.days_to_expiry, "days_to_expiry")
        self.upcoming_dividend_usd = _require_non_negative(
            self.upcoming_dividend_usd, "upcoming_dividend_usd")

        # days_to_ex_div may legitimately be +inf ("no dividend scheduled").
        days_to_ex_div = float(self.days_to_ex_div)
        if math.isnan(days_to_ex_div) or days_to_ex_div < 0.0:
            raise EarlyExerciseRiskError(
                f"days_to_ex_div must be >= 0 (or +inf when no dividend is "
                f"scheduled), got {self.days_to_ex_div!r}.")
        self.days_to_ex_div = days_to_ex_div

        contracts = int(self.contracts_qty)
        if contracts <= 0:
            raise EarlyExerciseRiskError(
                f"contracts_qty is the size of the short position and must be a "
                f"positive count, got {self.contracts_qty!r}.")
        self.contracts_qty = contracts

        multiplier = int(self.contract_multiplier)
        if multiplier <= 0:
            raise EarlyExerciseRiskError(
                f"contract_multiplier must be > 0, got {self.contract_multiplier!r}.")
        self.contract_multiplier = multiplier

        if self.same_strike_put_price is not None:
            self.same_strike_put_price = _require_non_negative(
                self.same_strike_put_price, "same_strike_put_price")
        if self.same_strike_call_price is not None:
            self.same_strike_call_price = _require_non_negative(
                self.same_strike_call_price, "same_strike_call_price")


@dataclass
class EarlyExerciseAuditReport:
    """Structured, auditable result of one position screen.

    ``assignment_risk_score`` is an ordinal 0-100 severity score, **not** a
    probability of assignment: OCC allocation to clearing members and the
    member's own FIFO/random allocation (FINRA Rule 2360(b)(23)(C)) determine
    whether a particular short account is assigned, and neither is an input to
    this engine.
    """

    position_id: str
    symbol: str
    option_type: str
    exercise_style: str
    strike: float
    underlying_price: float
    intrinsic_value_usd: float
    extrinsic_value_usd: float
    upcoming_dividend_usd: float
    assignment_risk_score: float
    risk_level: str
    recommended_action: str
    risk_summary: str
    # Which test produced the call verdict: 'MERTON_PUT_PARITY' (exact, put
    # price supplied), 'EXTRINSIC_SCREEN' (conservative fallback), or
    # 'NOT_APPLICABLE'.
    exercise_test_used: str = "NOT_APPLICABLE"
    # Dividend minus the call's ex-dividend time value (calls), or the put's
    # carry edge, when computable under the exact test. Positive => early
    # exercise is economically favoured for the holder.
    early_exercise_edge_usd: Optional[float] = None
    # Shares that would be delivered/received on full assignment, at spot.
    assigned_share_notional_usd: float = 0.0
    # Dividend the writer would owe if assigned before the ex-date (calls only).
    dividend_liability_usd: float = 0.0
    # True when the option quote is below intrinsic value: exercising already
    # beats selling, and the quote may also be stale or crossed.
    quoted_below_parity: bool = False
    # Non-fatal input-quality observations, for the audit trail.
    data_quality_flags: List[str] = field(default_factory=list)


class EarlyExerciseRiskEngine:
    """Screens short American option positions for early exercise / assignment risk.

    Args:
        min_extrinsic_threshold_usd: Absolute extrinsic floor for the
            "trading at parity" test.
        min_extrinsic_fraction_of_strike: Relative extrinsic floor, applied as
            ``max(absolute, fraction * strike)`` so the test scales with the
            contract's notional.
        ex_div_decision_days: Days-to-ex-dividend inside which a positive
            dividend test escalates to ``CRITICAL_ASSIGNMENT_RISK``.
        ex_div_warning_days: Wider window inside which a positive dividend test
            raises ``ELEVATED_ASSIGNMENT_RISK`` as a pre-warning.
        risk_free_rate: Continuously compounded annual rate used for the
            interest term of the exact tests. Defaults to 0.0, which reduces the
            call test to ``D > put price`` -- set it deliberately.
    """

    def __init__(
        self,
        min_extrinsic_threshold_usd: float = DEFAULT_MIN_EXTRINSIC_USD,
        min_extrinsic_fraction_of_strike: float = DEFAULT_MIN_EXTRINSIC_FRACTION_OF_STRIKE,
        ex_div_decision_days: float = DEFAULT_EX_DIV_DECISION_DAYS,
        ex_div_warning_days: float = DEFAULT_EX_DIV_WARNING_DAYS,
        risk_free_rate: float = 0.0,
    ) -> None:
        self.min_extrinsic_threshold_usd = _require_non_negative(
            min_extrinsic_threshold_usd, "min_extrinsic_threshold_usd")
        self.min_extrinsic_fraction_of_strike = _require_non_negative(
            min_extrinsic_fraction_of_strike, "min_extrinsic_fraction_of_strike")
        self.ex_div_decision_days = _require_non_negative(
            ex_div_decision_days, "ex_div_decision_days")
        self.ex_div_warning_days = _require_non_negative(
            ex_div_warning_days, "ex_div_warning_days")
        if self.ex_div_warning_days < self.ex_div_decision_days:
            raise EarlyExerciseRiskError(
                "ex_div_warning_days must be >= ex_div_decision_days.")
        self.risk_free_rate = _require_finite(risk_free_rate, "risk_free_rate")

    # --- internals -----------------------------------------------------

    def _parity_threshold(self, strike: float) -> float:
        """Extrinsic level at or below which an ITM option counts as at parity."""
        return max(self.min_extrinsic_threshold_usd,
                   self.min_extrinsic_fraction_of_strike * strike)

    def _interest_on_strike(self, strike: float, days_to_expiry: float) -> float:
        """K * (1 - exp(-r * tau)): interest forgone by paying the strike early."""
        tau = days_to_expiry / DAYS_PER_YEAR
        try:
            discount = math.exp(-self.risk_free_rate * tau)
        except OverflowError as exc:  # deeply negative rate over a long tenor
            raise EarlyExerciseRiskError(
                f"Discount factor overflowed for risk_free_rate="
                f"{self.risk_free_rate!r} over {days_to_expiry!r} days; check "
                f"the rate convention and the day count.") from exc
        return strike * (1.0 - discount)

    @staticmethod
    def _escalate(current: str, candidate: str) -> str:
        return candidate if _RISK_RANK[candidate] > _RISK_RANK[current] else current

    # --- public API ----------------------------------------------------

    def audit_short_position_assignment_risk(
        self, pos: ShortOptionPosition
    ) -> EarlyExerciseAuditReport:
        """Screen one short option position for early exercise / assignment risk.

        Args:
            pos: The short position. Validated at construction time.

        Returns:
            An :class:`EarlyExerciseAuditReport`. European-style positions
            short-circuit to ``LOW_RISK``; American positions are evaluated
            against the ex-dividend test (calls) and the parity test (both), and
            the reported risk level is the most severe rule that fires.
        """
        opt_type = pos.option_type
        style = pos.exercise_style
        S = pos.underlying_price
        K = pos.strike
        P = pos.option_market_price

        intrinsic_raw = max(0.0, S - K) if opt_type == "CALL" else max(0.0, K - S)
        # A quote below intrinsic is itself the strongest exercise signal, and
        # is also a data-quality tell. Clamp the reported extrinsic at zero but
        # never lose the fact that it was negative.
        quoted_below_parity = P < intrinsic_raw
        intrinsic = round(intrinsic_raw, 4)
        extrinsic = round(max(0.0, P - intrinsic_raw), 4)

        data_quality_flags: List[str] = []
        if P == 0.0:
            data_quality_flags.append("ZERO_OPTION_PRICE")
        if quoted_below_parity:
            data_quality_flags.append("QUOTE_BELOW_INTRINSIC")

        assigned_notional = round(
            pos.contracts_qty * pos.contract_multiplier * S, 2)

        if style == "EUROPEAN":
            return EarlyExerciseAuditReport(
                position_id=pos.position_id, symbol=pos.symbol,
                option_type=opt_type, exercise_style=style, strike=K,
                underlying_price=S, intrinsic_value_usd=intrinsic,
                extrinsic_value_usd=extrinsic,
                upcoming_dividend_usd=pos.upcoming_dividend_usd,
                assignment_risk_score=0.0, risk_level="LOW_RISK",
                recommended_action="NO_ACTION_REQUIRED",
                risk_summary=(
                    "European-style option; exercise is only possible at "
                    "expiration, so there is no early assignment risk. Pin risk "
                    "at expiry is a separate exposure."),
                assigned_share_notional_usd=assigned_notional,
                data_quality_flags=data_quality_flags,
            )

        risk_lvl = "LOW_RISK"
        action = "NO_ACTION_REQUIRED"
        score = 0.0
        test_used = "NOT_APPLICABLE"
        edge: Optional[float] = None
        dividend_liability = 0.0
        summaries: List[str] = []

        parity_threshold = self._parity_threshold(K)
        at_parity = intrinsic > 0.0 and extrinsic <= parity_threshold

        if opt_type == "CALL":
            # A dividend whose ex-date falls after this option expires cannot be
            # captured by exercising it, so it carries no assignment risk here.
            dividend_before_expiry = (
                pos.upcoming_dividend_usd > 0.0
                and pos.days_to_ex_div <= pos.days_to_expiry
            )
            if pos.upcoming_dividend_usd > 0.0 and not dividend_before_expiry:
                data_quality_flags.append("DIVIDEND_AFTER_EXPIRY_IGNORED")

            if dividend_before_expiry:
                interest_term = self._interest_on_strike(K, pos.days_to_expiry)
                if pos.same_strike_put_price is not None:
                    # Exact test: exercise optimal iff D > p_ex + K(1 - e^-r*tau).
                    time_value_ex = pos.same_strike_put_price + interest_term
                    edge = round(pos.upcoming_dividend_usd - time_value_ex, 4)
                    exercise_favoured = edge > 0.0
                    test_used = "MERTON_PUT_PARITY"
                    basis = (
                        f"D (${pos.upcoming_dividend_usd:.2f}) vs ex-dividend time "
                        f"value (${time_value_ex:.2f} = put "
                        f"${pos.same_strike_put_price:.2f} + strike interest "
                        f"${interest_term:.2f})")
                else:
                    # Conservative fallback screen; over-flags by construction.
                    exercise_favoured = pos.upcoming_dividend_usd > extrinsic
                    test_used = "EXTRINSIC_SCREEN"
                    basis = (
                        f"D (${pos.upcoming_dividend_usd:.2f}) vs cum-dividend "
                        f"extrinsic (${extrinsic:.2f}); conservative screen, "
                        f"supply same_strike_put_price for the exact test")

                if exercise_favoured and pos.days_to_ex_div <= self.ex_div_decision_days:
                    risk_lvl = self._escalate(risk_lvl, "CRITICAL_ASSIGNMENT_RISK")
                    action = "CLOSE_OR_ROLL_SHORT_CALL"
                    score = max(score, 95.0)
                    dividend_liability = round(
                        pos.upcoming_dividend_usd * pos.contracts_qty
                        * pos.contract_multiplier, 2)
                    summaries.append(
                        f"EX-DIVIDEND ASSIGNMENT RISK [{pos.symbol}]: {basis}. "
                        f"Early exercise is economically favoured for holders and "
                        f"the decision window is open (ex-div in "
                        f"{pos.days_to_ex_div:.2f}d). Assignment before the "
                        f"ex-date leaves the writer short the stock over the "
                        f"record date, owing ${dividend_liability:,.2f} in lieu "
                        f"of dividend. Act before the broker's exercise cutoff on "
                        f"the last cum-dividend session.")
                elif exercise_favoured and pos.days_to_ex_div <= self.ex_div_warning_days:
                    risk_lvl = self._escalate(risk_lvl, "ELEVATED_ASSIGNMENT_RISK")
                    if action == "NO_ACTION_REQUIRED":
                        action = "MONITOR"
                    score = max(score, 55.0)
                    summaries.append(
                        f"EX-DIVIDEND PRE-WARNING [{pos.symbol}]: {basis}. Ex-div "
                        f"in {pos.days_to_ex_div:.2f}d; the decision window opens "
                        f"inside {self.ex_div_decision_days:.2f}d.")

        else:  # PUT
            # Exercising a put frees the strike as cash, which then earns
            # interest, so extrinsic <= 0 is the exercise boundary. Report the
            # carry edge when the same-strike call price is available:
            # put extrinsic = call - K(1 - e^-r*tau) + PV(dividends to expiry).
            interest_term = self._interest_on_strike(K, pos.days_to_expiry)
            if pos.same_strike_call_price is not None:
                dividend_to_expiry = (
                    pos.upcoming_dividend_usd
                    if pos.days_to_ex_div <= pos.days_to_expiry else 0.0)
                edge = round(
                    interest_term - pos.same_strike_call_price - dividend_to_expiry, 4)
                test_used = "MERTON_PUT_PARITY"

        if at_parity:
            risk_lvl = self._escalate(risk_lvl, "HIGH_ASSIGNMENT_RISK")
            # A CRITICAL ex-dividend verdict already carries the close/roll
            # directive; otherwise parity itself is enough to demand one.
            if risk_lvl != "CRITICAL_ASSIGNMENT_RISK":
                action = ("CLOSE_OR_ROLL_SHORT_CALL" if opt_type == "CALL"
                          else "CLOSE_OR_ROLL_SHORT_PUT")
            score = max(score, 80.0)
            summaries.append(
                f"AT-PARITY ASSIGNMENT RISK [{pos.symbol}]: ITM short "
                f"{opt_type.lower()} with extrinsic ${extrinsic:.2f} at or below "
                f"the ${parity_threshold:.2f} parity threshold. A holder gives up "
                f"nothing by exercising, so assignment can occur on any session.")

        if quoted_below_parity:
            summaries.append(
                "Quote is below intrinsic value: exercising already beats selling "
                "for the holder, and the quote may be stale or crossed. Verify "
                "the mark before acting.")

        if not summaries:
            summaries.append(
                f"Extrinsic value (${extrinsic:.2f}) exceeds the parity threshold "
                f"(${parity_threshold:.2f}) and no favourable dividend capture is "
                f"in window; early exercise is not economically favoured.")

        summary = " ".join(summaries)
        if risk_lvl == "CRITICAL_ASSIGNMENT_RISK":
            logger.critical(summary)
        elif risk_lvl in ("HIGH_ASSIGNMENT_RISK", "ELEVATED_ASSIGNMENT_RISK"):
            logger.warning(summary)
        else:
            logger.debug(summary)

        return EarlyExerciseAuditReport(
            position_id=pos.position_id,
            symbol=pos.symbol,
            option_type=opt_type,
            exercise_style=style,
            strike=K,
            underlying_price=S,
            intrinsic_value_usd=intrinsic,
            extrinsic_value_usd=extrinsic,
            upcoming_dividend_usd=pos.upcoming_dividend_usd,
            assignment_risk_score=score,
            risk_level=risk_lvl,
            recommended_action=action,
            risk_summary=summary,
            exercise_test_used=test_used,
            early_exercise_edge_usd=edge,
            assigned_share_notional_usd=assigned_notional,
            dividend_liability_usd=dividend_liability,
            quoted_below_parity=quoted_below_parity,
            data_quality_flags=data_quality_flags,
        )
