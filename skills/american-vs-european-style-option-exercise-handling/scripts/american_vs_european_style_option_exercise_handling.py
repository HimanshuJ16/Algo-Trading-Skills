"""american-vs-european-style-option-exercise-handling:
Holder-side exercise-vs-sell decision for American-style options.

Scope
-----
This module answers one question for the **holder** of a long American option:
right now, is exercising worth more than selling? European-style options are out
of scope -- they carry no early-exercise decision at all.

The decision rule
-----------------
Exercising a call delivers stock worth ``spot_price`` (cum-dividend, while the
underlying has not yet gone ex) against payment of ``strike_price``, so the
holder realises exactly ``spot - strike``: the intrinsic value, and nothing
else. Selling the option realises the bid. Therefore

    exercise early  <=>  intrinsic value > proceeds of selling

and that single comparison is the whole rule, for calls and puts alike. It needs
no interest rate, no volatility and no time to expiry, because every one of them
is already priced into the quote the holder can sell into. The same argument in
its other direction: a holder who actually wants the shares can sell the call and
buy the stock for ``spot - bid``, which beats the ``strike`` paid on exercise
exactly when ``bid > intrinsic``.

Why the dividend is not a separate trigger
------------------------------------------
The familiar desk rule "exercise the call if the dividend exceeds the option's
remaining time value" compares the dividend against the **cum-dividend** time
value, which already reflects the coming price drop. By put-call parity the
cum-dividend time value of a call is

    TV_cum = TV_ex - PV(D)

where ``TV_ex`` is the call's time value at the ex-dividend underlying price.
Feeding ``TV_cum`` into "D > TV" therefore counts the dividend twice and fires
across the whole region ``0 <= TV_cum < D`` -- a region in which selling realises
``intrinsic + TV_cum`` while exercising realises only ``intrinsic``. For the
*writer* of the option that over-flagging is harmless and deliberate (see
``early-exercise-assignment-risk-management``, which uses it as a conservative
assignment screen). For the *holder* it is a standing instruction to give away
``TV_cum`` per share.

``EarlyExerciseEvaluator.evaluate`` therefore decides on the quote alone. The
dividend inputs shape the explanation and tell the operator when the decision is
live. The exact model-based condition is available separately as
``EarlyExerciseEvaluator.dividend_capture_test`` for use when the option's own
quote cannot be trusted.

References: ``references/standards.md``.
"""
from __future__ import annotations

import dataclasses
import logging
import math
from typing import Tuple

logger = logging.getLogger(__name__)

VALID_OPTION_TYPES = frozenset({"CALL", "PUT"})

_PRICE_FIELDS = ("spot_price", "strike_price", "market_price", "dividend_amount")


def _require_finite_non_negative(value: object, name: str) -> float:
    """Validate a per-share money amount. Rejects bool, NaN, inf and negatives."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}.")
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")
    if number < 0.0:
        raise ValueError(f"{name} must be non-negative, got {value!r}.")
    return number


@dataclasses.dataclass(frozen=True)
class OptionState:
    """Immutable snapshot of one American option position, from the holder's side.

    Attributes:
        option_type: ``'CALL'`` or ``'PUT'`` (case-insensitive, normalised to
            uppercase). An unrecognised value is rejected, never defaulted.
        spot_price: Current price of the underlying. Cum-dividend while the
            underlying has not yet gone ex.
        strike_price: Strike price of the option.
        market_price: **The bid the holder could actually sell into**, per share
            -- not the mid and not the last trade. The alternative to exercising
            is selling, and a sale realises the bid. At the true early-exercise
            boundary an American option's fair value sits exactly at parity, so a
            mid-based comparison systematically misses live exercise decisions,
            and a last-trade-based one can invent them out of stale prints.
        is_ex_dividend_tomorrow: True on the last cum-dividend session, i.e. the
            session on which an exercise still settles onto the record-date books.
        dividend_amount: Declared dividend per share, gross. Context for the
            explanation and input to ``dividend_capture_test``; it is deliberately
            not an input to ``evaluate`` -- see the module docstring.
    """

    option_type: str
    spot_price: float
    strike_price: float
    market_price: float

    is_ex_dividend_tomorrow: bool = False
    dividend_amount: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.option_type, str):
            raise ValueError(
                f"option_type must be a string, got {self.option_type!r}."
            )
        normalized = self.option_type.strip().upper()
        if normalized not in VALID_OPTION_TYPES:
            raise ValueError(
                f"Invalid option_type {self.option_type!r}. "
                f"Must be one of {sorted(VALID_OPTION_TYPES)}."
            )
        object.__setattr__(self, "option_type", normalized)

        for field_name in _PRICE_FIELDS:
            validated = _require_finite_non_negative(
                getattr(self, field_name), field_name
            )
            object.__setattr__(self, field_name, validated)

    @property
    def intrinsic_value(self) -> float:
        """Value realised by exercising now: ``max(0, S - K)`` / ``max(0, K - S)``."""
        if self.option_type == "CALL":
            return max(0.0, self.spot_price - self.strike_price)
        return max(0.0, self.strike_price - self.spot_price)

    @property
    def time_value(self) -> float:
        """Quoted time value, ``bid - intrinsic``.

        Deliberately **not** clamped at zero: a negative value is the below-parity
        condition this engine exists to detect, and clamping would hide it.
        """
        return self.market_price - self.intrinsic_value

    @property
    def early_exercise_edge(self) -> float:
        """Per-share gain from exercising rather than selling, ``intrinsic - bid``.

        Positive means exercising beats selling. Negative means selling beats
        exercising by that amount -- the value an unnecessary exercise destroys.
        """
        return self.intrinsic_value - self.market_price


@dataclasses.dataclass(frozen=True)
class DividendCaptureTest:
    """Result of the exact ex-dividend early-exercise condition for a call."""

    is_exercise_optimal: bool
    dividend: float
    time_value_ex_dividend: float
    put_price: float
    interest_on_strike: float
    detail: str


class EarlyExerciseEvaluator:
    """Decides whether the holder of an American option should exercise now.

    The primary rule is ``evaluate``, which compares the intrinsic value against
    the option's bid. ``dividend_capture_test`` implements the exact model-based
    ex-dividend condition for calls, for use when the call's own quote is stale,
    crossed or absent.
    """

    # --- primary, quote-based decision ---------------------------------

    def evaluate(self, state: OptionState) -> Tuple[bool, str]:
        """Returns ``(should_exercise, reason)`` for the given state.

        The decision is ``intrinsic > bid``. At exact parity the two routes are
        worth the same and the engine does **not** exercise: selling avoids taking
        delivery, avoids finding the cash to pay the strike, and leaves no
        resulting stock position to manage, at no cost in value.

        ``should_exercise=False`` means *do not exercise*, which is not the same
        as *do nothing*. An ITM option quoted at parity has no time value left to
        protect, and holding it through an ex-date surrenders the dividend for the
        smaller ex-dividend time value. The reason string says so when that is the
        case; route on the reason, not on the flag alone.
        """
        intrinsic = state.intrinsic_value
        bid = state.market_price
        edge = state.early_exercise_edge
        has_dividend = state.is_ex_dividend_tomorrow and state.dividend_amount > 0.0

        if intrinsic <= 0.0:
            reason = (
                "Do not exercise: the option is out-of-the-money (OTM) or "
                "at-the-money (ATM), so exercising realises nothing."
            )
            logger.debug("%s exercise=False: %s", state.option_type, reason)
            return False, reason

        if edge > 0.0:
            reason = (
                f"Exercise: intrinsic value ({intrinsic:.4f}) exceeds the bid "
                f"({bid:.4f}) by {edge:.4f} per share, so exercising realises more "
                f"than selling."
            )
            if has_dividend and state.option_type == "CALL":
                reason += (
                    f" An ex-dividend date is pending ({state.dividend_amount:.4f} "
                    f"per share); a bid falling to or below parity is the expected "
                    f"signature of a dividend-driven exercise. Submit the notice "
                    f"before the carrying firm's cut-off on this session."
                )
            elif has_dividend:
                reason += (
                    f" The pending {state.dividend_amount:.4f} dividend is not the "
                    f"driver here: a dividend before expiry makes early exercise of a "
                    f"put less attractive, not more, because the exercising holder "
                    f"gives up the stock and the dividend with it. Verify the quote "
                    f"is executable before acting."
                )
            else:
                reason += (
                    " Confirm the quote is live rather than stale, wide or crossed "
                    "before acting -- an executable below-parity bid is unusual."
                )
            logger.info("%s exercise=True: %s", state.option_type, reason)
            return True, reason

        forgone = -edge
        if state.option_type == "CALL":
            if has_dividend:
                reason = (
                    f"Do not exercise: the bid ({bid:.4f}) is at or above intrinsic "
                    f"({intrinsic:.4f}); selling realises {forgone:.4f} more per share "
                    f"than exercising. The bid is cum-dividend and already prices the "
                    f"pending {state.dividend_amount:.4f} dividend, so comparing that "
                    f"dividend against the quoted time value would count it twice. If "
                    f"the quote is not trustworthy, run dividend_capture_test() before "
                    f"acting."
                )
            else:
                reason = (
                    f"Do not exercise: with no imminent dividend, early exercise of an "
                    f"American call is never optimal (Merton 1973). The bid ({bid:.4f}) "
                    f"is at or above intrinsic ({intrinsic:.4f}); sell to capture the "
                    f"{forgone:.4f} per share of time value that exercising forfeits."
                )
        else:
            reason = (
                f"Do not exercise: the bid ({bid:.4f}) is at or above intrinsic "
                f"({intrinsic:.4f}); selling realises {forgone:.4f} more per share. A "
                f"put's early-exercise value comes from interest on the strike, and "
                f"that is already embedded in the quote."
            )
        if edge == 0.0:
            reason += (
                " The quote is exactly at parity, so there is no time value left to "
                "protect: selling and exercising are worth the same, but holding is "
                "worth less than either. Close the position before the carrying "
                "firm's cut-off rather than doing nothing."
            )
        logger.debug("%s exercise=False: %s", state.option_type, reason)
        return False, reason

    # --- exact model-based ex-dividend condition (calls only) ----------

    def dividend_capture_test(
        self,
        state: OptionState,
        same_strike_put_price: float,
        risk_free_rate: float,
        years_to_expiry: float,
    ) -> DividendCaptureTest:
        """Exact condition for exercising a call immediately before an ex-date.

        Exercise is optimal exactly when the dividend exceeds the call's time
        value at the *ex-dividend* underlying price:

            D > TV_ex = p_ex + K * (1 - exp(-r * tau))

        where ``p_ex`` is the same-strike, same-expiry put and the second term is
        the interest forgone by paying the strike now instead of at expiry
        (Merton 1973). Because ``p_ex >= 0``, ``D > K * (1 - exp(-r * tau))`` is a
        necessary condition -- the form usually quoted in textbooks, and one that
        is necessary but not sufficient on its own.

        Use this only when the call's own bid cannot be trusted. Given a fair,
        executable call quote it is algebraically the same test as ``evaluate``,
        and where the two disagree the call quote is stale or crossed -- that is a
        data-quality finding, not a licence to exercise on the model.

        Args:
            state: A ``CALL`` state on its last cum-dividend session.
            same_strike_put_price: Price of the same-strike, same-expiry put.
            risk_free_rate: Continuously compounded annual rate (may be negative).
            years_to_expiry: Time to expiry in years, ``tau``.

        Raises:
            ValueError: For a put, for a state with no pending dividend, or for
                any invalid model input.
        """
        if state.option_type != "CALL":
            raise ValueError(
                "dividend_capture_test applies to calls only. A dividend before "
                "expiry makes early exercise of a put less attractive, not more: "
                "the exercising put holder gives up the stock and the dividend "
                "with it."
            )
        if not (state.is_ex_dividend_tomorrow and state.dividend_amount > 0.0):
            raise ValueError(
                "dividend_capture_test requires is_ex_dividend_tomorrow=True and a "
                "positive dividend_amount. Outside the last cum-dividend session "
                "there is no dividend to capture, and early exercise of an American "
                "call is never optimal (Merton 1973)."
            )

        put_price = _require_finite_non_negative(
            same_strike_put_price, "same_strike_put_price"
        )
        tau = _require_finite_non_negative(years_to_expiry, "years_to_expiry")
        if isinstance(risk_free_rate, bool) or not isinstance(
            risk_free_rate, (int, float)
        ):
            raise ValueError(
                f"risk_free_rate must be a real number, got {risk_free_rate!r}."
            )
        rate = float(risk_free_rate)
        if math.isnan(rate) or math.isinf(rate):
            raise ValueError(
                f"risk_free_rate must be a finite number, got {risk_free_rate!r}."
            )

        try:
            discount = math.exp(-rate * tau)
        except OverflowError as exc:  # deeply negative rate over a long tenor
            raise ValueError(
                f"Discount factor overflowed for risk_free_rate={risk_free_rate!r} "
                f"over {years_to_expiry!r} years; check the rate convention and the "
                f"day count."
            ) from exc

        interest_on_strike = state.strike_price * (1.0 - discount)
        time_value_ex_dividend = put_price + interest_on_strike
        is_optimal = state.dividend_amount > time_value_ex_dividend

        detail = (
            f"D={state.dividend_amount:.4f} vs TV_ex={time_value_ex_dividend:.4f} "
            f"(put {put_price:.4f} + interest on strike {interest_on_strike:.4f}). "
            + (
                "Dividend exceeds the ex-dividend time value: exercise is optimal."
                if is_optimal
                else "Dividend does not exceed the ex-dividend time value: hold or sell."
            )
        )
        logger.debug("dividend_capture_test: %s", detail)
        return DividendCaptureTest(
            is_exercise_optimal=is_optimal,
            dividend=state.dividend_amount,
            time_value_ex_dividend=time_value_ex_dividend,
            put_price=put_price,
            interest_on_strike=interest_on_strike,
            detail=detail,
        )
